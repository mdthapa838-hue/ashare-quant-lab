# -*- coding: utf-8 -*-
"""生成覆盖率扩展验证报告。"""
from __future__ import annotations
import os, sys, glob
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "output"


def newest(pat):
    fs = sorted(glob.glob(str(OUT / pat)), key=os.path.getmtime)
    return fs[-1] if fs else None


def width(writer, name, widths):
    ws = writer.sheets[name]
    from openpyxl.utils import get_column_letter
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def main():
    rf = newest("lab_results_*.csv")
    df = newest("lab_dsr_*.csv")
    cov = OUT / "coverage_diag.csv"
    uni = OUT / "universe_union.csv"
    R = pd.read_csv(rf) if rf else pd.DataFrame()
    D = pd.read_csv(df) if df else pd.DataFrame()
    C = pd.read_csv(cov) if cov.exists() else pd.DataFrame()
    U = pd.read_csv(uni, dtype={"code6": str}) if uni.exists() else pd.DataFrame()

    xlsx = HERE / "覆盖率扩展验证报告_20261015.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        # 1 摘要
        s1 = pd.DataFrame([
            ["报告日期", "2026-10-15"],
            ["目标", "把回测数据覆盖率从 30.3% 提升到接近 100%，并验证结论稳健性"],
            ["", ""],
            ["★ 数据扩展结果", ""],
            ["  并集计算", "2015-2026 曾进入 30-80亿 的股票：4441 只"],
            ["  新增K线", "3091 只（无数据 18 只，失败 0）"],
            ["  K线总数", "4199 只（含 257 只退市股）"],
            ["  基本面数据", "4441 只（fina_indicator，含公告日）"],
            ["  新增下载", "4421 只（失败 0）"],
            ["", ""],
            ["★ 覆盖率提升", "30.3% → 99.5%"],
            ["", ""],
            ["★ 核心结论", "覆盖率提升后，正收益策略从 3 个降到 1 个"],
            ["  原（30.3%）", "20 策略中 3 个正收益（接近新高+低波、布林收口、低彩票）"],
            ["  新（99.5%）", "20 策略中仅 1 个正收益（低彩票，Sharpe 0.15）"],
            ["  含义", "覆盖率提升后结论更严格，原「正收益」多为小样本偶然"],
            ["", ""],
            ["★ DSR 判定", "120次试验中 0 次通过"],
            ["  门槛", "零假设期望最大 Sharpe = 1.3591"],
            ["  观测最大", "Sharpe = 0.15"],
            ["  t值最高", "0.11（p=0.91）"],
            ["", ""],
            ["★ 出场方案最优", "H60/S15（60日持有 / 15%止损）平均 Sharpe -0.15"],
            ["  最差", "H60/S15/P10（10只持仓）平均 Sharpe -0.51"],
            ["", ""],
            ["结论", "这批价量因子在小盘股上无稳定 alpha，结论在全量数据下更稳固"],
        ], columns=["项目", "说明"])
        s1.to_excel(w, sheet_name="1_结论摘要", index=False)
        width(w, "1_结论摘要", [22, 86])

        # 2 覆盖率
        if len(C):
            C.to_excel(w, sheet_name="2_覆盖率", index=False)
            width(w, "2_覆盖率", [12, 10, 10, 10, 10])

        # 3 策略排名
        if len(R):
            b = R.sort_values("Sharpe", ascending=False).groupby(
                "策略", as_index=False).first().sort_values("Sharpe", ascending=False)
            b.to_excel(w, sheet_name="3_策略排名", index=False)
            width(w, "3_策略排名", [20, 16, 10, 8, 9, 8, 9, 13, 8, 8])

        # 4 新旧对比
        old = {
            "接近新高+低波": 0.06, "布林收口": 0.01, "低彩票": 0.00, "低波动": -0.04,
            "强势收盘位置": -0.12, "动量+低波": -0.17, "缩量": -0.20, "低下行波": -0.21,
            "动量120-20": -0.33, "布林下轨": -0.37, "反转+缩量": -0.37, "反转60日": -0.41,
            "反转60日(中)": -0.41, "距高点回撤": -0.50, "高彩票": -0.44,
            "反转20日": -0.44, "动量250": -0.42, "高波动": -0.51,
            "接近新高": -0.78, "放量": -0.78,
        }
        rows = []
        if len(R):
            b = R.sort_values("Sharpe", ascending=False).groupby(
                "策略", as_index=False).first()
            for _, r in b.iterrows():
                n = r["策略"]
                o = old.get(n)
                rows.append({
                    "策略": n,
                    "旧覆盖率30.3%_Sharpe": o,
                    "新覆盖率99.5%_Sharpe": round(r["Sharpe"], 2),
                    "变化": round(r["Sharpe"] - o, 2) if o is not None else None,
                    "新CAGR%": r["CAGR%"],
                    "新交易数": r["交易"],
                })
        s4 = pd.DataFrame(rows)
        if len(s4):
            s4 = s4.sort_values("新覆盖率99.5%_Sharpe", ascending=False)
            s4.to_excel(w, sheet_name="4_新旧对比", index=False)
            width(w, "4_新旧对比", [20, 18, 18, 10, 11, 10])

        # 5 出场
        if len(R):
            g = R.groupby("出场").agg(
                策略数=("策略", "nunique"), 平均CAGR=("CAGR%", "mean"),
                平均Sharpe=("Sharpe", "mean"), 平均回撤=("回撤%", "mean"),
                平均交易=("交易", "mean"), 平均胜率=("胜率%", "mean")).round(2)
            g = g.sort_values("平均Sharpe", ascending=False).reset_index()
            g.to_excel(w, sheet_name="5_出场方案", index=False)
            width(w, "5_出场方案", [16, 8, 11, 11, 11, 10, 10])

        # 6 DSR
        if len(D):
            D.to_excel(w, sheet_name="6_DSR明细", index=False)
            width(w, "6_DSR明细", [20, 8, 11, 8, 10, 20, 10, 12])

        # 7 数据资产
        s7 = pd.DataFrame([
            ["K线数据", "4199 只", "kline/ 4199 + kline_delisted/ 257", "日线前复权"],
            ["基本面", "4441 只", "fundamental/", "fina_indicator 89字段 + ann_date"],
            ["市值快照", "47 个", "hist_mktcap/", "季度末全市场 total_mv/circ_mv"],
            ["股票基础", "5913 条", "stock_basic_all.csv", "存续5574 + 退市339"],
            ["并集清单", "4441 只", "universe_union.csv", "曾进入30-80亿"],
            ["", "", "", ""],
            ["可支持的分析", "", "", ""],
            ["  价量因子", "✓", "29个时序因子→横截面分位", "已测20策略"],
            ["  基本面因子", "✓ 就绪", "ROE/毛利率/现金流/负债率/增速", "待测"],
            ["  事件因子", "可扩展", "业绩预告/快报（需另下）", "待建"],
            ["  动态股票池", "✓", "每交易日用当时市值重建", "已实现"],
            ["  DSR校正", "✓", "按试验次数打折", "已实现"],
            ["  Walk-forward", "✓", "滚动训练/测试", "已实现"],
        ], columns=["项目", "规模", "位置", "说明"])
        s7.to_excel(w, sheet_name="7_数据资产", index=False)
        width(w, "7_数据资产", [22, 12, 40, 36])

        # 8 下一步
        s8 = pd.DataFrame([
            ["1", "价量因子结论已确认",
             "在全量数据（99.5%覆盖）下，20策略仅1个正收益，DSR全不通过"],
            ["2", "转向基本面因子",
             "4441只 fina_indicator 已就绪（含公告日，PIT正确）"],
            ["3", "可测方向（学术验证过）",
             "ROE稳定性、毛利率趋势、应计项目、现金流质量、负债率"],
            ["4", "组合因子（QMJ框架）",
             "盈利质量 + 成长 + 价值 三位一体"],
            ["5", "做市场中性",
             "小盘多头承担beta；对冲后可能看到真实alpha"],
            ["6", "降低换手",
             "当前年交易 250-1400 笔，成本拖累显著"],
            ["7", "修正出场方案",
             "新数据下 H60/S15 最优（-0.15），而非之前的 H20/S10/TP20"],
        ], columns=["优先级", "建议", "理由"])
        s8.to_excel(w, sheet_name="8_下一步", index=False)
        width(w, "8_下一步", [8, 24, 68])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
