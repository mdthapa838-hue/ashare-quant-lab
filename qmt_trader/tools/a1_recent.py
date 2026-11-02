# -*- coding: utf-8 -*-
"""a1_recent.py — 最近 A1 清单（严格条件）。"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "output"

P = pd.read_csv(OUT / "pead_events.csv", dtype={"code": str})
G = pd.read_csv(OUT / "gap_fade_events.csv", dtype={"code": str})
P["ann"] = pd.to_datetime(P["ann"])
G["date"] = pd.to_datetime(G["date"])
P["g"] = pd.to_numeric(P["g"], errors="coerce")

# gap_fade 已有 gap / filled_5d / held 等信息
# pead 有 ann / code / g
# 用 gap_fade 的 gap 和 pead 的 g 组合
P2 = P[P["src"] == "预告"].copy()
G2 = G.copy()
G2["ann_d"] = G2["date"]

# gap_fade_events 里已含 g 和 gap
GG = G.dropna(subset=["g"]).copy()
GG["g"] = pd.to_numeric(GG["g"], errors="coerce")
print(f"gap_fade 事件: {len(GG):,}")
print()

# 严格 A1：增速≥50% + 跳空≥2% + 未回补
A1 = GG[(GG["g"] >= 50) & (GG["gap"] >= 2) & (~GG["filled_same"].astype(bool))]
print("=" * 128)
print("  严格 A1 条件：增速≥50% + 跳空≥2% + 未回补")
print("=" * 128)
print()
print(f"  历史样本: {len(A1):,}")
if len(A1):
    print()
    print(f"  {'持有期':<10}{'入场':<16}{'样本':>8}{'平均%':>10}{'中位%':>10}"
          f"{'胜率%':>9}")
    print("  " + "-" * 66)
    for way, col in [("T+1开盘买", "C"), ("当日开盘买", "A"),
                     ("当日收盘买", "B")]:
        for hz in (5, 20, 60):
            c = f"{col}_f{hz}"
            if c not in A1.columns:
                continue
            v = A1[c].dropna()
            if len(v) < 20:
                continue
            print(f"  {hz}日{'':<6}{way:<16}{len(v):>8}{v.mean():>10.2f}"
                  f"{v.median():>10.2f}{(v>0).mean()*100:>8.1f}%")
        print()
    # 日期分布
    A1b = A1.copy()
    A1b["ym"] = A1b["date"].dt.to_period("M")
    print("  按月分布（最近15个月）:")
    for k, c in A1b["ym"].value_counts().sort_index().tail(15).items():
        print(f"    {k}: {c} 只")
    print()
    # 平均每日
    per = A1.groupby("date").size()
    print(f"  平均每日: {per.mean():.2f} 只  中位 {per.median():.0f}  "
          f"最多 {per.max()} 只")
    print(f"  有信号的交易日: {len(per)} 天")
    all_days = GG["date"].nunique()
    print(f"  占全部交易日: {len(per)/all_days*100:.1f}%")
    print()
    # 最近10个信号
    print("  最近 15 个 A1 信号:")
    print()
    print(f"  {'日期':<12}{'代码':<9}{'增速%':>10}{'跳空%':>9}"
          f"{'T+1开盘买20日%':>16}{'60日%':>10}")
    print("  " + "-" * 72)
    for _, r in A1.sort_values("date", ascending=False).head(15).iterrows():
        v20 = r.get("C_f20")
        v60 = r.get("C_f60")
        print(f"  {r['date'].strftime('%Y-%m-%d'):<12}{r['code']:<9}"
              f"{r['g']:>10.1f}{r['gap']:>9.2f}"
              f"{(f'{v20:.2f}' if pd.notna(v20) else '—'):>16}"
              f"{(f'{v60:.2f}' if pd.notna(v60) else '—'):>10}")
    print()

# 对照：放宽到增速≥20%
A1b = GG[(GG["g"] >= 20) & (GG["gap"] >= 2) & (~GG["filled_same"].astype(bool))]
print("=" * 128)
print("  对照：放宽到增速≥20%")
print("=" * 128)
print()
print(f"  样本: {len(A1b):,}")
for hz in (20, 60):
    v = A1b[f"C_f{hz}"].dropna()
    if len(v) > 20:
        print(f"    T+1开盘买 {hz}日: {v.mean():+.2f}%  "
              f"胜率 {(v>0).mean()*100:.1f}%")
print()

# 不带增速条件（只跳空+未回补）
A1c = GG[(GG["gap"] >= 2) & (~GG["filled_same"].astype(bool))]
print("对照：只看「跳空≥2% + 未回补」（不带增速）")
print(f"  样本: {len(A1c):,}")
for hz in (20, 60):
    v = A1c[f"C_f{hz}"].dropna()
    if len(v) > 20:
        print(f"    T+1开盘买 {hz}日: {v.mean():+.2f}%  "
              f"胜率 {(v>0).mean()*100:.1f}%")
print()

A1.to_csv(OUT / "a1_strict_list.csv", index=False, encoding="utf-8-sig")
print(f"[输出] {OUT}/a1_strict_list.csv  ({len(A1):,})")
