# -*- coding: utf-8 -*-
"""
step19_action_plan.py — 可执行方案生成器

输入：你的资金、风险偏好
输出：精确到「买什么、买多少、什么价位买」的操作清单

同时生成一个每日监控脚本，自动告诉你今天该做什么。
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


# 宽基 ETF（流动性最好的）
ETFS = [
    ("510300.SH", "沪深300ETF", "沪深300", 0.40),
    ("510500.SH", "中证500ETF", "中证500", 0.25),
    ("512100.SH", "中证1000ETF", "中证1000", 0.20),
    ("563300.SH", "中证2000ETF", "中证2000", 0.15),
]


def get_etf_daily(pro, code, start, end):
    frames = []
    cur = pd.Timestamp(end)
    s0 = pd.Timestamp(start)
    for _ in range(20):
        seg = max(s0, cur - pd.Timedelta(days=500))
        got = None
        for attempt in range(3):
            try:
                got = pro.fund_daily(ts_code=code,
                                     start_date=seg.strftime("%Y%m%d"),
                                     end_date=cur.strftime("%Y%m%d"))
                break
            except Exception:
                time.sleep(0.8 * (attempt + 1))
        if got is None or len(got) == 0:
            break
        frames.append(got)
        earliest = pd.to_datetime(got["trade_date"].min(), format="%Y%m%d")
        if earliest <= s0:
            break
        cur = earliest - pd.Timedelta(days=1)
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d = d.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
    d["date"] = pd.to_datetime(d["trade_date"], format="%Y%m%d")
    for c in ("open", "high", "low", "close"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d[["date", "open", "high", "low", "close"]].reset_index(drop=True)


def main():
    cfg = load_config()
    setup_logger("act", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    # ---- 参数（可按需修改）
    CAPITAL = 500_000          # 总资金（元）
    RESERVE = 0.10             # 保留现金比例（应急）
    INVEST = CAPITAL * (1 - RESERVE)   # 可投资金
    print("=" * 120)
    print("  可执行方案")
    print("=" * 120)
    print()
    print(f"  总资金: {CAPITAL:,.0f} 元")
    print(f"  保留现金: {CAPITAL*RESERVE:,.0f} 元（{RESERVE*100:.0f}%）")
    print(f"  可投资金: {INVEST:,.0f} 元")
    print()

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    # ---- 1. 选工具（ETF）
    print("=" * 120)
    print("  一、买什么（宽基ETF）")
    print("=" * 120)
    print()
    rows = []
    for code, name, idx, weight in ETFS:
        d = get_etf_daily(pro, code, "20230101", "20261231")
        if d is None or len(d) < 60:
            print(f"  {name:<14} {code}  无数据")
            continue
        c = d["close"]
        cur = float(c.iloc[-1])
        hi250 = float(c.tail(250).max()) if len(c) >= 250 else float(c.max())
        dd = (cur / hi250 - 1) * 100
        amt = None
        rows.append({
            "代码": code, "名称": name, "跟踪": idx,
            "现价": round(cur, 3),
            "距250日高%": round(dd, 2),
            "建议权重%": int(weight * 100),
            "配置金额": round(INVEST * weight, 0),
        })
        print(f"  {name:<14} {code}  现价 {cur:>7.3f}  "
              f"距250日高 {dd:>7.2f}%  权重 {weight*100:>3.0f}%  "
              f"金额 {INVEST*weight:>10,.0f} 元")
    print()
    T = pd.DataFrame(rows)
    if len(T):
        T.to_csv(outdir / "action_etf.csv", index=False, encoding="utf-8-sig")

    # ---- 2. 仓位阶梯
    print("=" * 120)
    print("  二、什么时候买（仓位阶梯）")
    print("=" * 120)
    print()
    # 用沪深300 作为标尺
    STp = outdir / "market_state_vector.csv"
    ST = pd.read_csv(STp)
    ST["date"] = pd.to_datetime(ST["date"])
    ST = ST.set_index("date").sort_index()
    cur_row = ST.iloc[-1]
    price_col = "hs300" if "hs300" in ST.columns else "close"
    cur_price = float(cur_row[price_col])
    cur_dd = float(cur_row["dd250"])
    H = cur_price / (1 + cur_dd / 100)
    print(f"  标尺：沪深300  当前 {cur_price:.0f}  距250日高点 {cur_dd:.2f}%")
    print(f"  推算 250日高点：{H:.0f}")
    print()

    LADDER = [
        (-18, 0.15, "第一档：试探仓", 0.30),
        (-21, 0.30, "第二档：轻仓", 0.35),
        (-24, 0.50, "第三档：标准仓", 0.35),
        (-27, 0.70, "第四档：重仓", 0.30),
        (-30, 0.85, "第五档：深度仓", 0.25),
        (-35, 1.00, "第六档：极限仓", 0.20),
    ]
    # 更细的分配：每档增量
    print(f"  {'档位':<16}{'回撤':>7}{'沪深300点位':>13}{'距现价':>9}"
          f"{'累计仓位':>9}{'本档增量':>9}{'本档金额':>12}{'累计金额':>12}")
    print("  " + "-" * 108)
    prev = 0.0
    plan_rows = []
    for dd, cum, label, _ in LADDER:
        price = H * (1 + dd / 100)
        vs = (price / cur_price - 1) * 100
        inc = cum - prev
        print(f"  {label:<16}{dd:>6}%{price:>13.0f}{vs:>8.1f}%"
              f"{cum*100:>8.0f}%{inc*100:>8.0f}%"
              f"{INVEST*inc:>11,.0f}{INVEST*cum:>11,.0f}")
        plan_rows.append({
            "档位": label, "回撤%": dd, "沪深300点位": round(price, 0),
            "距现价%": round(vs, 1), "累计仓位%": int(cum * 100),
            "本档增量%": int(inc * 100),
            "本档金额": round(INVEST * inc, 0),
            "累计金额": round(INVEST * cum, 0),
        })
        prev = cum
    print()
    P = pd.DataFrame(plan_rows)
    P.to_csv(outdir / "action_ladder.csv", index=False, encoding="utf-8-sig")

    # ---- 3. 每档具体买什么
    print("=" * 120)
    print("  三、每档具体买什么（以第一档为例）")
    print("=" * 120)
    print()
    first = LADDER[0]
    amount = INVEST * first[1]
    print(f"  {first[2]}  仓位 {first[1]*100:.0f}%  金额 {amount:,.0f} 元")
    print()
    print(f"  {'ETF':<16}{'代码':<12}{'金额':>12}{'现价':>9}{'约买(份)':>12}")
    print("  " + "-" * 66)
    for code, name, idx, w in ETFS:
        sub = T[T["代码"] == code] if len(T) else pd.DataFrame()
        if len(sub) == 0:
            continue
        px = float(sub.iloc[0]["现价"])
        amt = amount * w
        shares = int(amt / px / 100) * 100
        print(f"  {name:<16}{code:<12}{amt:>11,.0f}{px:>9.3f}{shares:>12,}")
    print()
    print("  （ETF 按 100 份为一手，实际下单按市价）")
    print()

    # ---- 4. 什么时候卖
    print("=" * 120)
    print("  四、什么时候卖（止盈规则）")
    print("=" * 120)
    print()
    print("  分批止盈（按得分回落 + 涨幅）:")
    print()
    print(f"  {'触发条件':<30}{'卖出比例':>10}{'说明'}")
    print("  " + "-" * 76)
    sells = [
        ("指数反弹 +15%", "10%", "先落袋一部分"),
        ("指数反弹 +25%", "20%", "减仓"),
        ("指数反弹 +35%", "30%", "大幅减仓"),
        ("RSI > 70 且距年线 > +15%", "30%", "过热信号"),
        ("得分回落 < 4", "剩余全部", "熊市结束信号消失"),
        ("跌破前低 -5%", "止损该档", "认错"),
    ]
    for cond, pct, note in sells:
        print(f"  {cond:<30}{pct:>10}  {note}")
    print()

    # ---- 5. 每日监控脚本
    print("=" * 120)
    print("  五、每日怎么做（监控脚本）")
    print("=" * 120)
    print()
    print("  运行: python tools/daily_check.py")
    print("  输出: 今天该做什么（买/持/卖/等）")
    print()

    # ---- 6. 直白总结
    print("=" * 120)
    print("  六、今天（2026-10-15）该做什么")
    print("=" * 120)
    print()
    if cur_dd > -18:
        action = "买入第一档 15%"
        detail = f"沪深300 {cur_price:.0f}，距高点 {cur_dd:.2f}%（未到 -18%）"
        amt = INVEST * 0.15
    elif cur_dd > -21:
        action = "买入第二档（累计 30%）"
        detail = f"距高点 {cur_dd:.2f}%（在 -18~-21% 之间）"
        amt = INVEST * 0.15   # 补齐到30%需再买15%
    else:
        action = "按阶梯继续加仓"
        detail = f"距高点 {cur_dd:.2f}%"
        amt = None
    print(f"  【动作】{action}")
    print(f"  【依据】{detail}")
    if amt:
        print(f"  【金额】约 {amt:,.0f} 元")
        print()
        print(f"  具体下单:")
        for code, name, idx, w in ETFS:
            sub = T[T["代码"] == code] if len(T) else pd.DataFrame()
            if len(sub) == 0:
                continue
            px = float(sub.iloc[0]["现价"])
            a = amt * w
            sh = int(a / px / 100) * 100
            print(f"    {name:<14} {code}  买 {sh:>7,} 份  约 {a:>9,.0f} 元")
    print()
    print(f"  【下次动作】沪深300 跌到 {H*0.79:.0f}（-21%）时加仓")
    print(f"  【止损】若跌破前低 -5%，卖出该档")
    print()
    print(f"  【持有期】至少 1 年（短期胜率仅 58%，长期 75%）")
    print()

    # 保存
    with open(outdir / "action_plan.txt", "w", encoding="utf-8") as f:
        f.write(f"可执行方案 {pd.Timestamp.now():%Y-%m-%d}\n")
        f.write(f"总资金 {CAPITAL:,.0f}  可投 {INVEST:,.0f}\n\n")
        f.write(P.to_string(index=False))
        f.write("\n\n")
        f.write(T.to_string(index=False) if len(T) else "")
    print(f"[输出] {outdir}/action_ladder.csv")
    print(f"[输出] {outdir}/action_etf.csv")
    print(f"[输出] {outdir}/action_plan.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
