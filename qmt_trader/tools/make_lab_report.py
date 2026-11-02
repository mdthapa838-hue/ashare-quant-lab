# -*- coding: utf-8 -*-
"""生成策略实验室报告。"""
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
    cf = OUT / "coverage_diag.csv"
    R = pd.read_csv(rf) if rf else pd.DataFrame()
    D = pd.read_csv(df) if df else pd.DataFrame()
    C = pd.read_csv(cf) if cf.exists() else pd.DataFrame()

    xlsx = HERE / "策略实验室报告_20261015.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        # 1 摘要
        s1 = pd.DataFrame([
            ["报告日期", "2026-10-15"],
            ["测试目标", "用动态池 + Walk-forward + DSR 筛选新策略方向"],
            ["", ""],
            ["测试规模", ""],
            ["  股票池", "动态池（47个季度末市值快照，30-80亿）"],
            ["  数据", "1361 只K线（含257只退市股）"],
            ["  因子数", "29 个时序因子 → 横截面分位"],
            ["  策略数", "20 个（反转/动量/波动/量能/彩票/形态/组合）"],
            ["  出场所", "6 组（不同止损/持有期/止盈）"],
            ["  总回测", "120 次"],
            ["", ""],
            ["★ 核心结论", "20 个策略中仅 3 个正收益，且 Sharpe 都≈0"],
            ["", ""],
            ["  最好三个", ""],
            ["    接近新高+低波", "CAGR +0.42%  Sharpe 0.06  回撤 -67.27%"],
            ["    布林收口", "CAGR +0.19%  Sharpe 0.01  回撤 -71.18%"],
            ["    低彩票", "CAGR +0.25%  Sharpe 0.00  回撤 -72.66%"],
            ["", ""],
            ["  DSR 判定", "120次试验中 0 次通过（最大DSR概率 0.0000）"],
            ["  门槛", "零假设下期望最大Sharpe = 1.36"],
            ["  观测最大", "Sharpe = 0.06"],
            ["", ""],
            ["★ 关键限制", "股池覆盖率仅 30.3%（本地K线 1361 只 vs 真实池 1000-2200 只）"],
            ["  影响", "绝对收益被高估；但『全为负』的结论稳健（真实更差）"],
            ["", ""],
            ["★ 出场规则最重要", "同一策略不同出场，Sharpe 差异可达 0.5+"],
            ["  最优出场", "H20/S10/TP20（20日持有/10%止损/20%止盈）平均最优"],
            ["", ""],
            ["结论", "这批传统价量因子在小盘股上无稳定 alpha"],
        ], columns=["项目", "说明"])
        s1.to_excel(w, sheet_name="1_结论摘要", index=False)
        width(w, "1_结论摘要", [22, 86])

        # 2 策略排名
        if len(R):
            b = R.sort_values("Sharpe", ascending=False).groupby(
                "策略", as_index=False).first().sort_values("Sharpe", ascending=False)
            b.to_excel(w, sheet_name="2_策略排名", index=False)
            width(w, "2_策略排名", [20, 16, 10, 8, 9, 8, 9, 13, 8, 8])

            s2b = pd.DataFrame([
                ["反转20日", "rk_ret20", "low", "0.20", "过去20日跌幅最深20%"],
                ["反转60日", "rk_ret60", "low", "0.20", "过去60日跌幅最深20%"],
                ["反转60日(中)", "rk_ret60", "low", "0.40", "60日跌幅40%分位以下"],
                ["距高点回撤", "rk_dist_high252", "low", "0.20", "距252日高点最远20%"],
                ["动量120-20", "rk_mom_120_20", "high", "0.20", "跳过近20日的中期动量前20%"],
                ["动量250", "rk_ret250", "high", "0.20", "过去250日涨幅前20%"],
                ["接近新高", "rk_dist_high252", "high", "0.10", "距252日高点最近10%"],
                ["低波动", "rk_atr_pct", "low", "0.20", "ATR/价格最低20%"],
                ["高波动", "rk_atr_pct", "high", "0.20", "ATR/价格最高20%"],
                ["低下行波", "rk_downvol_60", "low", "0.20", "下行波动最低20%"],
                ["缩量", "rk_amt_ratio_5_20", "low", "0.20", "5日/20日成交额最低20%"],
                ["放量", "rk_amt_ratio_5_20", "high", "0.10", "5日/20日成交额最高10%"],
                ["低换手", "rk_turnover_proxy", "low", "0.20", "成交额/价格最低20%"],
                ["低彩票", "rk_max_ret_20", "low", "0.20", "20日最大单日涨幅最低20%"],
                ["高彩票", "rk_max_ret_20", "high", "0.20", "20日最大单日涨幅最高20%"],
                ["布林收口", "rk_bb_width", "low", "0.20", "布林带宽最窄20%"],
                ["布林下轨", "rk_bb_pctb", "low", "0.10", "%B最低10%"],
                ["强势收盘位置", "rk_up_ratio_20", "high", "0.20", "20日上涨天数占比最高20%"],
                ["动量+低波", "rk_mom_120_20+atr", "high+low", "0.30/0.40", "中期动量前30% 且 低波动前40%"],
                ["反转+缩量", "rk_ret20+amt", "low+low", "0.30/0.40", "20日反转最强30% 且 缩量前40%"],
                ["接近新高+低波", "rk_dist_high252+atr", "high+low", "0.20/0.40", "距新高最近20% 且 低波动前40%"],
            ], columns=["策略", "因子", "方向", "分位", "定义"])
            s2b.to_excel(w, sheet_name="3_策略定义", index=False)
            width(w, "3_策略定义", [20, 26, 12, 12, 40])

        # 4 出场对比
        if len(R):
            g = R.groupby("出场").agg(
                策略数=("策略", "nunique"), 平均CAGR=("CAGR%", "mean"),
                平均Sharpe=("Sharpe", "mean"), 平均回撤=("回撤%", "mean"),
                平均交易=("交易", "mean"), 平均胜率=("胜率%", "mean")).round(2)
            g = g.sort_values("平均Sharpe", ascending=False).reset_index()
            g.to_excel(w, sheet_name="4_出场方案对比", index=False)
            width(w, "4_出场方案对比", [16, 8, 11, 11, 11, 10, 10])

        # 5 DSR
        if len(D):
            D.to_excel(w, sheet_name="5_DSR明细", index=False)
            width(w, "5_DSR明细", [20, 8, 11, 8, 10, 20, 10, 12])
            s5 = pd.DataFrame([
                ["试验次数 N", 120, "20策略 × 6出场所"],
                ["观测最大 Sharpe", 0.06, "「接近新高+低波」"],
                ["零假设期望最大 Sharpe", 1.36, "120次随机搜索的期望最大值"],
                ["最大 DSR 概率", 0.0000, "全部不通过"],
                ["DSR>0.5 的次数", 0, "需要 >0.5 才算有证据"],
                ["Bonferroni 阈值", 0.000417, "0.05/120"],
                ["Bonferroni 通过", 4, "但都是负收益策略（t值为负）"],
                ["BH-FDR 通过", 16, "同为负收益"],
                ["", "", ""],
                ["解读", "120次试验中，即使策略完全无效，",
                 "期望也能找到 Sharpe 1.36 的最好结果。"],
                ["", "观测到的 0.06 远低于门槛，",
                 "说明结果完全可由随机搜索解释。"],
            ], columns=["项目", "数值", "说明"])
            s5.to_excel(w, sheet_name="6_DSR解读", index=False)
            width(w, "6_DSR解读", [24, 18, 46])

        # 7 覆盖率
        if len(C):
            C.to_excel(w, sheet_name="7_覆盖率诊断", index=False)
            width(w, "7_覆盖率诊断", [12, 10, 10, 10])
            s7 = pd.DataFrame([
                ["本地K线", "1361 只", "700分层样本 + 257退市 + 其他"],
                ["真实池规模", "1000-2200 只", "各时点30-80亿"],
                ["平均覆盖率", "30.3%", "2015-2026"],
                ["", "", ""],
                ["可比的部分", "", ""],
                ["  相对排序", "有效", "所有策略用同一股池"],
                ["  无未来函数", "已保证", "T收盘信号→T+1开盘"],
                ["  退市股", "已纳入", "257只"],
                ["", "", ""],
                ["不可比的部分", "", ""],
                ["  绝对收益", "被高估", "覆盖样本偏向存续股"],
                ["  原因", "涨太多离开区间的、跌太多退市的，覆盖率低"],
                ["", "", ""],
                ["结论口径", "", ""],
                ["  若受限样本下仍负", "结论稳健", "真实更差"],
                ["  若受限样本下为正", "需复核", "扩大样本"],
            ], columns=["项目", "数值", "说明"])
            s7.to_excel(w, sheet_name="8_覆盖率解读", index=False)
            width(w, "8_覆盖率解读", [22, 16, 46])

        # 9 方法论
        s9 = pd.DataFrame([
            ["1", "因子标准化", "所有因子转为「当日池内横截面分位」，避免时序阈值漂移"],
            ["2", "股票池", "每个交易日用当时市值重建（47个季度末快照 + 价格外推）"],
            ["3", "市值口径", "daily_basic快照 × 本地前复权价比值（避免复权混用）"],
            ["4", "成交约束", "T收盘信号 → T+1开盘成交；涨停不买、跌停不卖"],
            ["5", "退市处理", "退市前可交易，退市后自动移出池"],
            ["6", "上市过滤", "新股上市满120个交易日后才可入选"],
            ["7", "信号排序", "同日信号按因子强度排序，取前N（避免代码顺序任意性）"],
            ["8", "出场方案", "6组参数全测，避免单一参数误导"],
            ["9", "多重检验", "DSR按试验次数打折（120次 → 门槛Sharpe 1.36）"],
            ["10", "覆盖率披露", "明确30.3%覆盖率，界定结论适用范围"],
        ], columns=["序号", "方法", "说明"])
        s9.to_excel(w, sheet_name="9_方法论", index=False)
        width(w, "9_方法论", [6, 16, 76])

        # 10 下一步
        s10 = pd.DataFrame([
            ["1", "这批传统价量因子无稳定 alpha",
             "20策略仅3个正收益，Sharpe都≈0，DSR全部不通过"],
            ["2", "优先扩展数据覆盖",
             "当前30.3%覆盖率是最大限制；建议下载全市场30-80亿历史K线"],
            ["3", "出场规则比选股更重要",
             "同一策略不同出场，Sharpe差异达0.5+；H20/S10/TP20平均最优"],
            ["4", "换因子类型",
             "价量因子已充分测试，可试：财报因子、分析师预期、事件驱动"],
            ["5", "考虑市场中性",
             "小盘多头天然承担市场beta；做空对冲后可能看到真实alpha"],
            ["6", "降低换手",
             "年交易量在300-1400笔，成本拖累显著"],
            ["7", "保留基础设施",
             "动态池引擎 + DSR + Walk-forward 可复用到任何新策略"],
        ], columns=["优先级", "建议", "理由"])
        s10.to_excel(w, sheet_name="10_下一步", index=False)
        width(w, "10_下一步", [8, 26, 66])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
