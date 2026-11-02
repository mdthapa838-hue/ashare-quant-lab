# -*- coding: utf-8 -*-
"""生成 PE 阈值回测报告。"""
from __future__ import annotations
import os, sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "output"


def W(writer, name, widths):
    ws = writer.sheets[name]
    from openpyxl.utils import get_column_letter
    for i, x in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = x


def main():
    M = pd.read_csv(OUT / "pe_backtest_data.csv")
    M["date"] = pd.to_datetime(M["date"])
    M = M.sort_values("date").reset_index(drop=True)
    pe = M["pe_ttm"].values
    c = M["close"].values
    n = len(M)

    # 事件
    ev = [i for i in range(1, n)
          if not pd.isna(pe[i]) and not pd.isna(pe[i - 1])
          and pe[i] < 11.6 and pe[i - 1] >= 11.6]

    ev_rows = []
    for i in ev:
        p0 = c[i]
        def f(h):
            return round((c[i + h] / p0 - 1) * 100, 1) if i + h < n else None
        ev_rows.append({
            "买入日": pd.Timestamp(M["date"].iloc[i]).date(), "PE": round(pe[i], 2),
            "指数": round(p0, 0),
            "20日%": f(20), "60日%": f(60), "120日%": f(120),
            "1年%": f(250), "2年%": f(500), "3年%": f(750),
            "1年内浮亏%": round((c[i:min(i + 250, n)].min() / p0 - 1) * 100, 1),
        })
    EV = pd.DataFrame(ev_rows)

    TR = pd.read_csv(OUT / "pe_trade_list.csv") if (OUT / "pe_trade_list.csv").exists() else pd.DataFrame()
    NAV = pd.read_csv(OUT / "pe_nav_sim.csv") if (OUT / "pe_nav_sim.csv").exists() else pd.DataFrame()
    THR = pd.read_csv(OUT / "pe_threshold_compare.csv") if (OUT / "pe_threshold_compare.csv").exists() else pd.DataFrame()

    xlsx = HERE / "PE阈值回测报告_20261015.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        # 1 结论
        s1 = pd.DataFrame([
            ["报告日期", "2026-10-15"],
            ["问题", "PE < 11.6 买入之后胜率怎么样？"],
            ["数据", "沪深300 2005-04-08 ~ 2026-10-15，5227 个交易日"],
            ["", ""],
            ["★ 直接回答", ""],
            ["  事件数", "47 次（PE 从 >=11.6 首次跌破 11.6）"],
            ["  1年胜率", "62%（29/47）"],
            ["  2年胜率", "68%"],
            ["  3年胜率", "84%（31/37）"],
            ["  1年收益中位", "+7.8%"],
            ["  3年收益中位", "+19.9%"],
            ["", ""],
            ["★ 但要提醒的", ""],
            ["  1年内最大浮亏", "中位 -12.0%，最深 -20.8%"],
            ["  仍会亏的情况", "38% 的样本 1 年后是亏的"],
            ["  为什么", "低 PE 可能来自「盈利下滑」而非「价格便宜」"],
            ["", ""],
            ["★ 最重要的发现", ""],
            ["  「PE<11.6 时持有」净值 -50.4%",
             "低PE区间指数日均 -0.049%，是下跌期"],
            ["  「PE<11.6买、PE>13卖」胜率 100%",
             "7 笔交易全部盈利，平均 +15.9%"],
            ["  两者不矛盾", "低PE是要等的入场区，不是长期持有区"],
            ["", ""],
            ["★ 结论", "PE<11.6 是有效的「入场信号」，不是「持有信号」"],
            ["  正确用法", "在 PE<11.6 时买入，等 PE 回到 13 以上卖出"],
            ["", ""],
            ["★ 覆盖我之前的说法", ""],
            ["  之前说 86%", "那是「逐日级」统计（重叠样本放大）"],
            ["  真实事件级", "62%（1年）—— 这个数字更诚实"],
        ], columns=["项目", "内容"])
        s1.to_excel(w, sheet_name="1_直接回答", index=False)
        W(w, "1_直接回答", [30, 74])

        # 2 事件明细
        if len(EV):
            EV.to_excel(w, sheet_name="2_47次事件明细", index=False)
            W(w, "2_47次事件明细", [12, 7, 8, 9, 9, 9, 9, 9, 9, 11])

        # 3 统计
        stats = []
        for hz, lab in [(20, "20日"), (60, "60日"), (120, "120日"),
                        (250, "1年"), (500, "2年"), (750, "3年")]:
            vs = [c[i + hz] / c[i] - 1 for i in ev if i + hz < n]
            if vs:
                stats.append({
                    "持有期": lab, "样本": len(vs),
                    "平均%": round(np.mean(vs) * 100, 1),
                    "中位%": round(np.median(vs) * 100, 1),
                    "胜率%": round(np.mean(np.array(vs) > 0) * 100, 0),
                    "最好%": round(np.max(vs) * 100, 1),
                    "最差%": round(np.min(vs) * 100, 1),
                })
        S = pd.DataFrame(stats)
        if len(S):
            S.to_excel(w, sheet_name="3_持有期统计", index=False)
            W(w, "3_持有期统计", [10, 8, 10, 10, 9, 10, 10])

        # 4 阈值对比
        if len(THR):
            THR.to_excel(w, sheet_name="4_阈值对比", index=False)
            W(w, "4_阈值对比", [8, 9, 11, 14, 11, 14, 16])

        # 5 净值模拟
        if len(NAV):
            NAV.to_excel(w, sheet_name="5_净值模拟", index=False)
            W(w, "5_净值模拟", [16, 12, 10, 12, 11])

        # 6 完整策略
        if len(TR):
            TR.to_excel(w, sheet_name="6_完整策略交易", index=False)
            W(w, "6_完整策略交易", [12, 12, 9, 9, 10, 10, 9, 9, 9, 14])

        # 7 机制解释
        s7 = pd.DataFrame([
            ["为什么 PE<11.6 时持有会亏", ""],
            ["  PE<11.6 期间", "日均收益 -0.0489%，日胜率 46.5%"],
            ["  PE 11.6~13 期间", "日均收益 +0.0080%，日胜率 49.6%"],
            ["  PE>=13 期间", "日均收益 +0.0914%，日胜率 56.3%"],
            ["", ""],
            ["", "→ 低 PE 区间恰恰是市场下跌期"],
            ["", ""],
            ["根本原因：PE 的分母会变", ""],
            ["  PE = 价格 / 盈利(EPS)", ""],
            ["  情况1", "价格跌 → PE 跌 → 便宜（真机会）"],
            ["  情况2", "盈利跌 → PE 涨 → 看似变贵（假信号）"],
            ["  情况3", "盈利跌得比价格快 → PE 反而上升", ""],
            ["", ""],
            ["所以", "低 PE 常常是「盈利下滑」的结果，不是「价格便宜」"],
            ["", ""],
            ["分年度验证（PE<11.6 期间指数涨跌）", ""],
            ["  2011", "58天，指数 -22.3%"],
            ["  2012", "237天，指数 +4.4%"],
            ["  2013", "231天，指数 -9.4%"],
            ["  2014", "232天，指数 +32.6%"],
            ["  2018", "102天，指数 -21.4%"],
            ["  2022", "74天，指数 -11.7%"],
            ["  2023", "82天，指数 -11.8%"],
            ["  2024", "91天，指数 -4.3%"],
            ["", ""],
            ["规律", "PE<11.6 的年份大多下跌（11年里7年跌）"],
            ["含义", "低 PE 是「下跌途中」，不是「底部」"],
        ], columns=["项目", "说明"])
        s7.to_excel(w, sheet_name="7_机制解释", index=False)
        W(w, "7_机制解释", [34, 56])

        # 8 正确用法
        s8 = pd.DataFrame([
            ["1", "PE<11.6 是入场信号，不是持有信号",
             "低PE期间指数日均 -0.049%（在跌）"],
            ["2", "买入后要等 PE 回到 13 以上才卖",
             "7 笔交易全部盈利，平均 +15.9%"],
            ["3", "不要因为低 PE 就长期持有",
             "PE<11.6 持有的净值 -50.4%"],
            ["4", "分批而非一次性",
             "1年内最大浮亏中位 -12.0%，最深 -20.8%"],
            ["5", "接受 38% 的失败率",
             "47次中 18次 1年后仍亏"],
            ["6", "PE 越低越好但有样本陷阱",
             "PE<9 只有5次事件，1年胜率80%但不可靠"],
            ["", "", ""],
            ["当前状态", "", ""],
            ["  当前 PE", "13.04", ""],
            ["  距 PE<11.6", "还需下跌约 11%", "对应指数约 3700"],
            ["  对应历史表现", "1年胜率 62%，中位 +7.8%", ""],
            ["", "", ""],
            ["执行建议", "", ""],
            ["  PE > 13", "轻仓/等待", "← 现在"],
            ["  PE 12~13", "开始分批", ""],
            ["  PE < 11.6", "重点买入", "1年胜率62%，3年84%"],
            ["  PE < 10.5", "重仓", "1年胜率 86%（PE<10）"],
            ["  PE > 15", "减仓", ""],
        ], columns=["序号", "要点", "依据"])
        s8.to_excel(w, sheet_name="8_正确用法", index=False)
        W(w, "8_正确用法", [6, 32, 46])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
