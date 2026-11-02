# -*- coding: utf-8 -*-
"""生成锐科激光形态归因报告。"""
from __future__ import annotations
import sys
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
    E = pd.read_csv(OUT / "breakout_events.csv") \
        if (OUT / "breakout_events.csv").exists() else pd.DataFrame()
    Wt = None
    wp = OUT / "washout_base_events.csv"
    if wp.exists():
        Wt = pd.read_csv(wp)

    xlsx = HERE / "锐科激光为何上涨_归因分析_20261016.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:

        s1 = pd.DataFrame([
            ["问题", "为什么锐科激光涨了，而「缩量筑底+业绩好」的回测说会跌？"],
            ["", ""],
            ["★ 答案", "【因为它不是「缩量筑底」形态】"],
            ["", ""],
            ["对照「缩量筑底」的 5 个条件", ""],
            ["① 距250日高点 ≤ −30%", "−32.49%", "✅ PASS"],
            ["② 量能比 v20/v60 ≤ 0.75", "0.97", "❌ FAIL（不是缩量）"],
            ["③ 波动比 ≤ 0.80", "0.88", "❌ FAIL"],
            ["④ 位置(20日) ≤ 30%", "79.41%", "❌ FAIL（在顶部）"],
            ["⑤ 距MA20 ≤ −8%", "+4.55%", "❌ FAIL（站上均线）"],
            ["", "", ""],
            ["结论", "5 条中 4 条 FAIL —— 完全不是同一形态", ""],
            ["", ""],
            ["锐科激光实际是什么", ""],
            ["形态", "深跌后放量突破", ""],
            ["位置(20日)", "79.4%（区间顶部）", ""],
            ["距MA20/60/120", "+4.55% / +9.77% / +1.36%（全部站上）", ""],
            ["量能", "近5日/近60日 = 1.26（放量）", ""],
            ["", ""],
            ["★ 但回测显示：这个形态也是负期望", ""],
            ["20日", "−0.33%  胜率 42.9%", ""],
            ["60日", "−2.02%  胜率 36.5%", ""],
            ["120日", "−0.33%  胜率 38.9%", ""],
            ["250日", "+6.10%  胜率 42.4%", ""],
            ["60日浮亏", "−18.78%", ""],
            ["", ""],
            ["所以锐科上涨的真实原因", ""],
            ["不是形态", "形态本身是负期望的", ""],
            ["是「业绩快报」这个事件", "10-14 公告 Q3 归母 +96.34%", ""],
            ["是「个股事件驱动」", "不来自形态统计（形态不覆盖单股事件）", ""],
        ], columns=["项目", "内容", "判定"])
        s1.to_excel(w, sheet_name="1_结论", index=False)
        W(w, "1_结论", [30, 40, 26])

        # 2 对照
        s2 = pd.DataFrame([
            ["形态名称", "缩量筑底", "深跌后放量突破"],
            ["① 距250日高点", "≤ −30%", "≤ −25%"],
            ["② 量能", "v20/v60 ≤ 0.75（缩量）", "v5/v60 ≥ 1.10（放量）"],
            ["③ 波动比", "≤ 0.80（收缩）", "—"],
            ["④ 位置", "≤ 30%（底部）", "≥ 70%（顶部）"],
            ["⑤ 均线", "距MA20 ≤ −8%（破位）", "站上MA20+MA60"],
            ["⑥ 其他", "—", "过去120日曾跌破MA120 + 近5日有≥5%单日涨幅"],
            ["", "", ""],
            ["锐科激光符合", "❌ 4/5 FAIL", "✅ 基本符合"],
            ["协创数据符合", "✅ 5/5 PASS", "❌"],
        ], columns=["条件", "缩量筑底", "深跌后放量突破"])
        s2.to_excel(w, sheet_name="2_两种形态对照", index=False)
        W(w, "2_两种形态对照", [20, 30, 42])

        # 3 回测对比
        rows = []
        if Wt is not None:
            line = {"形态": "B 缩量筑底（协创数据符合）", "样本": len(Wt)}
            for hz in (20, 60, 120, 250):
                v = Wt[f"f{hz}"].dropna()
                line[f"{hz}日%"] = round(v.mean(), 2)
            for hz in (60, 250):
                v = Wt[f"f{hz}"].dropna()
                line[f"{hz}日胜率%"] = round((v > 0).mean() * 100, 1)
            md = Wt["mdd60"].dropna()
            line["60日浮亏%"] = round(md.mean(), 2)
            rows.append(line)
        if len(E):
            line = {"形态": "A 深跌后放量突破（锐科符合）", "样本": len(E)}
            for hz in (20, 60, 120, 250):
                v = E[f"f{hz}"].dropna()
                line[f"{hz}日%"] = round(v.mean(), 2)
            for hz in (60, 250):
                v = E[f"f{hz}"].dropna()
                line[f"{hz}日胜率%"] = round((v > 0).mean() * 100, 1)
            md = E["mdd60"].dropna()
            line["60日浮亏%"] = round(md.mean(), 2)
            rows.append(line)
        # 随机
        bp = OUT / "breakout_baseline.csv"
        if bp.exists():
            B = pd.read_csv(bp)
            line = {"形态": "C 全市场随机（基准）", "样本": len(B)}
            for hz in (20, 60, 120, 250):
                v = B[f"f{hz}"].dropna()
                line[f"{hz}日%"] = round(v.mean(), 2)
            for hz in (60, 250):
                v = B[f"f{hz}"].dropna()
                line[f"{hz}日胜率%"] = round((v > 0).mean() * 100, 1)
            md = B["mdd60"].dropna()
            line["60日浮亏%"] = round(md.mean(), 2)
            rows.append(line)
        if rows:
            pd.DataFrame(rows).to_excel(w, sheet_name="3_三形态回测", index=False)
            W(w, "3_三形态回测", [26, 8, 9, 9, 9, 10, 10, 11, 11])

        # 4 分层
        if len(E):
            E2 = E.dropna(subset=["f60"]).copy()
            lay = []

            def do(col, label, q=4):
                try:
                    E2["_q"] = pd.qcut(E2[col], q, labels=False, duplicates="drop")
                except Exception:
                    return
                for g in sorted(E2["_q"].dropna().unique()):
                    sub = E2[E2["_q"] == g]
                    v60 = sub["f60"].dropna()
                    v250 = sub["f250"].dropna()
                    lay.append({
                        "分层": label, "档": f"Q{int(g)+1}",
                        "范围": f"{sub[col].min():.1f}~{sub[col].max():.1f}",
                        "样本": len(sub),
                        "60日%": round(v60.mean(), 2),
                        "60日胜率%": round((v60 > 0).mean() * 100, 1),
                        "250日%": round(v250.mean(), 2),
                        "250日胜率%": round((v250 > 0).mean() * 100, 1),
                    })
                E2.drop(columns=["_q"], inplace=True, errors="ignore")

            do("dd250", "前期跌幅")
            do("vr", "放量程度")
            do("chg5", "近5日涨幅")
            do("maxday", "单日最大涨幅")
            pd.DataFrame(lay).to_excel(w, sheet_name="4_分层检验", index=False)
            W(w, "4_分层检验", [12, 7, 18, 8, 9, 11, 9, 11])

        # 5 分年度
        if len(E):
            E2 = E.dropna(subset=["f60"]).copy()
            E2["yr"] = pd.to_datetime(E2["date"]).dt.year
            yr = []
            for y, g in E2.groupby("yr"):
                if len(g) < 25:
                    continue
                v60 = g["f60"].dropna()
                v250 = g["f250"].dropna()
                yr.append({
                    "年份": y, "样本": len(g),
                    "60日%": round(v60.mean(), 2),
                    "60日胜率%": round((v60 > 0).mean() * 100, 1),
                    "250日%": round(v250.mean(), 2),
                    "250日胜率%": round((v250 > 0).mean() * 100, 1),
                })
            pd.DataFrame(yr).to_excel(w, sheet_name="5_分年度", index=False)
            W(w, "5_分年度", [8, 8, 10, 11, 10, 11])

        # 6 关键结论
        s6 = pd.DataFrame([
            ["1", "锐科不是「缩量筑底」", "4/5 条件 FAIL",
             "位置79.4%（vs 要求<30%）、站上均线（vs 要求破位）"],
            ["2", "它是「深跌后放量突破」", "位置高+放量+站上均线",
             "这是完全不同的形态"],
            ["3", "但这个形态也是负期望", "60日胜率仅 36.5%",
             "58,939 个样本验证"],
            ["4", "放量越猛反而越差", "vr>2.4 档胜率 33.3%",
             "追高放量是最差的"],
            ["5", "单日涨停越猛越差", "maxday>10% 档胜率 33.8%",
             "与之前「追涨停」回测一致"],
            ["6", "分年度波动极大", "2017年250日胜率4.5%，2024年75.4%",
             "环境决定成败"],
            ["", "", "", ""],
            ["★ 锐科上涨的真实原因", "", "", ""],
            ["", "不是形态", "形态统计上是负期望", ""],
            ["", "是「业绩快报」事件", "10-14 公告 Q3 归母 +96.34%", ""],
            ["", "是「个股事件驱动」", "形态回测不覆盖单股事件", ""],
            ["", "加上「位置已修复」", "9-29到10-12缩量横盘=洗盘", ""],
            ["", "加上「市场情绪配合」", "今日全市场67%上涨", ""],
        ], columns=["#", "要点", "数据", "说明"])
        s6.to_excel(w, sheet_name="6_关键结论", index=False)
        W(w, "6_关键结论", [4, 24, 30, 44])

        # 7 教训
        s7 = pd.DataFrame([
            ["教训1", "不要用「形态」解释「已发生」的上涨",
             "事后找形态总能找到，但形态本身可能负期望"],
            ["教训2", "同一只股票在不同时点是不同形态",
             "锐科 9-28 是「放量下跌」，10-16 是「放量突破」"],
            ["教训3", "形态回测无法捕捉单股事件",
             "业绩快报、增持、订单等是个股特异信息"],
            ["教训4", "两个回测结论不矛盾",
             "协创符合缩量筑底（+19.96%）；锐科符合放量突破（+6.10%）"],
            ["", "", ""],
            ["★ 正确的用法", "", ""],
            ["", "形态用于「筛选候选池」", "从 4000 只缩到几十只"],
            ["", "事件用于「决定是否扣扳机」", "业绩/催化/资金"],
            ["", "两者不能混为一谈", ""],
            ["", "", ""],
            ["对协创数据", "形态满配（缩量筑底5/5）", "但缺事件催化"],
            ["对锐科激光", "形态不符（放量突破）", "但有业绩快报催化"],
            ["→ 关键区别", "锐科有「事件」，协创还在等「事件」", ""],
        ], columns=["类型", "要点", "说明"])
        s7.to_excel(w, sheet_name="7_教训", index=False)
        W(w, "7_教训", [10, 30, 56])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
