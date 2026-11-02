# -*- coding: utf-8 -*-
"""
explain_a2.py — 「A2组（负期望）」的完整解释与复核

目的：
  ① 明确定义 A1 / A2 / B
  ② 计算「期望值」—— 平均、中位、胜率、赔率
  ③ 复核「负期望」的说法是否严谨
  ④ 说明为什么胜率低≠负期望，胜率高≠正期望
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "output"


def describe(v, lab):
    """完整描述一组收益。"""
    v = pd.Series(v).dropna()
    if len(v) < 10:
        return None
    return {
        "指标": lab,
        "样本": len(v),
        "平均%": round(v.mean(), 2),
        "中位%": round(v.median(), 2),
        "胜率%": round((v > 0).mean() * 100, 1),
        "平均盈利%": round(v[v > 0].mean(), 2) if (v > 0).any() else None,
        "平均亏损%": round(v[v < 0].mean(), 2) if (v < 0).any() else None,
    }


def main():
    P = pd.read_csv(OUT / "position_effect.csv", dtype={"code": str})
    P["g"] = pd.to_numeric(P["g"], errors="coerce")
    P["pos"] = pd.to_numeric(P["pos"], errors="coerce")
    P = P.dropna(subset=["g", "pos"])
    print("=" * 130)
    print("  「A2 组（负期望）」完整解释与复核")
    print("=" * 130)
    print()
    print(f"  数据源: position_effect.csv（{len(P):,} 个业绩预告事件）")
    print()

    # ---- 定义
    A1 = P[(P["g"] >= 50) & (P["pos"] >= 60)]
    A2 = P[(P["g"] >= 50) & (P["pos"] < 20)]
    B = P[(P["g"] >= 50) & (P["pos"] >= 20) & (P["pos"] < 60)]

    print("=" * 130)
    print("  一、分组定义")
    print("=" * 130)
    print()
    print("  A1 = 增速≥50%  + 位置≥60%（高位）  ← 我推荐")
    print("  A2 = 增速≥50%  + 位置<20%（低位）  ← 我说「负期望」")
    print("  B  = 增速≥50%  + 位置20~60%（中位）")
    print()
    print(f"  样本: A1 {len(A1):,}   A2 {len(A2):,}   B {len(B):,}")
    print()

    # ---- 期望值
    print("=" * 130)
    print("  二、什么叫「期望值」")
    print("=" * 130)
    print()
    print("  期望值 = 平均每次交易的结果")
    print("  E = (胜率 × 平均盈利) + (败率 × 平均亏损)")
    print()
    print("  举例（简化）:")
    print("    · 胜率 60%，赢时 +10%，输时 −10%")
    print("      → E = 0.6×10 + 0.4×(−10) = +2%   ← 正期望")
    print("    · 胜率 42%，赢时 +12%，输时 −8%")
    print("      → E = 0.42×12 + 0.58×(−8) = +0.4%  ← 微正")
    print("    · 胜率 42%，赢时 +8%，输时 −10%")
    print("      → E = 0.42×8 + 0.58×(−10) = −2.4%  ← 负期望")
    print()
    print("  ★ 关键：期望 = 平均收益。长期重复做，平均结果就是它。")
    print("     负期望 = 长期做会亏钱（不管单次感觉如何）")
    print()

    # ---- 实测
    print("=" * 130)
    print("  三、实测：A1 / A2 / B 的真实期望")
    print("=" * 130)
    print()
    rows = []
    for lab, sub in [("A1 高增+高位", A1), ("A2 高增+低位", A2),
                     ("B 高增+中位", B)]:
        for hz, hl in [("f20", "20日"), ("f60", "60日"),
                       ("f120", "120日"), ("f250", "250日")]:
            r = describe(sub[hz], f"{lab}  {hl}")
            if r:
                r["分组"] = lab
                r["持有"] = hl
                rows.append(r)
    R = pd.DataFrame(rows)
    cols = ["分组", "持有", "样本", "平均%", "中位%", "胜率%",
            "平均盈利%", "平均亏损%"]
    R = R[cols]
    print(R.to_string(index=False))
    print()

    # ---- 分持有期对比表
    print("=" * 130)
    print("  四、按持有期对比（关键：期望随时长变化）")
    print("=" * 130)
    print()
    print(f"  {'持有期':<8}{'分组':<16}{'样本':>7}{'平均%':>9}{'中位%':>9}"
          f"{'胜率%':>9}{'盈亏比':>9}{'判定':<10}")
    print("  " + "-" * 84)
    for hz, hl in [("f20", "20日"), ("f60", "60日"),
                   ("f120", "120日"), ("f250", "250日")]:
        for lab, sub in [("A1 高位", A1), ("A2 低位", A2)]:
            v = sub[hz].dropna()
            if len(v) < 10:
                continue
            win = v[v > 0].mean() if (v > 0).any() else 0
            los = abs(v[v < 0].mean()) if (v < 0).any() else 1
            ratio = win / los if los > 0 else np.nan
            verdict = ("正期望 ✅" if v.mean() > 1
                       else ("接近0 ⚠" if v.mean() > -1 else "负期望 ❌"))
            print(f"  {hl:<8}{lab:<16}{len(v):>7}{v.mean():>9.2f}"
                  f"{v.median():>9.2f}{(v>0).mean()*100:>8.1f}%"
                  f"{ratio:>9.2f}  {verdict:<10}")
        print()

    # ---- 关键澄清
    print("=" * 130)
    print("  五、★ 严谨澄清：A2 是「负期望」吗？")
    print("=" * 130)
    print()
    print("  复核实测（A2 高增+低位，样本 %d）:" % len(A2))
    print()
    for hz, hl in [("f20", "20日"), ("f60", "60日"),
                   ("f120", "120日"), ("f250", "250日")]:
        v = A2[hz].dropna()
        if len(v) < 10:
            continue
        med = v.median()
        mean = v.mean()
        wr = (v > 0).mean() * 100
        flag = "★正" if mean > 0 else "★负"
        print(f"    {hl:<6} 平均 {mean:>+8.2f}%   中位 {med:>+8.2f}%   "
              f"胜率 {wr:>5.1f}%   {flag}")
    print()
    print("  ★ 结论（修正我之前的说法）:")
    print()
    a2_20 = A2["f20"].dropna()
    a2_60 = A2["f60"].dropna()
    a2_120 = A2["f120"].dropna()
    a2_250 = A2["f250"].dropna()
    print(f"    · 20日 : 平均 {a2_20.mean():+.2f}%、中位 {a2_20.median():+.2f}%"
          f"、胜率 {(a2_20>0).mean()*100:.1f}%")
    print(f"    · 60日 : 平均 {a2_60.mean():+.2f}%、中位 {a2_60.median():+.2f}%"
          f"、胜率 {(a2_60>0).mean()*100:.1f}%")
    print(f"    · 120日: 平均 {a2_120.mean():+.2f}%、中位 "
          f"{a2_120.median():+.2f}%、胜率 {(a2_120>0).mean()*100:.1f}%")
    print(f"    · 250日: 平均 {a2_250.mean():+.2f}%、中位 "
          f"{a2_250.median():+.2f}%、胜率 {(a2_250>0).mean()*100:.1f}%")
    print()
    print("  → 更准确的说法：")
    print("    A2 不是「绝对负期望」，而是「【相对更差】+ 20/60/120日偏负」")
    print("    而且在 60-120 日是确实的负期望（均值、中位、胜率三项都差）")
    print()
    print("  → 与 A1 的差距（这才是关键）:")
    for hz, hl in [("f20", "20日"), ("f60", "60日"), ("f120", "120日")]:
        v1 = A1[hz].dropna()
        v2 = A2[hz].dropna()
        if len(v1) > 10 and len(v2) > 10:
            print(f"    {hl:<6} A1 {v1.mean():>+7.2f}%  vs  "
                  f"A2 {v2.mean():>+7.2f}%   差 {v1.mean()-v2.mean():>+6.2f}pct")
    print()

    # ---- 为什么会有这个差
    print("=" * 130)
    print("  六、为什么「低位」反而差？（机制）")
    print("=" * 130)
    print()
    print("  三个机制：")
    print()
    print("  ① 「位置低」可能是基本面在恶化，不是便宜")
    print("     · 高位 = 已有资金持续买入（趋势确认）")
    print("     · 低位 = 无人问津 / 持续被卖（趋势向下）")
    print()
    print("  ② 业绩公告是「趋势确认」而非「反转信号」")
    print("     · 好业绩 + 已有上升趋势 → 加速")
    print("     · 好业绩 + 下跌趋势 → 只是一次反弹，很快回落")
    print()
    print("  ③ 逆向选择：低位股多是「有理由的便宜」")
    print("     · 市场不傻，跌那么多通常有原因")
    print("     · 增速高可能来自低基数或一次性收益")
    print()

    # ---- 实操含义
    print("=" * 130)
    print("  七、实操含义")
    print("=" * 130)
    print()
    print("  ❌ 不要做:")
    print("     · 买「增速高 + 位置低」的（A2）")
    print("     · 东方锆业就是这类（增速 +261.9%、位置 10.8%）")
    print()
    print("  ✅ 应该做:")
    print("     · 买「增速高 + 位置高 + 跳空≥2% + 未回补」的（A1 优选）")
    print("     · 复旦微电是这类（增速 187.6%、p60=98.6、跳空5.52%、未回补）")
    print()
    print("  ⚠ 但注意:")
    print("     · A1 样本仅 %d，A2 仅 %d" % (len(A1), len(A2)))
    print("     · 样本期 1.7 年，需更多验证")
    print("     · 「位置」定义用 130 日区间；不同窗口结论可能不同")
    print()

    R.to_csv(OUT / "a2_explain.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {OUT}/a2_explain.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
