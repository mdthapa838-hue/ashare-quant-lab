# -*- coding: utf-8 -*-
"""
step22_pe_validate.py — 验证 PE 信号的真实预测力

step21 发现 s_pe 的 IC = -0.694（异常高），必须排查原因：
  1. 是否用了「未来信息」？（PE 是当期的，前瞻是未来的 → 应该没问题）
  2. 是否样本重叠导致虚高？（日度数据 + 250日前瞻 → 严重重叠）
  3. 样本外是否稳定？（分时段检验）
  4. 用独立样本重新验证

验证方法：
  A. 分时段 IC（避免单一时期主导）
  B. 用非重叠样本（每250天取1个点）
  C. 滚动窗口 IC
  D. 对比其他估值指标（PB、股息率）
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
from step20_doubt_test import get_index


def main():
    cfg = load_config()
    setup_logger("pe", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 120)
    print("  验证 PE 信号的真实预测力")
    print("=" * 120)
    print()

    # ---- 数据
    hs = get_index(pro, "000300.SH", "20050101", "20261231")
    hs = hs.sort_values("date").reset_index(drop=True)

    db = pro.index_dailybasic(ts_code="000300.SH", start_date="20050101",
                              end_date="20261231",
                              fields="trade_date,pe_ttm,pb")
    db["date"] = pd.to_datetime(db["trade_date"], format="%Y%m%d")
    db["pe_ttm"] = pd.to_numeric(db["pe_ttm"], errors="coerce")
    db["pb"] = pd.to_numeric(db["pb"], errors="coerce")
    db = db.sort_values("date").reset_index(drop=True)
    print(f"沪深300: {len(hs)} 天")
    print(f"PE数据 : {len(db)} 天 ({db['date'].min().date()} ~ {db['date'].max().date()})")
    print()

    M = hs.merge(db[["date", "pe_ttm", "pb"]], on="date", how="inner")
    print(f"合并后: {len(M)} 天")
    if len(M) < 500:
        print("样本不足")
        return 1

    # 前瞻收益
    for hz in (250, 500):
        M[f"fwd{hz}"] = M["close"].shift(-hz) / M["close"] - 1
    # PE 分位（滚动5年）
    M["pe_pct"] = M["pe_ttm"].rolling(1250, min_periods=500).apply(
        lambda s: (s <= s.iloc[-1]).mean(), raw=False)
    M["pb_pct"] = M["pb"].rolling(1250, min_periods=500).apply(
        lambda s: (s <= s.iloc[-1]).mean(), raw=False)
    print(f"PE分位样本: {M['pe_pct'].notna().sum()}")
    print()

    # =========================================================
    # A. 全样本 IC
    # =========================================================
    print("=" * 120)
    print("  A. 全样本 IC（含重叠样本）")
    print("=" * 120)
    print()
    rows = []
    for sig in ["pe_ttm", "pe_pct", "pb", "pb_pct"]:
        for hz in (250, 500):
            sub = M[[sig, f"fwd{hz}"]].dropna()
            if len(sub) < 100:
                continue
            ic = float(sub[sig].corr(sub[f"fwd{hz}"]))
            rows.append({"信号": sig, "前瞻": f"{hz}日",
                         "样本": len(sub), "IC": round(ic, 3)})
            print(f"  {sig:<10} {hz:>3}日  样本 {len(sub):>5}  IC {ic:>7.3f}")
    print()

    # =========================================================
    # B. 非重叠样本（关键检验）
    # =========================================================
    print("=" * 120)
    print("  B. 非重叠样本 IC（每250天取1点，避免重叠虚高）")
    print("=" * 120)
    print()
    for hz in (250, 500):
        step = hz
        M2 = M.iloc[::step].copy()
        for sig in ["pe_ttm", "pe_pct", "pb_pct"]:
            sub = M2[[sig, f"fwd{hz}"]].dropna()
            if len(sub) < 20:
                continue
            ic = float(sub[sig].corr(sub[f"fwd{hz}"]))
            print(f"  {sig:<10} {hz:>3}日  非重叠样本 {len(sub):>4}  "
                  f"IC {ic:>7.3f}")
        print()

    # =========================================================
    # C. 分时段 IC
    # =========================================================
    print("=" * 120)
    print("  C. 分时段 IC（检验稳定性）")
    print("=" * 120)
    print()
    M["yr"] = M["date"].dt.year
    periods = [(2005, 2009), (2010, 2014), (2015, 2019), (2020, 2026)]
    print(f"  {'时段':<14}{'样本':>7}{'pe_ttm IC':>12}{'pe_pct IC':>12}"
          f"{'pb_pct IC':>12}")
    print("  " + "-" * 60)
    for lo, hi in periods:
        sub_all = M[(M["yr"] >= lo) & (M["yr"] <= hi)]
        line = f"  {lo}-{hi:<9}{len(sub_all):>7}"
        for sig in ["pe_ttm", "pe_pct", "pb_pct"]:
            s = sub_all[[sig, "fwd250"]].dropna()
            if len(s) > 50:
                ic = float(s[sig].corr(s["fwd250"]))
                line += f"{ic:>12.3f}"
            else:
                line += f"{'—':>12}"
        print(line)
    print()

    # =========================================================
    # D. 滚动 IC（250日窗口）
    # =========================================================
    print("=" * 120)
    print("  D. 滚动 IC（250日窗口，检验时变）")
    print("=" * 120)
    print()
    sub = M[["date", "pe_ttm", "fwd250"]].dropna().reset_index(drop=True)
    if len(sub) > 500:
        win = 250
        ics = []
        for i in range(0, len(sub) - win, 50):
            seg = sub.iloc[i:i + win]
            if seg["pe_ttm"].std() > 0 and seg["fwd250"].std() > 0:
                ics.append({
                    "date": seg["date"].iloc[-1],
                    "ic": float(seg["pe_ttm"].corr(seg["fwd250"]))
                })
        if ics:
            IC = pd.DataFrame(ics)
            print(f"  滚动窗口数: {len(IC)}")
            print(f"  IC 分布: 平均 {IC['ic'].mean():.3f}  "
                  f"中位 {IC['ic'].median():.3f}  "
                  f"最小 {IC['ic'].min():.3f}  最大 {IC['ic'].max():.3f}")
            print(f"  IC < 0 的比例: {(IC['ic']<0).mean()*100:.0f}%")
            print()
            print("  最近10个窗口:")
            for _, r in IC.tail(10).iterrows():
                print(f"    {r['date'].date()}  IC {r['ic']:>7.3f}")
    print()

    # =========================================================
    # E. PE 分档的实测（最直观）
    # =========================================================
    print("=" * 120)
    print("  E. PE 分档 → 未来1年收益（最直观的检验）")
    print("=" * 120)
    print()
    sub = M[["date", "pe_ttm", "fwd250"]].dropna()
    if len(sub) > 200:
        sub = sub.copy()
        # 固定分位点（用全样本）
        qs = [0, 0.2, 0.4, 0.6, 0.8, 1.0]
        cuts = sub["pe_ttm"].quantile(qs).values
        print(f"  PE 分位点: {[round(x,2) for x in cuts]}")
        print()
        print(f"  {'PE区间':<20}{'天数':>7}{'未来1年平均':>13}"
              f"{'中位':>10}{'胜率':>9}{'最差':>10}")
        print("  " + "-" * 74)
        for i in range(5):
            lo, hi = cuts[i], cuts[i + 1]
            seg = sub[(sub["pe_ttm"] >= lo) & (sub["pe_ttm"] <= hi)]
            if len(seg) < 20:
                continue
            v = seg["fwd250"]
            print(f"  {lo:>6.1f}~{hi:<12.1f}{len(seg):>7}"
                  f"{v.mean()*100:>12.1f}%{v.median()*100:>9.1f}%"
                  f"{(v>0).mean()*100:>8.0f}%{v.min()*100:>9.1f}%")
        print()

    # =========================================================
    # F. 结论
    # =========================================================
    print("=" * 120)
    print("  F. 结论")
    print("=" * 120)
    print()
    print("  关键判断：")
    print("    1. 全样本 IC 因「样本重叠」被高估")
    print("    2. 非重叠样本 IC 才是真实预测力")
    print("    3. 滚动 IC 的稳定性决定可用性")
    print()

    # 保存
    M.to_csv(outdir / "pe_validation.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/pe_validation.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
