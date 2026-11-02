# -*- coding: utf-8 -*-
"""生成中性化研究报告。"""
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
    nf = newest("neutral_results_*.csv")
    mf = newest("market_neutral_*.csv")
    cf = newest("factor_corr_neu_*.csv")
    ff = newest("fund_results_*.csv")
    N = pd.read_csv(nf) if nf else pd.DataFrame()
    M = pd.read_csv(mf) if mf else pd.DataFrame()
    C = pd.read_csv(cf) if cf else pd.DataFrame()
    F = pd.read_csv(ff) if ff else pd.DataFrame()

    xlsx = HERE / "中性化与市场中性报告_20261015.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        # 1 摘要
        s1 = pd.DataFrame([
            ["报告日期", "2026-10-15"],
            ["目标", "① 行业中性化 ② 多因子组合 ③ 市场中性（对冲beta）"],
            ["", ""],
            ["★ 核心结论", "对冲掉市场beta后，所有策略的超额收益都是负的"],
            ["", ""],
            ["  关键数字", ""],
            ["    池内等权基准", "CAGR +4.34%（这是小盘股beta）"],
            ["    最好策略（负债下降）", "多头 +5.26%，超额 -0.55%"],
            ["    结论", "策略的收益几乎全部来自beta，不是选股alpha"],
            ["", ""],
            ["★ 行业中性化结果", ""],
            ["    负债下降", "CAGR +5.26%  Sharpe 0.25（原+6.15%/0.28，基本保持）"],
            ["    高流动比", "CAGR +1.83%  Sharpe 0.15（原+2.05%/0.16）"],
            ["    高资产周转", "CAGR +0.64%  Sharpe 0.10（原+4.87%/0.24，明显下降）"],
            ["    高毛利率", "CAGR -4.55%（原+2.33%，转为负）"],
            ["", ""],
            ["★ 多因子组合", ""],
            ["    3因子等权", "CAGR +0.54%  Sharpe 0.09"],
            ["    5因子等权", "CAGR -4.18%  Sharpe -0.03"],
            ["    6因子等权", "CAGR -5.29%  Sharpe -0.12"],
            ["    发现", "组合劣于最好的单因子（负债下降 +5.26%）"],
            ["    原因", "因子等权稀释了最强信号，且弱因子拖累"],
            ["", ""],
            ["★ 市场中性", ""],
            ["    全部11个组合", "超额CAGR均为负（-0.55% 到 -9.93%）"],
            ["    超额Sharpe最高", "0.09（负债下降）"],
            ["    超额回撤", "-63% 到 -83%"],
            ["", ""],
            ["最重要的判断", "这批因子无法产生稳定的选股alpha"],
            ["", ""],
            ["但有一个例外", "「负债下降」是唯一超额接近0的策略（-0.55%）"],
            ["  含义", "它是唯一可能有微弱选股能力的因子"],
        ], columns=["项目", "说明"])
        s1.to_excel(w, sheet_name="1_结论摘要", index=False)
        width(w, "1_结论摘要", [22, 88])

        # 2 行业中性化对比
        if len(N) and len(F):
            fb = F.sort_values("Sharpe", ascending=False).groupby(
                "策略", as_index=False).first()
            mapn = {
                "负债下降": "负债下降", "高流动比": "高流动比",
                "高资产周转": "高资产周转", "高毛利率": "高毛利率",
                "现金流增速": "现金流增速", "高ROA": "高ROA",
                "高营收增速": "高营收增速", "现金流质量": "现金流质量",
            }
            rows = []
            for _, r in N.iterrows():
                if r["策略"] not in mapn:
                    continue
                o = fb[fb["策略"] == mapn[r["策略"]]]
                rows.append({
                    "策略": r["策略"],
                    "原CAGR%": round(o.iloc[0]["CAGR%"], 2) if len(o) else None,
                    "中性CAGR%": r["CAGR%"],
                    "CAGR变化": round(r["CAGR%"] - o.iloc[0]["CAGR%"], 2) if len(o) else None,
                    "原Sharpe": round(o.iloc[0]["Sharpe"], 3) if len(o) else None,
                    "中性Sharpe": r["Sharpe"],
                    "Sharpe变化": round(r["Sharpe"] - o.iloc[0]["Sharpe"], 3) if len(o) else None,
                })
            s2 = pd.DataFrame(rows).sort_values("中性Sharpe", ascending=False)
            s2.to_excel(w, sheet_name="2_行业中性化对比", index=False)
            width(w, "2_行业中性化对比", [16, 11, 12, 11, 11, 12, 12])

        # 3 因子相关性
        if len(C):
            C.reindex(C["相关"].abs().sort_values(ascending=False).index).to_excel(
                w, sheet_name="3_因子相关性", index=False)
            width(w, "3_因子相关性", [22, 22, 10])

        # 4 多因子组合
        if len(N):
            combos = N[N["策略"].str.contains("因子", na=False)]
            if len(combos):
                combos.to_excel(w, sheet_name="4_多因子组合", index=False)
                width(w, "4_多因子组合", [14, 10, 8, 9, 8, 9, 11, 8, 8])

        # 5 市场中性
        if len(M):
            M.to_excel(w, sheet_name="5_市场中性", index=False)
            width(w, "5_市场中性", [16, 11, 12, 11, 11, 12, 13, 11, 12, 12])

        # 6 解读
        s6 = pd.DataFrame([
            ["基准是什么", "池内等权（30-80亿全部成员，每日再平衡）",
             "CAGR +4.34%"],
            ["为什么基准这么高", "2015-2026小盘股整体上涨（含壳价值+流动性溢价）", ""],
            ["超额怎么算", "超额 = 多头日收益 - 基准日收益", ""],
            ["", "", ""],
            ["全部为负说明什么", "", ""],
            ["  1", "策略选出的股票并不比池内平均水平好", ""],
            ["  2", "之前的正收益来自小盘股beta", ""],
            ["  3", "真正的选股能力（alpha）不存在或为负", ""],
            ["", "", ""],
            ["唯一接近0的", "「负债下降」超额 -0.55%", "微弱信号"],
            ["", "", ""],
            ["行业中性化的影响", "", ""],
            ["  负债下降", "+6.15% → +5.26%（稳健）", "说明不是行业暴露"],
            ["  高资产周转", "+4.87% → +0.64%（大幅下降）",
             "原收益主要来自行业暴露"],
            ["  高毛利率", "+2.33% → -4.55%（转负）", "同上"],
        ], columns=["项目", "说明", "数据"])
        s6.to_excel(w, sheet_name="6_关键解读", index=False)
        width(w, "6_关键解读", [20, 56, 20])

        # 7 下一步
        s7 = pd.DataFrame([
            ["1", "停止在30-80亿池内找alpha",
             "该池beta太高(+4.34%)，且无因子能稳定战胜"],
            ["2", "缩小股票池",
             "β太高的池里，alpha容易被淹没；可试更小市值或特定行业"],
            ["3", "换基准",
             "与中证1000/2000指数比，而非池内等权（后者beta更高）"],
            ["4", "加入真实对冲",
             "做空股指期货（IF/IC）才能真正隔离beta"],
            ["5", "关注「负债下降」",
             "唯一超额接近0的因子；可单独深挖（如结合其他财务改善信号）"],
            ["6", "降低换手",
             "年交易300笔左右，成本约0.15%单边，年化拖累约4-5%"],
            ["7", "接受结论",
             "小盘股池内，公开财务因子难以产生稳定alpha"],
        ], columns=["优先级", "建议", "理由"])
        s7.to_excel(w, sheet_name="7_下一步", index=False)
        width(w, "7_下一步", [8, 26, 60])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
