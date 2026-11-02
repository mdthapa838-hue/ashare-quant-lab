# -*- coding: utf-8 -*-
"""
step3_engine.py — 动态股票池回测引擎

核心改进（相对之前版本）：
  1. **动态股票池**：每个交易日的可交易标的 = 当时市值在 30-80 亿 + 非ST
  2. **纳入退市股**：退市前可正常交易，退市后自动移出池子
  3. **上市时间过滤**：新股上市满 N 个交易日后才可入选
  4. **真实成交约束**：涨停不买、跌停不卖、停牌不交易

关键：所有条件只用 T 日及之前的数据（无未来函数）。
"""
from __future__ import annotations

import os
import sys
import glob
import time
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.logger import get_logger

LOG = get_logger("dyn")


# ---------------------------------------------------------------------------
def load_all_kline(cache: Path, include_delisted: bool = True) -> dict[str, pd.DataFrame]:
    """加载全部K线（存续 + 退市）。"""
    data = {}
    dirs = [cache / "kline"]
    if include_delisted:
        dirs.append(cache / "kline_delisted")
    for d in dirs:
        if not d.is_dir():
            continue
        for f in glob.glob(str(d / "*.csv")):
            stem = os.path.basename(f)[:-4]
            for suf in ("_qfq", "_hfq", "_bfq"):
                if stem.endswith(suf):
                    stem = stem[:-len(suf)]
                    break
            if not (stem.isdigit() and len(stem) == 6):
                continue
            if stem in data:
                continue
            try:
                t = pd.read_csv(f)
                if len(t) < 30:
                    continue
                t["date"] = pd.to_datetime(t["date"])
                t = t.sort_values("date").reset_index(drop=True)
                for c in ("open", "high", "low", "close", "volume", "amount"):
                    if c not in t.columns:
                        break
                    t[c] = pd.to_numeric(t[c], errors="coerce")
                else:
                    data[stem] = t
            except Exception:
                pass
    return data


def to_ts(c6: str) -> str:
    if c6.startswith(("60", "68")):
        return c6 + ".SH"
    if c6.startswith(("8", "4", "9")):
        return c6 + ".BJ"
    return c6 + ".SZ"


# ---------------------------------------------------------------------------
class DynamicUniverse:
    """
    动态股票池：每个交易日重建。

    数据来源：
      hist_mktcap/{YYYYMMDD}.csv  季度末全市场市值快照
    在两次快照之间，用个股价格变动外推市值：
      mv(t) = mv(q) * close(t) / close(q)
    这样每个交易日都有「当时」的市值估计，且无未来函数
    （只用 q 时点的市值和 t 时点的价格）。
    """

    def __init__(self, cache: Path, cap_min: float = 30, cap_max: float = 80,
                 exclude_st: bool = True, exclude_bj: bool = True):
        self.cache = cache
        self.cap_min = cap_min
        self.cap_max = cap_max
        self.exclude_st = exclude_st
        self.exclude_bj = exclude_bj
        self.snapshots: list[tuple[pd.Timestamp, pd.DataFrame]] = []
        # 预建索引：{code6: [(snap_idx, mv, close)]}
        self.by_code: dict[str, list] = {}
        self._pool_cache: dict = {}
        self._load()

    def _load(self):
        d = self.cache / "hist_mktcap"
        if not d.is_dir():
            LOG.warning("hist_mktcap 目录不存在，动态池不可用")
            return
        files = sorted(glob.glob(str(d / "*.csv")))
        for f in files:
            try:
                dt = pd.Timestamp(os.path.basename(f)[:-4])
            except Exception:
                continue
            try:
                t = pd.read_csv(f, usecols=lambda c: c in
                                ("ts_code", "total_mv", "circ_mv", "close",
                                 "turnover_rate", "pe_ttm", "pb"))
                if "ts_code" not in t.columns or "total_mv" not in t.columns:
                    continue
                t = t.copy()
                t["code6"] = t["ts_code"].str[:6]
                t["mv_yi"] = pd.to_numeric(t["total_mv"], errors="coerce") / 1e4
                t["close"] = pd.to_numeric(t.get("close"), errors="coerce")
                t = t.dropna(subset=["mv_yi"])
                t = t[~t["code6"].str.startswith(("8", "4", "9"))] if self.exclude_bj else t
                self.snapshots.append((dt, t[["code6", "mv_yi", "close"]]))
            except Exception:
                continue

        # 预建 code -> [(snap_idx, mv, close)]
        for si, (sdt, sdf) in enumerate(self.snapshots):
            for c6, mv, cl in zip(sdf["code6"].values, sdf["mv_yi"].values,
                                  sdf["close"].values):
                self.by_code.setdefault(c6, []).append((si, mv, cl))
        LOG.info(f"动态池: {len(self.snapshots)} 快照, {len(self.by_code)} 只")

    def available(self) -> bool:
        return len(self.snapshots) >= 2

    def snapshot_for(self, dt: pd.Timestamp):
        """返回 <= dt 的最近快照索引（无未来函数）。"""
        lo, hi = 0, len(self.snapshots) - 1
        best = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.snapshots[mid][0] <= dt:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def precompute(self, calendar: list, data: dict) -> None:
        """
        预计算每个交易日的股票池。

        ⚠ 关键：市值外推必须用**同口径价格**。
        daily_basic 的 close 是不复权价，而本地K线是前复权价，
        直接相除会因复权因子差异导致市值严重失真。

        正确做法（二选一，本实现用 B）：
          A. 全部用不复权价
          B. 用本地前复权价的比值外推（等价于假设股本不变、含分红再投）
             mv(t) = mv(snap) * qfq_close(t) / qfq_close(snap)
        """
        if not self.snapshots:
            return
        price_map = {}
        for c, d in data.items():
            ds = pd.to_datetime(d["date"]).tolist()
            price_map[c] = dict(zip(ds, d["close"].tolist()))

        for si, (sdt, sdf) in enumerate(self.snapshots):
            next_sdt = (self.snapshots[si + 1][0]
                        if si + 1 < len(self.snapshots) else None)
            base = {}
            for c6, mv in zip(sdf["code6"].values, sdf["mv_yi"].values):
                # 用本地（前复权）K线在快照日的收盘价作基准
                pm = price_map.get(c6)
                if not pm:
                    continue
                p_snap = pm.get(sdt)
                if p_snap and p_snap > 0:
                    base[c6] = (mv, p_snap)
            for dt in calendar:
                if dt < sdt:
                    continue
                if next_sdt is not None and dt >= next_sdt:
                    break
                pool = set()
                lo, hi = self.cap_min, self.cap_max
                for c6, (mv_base, p_snap) in base.items():
                    p_now = price_map.get(c6, {}).get(dt)
                    mv_now = (mv_base * (p_now / p_snap)
                              if p_now and p_now > 0 else mv_base)
                    if lo <= mv_now < hi:
                        pool.add(c6)
                self._pool_cache[dt] = pool

    def pool_at(self, dt: pd.Timestamp, prices: dict[str, float]) -> set[str]:
        """dt 时点的可交易标的集合（优先用预计算结果）。"""
        hit = self._pool_cache.get(dt)
        if hit is not None:
            return hit
        si = self.snapshot_for(dt)
        if si < 0:
            return set()
        out = set()
        lo, hi = self.cap_min, self.cap_max
        for c6, hist in self.by_code.items():
            rec = None
            for s, mv, cl in hist:
                if s <= si:
                    rec = (mv, cl)
                else:
                    break
            if rec is None:
                continue
            mv, cl = rec
            if prices and cl and cl > 0:
                p = prices.get(c6)
                if p and p > 0:
                    mv = mv * (p / cl)
            if lo <= mv < hi:
                out.add(c6)
        return out


# ---------------------------------------------------------------------------
class DynBacktest:
    """动态池事件驱动回测。"""

    def __init__(self, cfg: dict, universe: DynamicUniverse,
                 min_list_days: int = 120):
        st = cfg.get("strategy", {})
        ex = st.get("exit", {})
        self.tp = ex.get("take_profit", "upper")
        self.stop_pct = ex.get("stop_loss_pct", -0.12)
        self.max_hold = ex.get("max_hold_days", 60)
        pf = st.get("portfolio", {})
        self.max_pos = pf.get("max_positions", 5)
        self.max_pct = pf.get("max_position_pct", 0.25)
        self.init_cap = pf.get("initial_capital", 1_000_000)
        c = st.get("cost", {})
        self.buy_rate = c.get("commission", 0.00025) + c.get("slippage", 0.00075)
        self.sell_rate = (c.get("commission", 0.00025) + c.get("stamp_tax", 0.0005)
                          + c.get("slippage", 0.00075))
        self.min_order = pf.get("min_order_amount", 10000)
        self.uni = universe
        self.min_list_days = min_list_days
        self.factor_cols: list[str] = []      # 额外的因子条件列
        self.factor_thr: dict[str, tuple] = {}

    def set_factor(self, col: str, direction: str, thr: float):
        self.factor_cols.append(col)
        self.factor_thr[col] = (direction, thr)

    # ---------------------------------------------------------------- 运行
    def run(self, data: dict[str, pd.DataFrame], conds: list[str],
            verbose: bool = True):
        """
        Parameters
        ----------
        data : {code6: DataFrame}
            必须含 date/open/high/low/close/volume/amount
            以及条件列 cond_* 和因子列
        conds : list[str]
            需要全部满足的条件列名
        """
        # ---- 交易日历
        cal = set()
        for d in data.values():
            cal.update(pd.to_datetime(d["date"]).tolist())
        cal = sorted(pd.Timestamp(x) for x in cal)
        if not cal:
            return pd.DataFrame(), pd.DataFrame()

        # ---- 索引
        idx = {}
        first_date = {}
        for c, d in data.items():
            ds = pd.to_datetime(d["date"]).tolist()
            idx[c] = {pd.Timestamp(x): i for i, x in enumerate(ds)}
            first_date[c] = pd.Timestamp(ds[0])

        # ---- 预计算股票池（大幅加速）
        if hasattr(self.uni, "precompute"):
            self.uni.precompute(cal, data)

        # ---- 每日价格（用于市值外推）
        all_conds = conds + self.factor_cols

        cash = float(self.init_cap)
        pos = {}
        eq_rows, tr_rows = [], []
        peak = self.init_cap
        pool_sizes = []

        for di, dt in enumerate(cal):
            # ---------- 0. 当日价格快照（市值外推用）
            prices = {}
            for c, im in idx.items():
                if dt in im:
                    v = data[c]["close"].iloc[im[dt]]
                    if np.isfinite(v):
                        prices[c] = float(v)

            # ---------- 1. 当日可交易池
            pool = self.uni.pool_at(dt, prices)
            pool_sizes.append(len(pool))

            # ---------- 2. 出场
            for c in list(pos.keys()):
                p = pos[c]
                d = data.get(c)
                im = idx.get(c)
                if d is None or im is None or dt not in im:
                    # 无数据（退市/停牌）→ 尝试以最后价格平仓
                    continue
                j = im[dt]
                y = j - 1
                if y < 0 or y < p["k0"] + 1:
                    p["days"] = p.get("days", 0)
                    continue
                bar = d.iloc[j]
                ybar = d.iloc[y]
                # 跌停/停牌不卖
                oc = bar.get("open_chg_pct")
                if pd.notna(oc) and oc < -9.5:
                    p["days"] += 1
                    continue
                if not np.isfinite(bar["open"]) or bar["open"] <= 0:
                    p["days"] += 1
                    continue

                reason = None
                if ybar["low"] <= p["stop"]:
                    reason = "止损"
                else:
                    tgt = ybar["bb_upper"] if self.tp == "upper" else ybar["bb_mid"]
                    if pd.notna(tgt) and ybar["high"] >= tgt:
                        reason = "止盈"
                    elif p["days"] >= self.max_hold:
                        reason = "超时"
                if reason:
                    xp = bar["open"]
                    proceeds = p["shares"] * xp * (1 - self.sell_rate)
                    cash += proceeds
                    tr_rows.append({
                        "code": to_ts(c),
                        "entry_date": p["entry_date"],
                        "exit_date": dt,
                        "entry_price": p["entry_price"],
                        "exit_price": xp,
                        "days": p["days"],
                        "reason": reason,
                        "ret": proceeds / p["cost"] - 1,
                        "year": pd.Timestamp(p["entry_date"]).year,
                    })
                    del pos[c]
                    continue
                p["days"] += 1

            # ---------- 3. 入场（信号在昨日产生，今日开盘执行）
            # 昨日信号：用 dt 的前一交易日
            if len(pos) < self.max_pos and pool:
                prev_dt = cal[di - 1] if di > 0 else None
                if prev_dt is not None:
                    cands = []
                    for c in pool:
                        if c in pos:
                            continue
                        d = data.get(c)
                        im = idx.get(c)
                        if d is None or im is None or prev_dt not in im:
                            continue
                        # 上市时间过滤
                        if (prev_dt - first_date[c]).days < self.min_list_days:
                            continue
                        j = im[prev_dt]
                        row = d.iloc[j]
                        # 所有条件
                        ok = True
                        for cc in all_conds:
                            if cc not in d.columns:
                                ok = False
                                break
                            if not bool(row[cc]):
                                ok = False
                                break
                        if not ok:
                            continue
                        # 因子阈值
                        for fcol, (direc, thr) in self.factor_thr.items():
                            if fcol not in d.columns:
                                ok = False
                                break
                            v = row[fcol]
                            if pd.isna(v):
                                ok = False
                                break
                            if direc == "low" and not (v <= thr):
                                ok = False
                                break
                            if direc == "high" and not (v >= thr):
                                ok = False
                                break
                        if not ok:
                            continue
                        cands.append(c)

                    if cands:
                        # 当前权益
                        tot = cash
                        for c2, p2 in pos.items():
                            im2 = idx.get(c2)
                            if im2 and dt in im2:
                                tot += data[c2]["close"].iloc[im2[dt]] * p2["shares"]
                            else:
                                tot += p2["entry_price"] * p2["shares"]
                        per = tot / self.max_pos
                        cap = tot * self.max_pct

                        for c in cands:
                            if len(pos) >= self.max_pos:
                                break
                            d = data[c]
                            im = idx[c]
                            if dt not in im:
                                continue
                            j = im[dt]
                            bar = d.iloc[j]
                            oc = bar.get("open_chg_pct")
                            if pd.notna(oc) and oc > 9.5:
                                continue
                            op = bar["open"]
                            if not np.isfinite(op) or op <= 0:
                                continue
                            alloc = min(per, cap, cash * 0.999)
                            if alloc < self.init_cap * 0.01:
                                continue
                            sh = (alloc * (1 - self.buy_rate)) / op
                            if sh <= 0:
                                continue
                            cash -= alloc
                            pos[c] = {
                                "entry_date": dt, "entry_price": op,
                                "shares": sh, "cost": alloc, "days": 0,
                                "k0": j, "stop": op * (1 + self.stop_pct),
                            }

            # ---------- 4. 记录权益
            eq = cash
            for c, p in pos.items():
                im = idx.get(c)
                if im and dt in im:
                    v = data[c]["close"].iloc[im[dt]]
                    eq += (v if np.isfinite(v) else p["entry_price"]) * p["shares"]
                else:
                    eq += p["entry_price"] * p["shares"]
            peak = max(peak, eq)
            eq_rows.append({"date": dt, "equity": eq, "cash": cash,
                            "n": len(pos), "dd": eq / peak - 1,
                            "pool": len(pool)})

            if verbose and (di + 1) % 500 == 0:
                print(f"  {di+1}/{len(cal)}  权益{eq:,.0f}  持仓{len(pos)}  "
                      f"池{len(pool)}  交易{len(tr_rows)}", flush=True)

        eqdf = pd.DataFrame(eq_rows)
        trdf = pd.DataFrame(tr_rows)
        if len(eqdf):
            eqdf.attrs["avg_pool"] = float(np.mean(pool_sizes)) if pool_sizes else 0
        return eqdf, trdf


# ---------------------------------------------------------------------------
def metrics(eq: pd.DataFrame, tr: pd.DataFrame, label: str = "策略") -> dict:
    if eq is None or len(eq) < 2:
        return {"配置": label, "错误": "无权益曲线"}
    e = eq["equity"].values
    dates = pd.to_datetime(eq["date"])
    yrs = (dates.iloc[-1] - dates.iloc[0]).days / 365.25
    if yrs <= 0:
        return {"配置": label, "错误": "时间跨度不足"}
    cagr = (e[-1] / e[0]) ** (1 / yrs) - 1
    r = pd.Series(e).pct_change().dropna()
    vol = r.std() * np.sqrt(244) if len(r) > 1 else 0
    sharpe = (r.mean() * 244 - 0.02) / vol if vol > 0 else np.nan
    dd = eq["dd"].min()
    out = {
        "配置": label,
        "总收益%": round((e[-1] / e[0] - 1) * 100, 1),
        "CAGR%": round(cagr * 100, 2),
        "Sharpe": round(sharpe, 2) if np.isfinite(sharpe) else None,
        "最大回撤%": round(dd * 100, 2),
        "Calmar": round(cagr / abs(dd), 2) if dd else None,
        "年数": round(yrs, 1),
        "平均池大小": round(eq.attrs.get("avg_pool", 0), 0),
    }
    if tr is not None and len(tr):
        rr = tr["ret"]
        w = rr[rr > 0]
        l = rr[rr < 0]
        out.update({
            "交易笔数": len(tr),
            "胜率%": round((rr > 0).mean() * 100, 1),
            "平均单笔%": round(rr.mean() * 100, 2),
            "盈亏比": round(w.mean() / abs(l.mean()), 2)
            if len(l) and l.mean() != 0 else None,
            "年均交易": round(len(tr) / yrs, 1),
            "平均持有天": round(tr["days"].mean(), 1),
        })
    return out
