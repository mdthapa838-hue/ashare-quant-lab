# -*- coding: utf-8 -*-
"""
backtest.py — 组合级回测引擎

特性：
  1. 日频事件驱动，信号次日开盘成交
  2. 涨跌停约束（开盘涨幅>9.5%不可买，跌幅<-9.5%不可卖）
  3. 完整成本模型（佣金/印花税/滑点）
  4. 组合资金约束（最多 N 只等权）
  5. 输出资金曲线、交易明细、完整风险指标

时间轴设计：
  使用统一交易日历（来自市场指数）。每只股票按自身日期映射到该日历，
  避免不同标的停牌导致的索引错位。
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# 成本模型
# ---------------------------------------------------------------------------
class CostModel:
    """交易成本模型。"""

    def __init__(self, cfg: dict):
        c = cfg.get("cost", {})
        self.commission = c.get("commission", 0.00025)
        self.stamp_tax = c.get("stamp_tax", 0.0005)
        self.slippage = c.get("slippage", 0.00075)

    @property
    def buy_rate(self) -> float:
        """买入单边成本率。"""
        return self.commission + self.slippage

    @property
    def sell_rate(self) -> float:
        """卖出单边成本率（含印花税）。"""
        return self.commission + self.stamp_tax + self.slippage

    @property
    def round_trip(self) -> float:
        """双边往返成本率。"""
        return self.buy_rate + self.sell_rate

    def __repr__(self):
        return (f"CostModel(buy={self.buy_rate:.5f}, sell={self.sell_rate:.5f}, "
                f"round_trip={self.round_trip:.5f})")


# ---------------------------------------------------------------------------
# 回测引擎
# ---------------------------------------------------------------------------
class Backtester:
    """
    组合回测引擎。

    Parameters
    ----------
    bundle : DataBundle
        已加载全部标的指标数据。
    signals : DataFrame
        collect_signals 输出。
    cfg : dict
        策略配置。
    codes : list[str], optional
        限定标的范围（默认使用 bundle 全部）。
    """

    def __init__(self, bundle, signals: pd.DataFrame, cfg: dict,
                 codes: list[str] | None = None):
        self.bundle = bundle
        self.signals = signals.copy()
        self.cfg = cfg
        self.codes = codes or bundle.codes()

        self.cost = CostModel(cfg)
        pf = cfg.get("portfolio", {})
        self.max_positions = pf.get("max_positions", 5)
        self.max_pos_pct = pf.get("max_position_pct", 0.25)
        self.initial_capital = pf.get("initial_capital", 1_000_000)

        ex = cfg.get("exit", {})
        self.take_profit = ex.get("take_profit", "upper")
        self.stop_loss_pct = ex.get("stop_loss_pct", -0.12)
        self.max_hold_days = ex.get("max_hold_days", 60)
        self.stop_mode = ex.get("stop_mode", "fixed")

        cn = cfg.get("constraints", {})
        self.no_buy_limit_up = cn.get("no_buy_limit_up", 0.095)
        self.no_sell_limit_down = cn.get("no_sell_limit_down", -0.095)

        self.risk = cfg.get("risk", {})

        # 建立 code -> {date: row_index} 映射
        # 注意：键统一用 pd.Timestamp，与日历元素类型一致，避免
        # numpy.datetime64 与 pd.Timestamp 哈希不相等导致的查表失败。
        self._idxmap = {}
        for c in self.codes:
            d = self.bundle.get(c)
            if d is not None:
                self._idxmap[c] = {
                    pd.Timestamp(dt): i for i, dt in enumerate(d["date"].tolist())
                }

        # 交易日历
        self.calendar = self._build_calendar()

        # 信号按入场日分组
        self._sig_by_entry = {}
        if len(self.signals):
            for _, r in self.signals.iterrows():
                self._sig_by_entry.setdefault(r["entry_date"], []).append(r["code"])

        # 标的行业（用于集中度控制）
        self.industry = {}
        indf = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))),
            "research", "smallcap_20261014", "out", "kline_features.csv")
        if os.path.exists(indf):
            try:
                t = pd.read_csv(indf, dtype={"code": str})
                if "行业" in t.columns:
                    self.industry = dict(zip(t["code"].str.zfill(6), t["行业"]))
            except Exception:
                pass

    # ---------------------------------------------------------------- 日历
    def _build_calendar(self) -> list:
        bt = self.cfg.get("backtest", {})
        start = bt.get("start_date", "2015-01-01")
        end = bt.get("end_date") or None

        if self.bundle.index_close is not None:
            cal = list(self.bundle.index_close.index)
        else:
            all_dates = set()
            for c in self.codes:
                d = self.bundle.get(c)
                if d is not None:
                    all_dates.update(d["date"].values)
            cal = sorted(all_dates)

        cal = [pd.Timestamp(x) for x in cal if pd.Timestamp(x) >= pd.Timestamp(start)]
        if end:
            cal = [x for x in cal if x <= pd.Timestamp(end)]
        return cal

    # ---------------------------------------------------------------- 主循环
    def run(self, verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        执行回测。

        Returns
        -------
        equity : DataFrame(date, equity, cash, n_positions, drawdown)
        trades : DataFrame(每笔交易明细)
        """
        cash = float(self.initial_capital)
        positions: dict[str, dict] = {}
        equity_rows, trade_rows = [], []
        peak = self.initial_capital

        for di, dt in enumerate(self.calendar):

            # ---------------- 1. 出场：用「昨日」已确认条件，今日开盘执行
            for code in list(positions.keys()):
                pos = positions[code]
                d = self.bundle.get(code)
                im = self._idxmap.get(code)
                if d is None or dt not in im:
                    continue
                j = im[dt]
                y = j - 1
                if y < 0:
                    continue

                trig = self._check_exit(d, pos, y)
                if trig is None:
                    # 更新持仓状态
                    pos["days"] += 1
                    hi = d["high"].iloc[j]
                    lo = d["low"].iloc[j]
                    pos["mfe"] = max(pos["mfe"], (hi / pos["entry_price"] - 1) * 100)
                    pos["mae"] = min(pos["mae"], (lo / pos["entry_price"] - 1) * 100)
                    continue

                # 执行卖出（今日开盘），受跌停约束
                oc = d["open_chg_pct"].iloc[j]
                if pd.notna(oc) and oc < self.no_sell_limit_down * 100:
                    # 跌停无法卖出，继续持有
                    pos["days"] += 1
                    pos["delayed_exit"] = trig
                    continue

                xp = d["open"].iloc[j]
                if not np.isfinite(xp) or xp <= 0:
                    xp = d["close"].iloc[j]
                gross = pos["shares"] * xp
                proceeds = gross * (1 - self.cost.sell_rate)
                cash += proceeds

                net_ret = (proceeds / pos["cost_basis"] - 1)
                trade_rows.append({
                    "code": code,
                    "industry": self.industry.get(code, ""),
                    "entry_date": pos["entry_date"],
                    "exit_date": dt,
                    "entry_price": pos["entry_price"],
                    "exit_price": xp,
                    "shares": pos["shares"],
                    "days": pos["days"],
                    "exit_reason": trig,
                    "gross_return": (xp / pos["entry_price"] - 1),
                    "net_return": net_ret,
                    "mfe_pct": pos["mfe"],
                    "mae_pct": pos["mae"],
                    "pnl": proceeds - pos["cost_basis"],
                })
                del positions[code]

            # ---------------- 2. 入场：信号日次日开盘
            cands = self._sig_by_entry.get(dt, [])
            if cands:
                free = self.max_positions - len(positions)
                if free > 0:
                    # 当前总权益
                    total = cash
                    for c, p in positions.items():
                        d = self.bundle.get(c)
                        im = self._idxmap.get(c)
                        if d is not None and dt in im:
                            total += d["close"].iloc[im[dt]] * p["shares"]
                        else:
                            total += p["entry_price"] * p["shares"]
                    per_slot = total / self.max_positions
                    cap = total * self.max_pos_pct

                    picked = 0
                    for code in cands:
                        if picked >= free:
                            break
                        if code in positions:
                            continue
                        d = self.bundle.get(code)
                        im = self._idxmap.get(code)
                        if d is None or dt not in im:
                            continue
                        j = im[dt]

                        # 涨停不可买
                        oc = d["open_chg_pct"].iloc[j]
                        if pd.notna(oc) and oc > self.no_buy_limit_up * 100:
                            continue
                        op = d["open"].iloc[j]
                        if not np.isfinite(op) or op <= 0:
                            continue

                        # 行业集中度
                        max_sec = self.risk.get("sector_concentration_max")
                        if max_sec:
                            sec = self.industry.get(code)
                            if sec:
                                same = sum(1 for c2 in positions
                                           if self.industry.get(c2) == sec)
                                if same >= max_sec:
                                    continue

                        alloc = min(per_slot, cap, cash * 0.999)
                        if alloc < self.initial_capital * 0.01:
                            continue

                        # 买入成本
                        cost_basis = alloc
                        shares = (alloc * (1 - self.cost.buy_rate)) / op
                        if shares <= 0:
                            continue
                        cash -= alloc

                        positions[code] = {
                            "entry_date": dt,
                            "entry_price": op,
                            "shares": shares,
                            "cost_basis": cost_basis,
                            "days": 0,
                            "mfe": 0.0,
                            "mae": 0.0,
                            "stop": self._initial_stop(d, j, op),
                            "delayed_exit": None,
                        }
                        picked += 1

            # ---------------- 3. 记录权益
            eq = cash
            for c, p in positions.items():
                d = self.bundle.get(c)
                im = self._idxmap.get(c)
                if d is not None and dt in im:
                    eq += d["close"].iloc[im[dt]] * p["shares"]
                else:
                    eq += p["entry_price"] * p["shares"]
            peak = max(peak, eq)
            equity_rows.append({
                "date": dt, "equity": eq, "cash": cash,
                "n_positions": len(positions),
                "drawdown": eq / peak - 1,
            })

            if verbose and (di + 1) % 500 == 0:
                print(f"  {di+1}/{len(self.calendar)}  权益 {eq:,.0f}  "
                      f"持仓 {len(positions)}  已平仓 {len(trade_rows)}", flush=True)

        equity = pd.DataFrame(equity_rows)
        trades = pd.DataFrame(trade_rows)
        return equity, trades

    # ---------------------------------------------------------------- 出场判定
    def _check_exit(self, d: pd.DataFrame, pos: dict, y: int) -> str | None:
        """检查第 y 根 K 线是否触发离场条件。返回原因或 None。"""
        # 被延后的出场条件（之前跌停卖不掉）
        if pos.get("delayed_exit"):
            return pos["delayed_exit"]

        lo = d["low"].iloc[y]
        hi = d["high"].iloc[y]

        # 止损优先
        if lo < pos["stop"]:
            return "stop"

        # 止盈
        tgt = d["bb_upper"].iloc[y] if self.take_profit == "upper" else d["bb_mid"].iloc[y]
        if pd.notna(tgt) and hi >= tgt:
            return "take_profit"

        # 时限
        if pos["days"] >= self.max_hold_days:
            return "timeout"

        return None

    def _initial_stop(self, d: pd.DataFrame, j: int, entry_price: float) -> float:
        """计算初始止损价。"""
        if self.stop_mode == "entry_low":
            return float(d["low"].iloc[j])
        if self.stop_mode == "atr":
            mult = self.cfg.get("exit", {}).get("atr_mult", 2.0)
            a = d["atr14"].iloc[j]
            if pd.notna(a) and a > 0:
                return float(entry_price - mult * a)
            return float(entry_price * (1 + self.stop_loss_pct))
        # fixed
        return float(entry_price * (1 + self.stop_loss_pct))


# ---------------------------------------------------------------------------
# 绩效指标
# ---------------------------------------------------------------------------
def performance(equity: pd.DataFrame, trades: pd.DataFrame, cfg: dict,
                label: str = "strategy") -> dict:
    """计算完整绩效指标。"""
    if len(equity) < 2:
        return {"策略": label, "错误": "权益曲线为空"}

    eq = equity["equity"].values
    dates = pd.to_datetime(equity["date"])
    years = max((dates.iloc[-1] - dates.iloc[0]).days / 365.25, 1e-6)

    total_ret = eq[-1] / eq[0] - 1
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1

    rets = pd.Series(eq).pct_change().dropna()
    vol = rets.std() * np.sqrt(244) if len(rets) > 1 else 0.0
    rf = cfg.get("backtest", {}).get("risk_free_rate", 0.02)
    sharpe = (rets.mean() * 244 - rf) / vol if vol > 0 else np.nan

    downside = rets[rets < 0].std() * np.sqrt(244)
    sortino = (rets.mean() * 244 - rf) / downside if downside and downside > 0 else np.nan

    dd = equity["drawdown"].min()
    calmar = cagr / abs(dd) if dd and dd != 0 else np.nan

    out = {
        "策略": label,
        "总收益%": round(total_ret * 100, 1),
        "CAGR%": round(cagr * 100, 2),
        "年化波动%": round(vol * 100, 2),
        "Sharpe": round(sharpe, 2) if np.isfinite(sharpe) else np.nan,
        "Sortino": round(sortino, 2) if np.isfinite(sortino) else np.nan,
        "最大回撤%": round(dd * 100, 2),
        "Calmar": round(calmar, 2) if np.isfinite(calmar) else np.nan,
        "年数": round(years, 1),
    }

    if trades is not None and len(trades):
        r = trades["net_return"]
        wins = r[r > 0]
        losses = r[r < 0]
        out.update({
            "交易笔数": len(trades),
            "胜率%": round((r > 0).mean() * 100, 1),
            "平均单笔%": round(r.mean() * 100, 2),
            "平均盈利%": round(wins.mean() * 100, 2) if len(wins) else np.nan,
            "平均亏损%": round(losses.mean() * 100, 2) if len(losses) else np.nan,
            "盈亏比": round(wins.mean() / abs(losses.mean()), 2) if len(losses) and losses.mean() != 0 else np.nan,
            "期望值%": round(r.mean() * 100, 2),
            "平均持有天": round(trades["days"].mean(), 1),
            "年均交易": round(len(trades) / years, 1),
            "止损率%": round((trades["exit_reason"] == "stop").mean() * 100, 1),
            "止盈率%": round((trades["exit_reason"] == "take_profit").mean() * 100, 1),
            "超时率%": round((trades["exit_reason"] == "timeout").mean() * 100, 1),
            "最大单笔盈利%": round(r.max() * 100, 2),
            "最大单笔亏损%": round(r.min() * 100, 2),
        })
    return out
