# -*- coding: utf-8 -*-
"""
step35_breakout_pattern.py — 锐科激光实际形态的回测

锐科激光不符合「缩量筑底」，它符合：
  「深跌后放量突破」形态

量化定义：
  a) 距250日高点 <= -25%       （前期深跌）
  b) 曾跌破MA120               （确认在底部区域）
  c) 现在站上MA20              （趋势转折）
  d) 位置(20日) >= 70%         （在区间顶部）
  e) 量能比 v5/v60 >= 1.1      （放量）
  f) 近5日有涨幅 >= 5% 的单日  （有爆发）

对照：
  · 纯「放量突破」但无深跌背景
  · 「缩量筑底」（step33 已测）
  · 全市场随机
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
    setup_logger("bo", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    print("=" * 128)
    print("  「深跌后放量突破」形态回测（锐科激光实际形态）")
    print("=" * 128)
    print()
    print("  锐科激光不符合「缩量筑底」（5条全FAIL），它实际是：")
    print("    距250日高点 -32.49%  +  站上所有均线  +  位置79.4%  +  放量1.26")
    print()

    # ---- 加载
    print("[1] 加载本地K线...")
    t0 = time.time()
    data = {}
    for d in ("kline", "kline_delisted", "kline_full"):
        for f in glob.glob(str(cache / d / "*.csv")):
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
                x = pd.read_csv(f, usecols=lambda c: c in
                                ("date", "open", "high", "low", "close", "volume"))
                if len(x) < 300:
                    continue
                x = x.copy()
                x["date"] = pd.to_datetime(x["date"])
                x = x.sort_values("date").reset_index(drop=True)
                for c in ("open", "high", "low", "close"):
                    x[c] = pd.to_numeric(x[c], errors="coerce")
                x["volume"] = pd.to_numeric(x.get("volume"), errors="coerce")
                if x["close"].isna().mean() > 0.05:
                    continue
                data[stem] = x
            except Exception:
                pass
    print(f"    {len(data)} 只  ({time.time()-t0:.0f}s)")
    print()

    # ---- 扫描
    print("[2] 扫描「深跌后放量突破」...")
    t0 = time.time()
    ev = []
    step = 3
    for c6, d in data.items():
        try:
            n = len(d)
            cl = d["close"].values
            hi = d["high"].values
            lo = d["low"].values
            op = d["open"].values
            vo = d["volume"].values.astype(float)
            dt = d["date"].values
            if n < 320:
                continue
            for i in range(250, n - 260, step):
                h250 = np.nanmax(hi[i - 249:i + 1])
                if not (np.isfinite(h250) and h250 > 0):
                    continue
                dd250 = cl[i] / h250 - 1
                if dd250 > -0.25:          # a) 前期深跌
                    continue
                ma20 = np.nanmean(cl[i - 19:i + 1])
                ma60 = np.nanmean(cl[i - 59:i + 1])
                ma120 = (np.nanmean(cl[i - 119:i + 1])
                         if i >= 119 else np.nan)
                if not (np.isfinite(ma20) and np.isfinite(ma60)):
                    continue
                # c) 站上MA20 且 MA60
                if cl[i] <= ma20 or cl[i] <= ma60:
                    continue
                # b) 过去120日内曾跌破MA120（确认底部区域）
                if np.isfinite(ma120) and i >= 130:
                    below = False
                    for k in range(max(120, i - 120), i):
                        m = np.nanmean(cl[k - 119:k + 1])
                        if np.isfinite(m) and cl[k] < m:
                            below = True
                            break
                    if not below:
                        continue
                # d) 位置 >= 70%
                h20 = np.nanmax(hi[i - 19:i + 1])
                l20 = np.nanmin(lo[i - 19:i + 1])
                if not (np.isfinite(h20) and h20 > l20):
                    continue
                pos = (cl[i] - l20) / (h20 - l20)
                if pos < 0.70:
                    continue
                # e) 放量
                v5 = np.nanmean(vo[i - 4:i + 1])
                v60 = np.nanmean(vo[i - 59:i + 1])
                if not (np.isfinite(v5) and np.isfinite(v60) and v60 > 0):
                    continue
                vr = v5 / v60
                if vr < 1.10:
                    continue
                # f) 近5日有爆发
                chg5 = cl[i] / cl[i - 5] - 1 if i >= 5 else np.nan
                maxday = np.nanmax(np.diff(cl[i - 5:i + 1]) / cl[i - 5:i]) \
                    if i >= 5 else np.nan
                if not (np.isfinite(maxday) and maxday >= 0.05):
                    continue
                p0 = op[i + 1]
                if not (np.isfinite(p0) and p0 > 0):
                    continue
                rec = {"date": pd.Timestamp(dt[i]), "code": c6,
                       "dd250": dd250 * 100, "pos": pos * 100,
                       "vr": vr, "chg5": chg5 * 100, "maxday": maxday * 100,
                       "dma20": (cl[i] / ma20 - 1) * 100}
                for hz in (20, 60, 120, 250):
                    j = i + 1 + hz
                    rec[f"f{hz}"] = (cl[j] / p0 - 1) * 100 if j < n else np.nan
                j2 = min(i + 1 + 60, n - 1)
                rec["mdd60"] = (np.nanmin(lo[i + 1:j2 + 1]) / p0 - 1) * 100
                ev.append(rec)
        except Exception:
            continue
    print(f"    匹配: {len(ev):,}  ({time.time()-t0:.0f}s)")
    print()
    if not ev:
        print("无匹配")
        return 1
    E = pd.DataFrame(ev)
    print(f"    时间范围: {E['date'].min().date()} ~ {E['date'].max().date()}")
    print(f"    涉及股票: {E['code'].nunique():,} 只")
    per_day = E.groupby("date").size()
    print(f"    平均每日: {per_day.mean():.1f} 只")
    print()

    # ---- 结果
    print("=" * 128)
    print("  一、之后怎么走")
    print("=" * 128)
    print()
    print(f"  {'持有期':<8}{'样本':>9}{'平均%':>9}{'中位%':>9}"
          f"{'胜率':>8}{'最好%':>10}{'最差%':>10}{'25分位':>9}{'75分位':>9}")
    print("  " + "-" * 86)
    for hz in (20, 60, 120, 250):
        v = E[f"f{hz}"].dropna()
        if len(v):
            print(f"  {hz}日{'':<4}{len(v):>9}{v.mean():>9.2f}{v.median():>9.2f}"
                  f"{(v>0).mean()*100:>7.1f}%{v.max():>10.1f}{v.min():>10.1f}"
                  f"{v.quantile(.25):>9.2f}{v.quantile(.75):>9.2f}")
    md = E["mdd60"].dropna()
    if len(md):
        print(f"  60日浮亏{'':<1}{len(md):>9}{md.mean():>9.2f}{md.median():>9.2f}")
    print()

    # ---- 对照：缩量筑底
    print("=" * 128)
    print("  二、三种形态对比")
    print("=" * 128)
    print()
    W = None
    wp = outdir / "washout_base_events.csv"
    if wp.exists():
        W = pd.read_csv(wp)
    print(f"  {'形态':<24}{'样本':>9}{'20日%':>9}{'60日%':>9}"
          f"{'120日%':>9}{'250日%':>9}{'60日胜率':>10}{'250日胜率':>11}")
    print("  " + "-" * 92)
    # A 突破
    line = f"  {'A 深跌后放量突破':<24}{len(E):>9}"
    for hz in (20, 60, 120, 250):
        v = E[f"f{hz}"].dropna()
        line += f"{v.mean():>9.2f}"
    for hz in (60, 250):
        v = E[f"f{hz}"].dropna()
        line += f"{(v>0).mean()*100:>9.1f}%"
    print(line)
    # B 缩量筑底
    if W is not None:
        line = f"  {'B 缩量筑底':<24}{len(W):>9}"
        for hz in (20, 60, 120, 250):
            v = W[f"f{hz}"].dropna()
            line += f"{v.mean():>9.2f}"
        for hz in (60, 250):
            v = W[f"f{hz}"].dropna()
            line += f"{(v>0).mean()*100:>9.1f}%"
        print(line)
    print()

    # ---- 基准
    print("[3] 采集随机基准...")
    t0 = time.time()
    base = []
    for c6, d in data.items():
        try:
            n = len(d)
            cl = d["close"].values
            op = d["open"].values
            lo = d["low"].values
            for i in range(250, n - 260, 40):
                p0 = op[i + 1]
                if not (np.isfinite(p0) and p0 > 0):
                    continue
                rec = {}
                for hz in (20, 60, 120, 250):
                    j = i + 1 + hz
                    rec[f"f{hz}"] = (cl[j] / p0 - 1) * 100 if j < n else np.nan
                j2 = min(i + 1 + 60, n - 1)
                rec["mdd60"] = (np.nanmin(lo[i + 1:j2 + 1]) / p0 - 1) * 100
                base.append(rec)
        except Exception:
            continue
    B = pd.DataFrame(base)
    print(f"    {len(B):,}  ({time.time()-t0:.0f}s)")
    print()
    print(f"  {'指标':<12}{'放量突破':>12}{'全市场随机':>12}{'超额':>10}")
    print("  " + "-" * 48)
    for hz in (20, 60, 120, 250):
        v1 = E[f"f{hz}"].dropna()
        v2 = B[f"f{hz}"].dropna()
        print(f"  {hz}日收益%{'':<3}{v1.mean():>12.2f}{v2.mean():>12.2f}"
              f"{v1.mean()-v2.mean():>+10.2f}")
    for hz in (20, 60, 120, 250):
        v1 = E[f"f{hz}"].dropna()
        v2 = B[f"f{hz}"].dropna()
        print(f"  {hz}日胜率%{'':<3}{(v1>0).mean()*100:>12.1f}"
              f"{(v2>0).mean()*100:>12.1f}"
              f"{((v1>0).mean()-(v2>0).mean())*100:>+10.1f}")
    v1, v2 = E["mdd60"].dropna(), B["mdd60"].dropna()
    print(f"  {'60日浮亏%':<11}{v1.mean():>12.2f}{v2.mean():>12.2f}"
          f"{v1.mean()-v2.mean():>+10.2f}")
    print()

    # ---- 分层
    print("=" * 128)
    print("  三、分层检验")
    print("=" * 128)
    print()
    E2 = E.dropna(subset=["f60"]).copy()

    def layer(col, label, q=4):
        if E2[col].nunique() < 3:
            return
        try:
            E2["_q"] = pd.qcut(E2[col], q, labels=False, duplicates="drop")
        except Exception:
            return
        print(f"  【{label}】")
        print(f"    {'档':<6}{'范围':<22}{'样本':>8}{'60日%':>10}"
              f"{'60日胜率':>10}{'250日%':>10}{'250日胜率':>10}")
        print("    " + "-" * 76)
        for g in sorted(E2["_q"].dropna().unique()):
            sub = E2[E2["_q"] == g]
            v60 = sub["f60"].dropna()
            v250 = sub["f250"].dropna()
            print(f"    Q{int(g)+1:<5}{f'{sub[col].min():.1f}~{sub[col].max():.1f}':<22}"
                  f"{len(sub):>8}{v60.mean():>10.2f}{(v60>0).mean()*100:>9.1f}%"
                  f"{v250.mean():>10.2f}{(v250>0).mean()*100:>9.1f}%")
        print()
        E2.drop(columns=["_q"], inplace=True, errors="ignore")

    layer("dd250", "前期跌幅（dd250）")
    layer("vr", "放量程度（v5/v60）")
    layer("chg5", "近5日涨幅")
    layer("maxday", "单日最大涨幅")

    # ---- 分年度
    print("=" * 128)
    print("  四、分年度")
    print("=" * 128)
    print()
    E2["yr"] = E2["date"].dt.year
    print(f"  {'年份':<8}{'样本':>8}{'60日%':>10}{'60日胜率':>11}"
          f"{'250日%':>10}{'250日胜率':>11}")
    print("  " + "-" * 60)
    for y, g in E2.groupby("yr"):
        if len(g) < 25:
            continue
        v60 = g["f60"].dropna()
        v250 = g["f250"].dropna()
        print(f"  {y:<8}{len(g):>8}{v60.mean():>10.2f}"
              f"{(v60>0).mean()*100:>10.1f}%"
              f"{(v250.mean() if len(v250) else float('nan')):>10.2f}"
              f"{((v250>0).mean()*100 if len(v250) else float('nan')):>10.1f}%")
    print()

    E.to_csv(outdir / "breakout_events.csv", index=False, encoding="utf-8-sig")
    B.to_csv(outdir / "breakout_baseline.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/breakout_events.csv  ({len(E):,})")
    print(f"[输出] {outdir}/breakout_baseline.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
