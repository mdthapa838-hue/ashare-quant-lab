# -*- coding: utf-8 -*-
"""
step6_lab.py — 策略实验室（可插拔选股逻辑）

设计目标：
  1. 任何选股逻辑 = 横截面因子 + 分位方向，无需改引擎
  2. 自动跑动态股票池（含退市股）
  3. 自动 Walk-forward
  4. 自动 DSR 打折（试验数自动统计）

因子全部为**横截面分位**（每个交易日在池内排名），避免时序阈值漂移。

无未来函数保证：
  - 信号在 T 日收盘用 [0, T] 数据计算
  - 成交在 T+1 开盘
  - 股票池用 T 日市值估计
"""
from __future__ import annotations

import os
import sys
import time
import json
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from core.logger import get_logger

LOG = get_logger("lab")


# ===========================================================================
# 一、因子库（全部因果，只用历史）
# ===========================================================================
def add_price_factors(d: pd.DataFrame) -> pd.DataFrame:
    """为单只股票计算时序因子（不含横截面排名）。"""
    c = d["close"]
    h = d["high"]
    l = d["low"]
    o = d["open"]
    v = d["volume"]
    amt = d["amount"]

    # --- 收益类
    for n in (5, 20, 60, 120, 250):
        d[f"ret{n}"] = (c / c.shift(n) - 1) * 100
    # 跳过最近20日的动量（经典动量定义）
    d["mom_120_20"] = (c.shift(20) / c.shift(140) - 1) * 100

    # --- 均线关系
    for n in (5, 10, 20, 60, 120):
        ma = c.rolling(n, min_periods=n).mean()
        d[f"ma{n}"] = ma
        d[f"dist_ma{n}"] = (c / ma - 1) * 100

    # --- 位置类
    llv252 = l.rolling(252, min_periods=120).min()
    hhv252 = h.rolling(252, min_periods=120).max()
    d["dist_high252"] = (c / hhv252 - 1) * 100
    d["pos252"] = (c - llv252) / (hhv252 - llv252).replace(0, np.nan)

    # --- 波动类
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    d["atr_pct"] = tr.rolling(14, min_periods=14).mean() / c * 100
    d["vol_20"] = c.pct_change().rolling(20, min_periods=20).std() * 100
    d["vol_60"] = c.pct_change().rolling(60, min_periods=60).std() * 100
    # 下行波动
    neg = c.pct_change()
    neg = neg.where(neg < 0, 0.0)
    d["downvol_60"] = neg.rolling(60, min_periods=60).std() * 100

    # --- 量能类
    a5 = amt.rolling(5, min_periods=5).mean()
    a20 = amt.rolling(20, min_periods=20).mean()
    a60 = amt.rolling(60, min_periods=60).mean()
    d["amt_ma20"] = a20
    d["amt_ratio_5_20"] = a5 / a20.replace(0, np.nan)
    d["amt_ratio_20_60"] = a20 / a60.replace(0, np.nan)
    v20 = v.rolling(20, min_periods=20).mean()
    d["vol_ratio_1_20"] = v / v20.replace(0, np.nan)

    # --- 彩票偏好（最大单日涨幅）
    dr = c.pct_change()
    d["max_ret_20"] = dr.rolling(20, min_periods=20).max() * 100
    d["max_ret_60"] = dr.rolling(60, min_periods=60).max() * 100
    # 收益偏度
    d["skew_60"] = dr.rolling(60, min_periods=60).skew()

    # --- 趋势一致性（上涨天数占比）
    up = (dr > 0).astype(float)
    d["up_ratio_20"] = up.rolling(20, min_periods=20).mean() * 100
    d["up_ratio_60"] = up.rolling(60, min_periods=60).mean() * 100

    # --- 布林
    mid = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std(ddof=0)
    d["bb_upper"] = mid + 2 * sd
    d["bb_mid"] = mid
    d["bb_lower"] = mid - 2 * sd
    d["bb_width"] = (d["bb_upper"] - d["bb_lower"]) / mid.replace(0, np.nan) * 100
    d["bb_pctb"] = (c - d["bb_lower"]) / (d["bb_upper"] - d["bb_lower"]).replace(0, np.nan)

    # --- 连续涨跌
    down = (c < c.shift(1)).fillna(False)
    d["down_streak"] = down.groupby((down != down.shift()).cumsum()).cumsum().astype(int)

    # --- 换手代理（成交额/价格，近似成交量）
    d["turnover_proxy"] = amt / c.replace(0, np.nan)
    d["log_amt"] = np.log1p(a20)

    return d


# 用于横截面排名的因子
CS_FACTORS = [
    "ret5", "ret20", "ret60", "ret120", "ret250", "mom_120_20",
    "dist_ma5", "dist_ma10", "dist_ma20", "dist_ma60", "dist_ma120",
    "dist_high252", "pos252",
    "atr_pct", "vol_20", "vol_60", "downvol_60",
    "amt_ratio_5_20", "amt_ratio_20_60", "vol_ratio_1_20", "log_amt",
    "max_ret_20", "max_ret_60", "skew_60",
    "up_ratio_20", "up_ratio_60",
    "bb_width", "bb_pctb", "down_streak",
]


def add_cross_sectional_ranks(data: dict, pool_by_date: dict,
                              factors: list[str] = None) -> None:
    """
    计算横截面分位（0~1），**只在当日股票池内排名**。

    实现：按因子逐个构建长表 → 按 date 分组 rank → 写回。
    内存友好（一次一个因子）。
    """
    factors = factors or CS_FACTORS
    # 长表：date, code, value
    print(f"  [横截面] 构建长表...", flush=True)
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
    print(f"  [横截面] {len(LONG):,} 行", flush=True)

    # 池过滤：只保留在池中的 (date, code)
    rows = []
    for dt, pool in pool_by_date.items():
        for c in pool:
            rows.append((dt, c))
    POOL = pd.DataFrame(rows, columns=["date", "code"])
    print(f"  [横截面] 池内 {len(POOL):,} 行", flush=True)

    LONG = LONG.merge(POOL, on=["date", "code"], how="inner")
    print(f"  [横截面] 合并后 {len(LONG):,} 行", flush=True)

    # 逐因子排名
    for f in factors:
        if f not in LONG.columns:
            continue
        rk = LONG.groupby("date")[f].rank(pct=True, na_option="keep")
        LONG[f"rk_{f}"] = rk.astype(np.float32)
        if (factors.index(f) + 1) % 10 == 0:
            print(f"  [横截面] 排名 {factors.index(f)+1}/{len(factors)}", flush=True)

    # 写回
    rank_cols = [c for c in LONG.columns if c.startswith("rk_")]
    for c6, sub in LONG.groupby("code"):
        d = data.get(c6)
        if d is None:
            continue
        m = dict(zip(sub["date"], [tuple(x) for x in sub[rank_cols].values]))
        for i, col in enumerate(rank_cols):
            d[col] = d["date"].map(lambda x, i=i: m.get(x, (None,) * len(rank_cols))[i])
    del LONG
    print(f"  [横截面] 完成", flush=True)


# ===========================================================================
# 二、策略定义
# ===========================================================================
# (名称, 因子, 方向, 分位, 附加条件列表, 说明)
#   factor='rk_xxx' 时使用横截面分位
STRATEGIES = [
    # ---- 反转类
    ("反转20日", "rk_ret20", "low", 0.20, [], "过去20日跌幅最深20%"),
    ("反转60日", "rk_ret60", "low", 0.20, [], "过去60日跌幅最深20%"),
    ("反转60日(中)", "rk_ret60", "low", 0.40, [], "60日跌幅40%分位以下"),
    ("距高点回撤", "rk_dist_high252", "low", 0.20, [], "距252日高点最远20%"),

    # ---- 动量类
    ("动量120-20", "rk_mom_120_20", "high", 0.20, [], "跳过近20日的中期动量前20%"),
    ("动量250", "rk_ret250", "high", 0.20, [], "过去250日涨幅前20%"),
    ("接近新高", "rk_dist_high252", "high", 0.10, [], "距252日高点最近10%"),

    # ---- 波动类
    ("低波动", "rk_atr_pct", "low", 0.20, [], "ATR/价格最低20%"),
    ("高波动", "rk_atr_pct", "high", 0.20, [], "ATR/价格最高20%（彩票）"),
    ("低下行波", "rk_downvol_60", "low", 0.20, [], "下行波动最低20%"),

    # ---- 量能类
    ("缩量", "rk_amt_ratio_5_20", "low", 0.20, [], "5日/20日成交额最低20%"),
    ("放量", "rk_amt_ratio_5_20", "high", 0.10, [], "5日/20日成交额最高10%"),
    ("低换手", "rk_turnover_proxy", "low", 0.20, [], "成交额/价格最低20%"),

    # ---- 彩票类
    ("低彩票", "rk_max_ret_20", "low", 0.20, [], "20日最大单日涨幅最低20%"),
    ("高彩票", "rk_max_ret_20", "high", 0.20, [], "20日最大单日涨幅最高20%"),

    # ---- 形态类
    ("布林收口", "rk_bb_width", "low", 0.20, [], "布林带宽最窄20%"),
    ("布林下轨", "rk_bb_pctb", "low", 0.10, [], "%B最低10%"),
    ("强势收盘位置", "rk_up_ratio_20", "high", 0.20, [], "20日上涨天数占比最高20%"),

    # ---- 组合类
    ("动量+低波", "rk_mom_120_20", "high", 0.30, ["rk_atr_pct:low:0.40"],
     "中期动量前30% 且 低波动前40%"),
    ("反转+缩量", "rk_ret20", "low", 0.30, ["rk_amt_ratio_5_20:low:0.40"],
     "20日反转最强30% 且 缩量前40%"),
    ("接近新高+低波", "rk_dist_high252", "high", 0.20, ["rk_atr_pct:low:0.40"],
     "距新高最近20% 且 低波动前40%"),
]


# ===========================================================================
# 三、回测引擎（信号驱动）
# ===========================================================================
class SignalBacktest:
    """给定 {date: [codes]} 信号，跑组合回测。"""

    def __init__(self, cfg: dict, max_pos: int = 5, max_pct: float = 0.25,
                 stop_pct: float = -0.10, tp_pct: float | None = None,
                 max_hold: int = 20, min_list_days: int = 120):
        st = cfg.get("strategy", {})
        pf = st.get("portfolio", {})
        c = st.get("cost", {})
        self.buy_rate = c.get("commission", 0.00025) + c.get("slippage", 0.00075)
        self.sell_rate = (c.get("commission", 0.00025) + c.get("stamp_tax", 0.0005)
                          + c.get("slippage", 0.00075))
        self.init_cap = pf.get("initial_capital", 1_000_000)
        self.max_pos = max_pos
        self.max_pct = max_pct
        self.stop_pct = stop_pct
        self.tp_pct = tp_pct
        self.max_hold = max_hold
        self.min_list_days = min_list_days

    def run(self, data: dict, signals: dict, pool_by_date: dict):
        cal = sorted(pool_by_date.keys())
        if not cal:
            return pd.DataFrame(), pd.DataFrame()

        idx, fdate = {}, {}
        for c, d in data.items():
            ds = pd.to_datetime(d["date"]).tolist()
            idx[c] = {pd.Timestamp(x): i for i, x in enumerate(ds)}
            fdate[c] = pd.Timestamp(ds[0])

        cash = float(self.init_cap)
        pos = {}
        eq_rows, tr_rows = [], []
        peak = self.init_cap

        for di, dt in enumerate(cal):
            # ---- 出场（昨日已完成K线判定）
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
                elif self.tp_pct is not None and ybar["high"] >= p["tp"]:
                    reason = "止盈"
                elif p["days"] >= self.max_hold:
                    reason = "超时"
                if reason:
                    proceeds = p["shares"] * op * (1 - self.sell_rate)
                    cash += proceeds
                    tr_rows.append({
                        "code": c, "entry_date": p["entry_date"],
                        "exit_date": dt, "days": p["days"], "reason": reason,
                        "ret": proceeds / p["cost"] - 1,
                        "year": pd.Timestamp(p["entry_date"]).year,
                    })
                    del pos[c]
                    continue
                p["days"] += 1

            # ---- 入场（昨日信号，今日开盘）
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
                        if (dt - fdate[c]).days < self.min_list_days:
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
                        if alloc < self.init_cap * 0.001:   # 放宽：0.1%
                            continue
                        sh = (alloc * (1 - self.buy_rate)) / op
                        if sh <= 0:
                            continue
                        cash -= alloc
                        stop = op * (1 + self.stop_pct)
                        pos[c] = {
                            "entry_date": dt, "entry_price": op, "shares": sh,
                            "cost": alloc, "days": 0, "k0": j, "stop": stop,
                            "tp": op * (1 + self.tp_pct) if self.tp_pct else None,
                        }

            # ---- 入场B：当日新产生的信号也允许当日开盘不能成交，故不处理
            #      （严格 T 收盘信号 → T+1 开盘成交）

            # ---- 权益
            eq = cash
            for c, p in pos.items():
                im = idx.get(c)
                if im and dt in im:
                    v = data[c]["close"].iloc[im[dt]]
                    eq += (v if np.isfinite(v) else p["entry_price"]) * p["shares"]
                else:
                    eq += p["entry_price"] * p["shares"]
            peak = max(peak, eq)
            eq_rows.append({"date": dt, "equity": eq, "n": len(pos),
                            "dd": eq / peak - 1})

        return pd.DataFrame(eq_rows), pd.DataFrame(tr_rows)


def build_signals(data: dict, spec: tuple, pool_by_date: dict,
                  rank_by_strength: bool = True) -> dict:
    """
    根据策略定义生成信号 {date: [codes]}。

    spec = (name, factor, direction, q, extra, desc)

    rank_by_strength=True 时，同日信号按因子强度排序（最强的排前面），
    回测取前 N 个，避免按代码顺序的任意性。
    """
    name, factor, direction, q, extra, desc = spec
    extras = []
    for e in extra or []:
        parts = e.split(":")
        extras.append((parts[0], parts[1], float(parts[2])))

    sig = defaultdict(list)
    for c6, d in data.items():
        if factor not in d.columns:
            continue
        if direction == "low":
            mask = d[factor] <= q
        else:
            mask = d[factor] >= (1 - q)
        mask = mask.fillna(False)
        for ef, edir, eq_ in extras:
            if ef not in d.columns:
                mask = mask & False
                break
            if edir == "low":
                mask = mask & (d[ef] <= eq_).fillna(False)
            else:
                mask = mask & (d[ef] >= (1 - eq_)).fillna(False)

        idxs = np.where(mask.values)[0]
        if len(idxs) == 0:
            continue
        ds = d["date"].tolist()
        vals = d[factor].values
        for k in idxs:
            sig[pd.Timestamp(ds[k])].append((c6, vals[k]))

    # 按强度排序：low 方向取最小，high 方向取最大
    out = {}
    for dt, items in sig.items():
        if rank_by_strength:
            items.sort(key=lambda x: (x[1] if np.isfinite(x[1]) else 9e9),
                       reverse=(direction != "low"))
        out[dt] = [c for c, _ in items]
    return out


def perf(eq, tr, label):
    if eq is None or len(eq) < 2:
        return {"策略": label, "错误": "无曲线"}
    e = eq["equity"].values
    dd_ = pd.to_datetime(eq["date"])
    yrs = (dd_.iloc[-1] - dd_.iloc[0]).days / 365.25
    if yrs <= 0:
        return {"策略": label, "错误": "跨度不足"}
    cagr = (e[-1] / e[0]) ** (1 / yrs) - 1
    r = pd.Series(e).pct_change().dropna()
    vol = r.std() * np.sqrt(244) if len(r) > 1 else 0
    sharpe = (r.mean() * 244 - 0.02) / vol if vol > 0 else np.nan
    mdd = eq["dd"].min()
    out = {
        "策略": label,
        "CAGR%": round(cagr * 100, 2),
        "Sharpe": round(sharpe, 2) if np.isfinite(sharpe) else None,
        "回撤%": round(mdd * 100, 2),
        "Calmar": round(cagr / abs(mdd), 2) if mdd else None,
        "年数": round(yrs, 1),
    }
    if tr is not None and len(tr):
        rr = tr["ret"]
        out.update({
            "交易": len(tr),
            "胜率%": round((rr > 0).mean() * 100, 1),
            "单笔%": round(rr.mean() * 100, 2),
            "年交易": round(len(tr) / yrs, 1),
            "持有天": round(tr["days"].mean(), 1),
        })
    # 保存日收益供 DSR
    out["_returns"] = r.values
    return out
