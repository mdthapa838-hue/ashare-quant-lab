# -*- coding: utf-8 -*-
"""
diag_002167.py — 东方锆业（002167）失败案例诊断

用户反馈：公告后第二天买入，然后开始跌。
诊断：
  ① 公告日、反应日、跳空情况
  ② 买入点在哪、后续走势
  ③ 用 step41/step42 的规则检查它是否该买
  ④ 它属于哪个分组（A1 还是 A2？）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

CODE = "002167"
TS = "002167.SZ"
NAME = "东方锆业"


def main():
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 128)
    print(f"  {NAME}（{CODE}）失败案例诊断")
    print("=" * 128)
    print()

    # ---- 1. 业绩预告
    print("[1] 业绩预告记录")
    print()
    d = None
    for _ in range(3):
        try:
            d = pro.forecast_vip(ts_code=TS, start_date="20260101",
                                 end_date="20261231")
            break
        except Exception:
            time.sleep(1.0)
    if d is not None and len(d):
        d = d.copy()
        d = d.sort_values("ann_date")
        for _, r in d.iterrows():
            print(f"  公告日: {r['ann_date']}   报告期: {r['end_date']}   "
                  f"类型: {r.get('type','')}")
            print(f"  增速: {r.get('p_change_min')}% ~ {r.get('p_change_max')}%")
            print(f"  净利: {r.get('net_profit_min')}万 ~ "
                  f"{r.get('net_profit_max')}万")
            print(f"  去年同期: {r.get('last_parent_net')}万")
            print()
            cr = str(r.get("change_reason", "") or "")
            if cr:
                print(f"  变动原因: {cr[:400]}")
                print()
    else:
        print("  未找到预告")
    print()

    # ---- 2. 价格走势
    print("=" * 128)
    print("  二、价格走势（关键）")
    print("=" * 128)
    print()
    q = pro.daily(ts_code=TS, start_date="20260901", end_date="20261016")
    if q is None or len(q) == 0:
        print("  无行情")
        return 1
    q = q.copy()
    q = q.sort_values("trade_date").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "pct_chg", "vol"):
        q[c] = pd.to_numeric(q[c], errors="coerce")

    cl = q["close"].values
    op = q["open"].values
    hi = q["high"].values
    lo = q["low"].values

    print(f"  {'日期':<12}{'开盘':>9}{'最高':>9}{'最低':>9}{'收盘':>9}"
          f"{'涨跌%':>9}{'量(万手)':>11}   标记")
    print("  " + "-" * 106)
    ann_dates = set()
    if d is not None and len(d):
        ann_dates = set(d["ann_date"].astype(str))
    for i, r in q.iterrows():
        marks = []
        td = str(r["trade_date"])
        if td in ann_dates:
            marks.append("★公告日")
        if r["pct_chg"] >= 9:
            marks.append("涨停")
        if r["pct_chg"] <= -9:
            marks.append("跌停")
        print(f"  {td:<12}{r['open']:>9.2f}{r['high']:>9.2f}"
              f"{r['low']:>9.2f}{r['close']:>9.2f}{r['pct_chg']:>8.2f}%"
              f"{r['vol']/10000:>11.1f}   {' / '.join(marks)}")
    print()

    # ---- 3. 定位公告日与买入点
    print("=" * 128)
    print("  三、关键点位分析")
    print("=" * 128)
    print()
    n = len(q)
    if ann_dates:
        ad = sorted(ann_dates)[-1]
        idx = q.index[q["trade_date"].astype(str) == ad]
        if len(idx):
            i0 = int(idx[0])
            prev_close = cl[i0 - 1] if i0 > 0 else np.nan
            o0 = op[i0]
            gap = (o0 / prev_close - 1) * 100 if np.isfinite(prev_close) else np.nan
            lowest0 = lo[i0]
            filled = lowest0 <= prev_close if np.isfinite(prev_close) else None
            close0 = cl[i0]
            chg0 = (close0 / prev_close - 1) * 100

            print(f"  公告日（反应日）: {ad}")
            print(f"    前收: {prev_close:.2f}")
            print(f"    开盘: {o0:.2f}   跳空 {gap:+.2f}%")
            print(f"    当日最低: {lowest0:.2f}")
            print(f"    当日收盘: {close0:.2f}   相对前收 {chg0:+.2f}%")
            print(f"    ★ 是否回补缺口（最低 ≤ 前收）: "
                  f"{'✅ 回补' if filled else '❌ 未回补'}")
            print()
            # T+1
            if i0 + 1 < n:
                o1 = op[i0 + 1]
                c1 = cl[i0 + 1]
                print(f"  T+1（用户买入日）: {q['trade_date'].iloc[i0+1]}")
                print(f"    开盘: {o1:.2f}   相对前收 "
                      f"{(o1/close0-1)*100:+.2f}%")
                print(f"    收盘: {c1:.2f}   {(c1/o1-1)*100:+.2f}%")
                print(f"    → 若开盘买入，当日 {(c1/o1-1)*100:+.2f}%")
                print()
            # 后续
            print(f"  T+1 之后的走势:")
            for k in range(1, min(11, n - i0)):
                j = i0 + k
                print(f"    T+{k:<2} {q['trade_date'].iloc[j]}  "
                      f"收盘 {cl[j]:>7.2f}  "
                      f"相对T+1开盘 {(cl[j]/op[i0+1]-1)*100:>+7.2f}%  "
                      f"相对公告日收盘 {(cl[j]/close0-1)*100:>+7.2f}%")
            print()
    else:
        print("  未定位到公告日")
        print()

    # ---- 4. 用规则检查
    print("=" * 128)
    print("  四、★ 用 step41/42 的规则检查：它该不该买？")
    print("=" * 128)
    print()
    if ann_dates and len(idx):
        i0 = int(idx[0])
        if i0 >= 130:
            cl_arr = cl
            hi_arr = hi
            lo_arr = lo
            px = cl[i0]
            for nn, lab in [(20, "20日"), (60, "60日"), (120, "120日")]:
                if i0 >= nn:
                    hh = np.nanmax(hi_arr[i0 - nn + 1:i0 + 1])
                    ll = np.nanmin(lo_arr[i0 - nn + 1:i0 + 1])
                    pos = (px - ll) / (hh - ll) * 100 if hh > ll else np.nan
                    print(f"  位置 {lab}: {pos:.1f}%")
            print()
        # 规则检查
        prev_close = cl[i0 - 1]
        gap = (op[i0] / prev_close - 1) * 100
        filled = lo[i0] <= prev_close
        print(f"  【规则①】当日不回补缺口")
        print(f"    实际: 最低 {lo[i0]:.2f} vs 前收 {prev_close:.2f} "
              f"→ {'❌ 回补了' if filled else '✅ 未回补'}")
        print()
        print(f"  【规则②】当日跳空 ≥ 2%")
        print(f"    实际: {gap:+.2f}% → "
              f"{'✅ 满足' if gap >= 2 else '❌ 不满足'}")
        print()
        print(f"  【规则③】增速 ≥ 50%")
        print(f"    实际: +261.9% → ✅ 满足")
        print()
        verdict = "❌ 不该买（至少不满足①/②）"
        print(f"  ★ 综合判定: {verdict}")
    print()

    # ---- 5. 分组归属
    print("=" * 128)
    print("  五、它属于哪个分组？")
    print("=" * 128)
    print()
    print("  据之前扫描（2026-10-16）:")
    print("    002167 东方锆业  增速 +261.9%  位置 10.8%  今日 -2.65%")
    print("    → 被分到 **A2 高增+低位**")
    print()
    print("  ★ 而 step39 回测结论:")
    print("    A1 高增+中期高位（p60/p120 ≥60）: 60日 +13.54%、胜率 59.1%")
    print("    A2 高增+低位（p20<20）:            60日 −1.98%、胜率 42.2%")
    print("    差异: +15.52pct")
    print()
    print("  → 东方锆业是 A2 组 —— 按我自己的回测，这组是【负期望】！")
    print()
    print("  ⚠ 这是关键：它在「低位」，不是「高位」。")
    print("     而我之前把 A2 也列在推荐里，这是错误的。")
    print()

    # ---- 6. 同类统计
    print("=" * 128)
    print("  六、A2 组（高增+低位）的真实表现")
    print("=" * 128)
    print()
    E = pd.read_csv(HERE / "output" / "gap_fade_events.csv",
                    dtype={"code": str})
    E["g"] = pd.to_numeric(E["g"], errors="coerce")
    A2 = E[(E["g"] >= 50) & (E["gap"] < 2)]
    print(f"  模拟 A2 条件（增速≥50% + 跳空<2%）: {len(A2):,} 个")
    for hz, lab in [("A_f20", "20日"), ("A_f60", "60日"),
                    ("B_f20", "20日(收盘买)"), ("B_f60", "60日(收盘买)")]:
        v = A2[hz].dropna()
        if len(v):
            print(f"    {lab:<16} 平均 {v.mean():>+7.2f}%   "
                  f"胜率 {(v>0).mean()*100:>5.1f}%")
    print()
    print(f"  对照 A1 近似（增速≥50% + 跳空≥2%）:")
    A1 = E[(E["g"] >= 50) & (E["gap"] >= 2)]
    print(f"    样本 {len(A1):,}")
    for hz, lab in [("B_f20", "20日"), ("B_f60", "60日")]:
        v = A1[hz].dropna()
        if len(v):
            print(f"    {lab:<16} 平均 {v.mean():>+7.2f}%   "
                  f"胜率 {(v>0).mean()*100:>5.1f}%")
    print()

    # ---- 7. 怎么办
    print("=" * 128)
    print("  七、★ 已经买了，怎么办？")
    print("=" * 128)
    print()
    if ann_dates and len(idx) and i0 + 1 < n:
        cost = op[i0 + 1]
        cur = cl[-1]
        pnl = (cur / cost - 1) * 100
        print(f"  假设 T+1 开盘买入（成本 {cost:.2f}）")
        print(f"  最新收盘 {cur:.2f}")
        print(f"  浮亏 {pnl:+.2f}%")
        print()
        print("  参考逻辑:")
        print(f"    · 20日持有期的历史均值: {A2['A_f20'].dropna().mean():+.2f}%")
        print(f"    · 60日持有期的历史均值: {A2['A_f60'].dropna().mean():+.2f}%")
        print(f"    · A2 组 60日胜率: "
              f"{(A2['A_f60'].dropna()>0).mean()*100:.1f}%")
        print()
        print("  三种处理方式:")
        print("    ① 严格止损：−15% 无条件卖出（回测最差 −78%）")
        print("    ② 时间止损：20日到期卖出，不管盈亏")
        print("    ③ 条件止损：跌破「公告日最低价」卖出")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
