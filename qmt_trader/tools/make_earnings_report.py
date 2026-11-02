# -*- coding: utf-8 -*-
"""生成「业绩驱动」标的筛选 + PEAD 回测报告。"""
from __future__ import annotations
import sys, glob, os
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
    ef = sorted(glob.glob(str(OUT / "earnings_driven_*.csv")),
                key=os.path.getmtime)
    A = pd.read_csv(ef[-1], dtype={"代码": str}) if ef else pd.DataFrame()
    P = pd.read_csv(OUT / "pead_events.csv", dtype={"code": str}) \
        if (OUT / "pead_events.csv").exists() else pd.DataFrame()
    PB = pd.read_csv(OUT / "pead_bands.csv") \
        if (OUT / "pead_bands.csv").exists() else pd.DataFrame()

    xlsx = HERE / "业绩驱动选股_20261016.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:

        # 1 结论
        s1 = pd.DataFrame([
            ["时点", "2026-10-16 17:10"],
            ["", ""],
            ["【第一部分】锐科是不是业绩驱动？", ""],
            ["结论", "✅ 是。已用同业对照验证"],
            ["锐科 10-12~10-16", "+18.90%"],
            ["同业同期", "大族激光 −9.02%、华工科技 −8.33%、杰普特 −7.03%"],
            ["→ 判定", "个股特异（非板块共振）"],
            ["催化剂", "10-14 业绩快报：Q3 归母 +96.34%、扣非 +161.13%"],
            ["", ""],
            ["【第二部分】能找类似的吗？回测结果", ""],
            ["PEAD 检验（业绩公告后买入）", ""],
            ["  20日", "+3.82%  胜率 50.1%  （超额 +2.36pct）"],
            ["  60日", "+6.31%  胜率 50.1%  （超额 +2.64pct）"],
            ["  120日", "+0.02%  胜率 —     （超额 −6.92pct）"],
            ["  250日", "+11.47% 胜率 39.4% （超额 −3.39pct）"],
            ["", ""],
            ["★ 关键发现：短期有效，长期衰减", ""],
            ["20/60日", "有超额（+2.4~2.6pct）"],
            ["120/250日", "转为负超额（−3.4~−6.9pct）"],
            ["", ""],
            ["★ 预告 vs 快报（差异巨大）", ""],
            ["  业绩预告后20日", "+4.89%  胜率 62.8%  ★有效"],
            ["  业绩快报后20日", "−5.35%  胜率 29.1%  ✗无效"],
            ["", ""],
            ["【第三部分】今日筛选结果", ""],
            ["已披露前三季业绩", f"{len(A) if len(A) else 0} 家"],
            ["高增+低位（≥50%+位置≤40%）", "38 只"],
            ["高增+中位", "14 只"],
            ["高增+高位（已涨）", "3 只"],
            ["中增+低位（20~50%）", "9 只"],
            ["", ""],
            ["⚠ 数据局限", ""],
            ["PEAD 样本", "仅 2025-02 ~ 2026-10（约 1.7 年）"],
            ["原因", "Tushare 预告接口按报告期拉取，历史受限"],
            ["含义", "结论受单一市场环境影响，需谨慎"],
        ], columns=["项目", "内容"])
        s1.to_excel(w, sheet_name="1_结论", index=False)
        W(w, "1_结论", [30, 62])

        # 2 锐科同业对比
        s2 = pd.DataFrame([
            ["锐科激光", -2.09, 5.78, 5.04, 0.03, 6.98, 18.90, "← 标的"],
            ["大族激光", -7.05, 1.05, -4.17, -6.75, 0.76, -9.02, ""],
            ["华工科技", -3.94, 1.25, -4.14, -4.84, -0.75, -8.33, ""],
            ["杰普特", -6.42, 4.87, 0.26, -7.65, -4.26, -7.03, ""],
            ["柏楚电子", -2.75, -1.13, -1.34, 0.50, 0.12, -1.86, ""],
            ["金橙子", -4.39, -1.21, -1.85, -5.18, 0.03, -8.03, ""],
            ["光库科技", -3.08, 5.10, -4.16, -3.91, 2.19, -1.09, ""],
            ["帝尔激光", -6.56, 1.01, -6.45, -5.50, -3.27, -13.62, ""],
            ["晶盛机电", -4.40, 1.32, -2.66, -1.37, -2.86, -5.51, ""],
        ], columns=["名称", "10-12", "10-13", "10-14", "10-15", "10-16",
                    "累计%", "说明"])
        s2.to_excel(w, sheet_name="2_同业对比", index=False)
        W(w, "2_同业对比", [12, 9, 9, 9, 9, 9, 10, 12])

        # 3 PEAD 分层
        if len(PB):
            PB.to_excel(w, sheet_name="3_PEAD分层", index=False)
            W(w, "3_PEAD分层", [14, 8, 9, 9, 10, 10, 11, 11, 10])

        # 4 十分位
        if len(P):
            E2 = P.dropna(subset=["f60"]).copy()
            E2["g"] = pd.to_numeric(E2["g"], errors="coerce")
            E2 = E2.dropna(subset=["g"])
            try:
                E2["q"] = pd.qcut(E2["g"], 10, labels=False, duplicates="drop")
                dec = []
                for q, g in E2.groupby("q"):
                    v60 = g["f60"].dropna()
                    v250 = g["f250"].dropna()
                    dec.append({
                        "十分位": f"D{int(q)+1}",
                        "增速范围": f"{g['g'].min():.0f}%~{g['g'].max():.0f}%",
                        "样本": len(g),
                        "60日%": round(v60.mean(), 2),
                        "60日胜率%": round((v60 > 0).mean() * 100, 1),
                        "250日%": round(v250.mean(), 2),
                        "250日胜率%": round((v250 > 0).mean() * 100, 1),
                    })
                pd.DataFrame(dec).to_excel(w, sheet_name="4_十分位", index=False)
                W(w, "4_十分位", [9, 24, 8, 10, 11, 10, 11])
            except Exception:
                pass

        # 5 预告 vs 快报
        if len(P):
            rows = []
            for src in P["src"].unique():
                s = P[P["src"] == src]
                if len(s) < 30:
                    continue
                r = {"来源": src, "样本": len(s)}
                for hz in (20, 60, 120, 250):
                    v = s[f"f{hz}"].dropna()
                    r[f"{hz}日%"] = round(v.mean(), 2)
                    r[f"{hz}日胜率%"] = round((v > 0).mean() * 100, 1)
                md = s["mdd60"].dropna()
                r["60日浮亏%"] = round(md.mean(), 2)
                rows.append(r)
            if rows:
                pd.DataFrame(rows).to_excel(w, sheet_name="5_预告vs快报",
                                            index=False)
                W(w, "5_预告vs快报",
                  [10, 8, 9, 10, 9, 10, 10, 11, 11, 11])

        # 6 与随机对比
        s6 = pd.DataFrame([
            ["20日收益", "+3.82%", "+1.46%", "+2.36"],
            ["60日收益", "+6.31%", "+3.68%", "+2.64"],
            ["120日收益", "+0.02%", "+6.94%", "−6.92"],
            ["250日收益", "+11.47%", "+14.87%", "−3.39"],
            ["60日胜率", "50.1%", "48.3%", "+1.8"],
            ["250日胜率", "39.4%", "50.8%", "−11.4"],
            ["60日浮亏", "−14.43%", "−14.90%", "+0.47"],
        ], columns=["指标", "业绩公告后", "全市场随机", "超额"])
        s6.to_excel(w, sheet_name="6_与随机对比", index=False)
        W(w, "6_与随机对比", [12, 14, 14, 10])

        # 7 候选
        if len(A):
            A2 = A.copy()
            A2["增速"] = A2["净利增速下限%"].fillna(A2["净利增速上限%"])
            A2 = A2.dropna(subset=["增速"])
            A2 = A2.sort_values("增速", ascending=False)
            A2.to_excel(w, sheet_name="7_全部候选", index=False)
            W(w, "7_全部候选", [10, 12, 12, 10, 11, 9, 9, 8, 9, 9, 9, 9, 9, 8])

            # 分组
            T2 = A2.dropna(subset=["位置%"]).copy()
            grp = []
            defs = [
                ("A 高增+低位（最优）", (T2["增速"] >= 50) & (T2["位置%"] <= 40)),
                ("B 高增+中位", (T2["增速"] >= 50) & (T2["位置%"] > 40) &
                 (T2["位置%"] <= 70)),
                ("C 高增+高位（已涨）", (T2["增速"] >= 50) & (T2["位置%"] > 70)),
                ("D 中增+低位", (T2["增速"] >= 20) & (T2["增速"] < 50) &
                 (T2["位置%"] <= 40)),
                ("E 负增长", T2["增速"] < 0),
            ]
            for lab, mask in defs:
                s = T2[mask].sort_values("增速", ascending=False)
                for _, r in s.iterrows():
                    grp.append({
                        "分组": lab, "代码": r["代码"], "名称": r["名称"],
                        "增速%": r["增速"], "公告日": r["公告日"],
                        "来源": r["来源"], "现价": r["现价"],
                        "今日涨跌%": r["今日涨跌%"],
                        "位置%": r["位置%"], "距130日高%": r["距130日高%"],
                        "PE": r["PE"], "总市值亿": r["总市值亿"],
                    })
            if grp:
                G = pd.DataFrame(grp)
                G.to_excel(w, sheet_name="8_分组候选", index=False)
                W(w, "8_分组候选", [22, 10, 12, 9, 12, 8, 8, 10, 9, 12, 9, 10])
                # 摘要
                sm = G.groupby("分组").agg(
                    数量=("代码", "size"),
                    平均增速=("增速%", "mean"),
                    平均位置=("位置%", "mean"),
                    平均市值=("总市值亿", "mean")).round(1).reset_index()
                sm.to_excel(w, sheet_name="9_分组摘要", index=False)
                W(w, "9_分组摘要", [22, 8, 11, 11, 11])

        # 10 结论与用法
        s10 = pd.DataFrame([
            ["1", "锐科确实是业绩驱动", "同业全跌，它 +18.90%",
             "大族 −9.02%、华工 −8.33%"],
            ["2", "但 PEAD 只在短期有效", "20日 +2.36pct 超额，250日 −3.39pct",
             "60日本策略 +6.31%"],
            ["3", "业绩预告 > 业绩快报", "预告20日胜率 62.8% vs 快报 29.1%",
             "锐科用的是快报（但结合了底部位置）"],
            ["4", "增速与收益无单调关系", "Spearman 60日 +0.0176（不显著）",
             "不是增速越高越好"],
            ["5", "候选有 38 只高增+低位", "位置≤40% 且增速≥50%",
             "但需逐个核实"],
            ["", "", "", ""],
            ["★ 正确用法", "", "", ""],
            ["", "用公告筛选（缩小范围）", "已披露前三季业绩的公司", ""],
            ["", "用位置过滤（避开已涨）", "位置≤40%", ""],
            ["", "用增速排序（但不迷信）", "≥50% 即可，不必追求最高", ""],
            ["", "逐个核实（关键）", "公告原文、是否一次性收益、行业景气", ""],
            ["", "控制持有期", "PEAD 有效期约 20-60 日", ""],
            ["", "", "", ""],
            ["⚠ 局限", "", "", ""],
            ["", "样本期短", "2025-02 ~ 2026-10（1.7年）", ""],
            ["", "单一市场环境", "含 2026 熊市，可能低估", ""],
        ], columns=["#", "要点", "数据", "说明"])
        s10.to_excel(w, sheet_name="10_结论用法", index=False)
        W(w, "10_结论用法", [4, 24, 34, 38])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
