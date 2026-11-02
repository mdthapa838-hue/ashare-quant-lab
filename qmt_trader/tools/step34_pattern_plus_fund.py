# -*- coding: utf-8 -*-
"""
step34_pattern_plus_fund.py — 「缩量筑底 + 业绩好」组合回测

用户的假设：形态 + 业绩预期好 = 更容易涨？

严谨设计：
  ① 复用 step33 的 25,125 个「缩量筑底」事件
  ② 为每个事件附加「当时可得」的财务数据（PIT：ann_date <= 事件日）
  ③ 按业绩分组，对比前瞻收益
  ④ 关键对照：业绩好的形态 vs 业绩差的形态 vs 业绩好的非形态

PIT 要求（这是重点）：
  · 财务数据必须在公告日之后才可用
  · 同一报告期取最早公告（避免用修订后数据）
  · 事件日 t 只能看到 ann_date <= t 的最近一期
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
sys.path.insert(0, str(HERE / "tools"))

from core.config import load_config
from core.logger import setup_logger

LOOKBACK = 130


def load_fund(cache: Path, codes: list) -> dict:
    """
    载入财务数据（PIT 就绪）。

    返回 {code6: DataFrame(ann_date, end_date, <metrics>)}
    """
    fund_dir = cache / "fundamental"
    out = {}
    want = ["netprofit_yoy", "dt_netprofit_yoy", "or_yoy", "op_yoy",
            "roe", "roe_dt", "grossprofit_margin", "netprofit_margin",
            "ocf_to_or", "debt_to_assets", "eps", "bps"]
    n = 0
    for c6 in codes:
        p = fund_dir / f"{c6}.csv"
        if not p.exists():
            continue
        try:
            d = pd.read_csv(p, low_memory=False)
        except Exception:
            continue
        if len(d) == 0 or "ann_date" not in d.columns:
            continue
        d = d.loc[:, ~d.columns.duplicated()].copy()
        for c in ("ann_date", "end_date"):
            d[c] = pd.to_datetime(d[c].astype(str), format="%Y%m%d",
                                  errors="coerce")
        d = d.dropna(subset=["ann_date", "end_date"])
        if len(d) == 0:
            continue
        keep = [c for c in want if c in d.columns]
        d = d[["ann_date", "end_date"] + keep].copy()
        for c in keep:
            s = d[c]
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
                d[c] = s
            d[c] = pd.to_numeric(s, errors="coerce")
        # 同一报告期取最早公告（PIT）
        d = d.sort_values(["end_date", "ann_date"])
        d = d.drop_duplicates(subset=["end_date"], keep="first")
        d = d.sort_values("ann_date").reset_index(drop=True)
        out[c6] = d
        n += 1
    return out


def attach_pit(events: pd.DataFrame, fund: dict) -> pd.DataFrame:
    """
    为每个事件附加「当时可得」的财务数据。

    对事件日 t，取 ann_date <= t 的最近一期。
    """
    rows = []
    # 按 code 分组加速
    by_code = dict(tuple(events.groupby("code")))
    miss = 0
    for c6, grp in by_code.items():
        f = fund.get(c6)
        if f is None or len(f) == 0:
            miss += len(grp)
            continue
        ann = f["ann_date"].values
        for _, r in grp.iterrows():
            t = np.datetime64(r["date"])
            # 找 ann_date <= t 的最后一个
            idx = np.searchsorted(ann, t, side="right") - 1
            if idx < 0:
                miss += 1
                continue
            fr = f.iloc[idx]
            rec = r.to_dict()
            for c in f.columns:
                if c in ("ann_date", "end_date"):
                    continue
                rec[f"f_{c}"] = fr.get(c)
            rec["f_ann_date"] = fr["ann_date"]
            rec["f_end_date"] = fr["end_date"]
            # 数据滞后天数
            rec["lag_days"] = (r["date"] - fr["ann_date"]).days
            rows.append(rec)
    print(f"    PIT 匹配: {len(rows):,}   无财务数据: {miss:,}")
    return pd.DataFrame(rows)


def main():
    cfg = load_config()
    setup_logger("pf", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    print("=" * 128)
    print("  「缩量筑底 + 业绩好」组合回测")
    print("=" * 128)
    print()
    print("  假设检验：形态 + 业绩预期好 = 更容易涨？")
    print()

    # ---- 载入事件
    ep = outdir / "washout_base_events.csv"
    if not ep.exists():
        print("缺少 washout_base_events.csv，请先运行 step33")
        return 1
    E = pd.read_csv(ep, dtype={"code": str})
    E["date"] = pd.to_datetime(E["date"])
    print(f"[1] 缩量筑底事件: {len(E):,}  ({E['date'].min().date()} ~ {E['date'].max().date()})")
    print()

    # ---- 载入财务
    print("[2] 载入财务数据（PIT）...")
    t0 = time.time()
    codes = sorted(E["code"].unique())
    fund = load_fund(cache, codes)
    print(f"    可用财务: {len(fund):,}/{len(codes):,} 只  ({time.time()-t0:.0f}s)")
    print()

    # ---- PIT 附加
    print("[3] 附加财务数据（按公告日生效）...")
    t0 = time.time()
    FE = attach_pit(E, fund)
    print(f"    完成  ({time.time()-t0:.0f}s)")
    print()
    if len(FE) == 0:
        print("无匹配")
        return 1

    # 数据滞后检查
    lag = FE["lag_days"].dropna()
    print(f"  公告滞后: 中位 {lag.median():.0f} 天   90分位 {lag.quantile(.9):.0f} 天")
    print()

    # ---- 分组
    print("=" * 128)
    print("  一、按「净利润增速」分组（PIT）")
    print("=" * 128)
    print()
    FE2 = FE.dropna(subset=["f_netprofit_yoy", "f60"]).copy()
    print(f"  有效样本: {len(FE2):,}")
    print()

    groups = [
        ("业绩负增长", FE2["f_netprofit_yoy"] < 0),
        ("0~20%", (FE2["f_netprofit_yoy"] >= 0) & (FE2["f_netprofit_yoy"] < 20)),
        ("20~50%", (FE2["f_netprofit_yoy"] >= 20) & (FE2["f_netprofit_yoy"] < 50)),
        ("50~100%", (FE2["f_netprofit_yoy"] >= 50) & (FE2["f_netprofit_yoy"] < 100)),
        ("100~300%", (FE2["f_netprofit_yoy"] >= 100) & (FE2["f_netprofit_yoy"] < 300)),
        ("300%以上", FE2["f_netprofit_yoy"] >= 300),
    ]
    print(f"  {'业绩档':<12}{'样本':>8}{'20日%':>9}{'60日%':>9}{'120日%':>9}"
          f"{'250日%':>9}{'20日胜率':>10}{'60日胜率':>10}"
          f"{'250日胜率':>10}{'60日浮亏':>10}")
    print("  " + "-" * 104)
    for lab, mask in groups:
        sub = FE2[mask]
        if len(sub) < 30:
            continue
        line = f"  {lab:<12}{len(sub):>8}"
        for hz in (20, 60, 120, 250):
            v = sub[f"f{hz}"].dropna()
            line += f"{v.mean():>9.2f}" if len(v) else f"{'—':>9}"
        for hz in (20, 60, 250):
            v = sub[f"f{hz}"].dropna()
            line += f"{(v>0).mean()*100:>9.1f}%" if len(v) else f"{'—':>10}"
        md = sub["mdd60"].dropna()
        line += f"{md.mean():>10.2f}" if len(md) else f"{'—':>10}"
        print(line)
    print()

    # ---- 对照：业绩好的非形态
    print("=" * 128)
    print("  二、关键对照：是「业绩」起作用还是「形态」起作用？")
    print("=" * 128)
    print()
    print("  三组对比：")
    print("    A. 形态 + 业绩好   （用户假设的组合）")
    print("    B. 形态 + 业绩差")
    print("    C. 无形态 + 业绩好 （只有业绩）")
    print()

    good = FE2["f_netprofit_yoy"] >= 50
    bad = FE2["f_netprofit_yoy"] < 0
    A = FE2[good]
    B = FE2[bad]
    print(f"  {'组':<22}{'样本':>8}{'20日%':>9}{'60日%':>9}{'120日%':>9}"
          f"{'250日%':>9}{'60日胜率':>10}{'250日胜率':>10}{'60日浮亏':>10}")
    print("  " + "-" * 96)
    for lab, sub in [("A 形态+业绩好(≥50%)", A), ("B 形态+业绩差(<0)", B)]:
        if len(sub) < 30:
            print(f"  {lab:<22}{len(sub):>8}  样本不足")
            continue
        line = f"  {lab:<22}{len(sub):>8}"
        for hz in (20, 60, 120, 250):
            v = sub[f"f{hz}"].dropna()
            line += f"{v.mean():>9.2f}" if len(v) else f"{'—':>9}"
        for hz in (60, 250):
            v = sub[f"f{hz}"].dropna()
            line += f"{(v>0).mean()*100:>9.1f}%" if len(v) else f"{'—':>10}"
        md = sub["mdd60"].dropna()
        line += f"{md.mean():>10.2f}" if len(md) else f"{'—':>10}"
        print(line)
    print()

    # 差异
    if len(A) >= 30 and len(B) >= 30:
        print("  A - B 差异：")
        for hz in (20, 60, 120, 250):
            va = A[f"f{hz}"].dropna()
            vb = B[f"f{hz}"].dropna()
            if len(va) and len(vb):
                print(f"    {hz:>3}日: {va.mean():>7.2f}% vs {vb.mean():>7.2f}%  "
                      f"差 {va.mean()-vb.mean():>+6.2f}pct   "
                      f"胜率 {(va>0).mean()*100:.1f}% vs {(vb>0).mean()*100:.1f}%")
        print()

    # ---- 多因子组合
    print("=" * 128)
    print("  三、叠加更多业绩条件")
    print("=" * 128)
    print()

    combos = [
        ("净利增速≥50%", FE2["f_netprofit_yoy"] >= 50),
        ("净利增速≥50% + ROE≥5%",
         (FE2["f_netprofit_yoy"] >= 50) & (FE2["f_roe"] >= 5)),
        ("净利增速≥50% + 毛利≥25%",
         (FE2["f_netprofit_yoy"] >= 50) & (FE2["f_grossprofit_margin"] >= 25)),
        ("净利增速≥50% + 低负债<50%",
         (FE2["f_netprofit_yoy"] >= 50) & (FE2["f_debt_to_assets"] < 50)),
        ("净利≥50% + ROE≥5% + 毛利≥25%",
         (FE2["f_netprofit_yoy"] >= 50) & (FE2["f_roe"] >= 5) &
         (FE2["f_grossprofit_margin"] >= 25)),
        ("扣非增速≥50%", FE2["f_dt_netprofit_yoy"] >= 50),
        ("营收增速≥20% + 净利≥50%",
         (FE2["f_or_yoy"] >= 20) & (FE2["f_netprofit_yoy"] >= 50)),
        ("营业利润增速≥50%", FE2["f_op_yoy"] >= 50),
    ]
    print(f"  {'组合条件':<34}{'样本':>8}{'20日%':>9}{'60日%':>9}{'120日%':>9}"
          f"{'250日%':>9}{'60日胜率':>10}{'250日胜率':>10}")
    print("  " + "-" * 106)
    rows = []
    for lab, mask in combos:
        sub = FE2[mask]
        if len(sub) < 30:
            print(f"  {lab:<34}{len(sub):>8}  样本不足")
            continue
        line = f"  {lab:<34}{len(sub):>8}"
        stat = {"组合": lab, "样本": len(sub)}
        for hz in (20, 60, 120, 250):
            v = sub[f"f{hz}"].dropna()
            line += f"{v.mean():>9.2f}" if len(v) else f"{'—':>9}"
            stat[f"{hz}日%"] = round(v.mean(), 2) if len(v) else None
        for hz in (60, 250):
            v = sub[f"f{hz}"].dropna()
            line += f"{(v>0).mean()*100:>9.1f}%" if len(v) else f"{'—':>10}"
            stat[f"{hz}日胜率%"] = round((v > 0).mean() * 100, 1) if len(v) else None
        md = sub["mdd60"].dropna()
        stat["60日浮亏%"] = round(md.mean(), 2) if len(md) else None
        rows.append(stat)
        print(line)
    print()

    # ---- 与「全部形态」对比
    print("=" * 128)
    print("  四、与「全部形态」（不加业绩条件）对比")
    print("=" * 128)
    print()
    allv = FE2
    print(f"  {'组':<30}{'样本':>8}{'60日%':>10}{'250日%':>10}"
          f"{'60日胜率':>11}{'250日胜率':>11}")
    print("  " + "-" * 80)
    line = f"  {'全部形态（基准）':<30}{len(allv):>8}"
    for hz in (60, 250):
        v = allv[f"f{hz}"].dropna()
        line += f"{v.mean():>10.2f}"
    for hz in (60, 250):
        v = allv[f"f{hz}"].dropna()
        line += f"{(v>0).mean()*100:>10.1f}%"
    print(line)
    if len(A) >= 30:
        line = f"  {'形态+业绩≥50%':<30}{len(A):>8}"
        for hz in (60, 250):
            v = A[f"f{hz}"].dropna()
            line += f"{v.mean():>10.2f}"
        for hz in (60, 250):
            v = A[f"f{hz}"].dropna()
            line += f"{(v>0).mean()*100:>10.1f}%"
        print(line)
        va = A["f60"].dropna()
        vb = allv["f60"].dropna()
        v250a = A["f250"].dropna()
        v250b = allv["f250"].dropna()
        print()
        print(f"  加业绩条件的增量：")
        print(f"    60日:  {va.mean():>+7.2f}% vs {vb.mean():>+7.2f}%  "
              f"({va.mean()-vb.mean():>+6.2f}pct)")
        print(f"    250日: {v250a.mean():>+7.2f}% vs {v250b.mean():>+7.2f}%  "
              f"({v250a.mean()-v250b.mean():>+6.2f}pct)")
        print(f"    60日胜率: {(va>0).mean()*100:.1f}% vs {(vb>0).mean()*100:.1f}%")
    print()

    # ---- 分年度
    print("=" * 128)
    print("  五、分年度稳定性（形态+业绩≥50%）")
    print("=" * 128)
    print()
    A2 = A.copy()
    A2["yr"] = A2["date"].dt.year
    print(f"  {'年份':<8}{'样本':>8}{'60日%':>10}{'60日胜率':>11}"
          f"{'250日%':>10}{'250日胜率':>11}")
    print("  " + "-" * 60)
    for yr, g in A2.groupby("yr"):
        if len(g) < 20:
            continue
        v60 = g["f60"].dropna()
        v250 = g["f250"].dropna()
        print(f"  {yr:<8}{len(g):>8}{v60.mean():>10.2f}"
              f"{(v60>0).mean()*100:>10.1f}%"
              f"{(v250.mean() if len(v250) else float('nan')):>10.2f}"
              f"{((v250>0).mean()*100 if len(v250) else float('nan')):>10.1f}%")
    print()

    # ---- 输出
    FE.to_csv(outdir / "pattern_fund_events.csv", index=False,
              encoding="utf-8-sig")
    if rows:
        pd.DataFrame(rows).to_csv(outdir / "pattern_fund_combos.csv",
                                  index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/pattern_fund_events.csv  ({len(FE):,} 事件)")
    print(f"[输出] {outdir}/pattern_fund_combos.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
