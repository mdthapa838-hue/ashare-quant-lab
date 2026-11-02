# -*- coding: utf-8 -*-
"""生成业绩快报/预告监控报告。"""
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
    mf = sorted(glob.glob(str(OUT / "earnings_monitor_*.csv")),
                key=os.path.getmtime)
    A = pd.read_csv(mf[-1], dtype={"代码": str}) if mf else pd.DataFrame()
    P = pd.read_csv(OUT / "pead_events.csv", dtype={"code": str}) \
        if (OUT / "pead_events.csv").exists() else pd.DataFrame()

    xlsx = HERE / "业绩快报监控_20261016.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:

        # 1 直接回答
        s1 = pd.DataFrame([
            ["问题", "哪些股票有业绩快报？快报出来时容易涨吗？怎么监控？"],
            ["", ""],
            ["【回答1：今天有哪些】", ""],
            ["今日（10-16）预告", "15 家"],
            ["今日（10-16）快报", "1 家（603019 中科曙光）"],
            ["", ""],
            ["【回答2：快报出来容易涨吗？】", ""],
            ["单纯「快报」", "❌ 20日胜率仅 29.1%（负期望）"],
            ["「业绩预告」", "✅ 20日胜率 59.9%~65.1%（有效）"],
            ["差异原因", "预告是首次披露（信息增量大）"],
            ["", "快报通常在预告之后（信息已消化）"],
            ["", ""],
            ["★ 但锐科用的是快报，为什么涨？", ""],
            ["原因", "锐科的快报 = 前一天刚公告，且是「首次披露前三季度」"],
            ["", "多数年报快报在 2 月集中发布（预告已先行）"],
            ["", "锐科这种「三季度快报」反而少见（仅 7 家）"],
            ["", ""],
            ["【回答3：怎么监控】", ""],
            ["命令", "python tools/monitor_earnings.py"],
            ["可选参数", "--date=20261016  或  --days=3"],
            ["输出", "当日新增 + 位置/PE/市值 + 分组"],
            ["", ""],
            ["★ 今日 A 组（高增+低位）", ""],
            ["002064 华峰化学", "增速 +98.4%  位置 8.1%  市值 445亿  PE 15.6"],
            ["002044 美年健康", "增速 +75.5%  位置 15.8%  市值 182亿  PE 56.0"],
            ["002281 光迅科技", "增速 +65.4%  位置 25.7%  市值 1107亿  PE 95.8"],
        ], columns=["项目", "内容"])
        s1.to_excel(w, sheet_name="1_直接回答", index=False)
        W(w, "1_直接回答", [28, 70])

        # 2 今日清单
        if len(A):
            A.to_excel(w, sheet_name="2_今日公告", index=False)
            W(w, "2_今日公告", [10, 12, 8, 8, 10, 9, 9, 9, 10, 9, 10, 10])

        # 3 修正后的回测
        if len(P):
            F = P[P["src"] == "预告"].copy()
            rows = []
            bands = [
                ("<0%", F["g"] < 0), ("0~20%", (F["g"] >= 0) & (F["g"] < 20)),
                ("20~50%", (F["g"] >= 20) & (F["g"] < 50)),
                ("50~100%", (F["g"] >= 50) & (F["g"] < 100)),
                ("100~300%", (F["g"] >= 100) & (F["g"] < 300)),
                ("300~1000%", (F["g"] >= 300) & (F["g"] < 1000)),
                (">1000%", F["g"] >= 1000),
            ]
            for lab, mask in bands:
                s = F[mask]
                if len(s) < 30:
                    continue
                r = {"增速档": lab, "样本": len(s)}
                for hz in (20, 60, 120, 250):
                    v = s[f"f{hz}"].dropna()
                    r[f"{hz}日%"] = round(v.mean(), 2)
                    r[f"{hz}日胜率%"] = round((v > 0).mean() * 100, 1)
                rows.append(r)
            pd.DataFrame(rows).to_excel(w, sheet_name="3_预告回测",
                                         index=False)
            W(w, "3_预告回测",
              [12, 8, 9, 10, 10, 10, 10, 11, 11, 11])

            # 预告 vs 快报
            rows2 = []
            for src in P["src"].unique():
                s = P[P["src"] == src]
                if len(s) < 30:
                    continue
                r = {"来源": src, "样本": len(s)}
                for hz in (20, 60, 120, 250):
                    v = s[f"f{hz}"].dropna()
                    r[f"{hz}日%"] = round(v.mean(), 2)
                    r[f"{hz}日胜率%"] = round((v > 0).mean() * 100, 1)
                rows2.append(r)
            pd.DataFrame(rows2).to_excel(w, sheet_name="4_预告vs快报",
                                          index=False)
            W(w, "4_预告vs快报", [10, 8, 9, 10, 9, 10, 10, 11, 11, 11])

        # 5 监控说明
        s5 = pd.DataFrame([
            ["基本用法", "python tools/monitor_earnings.py",
             "查当日新增预告/快报"],
            ["指定日期", "python tools/monitor_earnings.py --date=20261016",
             "查指定日"],
            ["最近N天", "python tools/monitor_earnings.py --days=3",
             "查最近3天"],
            ["", "", ""],
            ["输出内容", "", ""],
            ["① 公告清单", "按增速排序", "含代码/名称/类型/增速"],
            ["② 行情附加", "位置/距高/PE/PB/市值/换手", "自动计算"],
            ["③ 分组", "A高增+低位 / B中位 / C已涨 / D低增+低位",
             "回测依据：预告20日胜率60%"],
            ["④ 公告要点", "从 summary 提取前120字", "快速了解原因"],
            ["", "", ""],
            ["保存位置", "output/earnings_monitor_YYYYMMDD.csv", ""],
            ["", "", ""],
            ["建议运行时间", "每日 18:00 后", "公告一般在收盘后披露"],
            ["", "", ""],
            ["⚠ 注意", "", ""],
            ["", "预告 > 快报", "预告20日胜率62.8%，快报29.1%"],
            ["", "20日是有效期", "60日后超额转负"],
            ["", "不迷信高增速", "20~50% 档最好"],
            ["", "逐个核实", "是否一次性收益、低基数"],
        ], columns=["项目", "命令/内容", "说明"])
        s5.to_excel(w, sheet_name="5_监控方案", index=False)
        W(w, "5_监控方案", [16, 44, 36])

        # 6 今日15家明细
        if len(A):
            s6 = A.copy()
            s6.to_excel(w, sheet_name="6_今日明细", index=False)
            W(w, "6_今日明细", [10, 12, 8, 8, 10, 9, 9, 9, 10, 9, 10, 10])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
