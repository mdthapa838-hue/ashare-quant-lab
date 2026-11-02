# -*- coding: utf-8 -*-
"""
step33_washout_base.py — 「缩量筑底」形态的历史统计

用协创数据的当前特征定义形态，然后在全历史上找相同形态：

协创数据当前特征（2026-10-16）：
  ① 从阶段高点回撤 -39.8%（跌幅大）
  ② 近30日回撤 -23.1%
  ③ 量能比（20日/60日）= 0.64（显著缩量）
  ④ 波动比（20日/60日）= 0.65（波动收缩）
  ⑤ 位置在20日区间 14.7%（接近底部）
  ⑥ 均线全部破位（距MA20 -13.3%）
  ⑦ 下跌日量能/上涨日量能 = 0.87（缩量下跌）

把这7条参数化，放开阈值做扫描，统计：
  · 形态出现频率（多不多？）
  · 之后 20/60/120/250 日收益分布
  · 与全市场随机基准对比
  · 哪些变量影响成功率（分层）
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

LOOKBACK = 130


def main():
    cfg = load_config()
    setup_logger("wb", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    print("=" * 125)
    print("  「缩量筑底」形态的历史统计")
    print("=" * 125)
    print()
    print("  目标：回答「协创数据这种形态历史上多吗？之后怎么走？」")
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
                if x["close"].isna().mean() > 0.05:
                    continue
                if "volume" in x.columns:
                    x["volume"] = pd.to_numeric(x["volume"], errors="coerce")
                else:
                    x["volume"] = np.nan
                data[stem] = x
            except Exception:
                pass
    print(f"    {len(data)} 只  ({time.time()-t0:.0f}s)")
    print()

    # ---- 形态扫描
    print("[2] 扫描「缩量筑底」形态...")
    print()
    print("  形态条件（可调的宽松版）：")
    print("    a) 距250日高点 <= -30%       （跌得够多）")
    print("    b) 量能比(20日/60日) <= 0.75  （显著缩量）")
    print("    c) 波动比(20日/60日) <= 0.80  （波动收缩）")
    print("    d) 位置(20日区间) <= 30%      （接近短期底部）")
    print("    e) 距MA20 <= -8%              （均线破位）")
    print()

    t0 = time.time()
    ev = []
    step = 3
    for c6, d in data.items():
        try:
            n = len(d)
            if n < 300:
                continue
            cl = d["close"].values
            lo = d["low"].values
            hi = d["high"].values
            op = d["open"].values
            vo = d["volume"].values.astype(float)
            dt = d["date"].values
            # 预计算
            for i in range(250, n - 260, step):
                # 距250日高点
                h250 = np.nanmax(hi[i - 249:i + 1])
                if not np.isfinite(h250) or h250 <= 0:
                    continue
                dd250 = cl[i] / h250 - 1
                if dd250 > -0.30:
                    continue
                # 量能比
                v20 = np.nanmean(vo[i - 19:i + 1])
                v60 = np.nanmean(vo[i - 59:i + 1])
                if not (np.isfinite(v20) and np.isfinite(v60) and v60 > 0):
                    continue
                vr = v20 / v60
                if vr > 0.75:
                    continue
                # 波动比
                seg_cl = cl[i - 60:i + 1]
                r = np.diff(seg_cl) / seg_cl[:-1]
                if len(r) < 59:
                    continue
                v20d = np.nanstd(r[-19:])
                v60d = np.nanstd(r)
                if not (np.isfinite(v20d) and np.isfinite(v60d) and v60d > 0):
                    continue
                vbr = v20d / v60d
                if vbr > 0.80:
                    continue
                # 位置
                h20 = np.nanmax(hi[i - 19:i + 1])
                l20 = np.nanmin(lo[i - 19:i + 1])
                if not (np.isfinite(h20) and h20 > l20):
                    continue
                pos = (cl[i] - l20) / (h20 - l20)
                if pos > 0.30:
                    continue
                # 距MA20
                ma20 = np.nanmean(cl[i - 19:i + 1])
                if not (np.isfinite(ma20) and ma20 > 0):
                    continue
                dma20 = cl[i] / ma20 - 1
                if dma20 > -0.08:
                    continue
                # 下跌日/上涨日量能
                seg_cl2 = cl[i - 30:i + 1]
                chg = np.diff(seg_cl2) / seg_cl2[:-1]
                vols = vo[i - 29:i + 1].astype(float)
                m = min(len(chg), len(vols))
                chg = chg[:m]
                vols = vols[:m]
                dn_m = np.nanmean(vols[chg < 0]) if (chg < 0).any() else np.nan
                up_m = np.nanmean(vols[chg > 0]) if (chg > 0).any() else np.nan
                dvu = dn_m / up_m if (np.isfinite(dn_m) and np.isfinite(up_m)
                                      and up_m > 0) else np.nan
                # 前瞻（次日开盘买入）
                if i + 1 >= n:
                    continue
                p0 = op[i + 1]
                if not (np.isfinite(p0) and p0 > 0):
                    continue
                rec = {"date": pd.Timestamp(dt[i]), "code": c6,
                       "dd250": dd250 * 100, "vratio": vr, "vbratio": vbr,
                       "pos": pos * 100, "dma20": dma20 * 100, "dvu": dvu}
                for hz in (20, 60, 120, 250):
                    j = i + 1 + hz
                    rec[f"f{hz}"] = (cl[j] / p0 - 1) * 100 if j < n else np.nan
                j2 = min(i + 1 + 60, n - 1)
                rec["mdd60"] = (np.nanmin(lo[i + 1:j2 + 1]) / p0 - 1) * 100
                ev.append(rec)
        except Exception:
            continue
    print(f"    匹配事件: {len(ev):,}  ({time.time()-t0:.0f}s)")
    print()
    if not ev:
        print("  无匹配")
        return 1
    E = pd.DataFrame(ev)
    print(f"    时间范围: {E['date'].min().date()} ~ {E['date'].max().date()}")
    print(f"    涉及股票: {E['code'].nunique()} 只")
    print()

    # ---- 频率
    print("=" * 125)
    print("  一、形态出现频率（回答「多吗」）")
    print("=" * 125)
    print()
    yrs = (E["date"].max() - E["date"].min()).days / 365.25
    total_stocks = len(data)
    # 每个交易日平均多少只处于该形态
    per_day = E.groupby("date").size()
    print(f"  总匹配事件: {len(E):,}")
    print(f"  时间跨度: {yrs:.1f} 年")
    print(f"  股票池: {total_stocks:,} 只")
    print()
    print(f"  平均每个交易日: 有 {per_day.mean():.1f} 只处于该形态")
    print(f"    （占全市场 {per_day.mean()/total_stocks*100:.1f}%）")
    print(f"  中位每个交易日: {per_day.median():.0f} 只")
    print(f"  最多一天: {per_day.max():.0f} 只  ({per_day.idxmax().date()})")
    print(f"  最少一天: {per_day.min():.0f} 只")
    print()
    print(f"  → 若一个交易日有 {per_day.mean():.0f} 只符合，说明这个形态")
    print(f"    「不算罕见」，全市场约 {per_day.mean()/total_stocks*100:.1f}% 的股票同时具备")
    print()

    # ---- 前瞻收益
    print("=" * 125)
    print("  二、之后怎么走（前瞻收益）")
    print("=" * 125)
    print()
    print(f"  {'持有期':<8}{'样本':>9}{'平均%':>9}{'中位%':>9}"
          f"{'胜率':>8}{'最好%':>9}{'最差%':>9}{'25分位':>9}{'75分位':>9}")
    print("  " + "-" * 82)
    for hz in (20, 60, 120, 250):
        v = E[f"f{hz}"].dropna()
        if len(v):
            print(f"  {hz}日{'':<4}{len(v):>9}{v.mean():>9.2f}{v.median():>9.2f}"
                  f"{(v>0).mean()*100:>7.1f}%{v.max():>9.1f}{v.min():>9.1f}"
                  f"{v.quantile(.25):>9.2f}{v.quantile(.75):>9.2f}")
    md = E["mdd60"].dropna()
    if len(md):
        print(f"  60日内浮亏{'':<1}{len(md):>9}{md.mean():>9.2f}{md.median():>9.2f}"
              f"{'':>8}{md.max():>9.1f}{md.min():>9.1f}")
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
    print(f"    {len(B):,} 样本  ({time.time()-t0:.0f}s)")
    print()
    print(f"  {'指标':<12}{'缩量筑底':>12}{'全市场随机':>12}{'超额':>10}")
    print("  " + "-" * 48)
    for hz in (20, 60, 120, 250):
        v1 = E[f"f{hz}"].dropna()
        v2 = B[f"f{hz}"].dropna()
        if len(v1) and len(v2):
            print(f"  {hz}日收益%{'':<3}{v1.mean():>12.2f}{v2.mean():>12.2f}"
                  f"{v1.mean()-v2.mean():>+10.2f}")
    for hz in (20, 60, 120, 250):
        v1 = E[f"f{hz}"].dropna()
        v2 = B[f"f{hz}"].dropna()
        if len(v1) and len(v2):
            print(f"  {hz}日胜率%{'':<3}{(v1>0).mean()*100:>12.1f}"
                  f"{(v2>0).mean()*100:>12.1f}"
                  f"{((v1>0).mean()-(v2>0).mean())*100:>+10.1f}")
    v1 = E["mdd60"].dropna()
    v2 = B["mdd60"].dropna()
    if len(v1) and len(v2):
        print(f"  {'60日浮亏%':<11}{v1.mean():>12.2f}{v2.mean():>12.2f}"
              f"{v1.mean()-v2.mean():>+10.2f}")
    print()

    # ---- 分层
    print("=" * 125)
    print("  三、哪些变量影响成功率（分层检验）")
    print("=" * 125)
    print()
    E2 = E.dropna(subset=["f60"]).copy()

    def layer(col, q, label, ascending=True):
        if len(E2) < 200 or E2[col].nunique() < 3:
            return
        try:
            E2["_q"] = pd.qcut(E2[col], q, labels=False, duplicates="drop")
        except Exception:
            return
        print(f"  【按 {label} 分层】")
        print(f"    {'档':<6}{'范围':<20}{'样本':>8}{'60日平均':>11}"
              f"{'60日胜率':>11}{'250日平均':>12}{'250日胜率':>11}")
        print("    " + "-" * 82)
        for g in sorted(E2["_q"].dropna().unique()):
            sub = E2[E2["_q"] == g]
            lo = sub[col].min()
            hi = sub[col].max()
            v60 = sub["f60"].dropna()
            v250 = sub["f250"].dropna()
            if len(v60) < 20:
                continue
            print(f"    Q{int(g)+1:<5}{f'{lo:.2f}~{hi:.2f}':<20}{len(sub):>8}"
                  f"{v60.mean():>11.2f}{(v60>0).mean()*100:>10.1f}%"
                  f"{(v250.mean() if len(v250) else float('nan')):>12.2f}"
                  f"{((v250>0).mean()*100 if len(v250) else float('nan')):>10.1f}%")
        print()
        E2.drop(columns=["_q"], inplace=True, errors="ignore")

    layer("dd250", 4, "回撤深度（dd250）")
    layer("vratio", 4, "量能比（v20/v60）")
    layer("vbratio", 4, "波动比（20/60）")
    layer("pos", 4, "位置分位")
    layer("dvu", 4, "下跌日/上涨日量能比")

    # ---- 分年度
    print("=" * 125)
    print("  四、分年度稳定性（60日收益）")
    print("=" * 125)
    print()
    E2["yr"] = E2["date"].dt.year
    print(f"  {'年份':<8}{'事件数':>9}{'60日平均':>11}{'60日胜率':>11}"
          f"{'250日平均':>12}{'250日胜率':>11}")
    print("  " + "-" * 62)
    for yr, g in E2.groupby("yr"):
        if len(g) < 30:
            continue
        v60 = g["f60"].dropna()
        v250 = g["f250"].dropna()
        print(f"  {yr:<8}{len(g):>9}{v60.mean():>11.2f}"
              f"{(v60>0).mean()*100:>10.1f}%"
              f"{(v250.mean() if len(v250) else float('nan')):>12.2f}"
              f"{((v250>0).mean()*100 if len(v250) else float('nan')):>10.1f}%")
    print()

    # ---- 输出
    E.to_csv(outdir / "washout_base_events.csv", index=False,
             encoding="utf-8-sig")
    B.to_csv(outdir / "washout_base_baseline.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/washout_base_events.csv  ({len(E):,} 事件)")
    print(f"[输出] {outdir}/washout_base_baseline.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
