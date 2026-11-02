# -*- coding: utf-8 -*-
"""生成基本面因子研究报告。"""
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
    rf = newest("fund_results_*.csv")
    df = newest("fund_dsr_*.csv")
    pq = newest("lab_results_*.csv")
    R = pd.read_csv(rf) if rf else pd.DataFrame()
    D = pd.read_csv(df) if df else pd.DataFrame()
    PQ = pd.read_csv(pq) if pq else pd.DataFrame()

    xlsx = HERE / "基本面因子研究报告_20261015.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        # 1 摘要
        s1 = pd.DataFrame([
            ["报告日期", "2026-10-15"],
            ["目标", "在 PIT 正确的前提下测试基本面因子是否优于价量因子"],
            ["", ""],
            ["★ 核心结论", "基本面因子明显优于价量因子，但仍未通过 DSR 门槛"],
            ["", ""],
            ["  正收益比例", ""],
            ["    价量因子（20个）", "1/20 = 5%"],
            ["    基本面因子（26个）", "13/26 = 50%"],
            ["", ""],
            ["  最优策略", ""],
            ["    负债下降", "CAGR +6.15%  Sharpe 0.28  回撤 -75.11%"],
            ["    高资产周转", "CAGR +4.87%  Sharpe 0.24  回撤 -63.36%"],
            ["    成长+现金流", "CAGR +4.42%  Sharpe 0.22  回撤 -57.91%"],
            ["", ""],
            ["  DSR 判定", "84次试验中 0 次通过"],
            ["    门槛", "零假设期望最大 Sharpe = 1.7456"],
            ["    观测最大", "Sharpe = 0.28"],
            ["    差距", "-1.47"],
            ["", ""],
            ["★ 数据质量", ""],
            ["  PIT 正确性", "已验证：财务数据按 ann_date 生效"],
            ["  公告滞后", "平均 55 天（最小29/中位45/最大119）"],
            ["  覆盖率", "99.5%（4441只 + 4199只K线）"],
            ["", ""],
            ["★ 与其他因子族对比", ""],
            ["  价量因子", "1/20 正收益，最大Sharpe 0.15"],
            ["  基本面因子", "13/26 正收益，最大Sharpe 0.28"],
            ["", ""],
            ["结论", "基本面方向有价值（50%正收益 vs 5%），但绝对收益仍不足以覆盖风险"],
        ], columns=["项目", "说明"])
        s1.to_excel(w, sheet_name="1_结论摘要", index=False)
        width(w, "1_结论摘要", [22, 86])

        # 2 排名
        if len(R):
            R.to_excel(w, sheet_name="2_策略排名", index=False)
            width(w, "2_策略排名", [18, 14, 10, 8, 9, 8, 9, 13, 8, 8])

        # 3 因子族对比
        families = {
            "盈利质量": ["高ROE", "高扣非ROE", "高ROA", "高ROIC", "高毛利率",
                        "高净利率", "ROE稳定", "低ROE波动"],
            "成长": ["高营收增速", "高净利增速", "高扣非增速", "高营业利润增速",
                    "持续成长"],
            "现金流": ["现金流质量", "高现金流", "现金流增速"],
            "应计": ["低应计"],
            "安全": ["低负债", "高流动比", "负债下降"],
            "趋势": ["毛利改善", "ROE改善"],
            "周转": ["高资产周转"],
            "组合": ["质量组合", "质量+低负债", "成长+现金流", "GARP组合",
                    "高质量低估值"],
        }
        rows = []
        for fam, names in families.items():
            sub = R[R["策略"].isin(names)] if len(R) else pd.DataFrame()
            if len(sub) == 0:
                continue
            rows.append({
                "因子族": fam, "策略数": len(sub),
                "平均CAGR%": round(sub["CAGR%"].mean(), 2),
                "平均Sharpe": round(sub["Sharpe"].mean(), 3),
                "最优策略": sub.loc[sub["Sharpe"].idxmax(), "策略"],
                "最优Sharpe": round(sub["Sharpe"].max(), 3),
                "正收益数": int((sub["CAGR%"] > 0).sum()),
            })
        s3 = pd.DataFrame(rows).sort_values("平均Sharpe", ascending=False)
        s3.to_excel(w, sheet_name="3_因子族对比", index=False)
        width(w, "3_因子族对比", [12, 8, 11, 11, 16, 11, 10])

        # 4 与价量对比
        if len(R) and len(PQ):
            pb = PQ.sort_values("Sharpe", ascending=False).groupby(
                "策略", as_index=False).first()
            s4 = pd.DataFrame([
                ["策略数", len(pb), len(R)],
                ["正收益数", int((pb["CAGR%"] > 0).sum()),
                 int((R["CAGR%"] > 0).sum())],
                ["正收益比例", f"{int((pb['CAGR%']>0).sum())/len(pb)*100:.0f}%",
                 f"{int((R['CAGR%']>0).sum())/len(R)*100:.0f}%"],
                ["最大CAGR%", round(pb["CAGR%"].max(), 2),
                 round(R["CAGR%"].max(), 2)],
                ["最大Sharpe", round(pb["Sharpe"].max(), 3),
                 round(R["Sharpe"].max(), 3)],
                ["平均Sharpe", round(pb["Sharpe"].mean(), 3),
                 round(R["Sharpe"].mean(), 3)],
                ["最好策略", pb.loc[pb["Sharpe"].idxmax(), "策略"],
                 R.loc[R["Sharpe"].idxmax(), "策略"]],
            ], columns=["指标", "价量因子", "基本面因子"])
            s4.to_excel(w, sheet_name="4_价量对比", index=False)
            width(w, "4_价量对比", [16, 20, 20])

        # 5 DSR
        if len(D):
            D.to_excel(w, sheet_name="5_DSR明细", index=False)
            width(w, "5_DSR明细", [18, 14, 8, 11, 8, 10, 14, 10, 12])
            s5 = pd.DataFrame([
                ["试验次数 N", 84, "26策略 × 3出场所（完整）/ 8×3（快速）"],
                ["观测最大 Sharpe", 0.28, "「负债下降」H20/S10"],
                ["零假设期望最大 Sharpe", 1.7456, "84次随机搜索的期望最大值"],
                ["最大 DSR 概率", 0.0000, "全部不通过"],
                ["Bonferroni 阈值", 0.000641, "0.05/78"],
                ["Bonferroni 通过", 0, "—"],
                ["BH-FDR 通过", 0, "—"],
                ["", "", ""],
                ["解读", "", ""],
                ["  门槛含义", "84次试验中即使策略完全无效，",
                 "期望也能找到 Sharpe 1.75 的最好结果"],
                ["  观测值", "0.28 远低于门槛，",
                 "无法排除随机搜索解释"],
                ["  但", "相比价量因子（0.15），基本面更接近但仍有差距", ""],
            ], columns=["项目", "数值", "说明"])
            s5.to_excel(w, sheet_name="6_DSR解读", index=False)
            width(w, "6_DSR解读", [22, 18, 48])

        # 7 方法论
        s7 = pd.DataFrame([
            ["1", "PIT 正确性", "财务数据严格按 ann_date（公告日）生效，平均滞后55天"],
            ["2", "去重规则", "同一报告期取最早公告（避免用修订后数据）"],
            ["3", "因子标准化", "所有因子转为当日池内横截面分位"],
            ["4", "动态股票池", "每交易日用当时市值重建（47个快照）"],
            ["5", "退市股纳入", "257只退市股历史数据"],
            ["6", "数据覆盖", "99.5%（4441只财务 + 4199只K线）"],
            ["7", "成交约束", "T收盘信号 → T+1开盘；涨停不买跌停不卖"],
            ["8", "多重检验", "DSR按84次试验打折"],
            ["9", "出场全测", "3组出场参数（H20/S10、H60/S15、H60/S15/P10）"],
            ["10", "因子族分类", "盈利质量/成长/现金流/应计/安全/趋势/周转/组合"],
        ], columns=["序号", "方法", "说明"])
        s7.to_excel(w, sheet_name="7_方法论", index=False)
        width(w, "7_方法论", [6, 16, 76])

        # 8 下一步
        s8 = pd.DataFrame([
            ["1", "基本面方向有价值",
             "13/26正收益 vs 价量1/20；最好策略「负债下降」+6.15%"],
            ["2", "但仍未过 DSR",
             "门槛1.75，观测0.28；需更强因子或更低试验数"],
            ["3", "最优因子族",
             "「安全」（负债下降/高流动比）+ 「周转」+ 「现金流」表现最好"],
            ["4", "可深挖方向", ""],
            ["  a", "组合因子优化", "选3-5个正交因子加权，而非单因子或二元组合"],
            ["  b", "行业中性化", "当前未做行业中性，可能混入行业beta"],
            ["  c", "市值中性化", "30-80亿内再分层"],
            ["  d", "市场中性", "做空指数对冲beta，可能看到真实alpha"],
            ["5", "降低试验数",
             "DSR门槛随试验数上升；应预先指定少数假设而非穷举"],
            ["6", "出场优化",
             "基本面因子适合更长持有期（H60/S15优于H20/S10）"],
            ["7", "加入事件因子",
             "业绩预告/快报、分析师上调、股东增持等"],
        ], columns=["优先级", "建议", "理由"])
        s8.to_excel(w, sheet_name="8_下一步", index=False)
        width(w, "8_下一步", [8, 20, 68])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
