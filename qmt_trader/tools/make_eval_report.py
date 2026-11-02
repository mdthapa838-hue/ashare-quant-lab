# -*- coding: utf-8 -*-
"""生成过滤器评估 + 风控优化汇总报告。"""
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
    fe = newest("filter_eval_*.csv")
    ro = newest("risk_optimize_*.csv")
    tr = newest("trades_B0_*.csv")
    F = pd.read_csv(fe) if fe else pd.DataFrame()
    R = pd.read_csv(ro) if ro else pd.DataFrame()
    T = pd.read_csv(tr) if tr else pd.DataFrame()

    xlsx = HERE / "策略评估_过滤器与风控_20261015.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        # 1 摘要
        s1 = pd.DataFrame([
            ["评估日期", "2026-10-15"],
            ["股池", "分层随机 700 只（板块比例对齐总体）"],
            ["区间", "2015-01 ~ 2026-10（约 11.8 年）"],
            ["", ""],
            ["★ 核心结论", "「月线」和「择时」两个过滤器是负价值，应移除"],
            ["  证据", "B0基础 CAGR 8.04% → 加月线 0.24% → 加择时 0.33%"],
            ["  信号损失", "17368 → 424（月线砍 97.6%）→ 10（择时再砍）"],
            ["", ""],
            ["★ 第二个结论", "B0 相对随机入场仅 +3.21pct/年，优势不明显"],
            ["", ""],
            ["★ 第三个结论", "B0 回撤 -60.38% 是致命问题，需风控"],
            ["  最优风控", "单只上限降到 10% → 回撤 -34.82%，CAGR 5.38%"],
            ["", ""],
            ["★ 样本量", "B0 有 561 笔交易，是唯一有统计意义的配置"],
            ["  对比", "完整策略仅 8 笔交易，无统计意义"],
            ["", ""],
            ["残留偏差", "幸存者偏差 + 静态池偏差仍未修复，实际收益可能更低"],
        ], columns=["项目", "说明"])
        s1.to_excel(w, sheet_name="1_结论摘要", index=False)
        width(w, "1_结论摘要", [20, 88])

        # 2 四配置对比
        if len(F):
            F.to_excel(w, sheet_name="2_四配置对比", index=False)
            width(w, "2_四配置对比", [20, 10, 9, 8, 11, 8, 10, 8, 11, 8, 10, 11])

        # 3 分年度
        for pat, nm in [("yearly_A_B0基础三条件_*.csv", "3_分年度_B0基础"),
                        ("yearly_D_B0plus月线plus择时_*.csv", "4_分年度_完整策略")]:
            f = newest(pat)
            if f:
                d = pd.read_csv(f)
                d.to_excel(w, sheet_name=nm, index=False)
                width(w, nm, [8, 8, 11, 8, 10, 10])

        # 5 风控优化
        if len(R):
            R.to_excel(w, sheet_name="5_风控优化", index=False)
            width(w, "5_风控优化", [26, 9, 8, 9, 12, 8, 10, 9, 8, 11, 8])

        # 6 随机对照
        s6 = pd.DataFrame([
            ["B0 基础三条件", 8.04, 0.36, -60.38, 561, 54.2, "真实策略"],
            ["随机入场", 4.83, 0.24, -70.73, 1181, 59.7, "对照"],
            ["", None, None, None, None, None, ""],
            ["差异", "+3.21pct/年", "", "", "", "", "⚠ 优势不明显"],
        ], columns=["方案", "CAGR%", "Sharpe", "最大回撤%", "交易", "胜率%", "备注"])
        s6.to_excel(w, sheet_name="6_随机对照", index=False)
        width(w, "6_随机对照", [20, 11, 8, 12, 8, 8, 16])

        # 7 收益集中度
        if len(T):
            r = T["ret"].sort_values(ascending=False)
            rows = []
            for n in [0, 1, 3, 5, 10, 20, 30, 50]:
                rr = r.iloc[n:]
                if len(rr):
                    rows.append({
                        "去掉最大N笔": n, "剩余笔数": len(rr),
                        "平均收益%": round(rr.mean() * 100, 2),
                        "胜率%": round((rr > 0).mean() * 100, 1)})
            s7 = pd.DataFrame(rows)
            s7.to_excel(w, sheet_name="7_收益集中度", index=False)
            width(w, "7_收益集中度", [12, 10, 12, 10])

            byc = T.groupby("code")["ret"].agg(["size", "mean", "sum"])
            byc.columns = ["交易次数", "平均收益", "累计贡献"]
            byc = byc.sort_values("累计贡献", ascending=False).head(30)
            byc["平均收益"] = (byc["平均收益"] * 100).round(2)
            byc["累计贡献"] = (byc["累计贡献"] * 100).round(1)
            byc.to_excel(w, sheet_name="8_贡献最大标的")
            width(w, "8_贡献最大标的", [12, 10, 11, 11])

        # 9 交易明细
        if len(T):
            t = T.copy()
            for c in ("entry_date", "exit_date"):
                if c in t.columns:
                    t[c] = pd.to_datetime(t[c]).dt.strftime("%Y-%m-%d")
            if "ret" in t.columns:
                t["ret"] = (t["ret"] * 100).round(2)
            t.to_excel(w, sheet_name="9_交易明细_B0", index=False)
            width(w, "9_交易明细_B0", [12, 12, 12, 12, 12, 8, 8, 9, 7])

        # 10 建议
        s10 = pd.DataFrame([
            ["1", "移除「月线」过滤器", "它把信号砍掉 97.6% 且摧毁收益（8.04%→0.24%）"],
            ["2", "移除「择时」过滤器", "仅 10 笔交易，无统计意义，CAGR 0.33%"],
            ["3", "保留 B0 基础三条件", "561 笔交易，CAGR 8.04%，是唯一有统计意义的配置"],
            ["4", "加风控：单只上限降至 10%", "回撤从 -60.38% 改善到 -34.82%，CAGR 5.38%"],
            ["5", "考虑止损收紧至 -8%", "CAGR 提升到 11.75%，但回撤仍 -57.66%"],
            ["6", "修复残留偏差后再决定", "动态股票池 + 退市股，否则结论不可信"],
            ["7", "不要实盘", "B0 仅比随机入场好 3.21pct，扣除偏差后可能为负"],
        ], columns=["优先级", "动作", "理由"])
        s10.to_excel(w, sheet_name="10_行动建议", index=False)
        width(w, "10_行动建议", [8, 30, 70])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
