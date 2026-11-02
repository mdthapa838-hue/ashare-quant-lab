# -*- coding: utf-8 -*-
"""
step17_bottom_analog.py — 历史类比：现在是不是底部？

方法（不预测，只做类比）：
  1. 定义「市场状态向量」（多维、无未来函数）
  2. 找出历史所有真实底部（阶段性低点）的状态向量
  3. 计算当前状态与每个历史时点的相似度
  4. 取最相似的 K 个历史时刻，统计它们之后 20/60/120/250 日的走势
  5. 输出：分布（而不是单点预测）

关键：所有指标只用 t 及之前的数据。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger
from step3_engine import load_all_kline


def get_index(pro, code, start, end):
    frames = []
    cur = pd.Timestamp(end)
    s0 = pd.Timestamp(start)
    for _ in range(40):
        seg = max(s0, cur - pd.Timedelta(days=500))
        got = None
        for attempt in range(3):
            try:
                got = pro.index_daily(ts_code=code,
                                      start_date=seg.strftime("%Y%m%d"),
                                      end_date=cur.strftime("%Y%m%d"))
                break
            except Exception:
                time.sleep(1.0 * (attempt + 1))
        if got is None or len(got) == 0:
            break
        frames.append(got)
        earliest = pd.to_datetime(got["trade_date"].min(), format="%Y%m%d")
        if earliest <= s0:
            break
        cur = earliest - pd.Timedelta(days=1)
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d = d.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
    d["date"] = pd.to_datetime(d["trade_date"], format="%Y%m%d")
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    return d[["date", "close"]].reset_index(drop=True)


def build_state(idx: pd.DataFrame, ew: pd.DataFrame,
                col: str = "hs300") -> pd.DataFrame:
    """
    构建市场状态向量（全部因果）。

    维度：
      dd250   距250日高点回撤
      ma_dist 距年线
      r60     60日收益
      r120    120日收益
      rsi14   RSI
      vol60   年化波动率
      vol_ratio 20日/250日波动比（恐慌扩张）
      below   跌破年线后的天数（持续时间）
      bb_pos  布林位置
    """
    d = idx.copy()
    c = d[col]
    d["ma20"] = c.rolling(20, min_periods=20).mean()
    d["ma60"] = c.rolling(60, min_periods=60).mean()
    d["ma250"] = c.rolling(250, min_periods=250).mean()
    hi250 = c.rolling(250, min_periods=250).max()
    d["dd250"] = (c / hi250 - 1) * 100
    d["ma_dist"] = (c / d["ma250"] - 1) * 100
    d["r20"] = (c / c.shift(20) - 1) * 100
    d["r60"] = (c / c.shift(60) - 1) * 100
    d["r120"] = (c / c.shift(120) - 1) * 100
    d["r250"] = (c / c.shift(250) - 1) * 100
    # RSI
    diff = c.diff()
    up = diff.clip(lower=0).rolling(14, min_periods=14).mean()
    dn = (-diff.clip(upper=0)).rolling(14, min_periods=14).mean()
    d["rsi14"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    # 波动
    ret = c.pct_change()
    d["vol60"] = ret.rolling(60, min_periods=60).std() * np.sqrt(244) * 100
    d["vol250"] = ret.rolling(250, min_periods=250).std() * np.sqrt(244) * 100
    d["vol_ratio"] = d["vol60"] / d["vol250"].replace(0, np.nan)
    # 跌破年线天数
    below = (c < d["ma250"]).astype(int)
    grp = (below != below.shift()).cumsum()
    d["below_days"] = below.groupby(grp).cumsum() * below
    # 布林位置
    mid = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std(ddof=0)
    d["bb_pos"] = ((c - (mid - 2 * sd)) /
                   ((mid + 2 * sd) - (mid - 2 * sd)).replace(0, np.nan)) * 100
    # 距均线
    d["dma20"] = (c / d["ma20"] - 1) * 100

    # 合并全市场等权（用于前瞻）
    e = ew.set_index("date")["eq_equity"]
    d = d.set_index("date")
    d["ew"] = e.reindex(d.index, method="ffill")
    return d


FEATURES = ["dd250", "ma_dist", "r60", "r120", "rsi14", "vol_ratio",
            "bb_pos", "dma20"]


def main():
    cfg = load_config()
    setup_logger("bottom", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 130)
    print("  历史类比：现在是不是底部？")
    print("=" * 130)
    print()

    # ---- 全市场等权（用于前瞻收益）
    ew_p = outdir / "market_equalweight.csv"
    if ew_p.exists():
        EW = pd.read_csv(ew_p)
        EW["date"] = pd.to_datetime(EW["date"])
        print(f"[1] 全市场等权: {len(EW)} 天 "
              f"({EW['date'].min().date()} ~ {EW['date'].max().date()})")
    else:
        print("缺少 market_equalweight.csv，请先运行 step16")
        return 1

    # ---- 指数
    idx = get_index(pro, "000300.SH", "20050101", "20261231")
    if idx is None:
        print("指数获取失败")
        return 1
    print(f"[2] 沪深300: {len(idx)} 天")

    # ---- 状态向量
    print("[3] 构建状态向量...")
    ST = build_state(idx, EW, "close")
    ST = ST.dropna(subset=FEATURES)
    print(f"    有效: {len(ST)} 天 "
          f"({ST.index.min().date()} ~ {ST.index.max().date()})")
    print()

    # ---- 前瞻收益（按全市场等权）
    for n in (20, 60, 120, 250):
        ST[f"fwd{n}"] = ST["ew"].shift(-n) / ST["ew"] - 1
    # 前瞻最大回撤（未来N日内的最低点）
    for n in (20, 60, 250):
        fwd_min = []
        vals = ST["ew"].values
        for i in range(len(vals)):
            j = min(i + n, len(vals) - 1)
            seg = vals[i:j + 1]
            fwd_min.append(seg.min() / vals[i] - 1 if len(seg) else np.nan)
        ST[f"fwdmin{n}"] = fwd_min
    print("[4] 前瞻收益计算完成")
    print()

    # ---- 真实底部识别（用于建立「底部画像」）
    print("[5] 识别历史真实底部...")
    c = ST["hs300"] if "hs300" in ST.columns else None
    # 用沪深300 的 ZigZag 低点
    dd = ST["dd250"]
    # 底部 = 此后 60 日收益 > 15% 的时点
    bottom_mask = ST["fwd60"] > 0.15
    B = ST[bottom_mask]
    print(f"    事后看是底部的天数: {len(B)}")
    if len(B):
        print()
        print("  【历史底部时的指标画像】")
        print(f"  {'指标':<12}{'底部均值':>10}{'底部中位':>10}"
              f"{'全样本均值':>12}{'分位':>8}")
        for f in FEATURES:
            v = B[f].dropna()
            allv = ST[f].dropna()
            pct = (allv <= v.mean()).mean() * 100
            print(f"  {f:<12}{v.mean():>10.2f}{v.median():>10.2f}"
                  f"{allv.mean():>12.2f}{pct:>7.0f}%")
        print()

    # ---- 当前状态
    print("[6] 当前状态")
    cur = ST.iloc[-1]
    cur_date = ST.index[-1]
    print(f"    日期: {cur_date.date()}")
    for f in FEATURES:
        hist = ST[f].dropna()
        pct = (hist <= cur[f]).mean() * 100
        print(f"    {f:<12}{cur[f]:>10.2f}   （历史分位 {pct:>5.1f}%）")
    print()

    # ---- 相似度匹配
    print("=" * 130)
    print("  历史类比：找出与当前最相似的历史时刻")
    print("=" * 130)
    print()
    # 标准化（用历史均值方差）
    mu = ST[FEATURES].mean()
    sd = ST[FEATURES].std().replace(0, np.nan)
    Z = (ST[FEATURES] - mu) / sd
    zc = (cur[FEATURES] - mu) / sd
    dist = np.sqrt(((Z - zc) ** 2).sum(axis=1))
    ST["dist"] = dist

    # 排除最近1年（避免用当前样本）
    cutoff = cur_date - pd.Timedelta(days=365)
    hist_pool = ST[ST.index < cutoff].copy()
    print(f"    历史样本池: {len(hist_pool)} 天（排除最近1年）")
    print()

    for K in (30, 60, 100):
        top = hist_pool.nsmallest(K, "dist")
        print(f"  【最相似的 {K} 天】距离范围 {top['dist'].min():.3f} ~ {top['dist'].max():.3f}")
        print(f"    时间跨度: {top.index.min().date()} ~ {top.index.max().date()}")
        print()
        print(f"    {'前瞻':<8}{'平均':>10}{'中位':>10}{'胜率':>9}"
              f"{'最差':>10}{'最好':>10}")
        for n in (20, 60, 120, 250):
            v = top[f"fwd{n}"].dropna()
            if len(v):
                print(f"    {n}日{'':<4}{v.mean()*100:>9.2f}%{v.median()*100:>9.2f}%"
                      f"{(v>0).mean()*100:>8.1f}%{v.min()*100:>9.2f}%{v.max()*100:>9.2f}%")
        print(f"    {'未来最大回撤':<8}")
        for n in (20, 60, 250):
            v = top[f"fwdmin{n}"].dropna()
            if len(v):
                print(f"    {n}日{'':<4}平均 {v.mean()*100:>9.2f}%   "
                      f"中位 {v.median()*100:>9.2f}%   "
                      f"最差 {v.min()*100:>9.2f}%")
        print()

    # ---- 用最相似30天的详细分布
    top30 = hist_pool.nsmallest(30, "dist")
    print("=" * 130)
    print("  最相似 30 个时点的明细")
    print("=" * 130)
    print()
    det = top30[["dd250", "ma_dist", "r60", "rsi14", "vol_ratio",
                 "fwd20", "fwd60", "fwd120", "fwd250", "fwdmin60"]
               ].copy()
    det["fwd20"] = (det["fwd20"] * 100).round(1)
    det["fwd60"] = (det["fwd60"] * 100).round(1)
    det["fwd120"] = (det["fwd120"] * 100).round(1)
    det["fwd250"] = (det["fwd250"] * 100).round(1)
    det["fwdmin60"] = (det["fwdmin60"] * 100).round(1)
    print(det.round(2).to_string())
    print()

    # ---- 概率化结论
    print("=" * 130)
    print("  结论（概率化）")
    print("=" * 130)
    print()
    for K in (30, 60):
        top = hist_pool.nsmallest(K, "dist")
        v60 = top["fwd60"].dropna()
        v120 = top["fwd120"].dropna()
        v250 = top["fwd250"].dropna()
        m60 = top["fwdmin60"].dropna()
        print(f"  以最相似的 {K} 天为参照:")
        if len(v60):
            print(f"    60日后: 上涨概率 {(v60>0).mean()*100:.0f}%  "
                  f"平均 {v60.mean()*100:+.1f}%  "
                  f"最差 {v60.min()*100:+.1f}%")
        if len(v120):
            print(f"    120日后: 上涨概率 {(v120>0).mean()*100:.0f}%  "
                  f"平均 {v120.mean()*100:+.1f}%")
        if len(v250):
            print(f"    250日后: 上涨概率 {(v250>0).mean()*100:.0f}%  "
                  f"平均 {v250.mean()*100:+.1f}%")
        if len(m60):
            print(f"    未来60日内最大回撤: 中位 {m60.median()*100:+.1f}%  "
                  f"最差 {m60.min()*100:+.1f}%")
        print()

    # ---- 输出
    ST.reset_index().to_csv(outdir / "market_state_vector.csv", index=False,
                            encoding="utf-8-sig")
    det.reset_index().to_csv(outdir / "analog_top30.csv", index=False,
                             encoding="utf-8-sig")
    print(f"[输出] {outdir}/market_state_vector.csv")
    print(f"[输出] {outdir}/analog_top30.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
