# -*- coding: utf-8 -*-
"""
step25_reality_check.py — 全轮实验总清算

把这一整轮跑过的所有策略放在同一张表里排名，加入被动基准，
回答一个问题：「到底什么在赚钱？」

包含：
  A. 全部主动策略（价量20 + 基本面26 + 中性化8 + 组合3）
  B. 全部被动基准（沪深300买入持有 / 全市场等权 / 池内等权）
  C. 择时策略（PE阈值）
  D. 成本影响测算
  E. 收益分解：beta vs alpha
"""
from __future__ import annotations

import os
import sys
import glob
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.logger import get_logger


def newest(pat, out):
    fs = sorted(glob.glob(str(out / pat)), key=os.path.getmtime)
    return fs[-1] if fs else None


def main():
    out = HERE / "output"
    print("=" * 130)
    print("  全轮实验总清算：到底什么在赚钱？")
    print("=" * 130)
    print()

    rows = []

    # ---- A. 价量策略
    f = newest("lab_results_*.csv", out)
    if f:
        d = pd.read_csv(f)
        b = d.sort_values("Sharpe", ascending=False).groupby(
            "策略", as_index=False).first()
        for _, r in b.iterrows():
            rows.append({
                "类别": "价量因子", "策略": r["策略"], "类型": "主动",
                "信号数": r.get("信号数"), "交易": r.get("交易"),
                "CAGR%": r["CAGR%"], "Sharpe": r["Sharpe"],
                "回撤%": r.get("回撤%"), "胜率%": r.get("胜率%"),
            })
    # ---- B. 基本面策略
    f = newest("fund_results_*.csv", out)
    if f:
        d = pd.read_csv(f)
        b = d.sort_values("Sharpe", ascending=False).groupby(
            "策略", as_index=False).first()
        for _, r in b.iterrows():
            rows.append({
                "类别": "基本面因子", "策略": r["策略"], "类型": "主动",
                "信号数": r.get("信号数"), "交易": r.get("交易"),
                "CAGR%": r["CAGR%"], "Sharpe": r["Sharpe"],
                "回撤%": r.get("回撤%"), "胜率%": r.get("胜率%"),
            })
    # ---- C. 行业中性化后
    f = newest("neutral_results_*.csv", out)
    if f:
        d = pd.read_csv(f)
        for _, r in d.iterrows():
            rows.append({
                "类别": "行业中性化", "策略": r["策略"], "类型": "主动",
                "信号数": r.get("信号数"), "交易": r.get("交易"),
                "CAGR%": r["CAGR%"], "Sharpe": r["Sharpe"],
                "回撤%": r.get("回撤%"), "胜率%": r.get("胜率%"),
            })

    # ---- D. 被动基准：沪深300 买入持有
    try:
        import credentials  # type: ignore
        import tushare as ts  # type: ignore
        pro = ts.pro_api(credentials.get_tushare_token())
        hs = pro.index_daily(ts_code="000300.SH", start_date="20151001",
                             end_date="20261231")
        if hs is not None and len(hs):
            hs["date"] = pd.to_datetime(hs["trade_date"], format="%Y%m%d")
            hs = hs.sort_values("date")
            c = pd.to_numeric(hs["close"], errors="coerce")
            # 2015-10 到现在的 CAGR
            yrs = (hs["date"].iloc[-1] - hs["date"].iloc[0]).days / 365.25
            tot = c.iloc[-1] / c.iloc[0] - 1
            cagr = (1 + tot) ** (1 / yrs) - 1
            r = c.pct_change().dropna()
            sharpe = r.mean() * 244 / (r.std() * np.sqrt(244))
            dd = (c / c.cummax() - 1).min()
            rows.append({
                "类别": "被动基准", "策略": "沪深300 买入持有", "类型": "被动",
                "信号数": None, "交易": 1,
                "CAGR%": round(cagr * 100, 2), "Sharpe": round(sharpe, 2),
                "回撤%": round(dd * 100, 2), "胜率%": None,
            })
    except Exception as e:
        print(f"  沪深300 基准获取失败: {str(e)[:50]}")

    # ---- E. 被动基准：全市场等权
    f = out / "market_equalweight.csv"
    if f.exists():
        EW = pd.read_csv(f)
        EW["date"] = pd.to_datetime(EW["date"])
        EW = EW.sort_values("date")
        eq = EW["eq_equity"]
        yrs = (EW["date"].iloc[-1] - EW["date"].iloc[0]).days / 365.25
        cagr = eq.iloc[-1] ** (1 / yrs) - 1
        r = EW["eq_ret"].dropna()
        sharpe = r.mean() * 244 / (r.std() * np.sqrt(244))
        dd = (eq / eq.cummax() - 1).min()
        rows.append({
            "类别": "被动基准", "策略": "全市场等权（4000+只）", "类型": "被动",
            "信号数": None, "交易": 1,
            "CAGR%": round(cagr * 100, 2), "Sharpe": round(sharpe, 2),
            "回撤%": round(dd * 100, 2), "胜率%": None,
        })

    R = pd.DataFrame(rows)
    if len(R) == 0:
        print("无数据")
        return 1

    # ---- 排名
    R = R.sort_values("CAGR%", ascending=False).reset_index(drop=True)
    print("=" * 130)
    print("  一、全部策略排名（按 CAGR）")
    print("=" * 130)
    print()
    print(f"  {'排名':<5}{'类别':<12}{'策略':<20}{'类型':<7}{'交易':>7}"
          f"{'CAGR%':>9}{'Sharpe':>9}{'回撤%':>9}")
    print("  " + "-" * 92)
    for i, r in R.iterrows():
        mark = "★" if r["类型"] == "被动" else " "
        tr = f"{int(r['交易'])}" if pd.notna(r["交易"]) else "—"
        dd = f"{r['回撤%']:.1f}" if pd.notna(r["回撤%"]) else "—"
        print(f"  {i+1:<5}{mark}{r['类别']:<11}{r['策略']:<20}{r['类型']:<7}"
              f"{tr:>7}{r['CAGR%']:>9.2f}{r['Sharpe']:>9.2f}{dd:>9}")
    print()
    print("  ★ = 被动基准（不需要任何选股能力）")
    print()

    # ---- 分类型统计
    print("=" * 130)
    print("  二、分类型统计")
    print("=" * 130)
    print()
    g = R.groupby("类别").agg(
        数量=("策略", "size"),
        CAGR中位=("CAGR%", "median"),
        CAGR最好=("CAGR%", "max"),
        Sharpe中位=("Sharpe", "median"),
        Sharpe最好=("Sharpe", "max"),
        正收益数=("CAGR%", lambda s: int((s > 0).sum())),
    ).round(2)
    g = g.sort_values("CAGR中位", ascending=False)
    print(g.to_string())
    print()

    # ---- 主动 vs 被动
    print("=" * 130)
    print("  三、主动 vs 被动")
    print("=" * 130)
    print()
    act = R[R["类型"] == "主动"]
    pas = R[R["类型"] == "被动"]
    print(f"  主动策略: {len(act)} 个")
    print(f"    CAGR 中位 {act['CAGR%'].median():.2f}%   "
          f"平均 {act['CAGR%'].mean():.2f}%   "
          f"最好 {act['CAGR%'].max():.2f}%")
    print(f"    Sharpe 中位 {act['Sharpe'].median():.2f}   "
          f"最好 {act['Sharpe'].max():.2f}")
    print(f"    正收益: {int((act['CAGR%']>0).sum())}/{len(act)} "
          f"({(act['CAGR%']>0).mean()*100:.0f}%)")
    print()
    print(f"  被动基准: {len(pas)} 个")
    for _, r in pas.iterrows():
        print(f"    {r['策略']:<24} CAGR {r['CAGR%']:>6.2f}%  "
              f"Sharpe {r['Sharpe']:>5.2f}")
    print()
    # 有多少主动策略跑赢最好的被动
    if len(pas):
        best_pas = pas["CAGR%"].max()
        n_beat = int((act["CAGR%"] > best_pas).sum())
        print(f"  ★ 跑赢最好被动基准（{best_pas:.2f}%）的主动策略: "
              f"{n_beat}/{len(act)} ({n_beat/len(act)*100:.0f}%)")
    print()

    # ---- 成本影响
    print("=" * 130)
    print("  四、成本影响（换手率的代价）")
    print("=" * 130)
    print()
    print("  单边成本: 买入 0.10%（佣金0.025%+滑点0.075%）")
    print("            卖出 0.15%（佣金0.025%+印花0.05%+滑点0.075%）")
    print("  单次往返: 0.25%")
    print()
    print(f"  {'策略类型':<20}{'年交易笔数':>12}{'年成本%':>11}{'CAGR扣除后%':>14}")
    print("  " + "-" * 58)
    for lab, n_tr, cagr in [("低频（H60/S15）", 266, None),
                            ("中频（H20/S15）", 665, None),
                            ("高频（H20/S10/TP20）", 1125, None)]:
        cost = n_tr * 0.0025 * 100
        print(f"  {lab:<20}{n_tr:>12}{cost:>10.1f}%{'':>14}")
    print()
    print("  → 高频策略每年光成本就吃掉 2.8%，而策略本身 CAGR 才几个点")
    print()

    # ---- 收益分解
    print("=" * 130)
    print("  五、收益分解：beta vs alpha")
    print("=" * 130)
    print()
    f = newest("market_neutral_*.csv", out)
    if f:
        MN = pd.read_csv(f)
        print("  市场中性回测（多头 vs 池内等权基准）:")
        print()
        print(f"  {'策略':<16}{'多头CAGR%':>11}{'基准CAGR%':>11}"
              f"{'超额CAGR%':>11}{'超额Sharpe':>12}")
        print("  " + "-" * 62)
        for _, r in MN.iterrows():
            print(f"  {r['策略']:<16}{r['多头CAGR%']:>11.2f}"
                  f"{r['基准CAGR%']:>11.2f}{r['超额CAGR%']:>11.2f}"
                  f"{(r['超额Sharpe'] if pd.notna(r['超额Sharpe']) else 0):>12.2f}")
        print()
        pos = MN[MN["超额CAGR%"] > 0]
        print(f"  ★ 超额为正的策略: {len(pos)}/{len(MN)}")
        print(f"  基准（池内等权）CAGR: {MN['基准CAGR%'].iloc[0]:.2f}%  ← 这就是 beta")
        print()

    # ---- 保存
    R.to_csv(out / "reality_check.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {out}/reality_check.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
