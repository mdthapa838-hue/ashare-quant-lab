# -*- coding: utf-8 -*-
"""
daily_check.py — 每日监控（一行命令告诉你今天做什么）

用法：
    python tools/daily_check.py              # 用最新数据
    python tools/daily_check.py --capital 500000

输出：
    当前市场状态 + 今天的具体动作（买/持/卖/等）+ 下单清单
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

ETFS = [
    ("510300.SH", "沪深300ETF", 0.40),
    ("510500.SH", "中证500ETF", 0.25),
    ("512100.SH", "中证1000ETF", 0.20),
    ("563300.SH", "中证2000ETF", 0.15),
]

# 仓位阶梯
LADDER = [
    (-18, 0.15, "第一档：试探仓"),
    (-21, 0.30, "第二档：轻仓"),
    (-24, 0.50, "第三档：标准仓"),
    (-27, 0.70, "第四档：重仓"),
    (-30, 0.85, "第五档：深度仓"),
    (-35, 1.00, "第六档：极限仓"),
]

RESERVE = 0.10


def get_index(pro, code, start, end, api="index_daily"):
    frames = []
    cur = pd.Timestamp(end)
    s0 = pd.Timestamp(start)
    for _ in range(20):
        seg = max(s0, cur - pd.Timedelta(days=500))
        got = None
        for attempt in range(3):
            try:
                got = getattr(pro, api)(ts_code=code,
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
    return d.reset_index(drop=True)


def main():
    # 参数
    capital = 500_000
    if "--capital" in sys.argv:
        i = sys.argv.index("--capital")
        if i + 1 < len(sys.argv):
            capital = float(sys.argv[i + 1])
    invest = capital * (1 - RESERVE)

    cfg = load_config()
    setup_logger("chk", log_dir=None, level="ERROR", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    # ---- 真实数据（不依赖缓存）
    d = get_index(pro, "000300.SH", "20230101", "20261231")
    if d is None:
        print("无法获取指数数据")
        return 1
    d = d.sort_values("date")
    c = d["close"]
    cur = float(c.iloc[-1])
    cur_date = d["date"].iloc[-1].date()

    ma20 = float(c.rolling(20, min_periods=20).mean().iloc[-1])
    ma60 = float(c.rolling(60, min_periods=60).mean().iloc[-1])
    ma250 = float(c.rolling(250, min_periods=250).mean().iloc[-1])
    hi250 = float(c.tail(250).max())
    dd = (cur / hi250 - 1) * 100
    ma_dist = (cur / ma250 - 1) * 100
    r60 = float(c.iloc[-1] / c.iloc[-61] - 1) * 100 if len(c) > 61 else np.nan
    diff = c.diff()
    up = diff.clip(lower=0).rolling(14, min_periods=14).mean()
    dn = (-diff.clip(upper=0)).rolling(14, min_periods=14).mean()
    rsi = float(100 - 100 / (1 + (up / dn.replace(0, np.nan)).iloc[-1]))

    # 抄底得分
    s1 = 3 if dd < -35 else 2 if dd < -25 else 1 if dd < -15 else 0
    s2 = 3 if ma_dist < -20 else 2 if ma_dist < -12 else 1 if ma_dist < -5 else 0
    s3 = 3 if r60 < -25 else 2 if r60 < -18 else 1 if r60 < -10 else 0
    s4 = 3 if rsi < 18 else 2 if rsi < 25 else 1 if rsi < 35 else 0
    score = s1 + s2 + s3 + s4

    # 仓位建议
    target = 0.0
    level_name = "空仓等待"
    for dd_thr, pos, name in LADDER:
        if dd <= dd_thr:
            target = pos
            level_name = name
    if target == 0 and dd <= -18:
        target = LADDER[0][1]
        level_name = LADDER[0][2]
    if dd > -18:
        # 未到第一档
        target = 0.0
        level_name = "未到建仓区（等 -18%）"

    target_amt = invest * target

    # 输出
    print("=" * 96)
    print(f"  每日检查  {cur_date}")
    print("=" * 96)
    print()
    print("【市场状态】")
    print(f"  沪深300          {cur:>10.2f}")
    print(f"  距250日高点      {dd:>10.2f}%   (250日高 {hi250:.0f})")
    print(f"  距年线           {ma_dist:>10.2f}%   (年线 {ma250:.0f})")
    print(f"  60日收益         {r60:>10.2f}%")
    print(f"  RSI14            {rsi:>10.1f}")
    print()
    print(f"【抄底得分】{score}/12")
    print(f"  s1 距离高点 {s1}/3   s2 距年线 {s2}/3   "
          f"s3 60日跌幅 {s3}/3   s4 RSI {s4}/3")
    print()

    # 历史参照
    hist_map = {
        0: "观望", 1: "观望", 2: "观望", 3: "关注",
        4: "准备", 5: "准备", 6: "★开始建仓", 7: "★加仓",
        8: "★重仓", 9: "★重仓", 10: "★★全力", 11: "★★全力", 12: "★★全力",
    }
    print(f"【历史参照】得分 {score} → {hist_map.get(score, '观望')}")
    print()

    print("【仓位建议】")
    print(f"  当前档位: {level_name}")
    print(f"  目标仓位: {target*100:.0f}%")
    print(f"  目标金额: {target_amt:,.0f} 元")
    print()

    # 下一档
    nxt = None
    for dd_thr, pos, name in LADDER:
        if dd > dd_thr:
            nxt = (dd_thr, pos, name)
            break
    if nxt:
        nxt_price = hi250 * (1 + nxt[0] / 100)
        print(f"【下一档】{nxt[2]}  -{abs(nxt[0])}%  →  沪深300 {nxt_price:.0f}  "
              f"（还需跌 {(nxt_price/cur-1)*100:.1f}%）")
        print()

    # 下单清单
    if target > 0:
        print("【下单清单】")
        print(f"  {'ETF':<16}{'代码':<12}{'目标金额':>12}{'现价':>9}{'份数':>10}")
        print("  " + "-" * 62)
        for code, name, w in ETFS:
            e = get_index(pro, code, "20260801", "20261231", api="fund_daily")
            if e is None or len(e) == 0:
                px = np.nan
            else:
                px = float(e["close"].iloc[-1])
            amt = target_amt * w
            sh = int(amt / px / 100) * 100 if np.isfinite(px) and px > 0 else 0
            print(f"  {name:<16}{code:<12}{amt:>11,.0f}{px:>9.3f}{sh:>10,}")
        print()

    # 卖出信号
    print("【卖出检查】")
    if rsi > 70 and ma_dist > 15:
        print("  ⚠ RSI>70 且距年线>+15% → 建议减仓 30%")
    elif ma_dist > 10:
        print("  △ 距年线>+10% → 开始留意止盈")
    else:
        print("  无卖出信号")
    print()

    print("【提醒】")
    print("  · 分批建仓，不要一次满仓")
    print("  · 持有期至少 1 年")
    print("  · 单档止损：跌破前低 -5%")
    print()

    # 保存
    rec = {
        "date": str(cur_date), "hs300": cur, "dd250": round(dd, 2),
        "ma_dist": round(ma_dist, 2), "r60": round(r60, 2),
        "rsi": round(rsi, 1), "score": score,
        "target_pos": target, "target_amt": target_amt,
        "level": level_name,
    }
    p = outdir / "daily_check_log.csv"
    if p.exists():
        L = pd.read_csv(p)
        L = pd.concat([L, pd.DataFrame([rec])], ignore_index=True)
    else:
        L = pd.DataFrame([rec])
    L = L.drop_duplicates(subset=["date"], keep="last")
    L.to_csv(p, index=False, encoding="utf-8-sig")
    print(f"[记录] {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
