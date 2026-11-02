# -*- coding: utf-8 -*-
"""生成「形态+业绩」组合回测报告。"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import numpy as np

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "output"


def W(writer, name, widths):
    ws = writer.sheets[name]
    from openpyxl.utils import get_column_letter
    for i, x in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = x


def main():
    FE = pd.read_csv(OUT / "pattern_fund_events.csv", dtype={"code": str})
    FE["date"] = pd.to_datetime(FE["date"])
    CB = pd.read_csv(OUT / "pattern_fund_combos.csv") \
        if (OUT / "pattern_fund_combos.csv").exists() else pd.DataFrame()

    xlsx = HERE / "形态加业绩回测_20261016.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:

        # 1 结论
        s1 = pd.DataFrame([
            ["问题", "「缩量筑底形态 + 业绩预期好 = 更容易涨」这个逻辑成立吗？"],
            ["数据", f"{len(FE):,} 个形态事件，全部按 PIT（公告日）附加财务数据"],
            ["", ""],
            ["★ 结论", "【假设不成立】—— 而且是反向的"],
            ["", ""],
            ["核心发现", ""],
            ["业绩负增长组", "250日 +22.86%   胜率 59.4%"],
            ["业绩≥50%组", "250日 +13.04%   胜率 49.3%"],
            ["差距", "−9.81pct（业绩好的反而跑输）"],
            ["", ""],
            ["十分位单调性", ""],
            ["D1（增速最低）", "250日 +24.77%   胜率 59.2%"],
            ["D10（增速最高）", "250日 +9.88%   胜率 44.9%"],
            ["Spearman 相关", "−0.0856（负相关，p<0.0001）"],
            ["", ""],
            ["★ 为什么", ""],
            ["解释1", "高增速已被市场提前定价（预期差为负）"],
            ["解释2", "增速越高，未来「维持高增速」越难（基数效应）"],
            ["解释3", "低增速组含「困境反转」标的，赔率更高"],
            ["解释4", "在「已跌 40%+」的形态里，业绩差的股票被过度抛售"],
            ["", ""],
            ["★ 正确的组合方向", ""],
            ["不是", "业绩越好越好"],
            ["而是", "「跌得够深 + 业绩没有继续恶化」"],
            ["", ""],
            ["对协创数据的含义", ""],
            ["协创 H1 净利 +325%", "按此回测，高增速反而是「已定价」信号"],
            ["关键变量", "不是业绩有多好，而是「还有多少预期差」"],
        ], columns=["项目", "内容"])
        s1.to_excel(w, sheet_name="1_结论", index=False)
        W(w, "1_结论", [22, 66])

        # 2 业绩分组
        FE2 = FE.dropna(subset=["f_netprofit_yoy", "f60"]).copy()
        groups = [
            ("业绩负增长", FE2["f_netprofit_yoy"] < 0),
            ("0~20%", (FE2["f_netprofit_yoy"] >= 0) & (FE2["f_netprofit_yoy"] < 20)),
            ("20~50%", (FE2["f_netprofit_yoy"] >= 20) & (FE2["f_netprofit_yoy"] < 50)),
            ("50~100%", (FE2["f_netprofit_yoy"] >= 50) & (FE2["f_netprofit_yoy"] < 100)),
            ("100~300%", (FE2["f_netprofit_yoy"] >= 100) & (FE2["f_netprofit_yoy"] < 300)),
            ("300%以上", FE2["f_netprofit_yoy"] >= 300),
        ]
        rows = []
        for lab, mask in groups:
            sub = FE2[mask]
            if len(sub) < 30:
                continue
            r = {"业绩档": lab, "样本": len(sub)}
            for hz in (20, 60, 120, 250):
                v = sub[f"f{hz}"].dropna()
                r[f"{hz}日收益%"] = round(v.mean(), 2) if len(v) else None
            for hz in (20, 60, 120, 250):
                v = sub[f"f{hz}"].dropna()
                r[f"{hz}日胜率%"] = round((v > 0).mean() * 100, 1) if len(v) else None
            md = sub["mdd60"].dropna()
            r["60日浮亏%"] = round(md.mean(), 2) if len(md) else None
            rows.append(r)
        pd.DataFrame(rows).to_excel(w, sheet_name="2_按业绩分组", index=False)
        W(w, "2_按业绩分组", [12, 8, 10, 10, 10, 10, 10, 10, 10, 10, 10])

        # 3 十分位
        F = FE.dropna(subset=["f_netprofit_yoy", "f250"]).copy()
        F["q"] = pd.qcut(F["f_netprofit_yoy"], 10, labels=False, duplicates="drop")
        dec = []
        for q, g in F.groupby("q"):
            lo, hi = g["f_netprofit_yoy"].min(), g["f_netprofit_yoy"].max()
            v250 = g["f250"].dropna()
            v60 = g["f60"].dropna()
            dec.append({
                "十分位": f"D{int(q)+1}",
                "增速范围": f"{lo:.0f}%~{hi:.0f}%",
                "样本": len(g),
                "250日收益%": round(v250.mean(), 2),
                "250日胜率%": round((v250 > 0).mean() * 100, 1),
                "60日收益%": round(v60.mean(), 2),
                "60日胜率%": round((v60 > 0).mean() * 100, 1),
            })
        pd.DataFrame(dec).to_excel(w, sheet_name="3_十分位检验", index=False)
        W(w, "3_十分位检验", [9, 20, 8, 12, 12, 12, 12])

        # 4 A/B对比
        s4 = pd.DataFrame([
            ["A 形态+业绩好(≥50%)", 5298, "+3.14%", "+3.40%", "+8.85%",
             "+13.04%", "49.9%", "49.3%", "-15.14%"],
            ["B 形态+业绩差(<0)", 12302, "+2.30%", "+3.11%", "+11.44%",
             "+22.86%", "49.4%", "59.4%", "-16.00%"],
            ["", "", "", "", "", "", "", "", ""],
            ["A − B", "", "+0.84", "+0.29", "-2.59",
             "-9.81", "+0.5", "-10.1", "+0.86"],
            ["", "", "", "", "", "", "", "", ""],
            ["全部形态（不加条件）", 22962, "—", "+3.40%", "—",
             "+19.74%", "50.3%", "56.2%", "—"],
            ["形态+业绩≥50% vs 全部形态", "", "", "+0.01", "",
             "-6.70", "-0.4", "-6.9", ""],
        ], columns=["组", "样本", "20日%", "60日%", "120日%",
                    "250日%", "60日胜率", "250日胜率", "60日浮亏"])
        s4.to_excel(w, sheet_name="4_A_B对比", index=False)
        W(w, "4_A_B对比", [30, 8, 9, 9, 9, 10, 10, 11, 10])

        # 5 多条件叠加
        if len(CB):
            CB.to_excel(w, sheet_name="5_多条件叠加", index=False)
            W(w, "5_多条件叠加", [34, 8, 9, 9, 10, 10, 11, 11])

        # 6 机制
        s6 = pd.DataFrame([
            ["1", "预期已被定价",
             "高增速公司在公告前股价已反映预期",
             "PIT 滞后中位 59 天，市场有时间定价"],
            ["2", "基数效应",
             "增速越高，下一年维持越难",
             "D10 组（增速>149%）250日胜率仅 44.9%"],
            ["3", "困境反转赔率",
             "业绩差组含大量超跌标的，赔率高",
             "负增长组 250日 +22.86%"],
            ["4", "形态本身已含信息",
             "「缩量筑底」形态说明市场已在消化坏消息",
             "形态组 250日 +19.74%（vs 随机 +14.87%）"],
            ["5", "均值回归",
             "跌 40%+ 后，基本面差的标的回归空间更大",
             "位置都在 7% 附近，但 dd250 有差异"],
        ], columns=["#", "机制", "说明", "数据依据"])
        s6.to_excel(w, sheet_name="6_机制解释", index=False)
        W(w, "6_机制解释", [4, 16, 36, 38])

        # 7 分年度
        A = FE2[FE2["f_netprofit_yoy"] >= 50].copy()
        A["yr"] = A["date"].dt.year
        yr = []
        for y, g in A.groupby("yr"):
            if len(g) < 20:
                continue
            v60 = g["f60"].dropna()
            v250 = g["f250"].dropna()
            yr.append({
                "年份": y, "样本": len(g),
                "60日%": round(v60.mean(), 2),
                "60日胜率%": round((v60 > 0).mean() * 100, 1),
                "250日%": round(v250.mean(), 2) if len(v250) else None,
                "250日胜率%": round((v250 > 0).mean() * 100, 1) if len(v250) else None,
            })
        pd.DataFrame(yr).to_excel(w, sheet_name="7_分年度", index=False)
        W(w, "7_分年度", [8, 8, 10, 11, 10, 12])

        # 8 修正后的方向
        s8 = pd.DataFrame([
            ["❌ 不要做", "堆叠业绩指标", "净利≥50%+ROE≥5% 的 250日胜率仅 39.6%"],
            ["❌ 不要做", "要求净利增速越高越好", "D10 组胜率 44.9%（最差）"],
            ["❌ 不要做", "营收+净利双高", "营收≥20%+净利≥50% 胜率 44.6%"],
            ["", "", ""],
            ["✅ 应该做", "找「形态 + 业绩不再恶化」",
             "负增长组 250日 +22.86%、胜率 59.4%"],
            ["✅ 应该做", "关注「预期差」而非「增速绝对值」",
             "关键：市场是否已定价"],
            ["✅ 应该做", "用低预期 + 深跌 + 缩量",
             "这组的赔率最高"],
            ["", "", ""],
            ["⭐ 最优组合（本次回测）", "形态 + 业绩负增长", "250日 +22.86%，胜率 59.4%"],
            ["", "但需注意", "含「困境反转」博弈，风险也更高"],
        ], columns=["类型", "做法", "数据依据"])
        s8.to_excel(w, sheet_name="8_修正方向", index=False)
        W(w, "8_修正方向", [12, 26, 52])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
