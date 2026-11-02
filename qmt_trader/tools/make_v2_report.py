# -*- coding: utf-8 -*-
"""生成 v2 监控升级报告。"""
from __future__ import annotations
import sys, glob, os
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "output"


def W(writer, name, widths):
    ws = writer.sheets[name]
    from openpyxl.utils import get_column_letter
    for i, x in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = x


def main():
    V = pd.read_csv(OUT / "v2_validation.csv") \
        if (OUT / "v2_validation.csv").exists() else pd.DataFrame()

    xlsx = HERE / "监控v2升级说明_20261016.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:

        # 1 升级内容
        s1 = pd.DataFrame([
            ["版本", "earnings_watch v2"],
            ["升级时间", "2026-10-16"],
            ["", ""],
            ["★ 四项改进", ""],
            ["① 位置三周期", "原来只有 130 日单一位置，现改为 20/60/120 日"],
            ["② 分组按持有期", "A1（中期优选）vs A2（短期观察）"],
            ["③ 措辞预警", "自动扫描公告负面词/正面词"],
            ["④ Tushare 补数", "本地缓存缺的大市值股自动补"],
            ["", ""],
            ["★ 新分组规则", ""],
            ["A1 优选", "高增(≥50%) + p60≥60 或 p120≥60 + 无措辞瑕疵"],
            ["A2 短期", "高增(≥50%) + p20<20（低位）"],
            ["B 中位", "高增(≥50%) + 其他"],
            ["D 中增", "20% ≤ 增速 < 50%"],
            ["E 低增", "增速 < 20%"],
            ["A1' 瑕疵", "高增+高位但负面词≥2（降级）"],
            ["", ""],
            ["★ 验证结果", ""],
            ["A1 高增+高位", "60日 +13.54%  胜率 59.1%"],
            ["A2 高增+低位", "60日 −1.98%  胜率 42.2%"],
            ["差异", "**+15.52pct**（60日）、+20.63pct（120日）"],
        ], columns=["项目", "内容"])
        s1.to_excel(w, sheet_name="1_升级内容", index=False)
        W(w, "1_升级内容", [20, 66])

        # 2 验证结果
        if len(V):
            G = V.copy()
            G = G.dropna(subset=["f20", "f60"])
            def v2(r):
                g = r.get("g")
                p = r.get("pos")
                if pd.isna(g) or pd.isna(p):
                    return None
                if g < 20:
                    return "E 低增"
                if g < 50:
                    return "D 中增"
                if p > 60:
                    return "A1 高增+高位"
                if p < 20:
                    return "A2 高增+低位"
                return "B 高增+中位"
            G["分组"] = G.apply(v2, axis=1)
            rows = []
            for lab in ["A1 高增+高位", "A2 高增+低位", "B 高增+中位",
                        "D 中增", "E 低增"]:
                s = G[G["分组"] == lab]
                if len(s) < 30:
                    continue
                r = {"分组": lab, "样本": len(s)}
                for hz, l in [("f20", "20日"), ("f60", "60日"),
                              ("f120", "120日"), ("f250", "250日")]:
                    v = s[hz].dropna()
                    r[f"{l}%"] = round(v.mean(), 2) if len(v) else None
                    r[f"{l}胜率%"] = round((v > 0).mean() * 100, 1) if len(v) else None
                rows.append(r)
            pd.DataFrame(rows).to_excel(w, sheet_name="2_验证结果", index=False)
            W(w, "2_验证结果", [16, 8] + [9] * 8)

        # 3 A1 vs A2
        s3 = pd.DataFrame([
            ["20日", "+4.57%", "+1.87%", "+2.70", ""],
            ["60日", "+13.54%", "−1.98%", "+15.52", "★"],
            ["120日", "+8.56%", "−12.07%", "+20.63", "★"],
            ["250日", "+18.16%", "+57.68%", "−39.52", "★（反转）"],
            ["", "", "", "", ""],
            ["20日胜率", "57.1%", "52.7%", "+4.4", ""],
            ["60日胜率", "59.1%", "42.2%", "+16.9", "★"],
        ], columns=["持有期", "A1 高增+高位", "A2 高增+低位", "差异pct", "备注"])
        s3.to_excel(w, sheet_name="3_A1vsA2", index=False)
        W(w, "3_A1vsA2", [12, 16, 16, 11, 14])
        s3b = pd.DataFrame([
            ["关键", "60/120日看 A1（高位）显著更好"],
            ["但", "250日看 A2（低位）反而好（+57.68%）"],
            ["含义", "时间尺度不同，结论不同"],
            ["", ""],
            ["短期（20日）", "两者接近（+4.57% vs +1.87%）"],
            ["中期（60-120日）", "A1 明显占优（+15.5 / +20.6pct）"],
            ["长期（250日）", "A2 占优（+39.5pct）→ 但样本仅 315"],
        ], columns=["项目", "说明"])
        s3b.to_excel(w, sheet_name="4_时间尺度", index=False)
        W(w, "4_时间尺度", [16, 50])

        # 5 措辞词表
        s5 = pd.DataFrame([
            ["负面词", "递延、滞后、顺延、下滑、压力、短缺、消化、调整"],
            ["", "下降、缩减、退单、取消、亏损扩大、不及预期、放缓、疲软、承压"],
            ["", "",],
            ["正面词", "环比提升、环比增长、创历史新高、满产、满稼动"],
            ["", "供不应求、订单充足、订单充裕、量价齐升、毛利率提升"],
            ["", "同比和环比、持续增长、显著提升、大幅增长"],
            ["", "",],
            ["判定规则", "负面词数 ≥2 → 降级为「A1' 措辞有瑕疵」"],
            ["案例", "瑞芯微：滞后/顺延/短缺/消化/调整（5个）→ 今日 −9.99%"],
            ["", "复旦微电：1个（影响有限）→ 今日 +14.61%"],
        ], columns=["类型", "词表/说明"])
        s5.to_excel(w, sheet_name="5_措辞词表", index=False)
        W(w, "5_措辞词表", [12, 76])

        # 6 今日运行结果
        mp = HERE / "earnings_watch" / "report_20261016.md"
        if mp.exists():
            txt = mp.read_text(encoding="utf-8")
            s6 = pd.DataFrame({"报告": txt.split("\n")[:80]})
            s6.to_excel(w, sheet_name="6_今日报告", index=False)
            W(w, "6_今日报告", [120])

        # 7 局限
        s7 = pd.DataFrame([
            ["1", "位置分档为近似", "position_effect 只有 130 日单一位置",
             "无法真实复现 p20/p60/p120 三周期"],
            ["2", "措辞预警未回测", "原文未程序化标签",
             "属逻辑推断，非统计验证"],
            ["3", "样本期短", "2025-03 ~ 2026-10（1.7年）",
             "含 2026 熊市，可能低估"],
            ["4", "250日反转", "A2 长期反而好（+57.68%）",
             "样本仅 315，可靠性有限"],
            ["", "", "", ""],
            ["已验证有效的", "", "", ""],
            ["", "位置 vs 60日", "Spearman +0.1745  p<0.0001", "★"],
            ["", "位置 vs 120日", "Spearman +0.2063  p<0.0001", "★"],
            ["", "D10 vs D1", "60日 +16.64% vs −4.13%", "★"],
        ], columns=["#", "项目", "数据", "说明"])
        s7.to_excel(w, sheet_name="7_局限", index=False)
        W(w, "7_局限", [4, 20, 34, 30])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
