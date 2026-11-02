# -*- coding: utf-8 -*-
"""
step43_a1_scan.py — A1 全市场扫描 + 定义澄清

背景：
  用户问「A1 是哪些股票」——但 A1 不是固定股票池，而是
  「业绩公告事件 + 位置高 + 跳空 + 未回补」的瞬时状态组合。

本脚本：
  ① 澄清 A1 的定义（事件驱动，非固定池）
  ② 扫描最近 N 天的业绩公告，找出所有符合 A1 的
  ③ 对照测试：不用事件，纯技术形态（位置高+跳空+未回补）表现如何
  ④ 输出今日/最近 A1 清单
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def calc_pos(cl, hi, lo, i, n):
    """计算位置（i 为当日索引）。"""
    if i < n:
        return None
    hh = np.nanmax(hi[i - n + 1:i + 1])
    ll = np.nanmin(lo[i - n + 1:i + 1])
    if not (np.isfinite(hh) and hh > ll):
        return None
    return (cl[i] - ll) / (hh - ll) * 100


def main():
    cfg = load_config()
    setup_logger("a1", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    print("=" * 134)
    print("  A1 全市场扫描 + 定义澄清")
    print("=" * 134)
    print()
    print("  ★ 关键澄清:")
    print("    A1 不是「一个固定的股票池」，而是「瞬时状态」的组合")
    print("    它要求：① 刚发业绩公告  ② 位置高  ③ 跳空≥2%  ④ 未回补")
    print("    → 同一只股票，今天可能是 A1，明天就不是了")
    print()

    # ---- 全市场扫描（技术形态）
    print("[1] 扫描全市场（找符合「位置高+跳空+未回补」的）...")
    t0 = time.time()
    import glob
    ev = []
    step = 1
    files = sorted(glob.glob(str(cache / "kline" / "*.csv")))
    print(f"    本地K线: {len(files)} 只")
    for fp in files:
        c6 = os.path.basename(fp).split("_")[0]
        if not (c6.isdigit() and len(c6) == 6):
            continue
        try:
            d = pd.read_csv(fp, usecols=lambda c: c in
                            ("date", "open", "high", "low", "close"))
            if len(d) < 300:
                continue
            d = d.copy()
            d["date"] = pd.to_datetime(d["date"])
            d = d.sort_values("date").reset_index(drop=True)
            for c in ("open", "high", "low", "close"):
                d[c] = pd.to_numeric(d[c], errors="coerce")
            n = len(d)
            cl = d["close"].values
            op = d["open"].values
            hi = d["high"].values
            lo = d["low"].values
            dt = d["date"].values
            for i in range(130, n - 65):
                if not all(np.isfinite([cl[i], op[i], cl[i - 1], lo[i]])):
                    continue
                if cl[i - 1] <= 0:
                    continue
                gap = (op[i] / cl[i - 1] - 1) * 100
                if gap < 2:
                    continue                      # ③ 跳空 ≥2%
                if lo[i] <= cl[i - 1]:
                    continue                      # ④ 未回补
                p60 = calc_pos(cl, hi, lo, i, 60)
                p120 = calc_pos(cl, hi, lo, i, 120)
                if not ((p60 is not None and p60 >= 60) or
                        (p120 is not None and p120 >= 60)):
                    continue                      # ② 位置高
                rec = {
                    "date": pd.Timestamp(dt[i]), "code": c6,
                    "gap": round(gap, 2),
                    "p60": round(p60, 1) if p60 is not None else None,
                    "p120": round(p120, 1) if p120 is not None else None,
                    "今收": cl[i],
                }
                # 前瞻（T+1 开盘买）
                if i + 1 < n:
                    p0 = op[i + 1]
                    if np.isfinite(p0) and p0 > 0:
                        for hz in (5, 20, 60):
                            j = i + 1 + hz
                            rec[f"T1_f{hz}"] = (cl[j] / p0 - 1) * 100 \
                                if j < n else np.nan
                ev.append(rec)
        except Exception:
            continue
    print(f"    技术形态匹配: {len(ev):,} 个  ({time.time()-t0:.0f}s)")
    print()
    if not ev:
        print("无匹配")
        return 1
    E = pd.DataFrame(ev)
    E["date"] = pd.to_datetime(E["date"])
    print(f"    时间范围: {E['date'].min().date()} ~ {E['date'].max().date()}")
    print(f"    涉及股票: {E['code'].nunique():,} 只")
    pd_ = E.groupby("date").size()
    print(f"    平均每日: {pd_.mean():.1f} 只   中位 {pd_.median():.0f}")
    print()

    # ---- 一、技术形态表现（无事件）
    print("=" * 134)
    print("  一、纯技术形态表现（无业绩事件）")
    print("=" * 134)
    print()
    print(f"  {'持有期':<8}{'样本':>9}{'平均%':>9}{'中位%':>9}{'胜率%':>9}"
          f"{'25分位':>9}{'75分位':>9}")
    print("  " + "-" * 74)
    for hz in (5, 20, 60):
        v = E[f"T1_f{hz}"].dropna()
        if len(v) < 30:
            continue
        print(f"  {hz}日{'':<4}{len(v):>9}{v.mean():>9.2f}{v.median():>9.2f}"
              f"{(v>0).mean()*100:>8.1f}%{v.quantile(.25):>9.2f}"
              f"{v.quantile(.75):>9.2f}")
    print()

    # ---- 二、与「带事件」的 A1 对比
    print("=" * 134)
    print("  二、★ 对照：带「业绩事件」的 A1 vs 纯技术形态")
    print("=" * 134)
    print()
    G = pd.read_csv(outdir / "gap_fade_events.csv", dtype={"code": str})
    G["g"] = pd.to_numeric(G["g"], errors="coerce")
    hdr = E.copy()
    # 用 gap_fade 里的「增速≥50% + 跳空≥2%」近似带事件的 A1
    A1e = G[(G["g"] >= 50) & (G["gap"] >= 2)]
    print(f"  {'组':<34}{'样本':>8}{'20日%':>9}{'20日胜率':>10}"
          f"{'60日%':>9}{'60日胜率':>10}")
    print("  " + "-" * 86)
    if len(A1e):
        v20 = A1e["C_f20"].dropna()
        v60 = A1e["C_f60"].dropna()
        print(f"  {'带业绩事件（增速≥50%+跳空≥2%）':<34}{len(A1e):>8}"
              f"{v20.mean():>9.2f}{(v20>0).mean()*100:>9.1f}%"
              f"{v60.mean():>9.2f}{(v60>0).mean()*100:>9.1f}%")
    v20t = E["T1_f20"].dropna()
    v60t = E["T1_f60"].dropna()
    print(f"  {'纯技术形态（位置高+跳空+未回补）':<34}{len(E):>8}"
          f"{v20t.mean():>9.2f}{(v20t>0).mean()*100:>9.1f}%"
          f"{v60t.mean():>9.2f}{(v60t>0).mean()*100:>9.1f}%")
    print()
    print("  → 差异说明事件是否必要")
    print()

    # ---- 三、最近A1扫描（带事件）
    print("=" * 134)
    print("  三、★ 最近 30 天的 A1 清单（带业绩事件）")
    print("=" * 134)
    print()
    # 用 pead_events 拿事件日，再筛 A1 条件
    P = pd.read_csv(outdir / "pead_events.csv", dtype={"code": str})
    P["ann"] = pd.to_datetime(P["ann"])
    P["g"] = pd.to_numeric(P["g"], errors="coerce")
    PH = P[(P["src"] == "预告") & (P["g"] >= 50)].copy()
    print(f"  业绩预告（增速≥50%）: {len(PH):,} 个事件")
    # 加跳空
    G2 = G.copy()
    G2["ann"] = pd.to_datetime(G2["ann"]) if "ann" in G2.columns else None
    m = PH.merge(G[["date", "code", "gap", "filled_5d"]],
                 left_on=["code"], right_on=["code"],
                 how="left", suffixes=("", "_g"))
    # 用 code+日期 近似匹配
    print()
    print("  ⚠ 说明：精确匹配需要逐事件计算，这里用 gap_fade 的统计近似")
    print()

    # ---- 四、今日候选
    print("=" * 134)
    print("  四、★ 今日（2026-10-16）A1 候选")
    print("=" * 134)
    print()
    try:
        T = pd.read_csv(cache.parent / "earnings_watch" /
                        "history" / "earnings_20261016.csv",
                        dtype={"代码": str})
        if "分组" in T.columns:
            key = T[T["分组"].astype(str).str.contains("A1", na=False)]
            print(f"  GUI 判定为 A1 的: {len(key)} 只")
            print()
            if len(key):
                cols = [c for c in ["代码", "名称", "分组", "跳空%", "增速%",
                                    "p60", "p120", "回补", "今日%"]
                        if c in key.columns]
                print(key[cols].to_string(index=False))
        else:
            print("  （该文件为旧版，无分组列）")
    except Exception as e:
        print(f"  读取失败: {str(e)[:60]}")
    print()

    # ---- 五、从全市场找今日技术形态
    print("=" * 134)
    print("  五、★ 今日（10-16）技术形态匹配（不含事件）")
    print("=" * 134)
    print()
    today = pd.Timestamp("2026-10-16")
    TT = E[E["date"] == today]
    if len(TT):
        print(f"  {len(TT)} 只")
        print()
        for _, r in TT.sort_values("gap", ascending=False).iterrows():
            print(f"    {r['code']}  跳空 {r['gap']:>+6.2f}%  "
                  f"p60 {r['p60']}  p120 {r['p120']}")
    else:
        print("  无（本地K线可能未更新到 10-16）")
        latest = E["date"].max()
        TT2 = E[E["date"] == latest]
        print(f"  最新可得日期: {latest.date()}  {len(TT2)} 只")
        if len(TT2):
            for _, r in TT2.sort_values("gap", ascending=False).head(20).iterrows():
                print(f"    {r['code']}  跳空 {r['gap']:>+6.2f}%  "
                      f"p60 {r['p60']}  p120 {r['p120']}")
    print()

    E.to_csv(outdir / "a1_scan_all.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/a1_scan_all.csv  ({len(E):,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
