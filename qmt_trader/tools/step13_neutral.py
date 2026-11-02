# -*- coding: utf-8 -*-
"""
step13_neutral.py — 行业中性化 + 多因子组合 + 市场中性

三个深化：

A. 行业中性化
   因子在池内**先去行业均值**，再做横截面排名。
   目的：排除行业beta（避免选中的其实是"某个行业"）

B. 多重因子正交与组合
   - 计算因子间相关性矩阵
   - 选低相关（|r| < 0.5）的因子组合
   - 等权合成复合分

C. 市场中性
   小盘多头天然承担市场beta。
   这里用「相对收益」近似中性：比较策略收益 vs 池内等权基准，
   用超额收益衡量真实alpha。

输出：
  - 行业中性化后的因子排名
  - 低相关因子组合
  - 市场中性（超额）绩效
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from core.logger import get_logger

LOG = get_logger("neu")


# ---------------------------------------------------------------------------
def load_industry_pit(cache: Path) -> tuple[pd.DataFrame, dict]:
    """
    载入 PIT 行业归属。

    Returns
    -------
    members : DataFrame(l1_code, l1_name, code6, in_date, out_date)
    by_code : {code6: [(in_date, out_date, l1_code, l1_name)]}
        按 in_date 排序，用于 asof 查询。
    """
    p = cache / "industry" / "sw_members_pit.csv"
    if not p.exists():
        return pd.DataFrame(), {}
    m = pd.read_csv(p, dtype={"con_code": str, "l1_code": str})
    m["code6"] = m["con_code"].str[:6]
    for c in ("in_date", "out_date"):
        m[c] = pd.to_datetime(m[c], errors="coerce")
    m = m.dropna(subset=["in_date"])
    by_code = defaultdict(list)
    for _, r in m.iterrows():
        by_code[r["code6"]].append(
            (r["in_date"], r["out_date"], r["l1_code"], r["l1_name"]))
    for k in by_code:
        by_code[k].sort(key=lambda x: x[0])
    return m, dict(by_code)


def industry_at(by_code: dict, c6: str, dt: pd.Timestamp) -> str | None:
    """
    返回 dt 时点该股的行业（PIT）。

    规则：取 in_date <= dt 且 (out_date 缺失 或 dt < out_date) 的记录。
    若同一时点有多条，取 in_date 最大的那条。

    注意：out_date 缺失可能是 NaT 而非 None，必须用 pd.isna 判断。
    """
    lst = by_code.get(c6)
    if not lst:
        return None
    hit = None
    hit_in = None
    for in_d, out_d, code, name in lst:
        if pd.isna(in_d):
            continue
        if in_d > dt:
            break
        if pd.isna(out_d) or dt < out_d:
            if hit_in is None or in_d >= hit_in:
                hit = name
                hit_in = in_d
    return hit


# ---------------------------------------------------------------------------
def build_neutral_ranks(data: dict, pool_by_date: dict,
                        factors: list[str], by_code: dict,
                        log_every: int = 8) -> None:
    """
    行业中性化因子排名。

    步骤：
      1. 构建 (date, code, factor) 长表
      2. 附加行业标签（PIT）
      3. 在每个 (date, industry) 组内去均值
      4. 在池内按去均值后的值排序，得 0~1 分位
    """
    print("  [中性化] 构建长表...", flush=True)
    frames = []
    for c6, d in data.items():
        keep = [c for c in factors if c in d.columns]
        if not keep:
            continue
        t = d[["date"] + keep].copy()
        t["code"] = c6
        frames.append(t)
    if not frames:
        return
    LONG = pd.concat(frames, ignore_index=True)
    del frames
    print(f"  [中性化] {len(LONG):,} 行", flush=True)

    # 池过滤
    rows = []
    for dt, pool in pool_by_date.items():
        for c in pool:
            rows.append((dt, c))
    POOL = pd.DataFrame(rows, columns=["date", "code"])
    LONG = LONG.merge(POOL, on=["date", "code"], how="inner")
    print(f"  [中性化] 池内 {len(LONG):,} 行", flush=True)

    # 行业标签（按月缓存，避免逐行 Python 查询）
    print("  [中性化] 匹配行业...", flush=True)
    tk = LONG["date"].dt.to_period("M")
    LONG["_ym"] = tk
    ind_map = {}
    for (c6, ym), idx in LONG.groupby(["code", "_ym"]).groups.items():
        dt = ym.to_timestamp("M")
        ind_map[(c6, ym)] = industry_at(by_code, c6, dt) or "NA"
    LONG["industry"] = [ind_map.get((c, y), "NA")
                        for c, y in zip(LONG["code"].values, LONG["_ym"].values)]
    LONG = LONG.drop(columns=["_ym"])
    cov = (LONG["industry"] != "NA").mean()
    print(f"  [中性化] 行业覆盖: {cov*100:.1f}%", flush=True)

    # 逐因子：去行业均值 → 池内排名
    for i, f in enumerate(factors):
        if f not in LONG.columns:
            continue
        v = pd.to_numeric(LONG[f], errors="coerce")
        # 按 (date, industry) 去均值
        grp = LONG.groupby(["date", "industry"])[f]
        mean_g = grp.transform("mean")
        demeaned = v - mean_g
        LONG[f"_neu_{f}"] = demeaned
        # 池内排名
        rk = demeaned.groupby(LONG["date"]).rank(pct=True, na_option="keep")
        LONG[f"rk_{f}"] = rk.astype(np.float32)
        if (i + 1) % log_every == 0:
            print(f"  [中性化] {i+1}/{len(factors)}", flush=True)

    # 写回
    rank_cols = [c for c in LONG.columns if c.startswith("rk_")]
    for c6, sub in LONG.groupby("code"):
        d = data.get(c6)
        if d is None:
            continue
        idxs = sub["date"].values
        for col in rank_cols:
            vals = sub[col].values
            d[col] = pd.Series(vals, index=idxs).reindex(d["date"].values).values
    del LONG
    print("  [中性化] 完成", flush=True)


# ---------------------------------------------------------------------------
class MarketNeutralBacktest:
    """
    市场中性回测（用超额收益衡量）。

    做法：
      - 多头组合按信号选股
      - 基准 = 池内等权日收益
      - 超额 = 多头日收益 - 基准日收益（每日再平衡的近似）

    输出：多头净值、基准净值、超额净值、超额指标
    """

    def __init__(self, cfg: dict, max_pos: int = 5, max_pct: float = 0.20,
                 stop_pct: float = -0.15, max_hold: int = 60):
        st = cfg.get("strategy", {})
        c = st.get("cost", {})
        self.buy_rate = c.get("commission", 0.00025) + c.get("slippage", 0.00075)
        self.sell_rate = (c.get("commission", 0.00025) + c.get("stamp_tax", 0.0005)
                          + c.get("slippage", 0.00075))
        pf = st.get("portfolio", {})
        self.init_cap = pf.get("initial_capital", 1_000_000)
        self.max_pos = max_pos
        self.max_pct = max_pct
        self.stop_pct = stop_pct
        self.max_hold = max_hold

    def _long_only(self, data, signals, pool_by_date, min_list_days=120):
        idx, fdate = {}, {}
        for c, d in data.items():
            ds = pd.to_datetime(d["date"]).tolist()
            idx[c] = {pd.Timestamp(x): i for i, x in enumerate(ds)}
            fdate[c] = pd.Timestamp(ds[0])
        cal = sorted(pool_by_date.keys())
        cash = float(self.init_cap)
        pos = {}
        rows = []
        peak = self.init_cap
        for di, dt in enumerate(cal):
            # 出场
            for c in list(pos.keys()):
                p = pos[c]
                d = data.get(c)
                im = idx.get(c)
                if d is None or im is None or dt not in im:
                    continue
                j = im[dt]
                y = j - 1
                if y < 0 or y < p["k0"] + 1:
                    continue
                bar = d.iloc[j]
                ybar = d.iloc[y]
                oc = bar.get("open_chg_pct")
                if pd.notna(oc) and oc < -9.5:
                    p["days"] += 1
                    continue
                op = bar["open"]
                if not np.isfinite(op) or op <= 0:
                    p["days"] += 1
                    continue
                reason = None
                if ybar["low"] <= p["stop"]:
                    reason = "止损"
                elif p["days"] >= self.max_hold:
                    reason = "超时"
                if reason:
                    cash += p["shares"] * op * (1 - self.sell_rate)
                    del pos[c]
                    continue
                p["days"] += 1
            # 入场
            if len(pos) < self.max_pos and di > 0:
                prev = cal[di - 1]
                cands = [c for c in signals.get(prev, [])
                         if c not in pos and c in pool_by_date.get(prev, ())]
                if cands:
                    tot = cash
                    for c2, p2 in pos.items():
                        im2 = idx.get(c2)
                        if im2 and dt in im2:
                            v = data[c2]["close"].iloc[im2[dt]]
                            tot += (v if np.isfinite(v) else p2["entry_price"]) * p2["shares"]
                        else:
                            tot += p2["entry_price"] * p2["shares"]
                    per = tot / self.max_pos
                    cap = tot * self.max_pct
                    for c in cands:
                        if len(pos) >= self.max_pos:
                            break
                        d = data.get(c)
                        im = idx.get(c)
                        if d is None or im is None or dt not in im:
                            continue
                        if (dt - fdate[c]).days < min_list_days:
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
                        if alloc < self.init_cap * 0.001:
                            continue
                        sh = (alloc * (1 - self.buy_rate)) / op
                        if sh <= 0:
                            continue
                        cash -= alloc
                        pos[c] = {"entry_date": dt, "entry_price": op,
                                  "shares": sh, "cost": alloc, "days": 0,
                                  "k0": j, "stop": op * (1 + self.stop_pct)}
            eq = cash
            for c, p in pos.items():
                im = idx.get(c)
                if im and dt in im:
                    v = data[c]["close"].iloc[im[dt]]
                    eq += (v if np.isfinite(v) else p["entry_price"]) * p["shares"]
                else:
                    eq += p["entry_price"] * p["shares"]
            peak = max(peak, eq)
            rows.append({"date": dt, "equity": eq, "n": len(pos),
                         "dd": eq / peak - 1})
        return pd.DataFrame(rows)

    def _benchmark(self, data, pool_by_date):
        """
        池内等权日收益（每日再平衡）。

        向量化：先把所有标的的收盘价拼成长表 {code: {date: close}}，
        再按日取池内成员计算均值。避免逐日 DataFrame 过滤。
        """
        # code -> {date: close}
        px = {}
        for c, d in data.items():
            ds = pd.to_datetime(d["date"]).tolist()
            px[c] = dict(zip(ds, d["close"].tolist()))

        cal = sorted(pool_by_date.keys())
        rets = [0.0]
        for di in range(1, len(cal)):
            dt = cal[di]
            prev = cal[di - 1]
            pool = pool_by_date.get(prev, ())
            rs = []
            for c in pool:
                m = px.get(c)
                if not m:
                    continue
                p0 = m.get(prev)
                p1 = m.get(dt)
                if p0 and p1 and p0 > 0 and np.isfinite(p0) and np.isfinite(p1):
                    rs.append(p1 / p0 - 1)
            rets.append(float(np.mean(rs)) if rs else 0.0)
        B = pd.DataFrame({"date": cal, "ret": rets})
        B["equity"] = (1 + B["ret"]).cumprod()
        return B

    def run(self, data, signals, pool_by_date):
        long_eq = self._long_only(data, signals, pool_by_date)
        bench = self._benchmark(data, pool_by_date)
        # 基准列重命名，避免与多头自身列冲突
        bench = bench.rename(columns={"ret": "ret_bench", "equity": "eq_bench"})
        m = long_eq.merge(bench, on="date", how="left")
        m["ret_b"] = m["ret_bench"].fillna(0)
        m["equity_b"] = m["eq_bench"].fillna(1.0)
        m["ret"] = m["equity"].pct_change().fillna(0)
        m["excess"] = m["ret"] - m["ret_b"]
        m["eq_excess"] = (1 + m["excess"]).cumprod()
        return m


def excess_metrics(m: pd.DataFrame, label: str = "") -> dict:
    """超额收益指标。"""
    if m is None or len(m) < 10:
        return {"策略": label, "错误": "数据不足"}
    e = m["eq_excess"].values
    dates = pd.to_datetime(m["date"])
    yrs = (dates.iloc[-1] - dates.iloc[0]).days / 365.25
    cagr = (e[-1] / e[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    r = m["excess"].dropna()
    vol = r.std() * np.sqrt(244)
    sharpe = (r.mean() * 244) / vol if vol > 0 else np.nan
    dd = (m["eq_excess"] / m["eq_excess"].cummax() - 1).min()
    # 多头自身
    lr = m["ret"].dropna()
    l_eq = m["equity"].values
    l_cagr = (l_eq[-1] / l_eq[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    b_eq = m["equity_b"].values
    b_cagr = (b_eq[-1] / b_eq[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    return {
        "策略": label,
        "年数": round(yrs, 1),
        "多头CAGR%": round(l_cagr * 100, 2),
        "基准CAGR%": round(b_cagr * 100, 2),
        "超额CAGR%": round(cagr * 100, 2),
        "超额Sharpe": round(sharpe, 2) if np.isfinite(sharpe) else None,
        "超额波动%": round(vol * 100, 2),
        "超额最大回撤%": round(dd * 100, 2),
        "超额胜率%": round((r > 0).mean() * 100, 1),
        "超额累计%": round((e[-1] / e[0] - 1) * 100, 1),
    }
