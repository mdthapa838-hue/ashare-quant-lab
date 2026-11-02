# -*- coding: utf-8 -*-
"""
step40_validate_v2.py — 验证 v2 分组的有效性

v2 分组规则：
  A1 高增 + p60>=60 或 p120>=60 + 无措辞瑕疵
  A2 高增 + p20<20
  B  高增 + 其他
  D  中增（20~50%）
  E  低增（<20%）

用 position_effect.csv（6,408 事件）验证。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
OUT = HERE / "output"


def main():
    Q = pd.read_csv(OUT / "position_effect.csv", dtype={"code": str})
    Q["pos"] = pd.to_numeric(Q["pos"], errors="coerce")
    Q = Q.dropna(subset=["pos", "f20", "f60"])
    print("=" * 128)
    print("  v2 分组有效性验证（6,408 个业绩公告事件）")
    print("=" * 128)
    print()
    print(f"  样本: {len(Q):,}")
    print()
    print("  说明：position_effect 只有单一 pos（130日），")
    print("        这里用 pos 的「低/中/高」近似 p20/p60/p120 的分档")
    print()

    # 用 pos 分档模拟
    Q = Q.copy()
    Q["pos_band"] = pd.cut(Q["pos"], [-1, 20, 60, 200],
                           labels=["低位(<20)", "中位(20-60)", "高位(>60)"])
    G = Q.dropna(subset=["g"]).copy()
    G["g_band"] = pd.cut(G["g"], [-1e9, 20, 50, 1e12],
                         labels=["低增(<20)", "中增(20-50)", "高增(>50)"])

    # v2 分组模拟
    def v2(r):
        if r["g_band"] == "低增(<20)":
            return "E 低增"
        if r["g_band"] == "中增(20-50)":
            return "D 中增"
        # 高增
        if r["pos_band"] == "高位(>60)":
            return "A1 高增+高位"
        if r["pos_band"] == "低位(<20)":
            return "A2 高增+低位"
        return "B 高增+中位"
    G["v2组"] = G.apply(v2, axis=1)

    print("=" * 128)
    print("  一、v2 分组表现")
    print("=" * 128)
    print()
    print(f"  {'分组':<20}{'样本':>8}{'20日%':>9}{'60日%':>9}{'120日%':>9}"
          f"{'250日%':>9}{'20日胜率':>10}{'60日胜率':>10}{'120日胜率':>10}")
    print("  " + "-" * 104)
    order = ["A1 高增+高位", "A2 高增+低位", "B 高增+中位",
             "D 中增", "E 低增"]
    for lab in order:
        s = G[G["v2组"] == lab]
        if len(s) < 30:
            print(f"  {lab:<20}{len(s):>8}  样本不足")
            continue
        line = f"  {lab:<20}{len(s):>8}"
        for hz in ("f20", "f60", "f120", "f250"):
            v = s[hz].dropna()
            line += f"{v.mean():>9.2f}" if len(v) else f"{'—':>9}"
        for hz in ("f20", "f60", "f120"):
            v = s[hz].dropna()
            line += f"{(v>0).mean()*100:>9.1f}%" if len(v) else f"{'—':>10}"
        print(line)
    print()

    # ---- A1 vs A2 关键对比
    print("=" * 128)
    print("  二、A1 vs A2（最关键对比）")
    print("=" * 128)
    print()
    A1 = G[G["v2组"] == "A1 高增+高位"]
    A2 = G[G["v2组"] == "A2 高增+低位"]
    cols = [("f20", "20日"), ("f60", "60日"), ("f120", "120日"),
            ("f250", "250日")]
    print(f"  {'组':<18}{'样本':>8}" +
          "".join(f"{l:>10}" for _, l in cols) +
          "".join(f"{l+'胜率':>12}" for _, l in cols[:2]))
    print("  " + "-" * 92)
    for lab, s in [("A1 高增+高位", A1), ("A2 高增+低位", A2)]:
        line = f"  {lab:<18}{len(s):>8}"
        for c, _ in cols:
            v = s[c].dropna()
            line += f"{v.mean():>10.2f}" if len(v) else f"{'—':>10}"
        for c, _ in cols[:2]:
            v = s[c].dropna()
            line += f"{(v>0).mean()*100:>11.1f}%" if len(v) else f"{'—':>12}"
        print(line)
    print()
    if len(A1) > 30 and len(A2) > 30:
        print("  A1 − A2 差异:")
        for c, l in cols:
            v1, v2 = A1[c].dropna(), A2[c].dropna()
            if len(v1) and len(v2):
                d = v1.mean() - v2.mean()
                mark = "★" if abs(d) > 5 else ""
                print(f"    {l:>5}: {v1.mean():>+7.2f}% vs {v2.mean():>+7.2f}%  "
                      f"差 {d:>+7.2f}pct  {mark}")
        print()

    # ---- 措辞的影响（用可信度高的样本）
    print("=" * 128)
    print("  三、说明与局限")
    print("=" * 128)
    print()
    print("  ⚠ 本次验证的局限:")
    print("    1. position_effect.csv 只有单一 130 日位置，")
    print("       无法真实复现 v2 的「p20/p60/p120 三周期」逻辑")
    print("    2. 措辞预警（负面词）未纳入回测（原文未程序化标签）")
    print("    3. 样本期仅 1.7 年")
    print()
    print("  已验证有效的部分（来自 step39）:")
    print("    · 位置 vs 60日: Spearman +0.1745  p<0.0001")
    print("    · 位置 vs 120日: Spearman +0.2063  p<0.0001")
    print("    · D10（位置≥84.7%）60日 +16.64% / 胜率 64.2%")
    print("    · D1（位置≤6.9%）60日 −4.13% / 胜率 35.3%")
    print()
    print("  → v2 的 A1/A2 划分基于上述已验证规律，")
    print("    但「措辞预警」部分属于逻辑推断，尚无回测支持")

    # 保存
    G.to_csv(OUT / "v2_validation.csv", index=False, encoding="utf-8-sig")
    print()
    print(f"[输出] {OUT}/v2_validation.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
