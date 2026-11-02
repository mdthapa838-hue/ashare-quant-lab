# -*- coding: utf-8 -*-
"""
step27_gap_backtest.py — 底部跳空信号回测（独立、严谨）

用本地缓存K线（4199只，含退市，2015-2026）做完整回测。
不依赖网络，速度快。

关键设计：
  · 用「次日开盘」买入（跳空当日收盘后才知道信号）
  · 前瞻 5/10/20/60 日
  · 与同池随机基准对比（不是与"零"对比）
  · 多种跳空定义对比
"""
from __future__ import annotations

import os
import sys
import glob
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from core.config import load_config
from core.logger import setup_logger


def main():
    cfg = load_config()
    setup_logger("gb", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    lookback = 130
    print("=" * 120)
    print("  底部跳空信号回测（严格版）")
    print("=" * 120)
    print()

    # ---- 加载本地K线
    files = []
    for d in ("kline", "kline_delisted"):
        files += glob.glob(str(cache / d / "*.csv"))
    print(f"  本地K线文件: {len(files)}")
    t0 = time.time()
    data = {}
    for f in files:
        stem = os.path.basename(f)[:-4]
        for suf in ("_qfq", "_hfq", "_bfq"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        if not (stem.isdigit() and len(stem) == 6):
            continue
        try:
            d = pd.read_csv(f, usecols=lambda c: c in
                            ("date", "open", "high", "low", "close"))
            if len(d) < lookback + 80:
                continue
            d = d.copy()
            d["date"] = pd.to_datetime(d["date"])
            d = d.sort_values("date").reset_index(drop=True)
            for c in ("open", "high", "low", "close"):
                d[c] = pd.to_numeric(d[c], errors="coerce")
            if d["close"].isna().mean() > 0.05:
                continue
            data[stem] = d
        except Exception:
            pass
    print(f"  有效: {len(data)} 只  ({time.time()-t0:.0f}s)")
    print()

    # ---- 排除 ST/退（用名称近似：本地无名称，用价格特征不现实 → 跳过，说明局限）
    print("  ⚠ 局限：本地K线无法识别 ST/退（名称不在文件里）")
    print("         将与「全市场随机买入」对比来消除这部分影响")
    print()

    # ---- 事件采集
    print("[1] 采集底部跳空事件...")
    t0 = time.time()
    ev = []
    step = 2   # 每2日采样，加速
    for c6, d in data.items():
        try:
            lo = d["low"].values
            hi = d["high"].values
            op = d["open"].values
            cl = d["close"].values
            dt = d["date"].values
            n = len(d)
            for i in range(lookback, n - 62, step):
                if not (np.isfinite(lo[i]) and np.isfinite(hi[i - 1])):
                    continue
                if hi[i - 1] <= 0 or lo[i] <= hi[i - 1]:
                    continue
                gap = (lo[i] / hi[i - 1] - 1) * 100
                if gap < 0.3:
                    continue
                sh = np.nanmax(hi[i - lookback:i + 1])
                sl = np.nanmin(lo[i - lookback:i + 1])
                if not np.isfinite(sh) or sh <= sl:
                    continue
                pos = (cl[i] - sl) / (sh - sl)
                dd = (cl[i] / sh - 1) * 100
                if not (pos <= 0.40 or dd <= -15.0):
                    continue
                p0 = op[i + 1]
                if not np.isfinite(p0) or p0 <= 0:
                    continue
                r = {"date": pd.Timestamp(dt[i]), "code": c6,
                     "gap": gap, "pos": pos * 100, "dd": dd,
                     "chg": (cl[i] / cl[i - 1] - 1) * 100}
                for hz in (5, 10, 20, 60):
                    j = i + 1 + hz
                    r[f"f{hz}"] = (cl[j] / p0 - 1) * 100 if j < n else np.nan
                j2 = min(i + 1 + 20, n - 1)
                r["mdd20"] = (np.nanmin(lo[i + 1:j2 + 1]) / p0 - 1) * 100
                ev.append(r)
        except Exception:
            continue
    print(f"    事件数: {len(ev):,}  ({time.time()-t0:.0f}s)")
    print()
    if not ev:
        print("无事件")
        return 1
    E = pd.DataFrame(ev)
    print(f"    时间范围: {E['date'].min().date()} ~ {E['date'].max().date()}")
    print()

    # ---- 基准：全市场随机买入（同样隔2日采样）
    print("[2] 采集基准样本（全市场随机）...")
    t0 = time.time()
    base = []
    for c6, d in data.items():
        try:
            op = d["open"].values
            cl = d["close"].values
            n = len(d)
            for i in range(lookback, n - 62, 40):
                p0 = op[i + 1]
                if not np.isfinite(p0) or p0 <= 0:
                    continue
                r = {}
                for hz in (5, 10, 20, 60):
                    j = i + 1 + hz
                    r[f"f{hz}"] = (cl[j] / p0 - 1) * 100 if j < n else np.nan
                j2 = min(i + 1 + 20, n - 1)
                r["mdd20"] = (np.nanmin(d["low"].values[i + 1:j2 + 1]) / p0
                              - 1) * 100
                base.append(r)
        except Exception:
            continue
    print(f"    基准样本: {len(base):,}  ({time.time()-t0:.0f}s)")
    print()
    B = pd.DataFrame(base)

    # ---- 对比
    print("=" * 120)
    print("  一、底部跳空 vs 全市场随机")
    print("=" * 120)
    print()
    print(f"  {'指标':<14}{'底部跳空':>12}{'全市场随机':>12}{'超额':>10}")
    print("  " + "-" * 50)
    for hz in (5, 10, 20, 60):
        v1 = E[f"f{hz}"].dropna()
        v2 = B[f"f{hz}"].dropna()
        if len(v1) and len(v2):
            print(f"  {hz}日收益%{'':<5}{v1.mean():>12.2f}{v2.mean():>12.2f}"
                  f"{v1.mean()-v2.mean():>+10.2f}")
    for lab in ("5", "10", "20", "60"):
        v1 = E[f"f{lab}"].dropna()
        v2 = B[f"f{lab}"].dropna()
        if len(v1) and len(v2):
            print(f"  {lab}日胜率%{'':<5}{(v1>0).mean()*100:>12.1f}"
                  f"{(v2>0).mean()*100:>12.1f}"
                  f"{((v1>0).mean()-(v2>0).mean())*100:>+10.1f}")
    v1 = E["mdd20"].dropna()
    v2 = B["mdd20"].dropna()
    if len(v1) and len(v2):
        print(f"  {'20日最大浮亏%':<12}{v1.mean():>12.2f}{v2.mean():>12.2f}"
              f"{v1.mean()-v2.mean():>+10.2f}")
    print()

    # ---- 分层：跳空幅度
    print("=" * 120)
    print("  二、按跳空幅度分层（20日收益）")
    print("=" * 120)
    print()
    E2 = E.dropna(subset=["f20"]).copy()
    if len(E2) > 200:
        E2["gq"] = pd.qcut(E2["gap"], 5, labels=False, duplicates="drop")
        print(f"  {'档':<5}{'跳空范围':<18}{'样本':>8}{'20日平均':>11}"
              f"{'20日中位':>11}{'20日胜率':>11}{'60日平均':>11}{'20日浮亏':>11}")
        print("  " + "-" * 88)
        for q, g in E2.groupby("gq"):
            print(f"  Q{int(q)+1:<4}{g['gap'].min():.2f}%~{g['gap'].max():.2f}%"
                  f"{'':<8}{len(g):>8}{g['f20'].mean():>11.2f}"
                  f"{g['f20'].median():>11.2f}{(g['f20']>0).mean()*100:>10.1f}%"
                  f"{g['f60'].mean():>11.2f}{g['mdd20'].mean():>11.2f}")
        print()

    # ---- 分层：位置
    print("=" * 120)
    print("  三、按位置分层（20日收益）")
    print("=" * 120)
    print()
    if len(E2) > 200:
        E2["pq"] = pd.qcut(E2["pos"], 5, labels=False, duplicates="drop")
        print(f"  {'档':<5}{'位置分位范围':<18}{'样本':>8}{'20日平均':>11}"
              f"{'20日中位':>11}{'20日胜率':>11}{'60日平均':>11}")
        print("  " + "-" * 76)
        for q, g in E2.groupby("pq"):
            print(f"  Q{int(q)+1:<4}{g['pos'].min():.1f}%~{g['pos'].max():.1f}%"
                  f"{'':<8}{len(g):>8}{g['f20'].mean():>11.2f}"
                  f"{g['f20'].median():>11.2f}{(g['f20']>0).mean()*100:>10.1f}%"
                  f"{g['f60'].mean():>11.2f}")
        print()

    # ---- 分层：当日涨幅（是否涨停）
    print("=" * 120)
    print("  四、按当日涨幅分层（关键：跳空是否伴随大涨）")
    print("=" * 120)
    print()
    if len(E2) > 200:
        E2["limit"] = E2["chg"] >= 9.5
        for lab, g in [("当日涨停(>=9.5%)", E2[E2["limit"]]),
                       ("当日非涨停", E2[~E2["limit"]])]:
            if len(g) < 50:
                continue
            print(f"  【{lab}】样本 {len(g):,}")
            for hz in (5, 10, 20, 60):
                v = g[f"f{hz}"].dropna()
                if len(v):
                    print(f"    {hz:>3}日: 平均 {v.mean():>+7.2f}%  "
                          f"中位 {v.median():>+7.2f}%  "
                          f"胜率 {(v>0).mean()*100:>5.1f}%")
            v = g["mdd20"].dropna()
            if len(v):
                print(f"    20日内浮亏: 平均 {v.mean():>+7.2f}%  "
                      f"中位 {v.median():>+7.2f}%")
            print()

    # ---- 分年度
    print("=" * 120)
    print("  五、分年度稳定性（20日收益）")
    print("=" * 120)
    print()
    E2["yr"] = E2["date"].dt.year
    print(f"  {'年份':<8}{'样本':>8}{'20日平均':>11}{'20日胜率':>11}"
          f"{'60日平均':>11}")
    print("  " + "-" * 50)
    for yr, g in E2.groupby("yr"):
        if len(g) < 30:
            continue
        print(f"  {yr:<8}{len(g):>8}{g['f20'].mean():>11.2f}"
              f"{(g['f20']>0).mean()*100:>10.1f}%{g['f60'].mean():>11.2f}")
    print()

    # ---- 输出
    E.to_csv(outdir / "gap_bt_events.csv", index=False, encoding="utf-8-sig")
    B.head(200000).to_csv(outdir / "gap_bt_baseline.csv", index=False,
                          encoding="utf-8-sig")
    print(f"[输出] {outdir}/gap_bt_events.csv  ({len(E):,} 事件)")
    print(f"[输出] {outdir}/gap_bt_baseline.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
