# -*- coding: utf-8 -*-
"""
tushare_trader/backtest.py — 组合级事件驱动回测

与实盘/模拟盘共用同一套 `BollBandStrategy` 与 `CostModel`，
保证「回测怎么算，实盘就怎么算」。

时间轴
------
统一使用交易日历（来自指数或全部标的日期并集），
每只股票按自身日期映射到日历，避免停牌导致的索引错位。

成交规则
--------
  * 信号在 T 日收盘后生成 -> T+1 开盘成交
  * 出场判定用 T 日收盘数据 -> T+1 开盘成交
  * 涨停不买、跌停不卖
  * 整手买入、全仓卖出
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .costs import CostModel
from .strategy import BollBandStrategy, MIN_BARS

LOG = logging.getLogger("tushare_trader.backtest")


class BacktestResult:
    """回测结果容器。"""

    def __init__(self, equity: pd.DataFrame, trades: pd.DataFrame, metrics: dict):
        self.equity = equity
        self.trades = trades
        self.metrics = metrics

    def __repr__(self) -> str:
        return f"BacktestResult(metrics={self.metrics})"


class Backtester:
    """
    组合回测引擎。

    Parameters
    ----------
    data : dict[str, pd.DataFrame]
        code -> 已计算指标的 K 线（compute_indicators 输出）。
    cfg : dict
        策略配置（含 entry/exit/portfolio/cost/risk/universe）。
    index_close : Series, optional
        基准指数收盘序列（用于交易日历与基准对比）。
    """

    def __init__(self, data: dict[str, pd.DataFrame], cfg: dict,
                 index_close: pd.Series | None = None):
        self.data = {str(k): v for k, v in data.items() if v is not None and len(v) >= MIN_BARS}
        self.cfg = cfg
        self.strategy = BollBandStrategy(cfg)
        self.cost = CostModel.from_config(cfg)
        self.index_close = index_close

        pf = cfg.get("portfolio", {})
        self.initial_capital = float(pf.get("initial_capital", 1_000_000))
        risk = cfg.get("risk", {})
        self.sector_max = int(risk.get("sector_concentration_max", 2) or 0)
        self.daily_max_orders = int(risk.get("daily_max_orders", 20) or 20)

        self._idxmap = {
            code: {pd.Timestamp(d): i for i, d in enumerate(df["date"].tolist())}
            for code, df in self.data.items()
        }
        self.calendar = self._build_calendar()

    # ------------------------------------------------------------ 日历
    def _build_calendar(self) -> list[pd.Timestamp]:
        if self.index_close is not None and len(self.index_close):
            dates = list(pd.DatetimeIndex(self.index_close.index))
        else:
            all_dates: set = set()
            for df in self.data.values():
                all_dates.update(df["date"].values)
            dates = sorted(pd.Timestamp(d) for d in all_dates)
        bt = self.cfg.get("backtest", {})
        start = bt.get("start_date")
        end = bt.get("end_date")
        if start:
            dates = [d for d in dates if d >= pd.Timestamp(start)]
        if end:
            dates = [d for d in dates if d <= pd.Timestamp(end)]
        return sorted(dates)

    # ------------------------------------------------------------ 主循环
    def run(self, verbose: bool = False) -> BacktestResult:
        cash = self.initial_capital
        positions: dict[str, dict] = {}
        equity_rows: list[dict] = []
        trade_rows: list[dict] = []
        peak = self.initial_capital

        for di, dt in enumerate(self.calendar):
            date_str = dt.strftime("%Y-%m-%d")
            n_orders = 0

            # ---------------- 1. 出场（昨日收盘触发 -> 今日开盘成交）
            for code in list(positions.keys()):
                pos = positions[code]
                df = self.data.get(code)
                im = self._idxmap.get(code)
                if df is None or im is None or dt not in im:
                    continue
                j = im[dt]
                trig = pos.get("pending_exit")
                if trig is None:
                    continue

                bar = df.iloc[j]
                chg = bar.get("open_chg_pct")
                max_down = float(self.cfg.get("constraints", {}).get(
                    "no_sell_limit_down", -0.095)) * 100
                if pd.notna(chg) and float(chg) < max_down:
                    continue          # 跌停卖不掉，条件保留到下一交易日

                xp = float(bar["open"])
                if not np.isfinite(xp) or xp <= 0:
                    continue
                amount = pos["shares"] * xp
                proceeds = amount - self.cost.sell_cost(amount)
                cash += proceeds
                trade_rows.append({
                    "code": code,
                    "entry_date": pos["entry_date"],
                    "exit_date": date_str,
                    "entry_price": pos["entry_price"],
                    "exit_price": xp,
                    "shares": pos["shares"],
                    "days": pos["days"],
                    "exit_reason": trig,
                    "net_return": proceeds / pos["cost_basis"] - 1,
                    "pnl": proceeds - pos["cost_basis"],
                })
                del positions[code]

            # ---------------- 2. 更新持仓状态 + 生成出场信号
            for code, pos in positions.items():
                df = self.data.get(code)
                im = self._idxmap.get(code)
                if df is None or im is None or dt not in im:
                    continue
                j = im[dt]
                bar = df.iloc[j]
                pos["days"] += 1
                if pos.get("pending_exit") is None:
                    reason = self.strategy.exit_reason(bar, pos, pos["days"])
                    if reason:
                        pos["pending_exit"] = reason
                        n_orders += 1

            # ---------------- 3. 入场（昨日信号 -> 今日开盘成交）
            free = self.strategy.max_positions - len(positions)
            if free > 0 and n_orders < self.daily_max_orders:
                marks = self._marks(dt, positions)
                equity_now = cash + sum(marks.values())
                per_slot = equity_now / self.strategy.max_positions
                cap = equity_now * self.strategy.max_position_pct

                for code, df in self.data.items():
                    if free <= 0:
                        break
                    if code in positions:
                        continue
                    im = self._idxmap[code]
                    if dt not in im:
                        continue
                    j = im[dt]
                    if j < 1:
                        continue
                    sig_bar = df.iloc[j - 1]       # 昨日收盘信号
                    if not bool(sig_bar.get("signal", False)):
                        continue

                    bar = df.iloc[j]
                    chg = bar.get("open_chg_pct")
                    max_up = float(self.cfg.get("constraints", {}).get(
                        "no_buy_limit_up", 0.095)) * 100
                    if pd.notna(chg) and float(chg) > max_up:
                        continue                     # 涨停买不到
                    op = float(bar["open"])
                    if not np.isfinite(op) or op <= 0:
                        continue

                    budget = min(per_slot, cap, cash * 0.999)
                    shares = int(budget / op / 100) * 100
                    if shares <= 0:
                        continue
                    amount = shares * op
                    fee = self.cost.buy_cost(amount)
                    if amount + fee > cash:
                        continue

                    cash -= (amount + fee)
                    stop = self.strategy.initial_stop(op, bar)
                    positions[code] = {
                        "entry_date": date_str,
                        "entry_price": op,
                        "shares": shares,
                        "cost_basis": amount + fee,
                        "days": 0,
                        "stop_price": stop,
                        "code": code,
                        "pending_exit": None,
                    }
                    free -= 1

            # ---------------- 4. 权益快照
            marks = self._marks(dt, positions)
            eq = cash + sum(marks.values())
            peak = max(peak, eq)
            equity_rows.append({
                "date": dt, "equity": eq, "cash": cash,
                "n_positions": len(positions),
                "drawdown": eq / peak - 1,
            })

            if verbose and (di + 1) % 250 == 0:
                LOG.info(f"  {di + 1}/{len(self.calendar)} 权益 {eq:,.0f} "
                         f"持仓 {len(positions)} 已平仓 {len(trade_rows)}")

        equity = pd.DataFrame(equity_rows)
        trades = pd.DataFrame(trade_rows)
        metrics = performance(equity, trades, self.cfg, self.index_close)
        return BacktestResult(equity, trades, metrics)

    # ------------------------------------------------------------ 辅助
    def _marks(self, dt: pd.Timestamp, positions: dict) -> dict[str, float]:
        marks: dict[str, float] = {}
        for code, pos in positions.items():
            df = self.data.get(code)
            im = self._idxmap.get(code)
            if df is not None and im is not None and dt in im:
                marks[code] = float(df.iloc[im[dt]]["close"]) * pos["shares"]
            else:
                marks[code] = pos["entry_price"] * pos["shares"]
        return marks


# ---------------------------------------------------------------------------
# 绩效指标
# ---------------------------------------------------------------------------
def performance(equity: pd.DataFrame, trades: pd.DataFrame, cfg: dict,
                index_close: pd.Series | None = None,
                label: str = "strategy") -> dict:
    """计算完整绩效指标。"""
    if equity is None or len(equity) < 2:
        return {"策略": label, "错误": "权益曲线为空"}

    eq = equity["equity"].values.astype(float)
    dates = pd.to_datetime(equity["date"])
    years = max((dates.iloc[-1] - dates.iloc[0]).days / 365.25, 1e-6)

    total_ret = eq[-1] / eq[0] - 1
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if eq[0] > 0 else np.nan

    rets = pd.Series(eq).pct_change().dropna()
    vol = float(rets.std() * np.sqrt(244)) if len(rets) > 1 else 0.0
    rf = float(cfg.get("backtest", {}).get("risk_free_rate", 0.02))
    sharpe = (rets.mean() * 244 - rf) / vol if vol > 0 else np.nan

    downside = float(rets[rets < 0].std() * np.sqrt(244)) if (rets < 0).any() else 0.0
    sortino = (rets.mean() * 244 - rf) / downside if downside > 0 else np.nan

    dd = float(equity["drawdown"].min())
    calmar = cagr / abs(dd) if dd < 0 else np.nan

    out = {
        "策略": label,
        "起始日": dates.iloc[0].strftime("%Y-%m-%d"),
        "结束日": dates.iloc[-1].strftime("%Y-%m-%d"),
        "总收益%": round(total_ret * 100, 1),
        "CAGR%": round(cagr * 100, 2),
        "年化波动%": round(vol * 100, 2),
        "Sharpe": round(sharpe, 2) if np.isfinite(sharpe) else None,
        "Sortino": round(sortino, 2) if np.isfinite(sortino) else None,
        "最大回撤%": round(dd * 100, 2),
        "Calmar": round(calmar, 2) if np.isfinite(calmar) else None,
        "年数": round(years, 1),
    }

    if trades is not None and len(trades):
        r = trades["net_return"].astype(float)
        wins, losses = r[r > 0], r[r < 0]
        out.update({
            "交易笔数": int(len(trades)),
            "胜率%": round(float((r > 0).mean()) * 100, 1),
            "平均单笔%": round(float(r.mean()) * 100, 2),
            "平均盈利%": round(float(wins.mean()) * 100, 2) if len(wins) else None,
            "平均亏损%": round(float(losses.mean()) * 100, 2) if len(losses) else None,
            "盈亏比": round(float(wins.mean() / abs(losses.mean())), 2)
            if len(losses) and losses.mean() != 0 else None,
            "平均持有天": round(float(trades["days"].mean()), 1),
            "年均交易": round(len(trades) / years, 1),
            "止损率%": round(float((trades["exit_reason"] == "stop").mean()) * 100, 1),
            "止盈率%": round(float((trades["exit_reason"] == "take_profit").mean()) * 100, 1),
            "超时率%": round(float((trades["exit_reason"] == "timeout").mean()) * 100, 1),
            "最大单笔盈利%": round(float(r.max()) * 100, 2),
            "最大单笔亏损%": round(float(r.min()) * 100, 2),
        })
    else:
        out["交易笔数"] = 0

    # 基准对比
    if index_close is not None and len(index_close) > 10:
        ic = index_close[(index_close.index >= dates.iloc[0]) &
                         (index_close.index <= dates.iloc[-1])]
        if len(ic) > 10 and ic.iloc[0] > 0:
            bench_ret = ic.iloc[-1] / ic.iloc[0] - 1
            bench_cagr = (1 + bench_ret) ** (1 / years) - 1
            out["基准总收益%"] = round(float(bench_ret) * 100, 1)
            out["基准CAGR%"] = round(float(bench_cagr) * 100, 2)
            out["超额CAGR%"] = round((cagr - bench_cagr) * 100, 2)
    return out
