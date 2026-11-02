# -*- coding: utf-8 -*-
"""
step18_buy_timing.py — 「现在买」会怎样？分价位回测

直接回答用户问题：
  Q: 现在这个位置是底部吗？适合买吗？

方法：
  A. 在各个「下跌深度」档位上买入，统计之后的收益分布
     （把当前 -17.7% 的档位单独拎出来看）
  B. 「立即买入」vs 「等更低」vs 「分批」的结果对比
  C. 持有期选择：买早 vs 买晚的差异
  D. 给出具体的分批建仓价位表
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def main():
    cfg = load_config()
    setup_logger("buy", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    # 用已有状态向量
    p = outdir / "market_state_vector.csv"
    if not p.exists():
        print("缺少 market_state_vector.csv，请先运行 step17")
        return 1
    ST = pd.read_csv(p)
    ST["date"] = pd.to_datetime(ST["date"])
    ST = ST.set_index("date").sort_index()
    print(f"状态向量: {len(ST)} 天 "
          f"({ST.index.min().date()} ~ {ST.index.max().date()})")
    print()

    # 确保有前瞻
    if "fwd250" not in ST.columns:
        for n in (20, 60, 120, 250):
            ST[f"fwd{n}"] = ST["ew"].shift(-n) / ST["ew"] - 1
    for n in (60, 120, 250):
        if f"fwdmin{n}" not in ST.columns:
            vals = ST["ew"].values
            fm = []
            for i in range(len(vals)):
                j = min(i + n, len(vals) - 1)
                seg = vals[i:j + 1]
                fm.append(seg.min() / vals[i] - 1 if len(seg) else np.nan)
            ST[f"fwdmin{n}"] = fm
    print("[1] 前瞻已就绪")
    print()

    CUR_DD = -17.66    # 当前距250日高点
    CUR_MA = -10.71    # 当前距年线
    CUR_RSI = 21.2

    # ---- A. 按回撤深度分档
    print("=" * 130)
    print("  一、按「距250日高点回撤」分档买入的结果")
    print("=" * 130)
    print()
    bins = [(-100, -40), (-40, -30), (-30, -25), (-25, -20), (-20, -17),
            (-17, -14), (-14, -10), (-10, 0)]
    labels = ["<-40%", "-40~-30%", "-30~-25%", "-25~-20%",
              "-20~-17.7%", "-17.7~-14%", "-14~-10%", ">-10%"]
    rows = []
    for (lo, hi), lab in zip(bins, labels):
        sub = ST[(ST["dd250"] > lo) & (ST["dd250"] <= hi)]
        if len(sub) < 20:
            continue
        r = {"回撤档位": lab, "天数": len(sub)}
        for n in (20, 60, 120, 250):
            v = sub[f"fwd{n}"].dropna()
            if len(v) > 10:
                r[f"{n}日收益%"] = round(v.mean() * 100, 2)
                r[f"{n}日胜率%"] = round((v > 0).mean() * 100, 1)
        for n in (60, 250):
            v = sub[f"fwdmin{n}"].dropna()
            if len(v) > 10:
                r[f"{n}日内最差%"] = round(v.median() * 100, 2)
        rows.append(r)
    A = pd.DataFrame(rows)
    print(A.to_string(index=False))
    print()
    print(f"  ★ 当前处于 {CUR_DD:.2f}% 档位（即 -20~-17.7% 那一行）")
    print()

    # ---- B. 「立即买」vs「等更低」
    print("=" * 130)
    print("  二、立即买入 vs 等待更低")
    print("=" * 130)
    print()
    # 定义：处于 CUR_DD 附近（-20%~-15%）
    near = ST[(ST["dd250"] > -20) & (ST["dd250"] <= -15)]
    print(f"  样本：历史上回撤在 -20%~-15% 的天数 = {len(near)}")
    print()
    print(f"  【立即买入（在这些位置全仓）】")
    for n in (20, 60, 120, 250):
        v = near[f"fwd{n}"].dropna()
        if len(v):
            print(f"    {n:>3}日后: 平均 {v.mean()*100:>+7.2f}%   "
                  f"中位 {v.median()*100:>+7.2f}%   "
                  f"胜率 {(v>0).mean()*100:>5.1f}%   "
                  f"最差 {v.min()*100:>+7.2f}%")
    print()
    print(f"  【等待：从这些位置继续跌到 -25% 才买】")
    # 模拟：从 near 的位置，等跌到 -25% 再买
    hits = []
    arr = ST["dd250"].values
    idx = ST.index
    for i in range(len(arr)):
        if pd.isna(arr[i]) or not (-20 < arr[i] <= -15):
            continue
        # 往后找 -25%
        j = i
        while j < len(arr) and not (arr[j] <= -25):
            j += 1
        if j < len(arr) and j + 250 < len(arr):
            # 从 j 买入
            e0 = ST["ew"].iloc[j]
            fr = {}
            for n in (20, 60, 120, 250):
                k = min(j + n, len(ST) - 1)
                fr[n] = ST["ew"].iloc[k] / e0 - 1
            hits.append(fr)
    if hits:
        H = pd.DataFrame(hits)
        print(f"    触发次数: {len(H)}")
        for n in (20, 60, 120, 250):
            v = H[n].dropna()
            if len(v):
                print(f"    {n:>3}日后: 平均 {v.mean()*100:>+7.2f}%   "
                      f"中位 {v.median()*100:>+7.2f}%   "
                      f"胜率 {(v>0).mean()*100:>5.1f}%   "
                      f"最差 {v.min()*100:>+7.2f}%")
    else:
        print("    无样本（历史上很少在 -25% 才买）")
    print()

    # ---- C. 分批 vs 一次性
    print("=" * 130)
    print("  三、分批建仓 vs 一次性买入")
    print("=" * 130)
    print()
    print("  模拟：从回撤 -15% 起，每跌 5% 加仓 25%")
    print()
    strat = []
    arr = ST["dd250"].values
    ew = ST["ew"].values
    for i in range(len(arr)):
        if pd.isna(arr[i]) or not (-16 < arr[i] <= -14):
            continue
        # 分批：-15% 买25%，-20% 25%，-25% 25%，-30% 25%
        targets = [-15, -20, -25, -30]
        buys = []
        for t in targets:
            j = i
            while j < len(arr):
                if not pd.isna(arr[j]) and arr[j] <= t:
                    buys.append((j, ew[j], 0.25))
                    break
                j += 1
            if j >= len(arr):
                break
        if not buys:
            continue
        # 计算建仓完成后的持有收益
        last_j = buys[-1][0]
        if last_j + 250 >= len(ew):
            continue
        cost = sum(b[2] * b[1] for b in buys)
        # 平均成本（按份额）
        shares = sum(b[2] / b[1] for b in buys)
        avg_cost = sum(b[2] for b in buys) / shares if shares else np.nan
        for n in (60, 120, 250):
            k = min(last_j + n, len(ew) - 1)
            strat.append({"n": n, "ret": ew[k] / avg_cost - 1})
    if strat:
        S = pd.DataFrame(strat)
        print("  【分批建仓（4批）】")
        for n in (60, 120, 250):
            v = S[S["n"] == n]["ret"]
            if len(v):
                print(f"    {n:>3}日后: 平均 {v.mean()*100:>+7.2f}%   "
                      f"中位 {v.median()*100:>+7.2f}%   "
                      f"胜率 {(v>0).mean()*100:>5.1f}%   n={len(v)}")
    print()

    # ---- D. 具体价位表
    print("=" * 130)
    print("  四、分批建仓价位表（以当前点位为基准）")
    print("=" * 130)
    print()
    # 当前沪深300 = 4166.01，dd250 = -17.66%
    # 推算出「回撤到 X%」对应的点位
    cur_price = 4166.01
    cur_dd = -17.66
    # 设 250日高点 H，则 cur_price = H*(1+cur_dd) → H = cur_price/(1+cur_dd)
    H = cur_price / (1 + cur_dd / 100)
    print(f"  当前沪深300: {cur_price:.2f}   距250日高点 {cur_dd:.2f}%")
    print(f"  推算 250日高点: {H:.2f}")
    print()
    print(f"  {'回撤档':<12}{'对应点位':>12}{'距现价':>10}{'历史60日胜率':>14}"
          f"{'历史250日胜率':>15}{'建议仓位':>10}")
    print("  " + "-" * 90)
    plans = [
        (-18, 0.10, "试探"),
        (-20, 0.20, "轻仓"),
        (-22, 0.30, "加仓"),
        (-25, 0.50, "重仓"),
        (-28, 0.70, "深度"),
        (-30, 0.85, "极限"),
        (-35, 1.00, "绝地"),
    ]
    for dd, pos, note in plans:
        price = H * (1 + dd / 100)
        vs = (price / cur_price - 1) * 100
        # 历史该档位的胜率
        sub = ST[(ST["dd250"] > dd - 2) & (ST["dd250"] <= dd + 2)]
        w60 = ((sub["fwd60"] > 0).mean() * 100) if len(sub) > 10 else np.nan
        w250 = ((sub["fwd250"] > 0).mean() * 100) if len(sub) > 10 else np.nan
        print(f"  {dd:>4}%{'':<7}{price:>12.0f}{vs:>9.1f}%"
              f"{w60:>13.0f}%{w250:>14.0f}%{pos*100:>9.0f}%  {note}")
    print()

    # ---- E. 直白回答
    print("=" * 130)
    print("  五、直白回答")
    print("=" * 130)
    print()
    # 当前档位统计
    cur_bin = ST[(ST["dd250"] > -20) & (ST["dd250"] <= -15)]
    v60 = cur_bin["fwd60"].dropna()
    v120 = cur_bin["fwd120"].dropna()
    v250 = cur_bin["fwd250"].dropna()
    m60 = cur_bin["fwdmin60"].dropna()
    m250 = cur_bin["fwdmin250"].dropna()
    print(f"  Q: 现在是底部吗？")
    print(f"    历史上「回撤 -20%~-15%」出现过 {len(cur_bin)} 天")
    print(f"    60日后上涨概率 {(v60>0).mean()*100:.0f}%   平均 {v60.mean()*100:+.1f}%")
    print(f"    120日后上涨概率 {(v120>0).mean()*100:.0f}%   平均 {v120.mean()*100:+.1f}%")
    print(f"    250日后上涨概率 {(v250>0).mean()*100:.0f}%   平均 {v250.mean()*100:+.1f}%")
    print()
    print(f"    → 短期（1-6月）不是底部（胜率 {((v60>0).mean()*100):.0f}%）")
    print(f"    → 长期（1年）接近底部（胜率 {(v250>0).mean()*100:.0f}%）")
    print()
    print(f"  Q: 适合买吗？")
    print(f"    若买了，未来60日内最大回撤中位 {m60.median()*100:+.1f}%  "
          f"最差 {m60.min()*100:+.1f}%")
    print(f"    未来250日内最大回撤中位 {m250.median()*100:+.1f}%  "
          f"最差 {m250.min()*100:+.1f}%")
    print()
    print(f"    → 可以买，但必须：")
    print(f"       1) 分批（不要一次满仓）")
    print(f"       2) 接受可能再跌 {abs(m60.median())*100:.0f}%~{abs(m60.min())*100:.0f}%")
    print(f"       3) 持有期至少 1 年")
    print()

    # ---- 输出
    A.to_csv(outdir / "buy_depth_table.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/buy_depth_table.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
