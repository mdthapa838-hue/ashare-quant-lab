# -*- coding: utf-8 -*-
"""生成最终修正报告（Excel）。"""
from __future__ import annotations
import os, sys, glob, time
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
    dp = newest("dynamic_pool_compare_*.csv")
    wf = newest("walkforward_*.csv")
    dsr = newest("dsr_result_*.csv")
    rg = newest("mktcap_range_*.csv")
    DP = pd.read_csv(dp) if dp else pd.DataFrame()
    WF = pd.read_csv(wf) if wf else pd.DataFrame()
    DSR = pd.read_csv(dsr) if dsr else pd.DataFrame()

    xlsx = HERE / "最终验证报告_20261015.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        # 1 结论
        s1 = pd.DataFrame([
            ["报告日期", "2026-10-15"],
            ["验证目标", "回答「距MA60乖离因子的17.60% CAGR是否为真」"],
            ["", ""],
            ["★ 最终结论", "策略在所有完全修正的口径下均为负收益，17.60% 不成立"],
            ["", ""],
            ["修正前（有偏差）", "CAGR 17.60%  Sharpe 0.75  回撤 -32.94%  （静态池+无退市股）"],
            ["修正后（完全）", "CAGR -5.46%  Sharpe -0.32  回撤 -80.51% （动态池+退市股）"],
            ["差异", "23.06pct/年"],
            ["", ""],
            ["已完成的修正", ""],
            ["① 退市股纳入", "339只退市股，下载257只历史K线并纳入回测"],
            ["② 动态股票池", "47个季度末市值快照，每个交易日用「当时」市值重建池"],
            ["③ 上市时间过滤", "新股上市满120个交易日后才可入选"],
            ["④ DSR多重检验", "按266次试验校正，Sharpe 0.75 的期望最大值是 2.02"],
            ["⑤ Walk-forward", "10个滚动窗口，仅 4/10 正收益，平均 CAGR -5.42%"],
            ["", ""],
            ["关键发现", ""],
            ["  静态池的魔力", "用今天的市值筛池 → 自动选中「历史涨了很多倍」的股票"],
            ["  量化影响", "同一策略 静态池 +9.01% vs 动态池 -5.46%"],
            ["  多重检验", "114组因子测试中挑最优 → Sharpe 必须打折，DSR 全部不通过"],
            ["", ""],
            ["为什么之前是正的", ""],
            ["  1. 未来函数", "月线用当月未结束的月末价（已修复：52.29% → 3.05%）"],
            ["  2. 股池板块偏差", "代码排序取前段，科创板37.5%（已修复）"],
            ["  3. 静态池幸存者", "用今天市值回看历史（本次修复）"],
            ["  4. 退市股缺失", "本策略专买下跌趋势股，正是退市高危群体（本次修复）"],
            ["", ""],
            ["建议", "不要实盘。该策略的收益全部来自数据偏差，不是真实 alpha。"],
        ], columns=["项目", "说明"])
        s1.to_excel(w, sheet_name="1_最终结论", index=False)
        width(w, "1_最终结论", [22, 88])

        # 2 四口径对比
        if len(DP):
            DP.to_excel(w, sheet_name="2_四口径对比", index=False)
            width(w, "2_四口径对比", [24, 10, 9, 8, 11, 8, 9, 8, 11, 11])

        # 3 市值区间敏感性
        if rg is not None and rg != "":
            try:
                R = pd.read_csv(rg)
                R.to_excel(w, sheet_name="3_市值区间", index=False)
                width(w, "3_市值区间", [14, 10, 8, 10, 8, 11, 8, 10])
            except Exception:
                pass

        # 4 DSR
        if len(DSR):
            DSR.to_excel(w, sheet_name="4_DSR多重检验", index=False)
            width(w, "4_DSR多重检验", [8, 8, 11, 9, 10, 20, 11, 12])
            s4 = pd.DataFrame([
                ["试验次数", 266, "19因子 × 14方向"],
                ["观测 Sharpe", 0.75, "修正前的「距MA60」组合"],
                ["零假设下期望最大 Sharpe", 2.02, "266次随机搜索的期望最大值"],
                ["DSR 概率", 0.0, "真实Sharpe>0的概率"],
                ["Bonferroni 阈值", 0.000188, "0.05/266"],
                ["判定", "✗ 全部不通过", "观测值远低于多重检验门槛"],
                ["", "", ""],
                ["解读", "在266次试验中，即使策略完全无效，",
                 "期望也能找到 Sharpe 2.02 的最好结果。"],
                ["", "观测到的 0.75 远低于这个门槛，",
                 "说明结果完全可以由随机搜索解释。"],
            ], columns=["项目", "数值", "说明"])
            s4.to_excel(w, sheet_name="5_DSR解读", index=False)
            width(w, "5_DSR解读", [24, 20, 50])

        # 6 Walk-forward
        if len(WF):
            TE = WF[WF["段"] == "测试"]
            if len(TE):
                TE.to_excel(w, sheet_name="6_WalkForward明细", index=False)
                width(w, "6_WalkForward明细", [8, 8, 12, 12, 12, 12, 9, 8, 9, 8, 10, 8])

                rows = []
                for cfgname in TE["配置"].unique():
                    sub = TE[TE["配置"] == cfgname]
                    c = sub["CAGR%"].dropna()
                    s = sub["Sharpe"].dropna()
                    rows.append({
                        "配置": cfgname,
                        "窗口数": len(c),
                        "平均CAGR%": round(c.mean(), 2) if len(c) else None,
                        "中位CAGR%": round(c.median(), 2) if len(c) else None,
                        "最差CAGR%": round(c.min(), 2) if len(c) else None,
                        "最好CAGR%": round(c.max(), 2) if len(c) else None,
                        "正收益窗口": f"{int((c>0).sum())}/{len(c)}" if len(c) else None,
                        "平均Sharpe": round(s.mean(), 2) if len(s) else None,
                        "正Sharpe窗口": f"{int((s>0).sum())}/{len(s)}" if len(s) else None,
                        "总交易": int(sub["交易"].sum()),
                    })
                pd.DataFrame(rows).to_excel(w, sheet_name="7_WalkForward汇总",
                                            index=False)
                width(w, "7_WalkForward汇总",
                      [20, 8, 11, 11, 11, 11, 12, 11, 13, 9])

        # 8 偏差来源拆解
        s8 = pd.DataFrame([
            ["1", "月线未来函数", "用当月未结束的月末收盘价算均线",
             "52.29% → 3.05%", "-49.24", "已修复"],
            ["2", "均线截断bug", "数据截断时把末行当月末",
             "8/18一致 → 18/18", "—", "已修复"],
            ["3", "股池板块偏差", "代码排序取前段，科创板37.5%",
             "创业板信号 0→10", "—", "已修复"],
            ["4", "静态池幸存者", "用今天市值回看历史",
             "+9.01% → -5.46%", "-14.47", "已修复"],
            ["5", "退市股缺失", "257只退市股未纳入",
             "影响较小", "~-0.1", "已修复"],
            ["6", "多重检验", "114组测试挑最优",
             "DSR全部不通过", "—", "已量化"],
            ["7", "上市时间", "新股无过滤",
             "影响可忽略", "0.00", "已修复"],
        ], columns=["序号", "偏差来源", "机制", "影响", "CAGR影响pct", "状态"])
        s8.to_excel(w, sheet_name="8_偏差来源拆解", index=False)
        width(w, "8_偏差来源拆解", [6, 18, 30, 20, 13, 10])

        # 9 修正历程
        s9 = pd.DataFrame([
            ["1", "原始回测", "52.29%", "2.02", "-21.32%", "含未来函数 + 有偏股池"],
            ["2", "修月线未来函数", "3.05%", "0.16", "-53.98%", "同一有偏股池"],
            ["3", "修股池板块偏差", "-3.40%", "-0.14", "-62.72%", "分层随机股池"],
            ["4", "修基础三条件", "7.93%", "0.35", "-60.38%", "分层池 + 无未来函数"],
            ["5", "加距MA60因子", "17.60%", "0.75", "-32.94%", "静态池（仍有偏差）"],
            ["6", "动态池 + 退市股", "-5.46%", "-0.32", "-80.51%", "完全修正"],
        ], columns=["版本", "阶段", "CAGR%", "Sharpe", "最大回撤%", "说明"])
        s9.to_excel(w, sheet_name="9_修正历程", index=False)
        width(w, "9_修正历程", [6, 20, 10, 9, 11, 30])

        # 10 教训
        s10 = pd.DataFrame([
            ["1", "回测收益率异常高时，第一反应应该是找 bug，不是高兴"],
            ["2", "任何「用今天的名单回看历史」的池子都有幸存者偏差"],
            ["3", "指数的「当前成分」不是历史成分，必须用 PIT 数据"],
            ["4", "月线/周线重采样极易引入未来函数（未结束的周期不可用）"],
            ["5", "周期是否结束应由交易日历判定，不能用数据末行"],
            ["6", "测了 N 组参数挑最优，Sharpe 必须按 sqrt(log N) 打折"],
            ["7", "低于「期望最大 Sharpe」的观测值，无法排除随机搜索"],
            ["8", "买卖「下跌趋势 + 低位」的股票，恰好是退市高危群体"],
            ["9", "静态池会系统性选中「历史涨幅大」的股票（本质是未来信息）"],
            ["10", "Walk-forward 比单次时间切分严格得多，应为默认验证方式"],
        ], columns=["序号", "教训"])
        s10.to_excel(w, sheet_name="10_教训", index=False)
        width(w, "10_教训", [6, 90])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
