# -*- coding: utf-8 -*-
"""
step15_bear_history.py — A股历史熊市拆解

识别并分析 2005-2026 的每轮熊市：
  1. 自动识别：从阶段高点到阶段低点，跌幅 > 20%
  2. 每轮的特征：
     - 起点/终点日期、持续月数
     - 最大跌幅、跌幅节奏（快跌/阴跌）
     - 见底信号：估值、宽度、成交量、政策
  3. 见底后的反弹幅度与形态
  4. 统计规律：熊市平均跌幅/时长、见底前特征

数据：中证全指/上证指数长历史
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
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def get_index(pro, code: str, start: str, end: str) -> pd.DataFrame | None:
    frames = []
    cur = pd.Timestamp(end)
    s0 = pd.Timestamp(start)
    for _ in range(60):
        seg = max(s0, cur - pd.Timedelta(days=500))
        got = None
        for attempt in range(3):
            try:
                got = pro.index_daily(ts_code=code,
                                      start_date=seg.strftime("%Y%m%d"),
                                      end_date=cur.strftime("%Y%m%d"))
                break
            except Exception:
                time.sleep(1.0 * (attempt + 1))
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
    for c in ("close", "amount"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d[["date", "close"] +
              ([c for c in ("amount",) if c in d.columns])].reset_index(drop=True)


def find_bears(d: pd.DataFrame, min_dd: float = 0.20,
               min_days: int = 60) -> list[dict]:
    """
    识别熊市：从高点到低点跌幅 >= min_dd，持续 >= min_days。

    算法（ZigZag 变体）：
      1. 追踪运行高点
      2. 回撤超过 min_dd 时，开始寻找低点
      3. 从低点反弹超过 min_dd*0.5 时，确认低点
    """
    c = d["close"].values
    dates = d["date"].values
    n = len(c)
    bears = []
    i = 0
    peak_i = 0
    peak_v = c[0]
    state = "up"      # up: 追踪高点; down: 追踪低点
    trough_i = None
    trough_v = None

    for i in range(1, n):
        v = c[i]
        if state == "up":
            if v > peak_v:
                peak_v = v
                peak_i = i
            elif v <= peak_v * (1 - min_dd):
                # 进入下跌
                state = "down"
                trough_v = v
                trough_i = i
        else:  # down
            if v < trough_v:
                trough_v = v
                trough_i = i
            elif v >= trough_v * (1 + min_dd * 0.5):
                # 确认低点
                dur = (pd.Timestamp(dates[trough_i]) -
                       pd.Timestamp(dates[peak_i])).days
                if dur >= min_days:
                    bears.append({
                        "peak_date": pd.Timestamp(dates[peak_i]),
                        "trough_date": pd.Timestamp(dates[trough_i]),
                        "peak": float(peak_v),
                        "trough": float(trough_v),
                        "dd%": round((trough_v / peak_v - 1) * 100, 2),
                        "days": dur,
                        "months": round(dur / 30.4, 1),
                        "recover_date": pd.Timestamp(dates[i]),
                        "recover_days": (pd.Timestamp(dates[i]) -
                                         pd.Timestamp(dates[trough_i])).days,
                    })
                # 重置
                state = "up"
                peak_v = v
                peak_i = i
                trough_i = None
                trough_v = None
    return bears


def analyze_bear(d: pd.DataFrame, b: dict) -> dict:
    """分析单轮熊市的细节。"""
    seg = d[(d["date"] >= b["peak_date"]) & (d["date"] <= b["trough_date"])]
    if len(seg) < 5:
        return b
    c = seg["close"]
    n = len(seg)
    # 跌幅节奏
    half = n // 2
    first_half_dd = float(c.iloc[half] / c.iloc[0] - 1) * 100
    second_half_dd = float(c.iloc[-1] / c.iloc[half] - 1) * 100
    # 波动
    vol = float(c.pct_change().std() * np.sqrt(244) * 100)
    # 跌破均线
    ma250_all = d["close"].rolling(250, min_periods=250).mean()
    below_ma = (seg["close"].values <
                ma250_all[(d["date"] >= b["peak_date"]) &
                          (d["date"] <= b["trough_date"])].values)
    # 最大单日跌幅
    worst_day = float(c.pct_change().min() * 100)
    # 见底前20日
    last20 = float(c.iloc[-1] / c.iloc[-21] - 1) * 100 if n > 21 else np.nan
    b2 = dict(b)
    b2.update({
        "前半段跌幅%": round(first_half_dd, 2),
        "后半段跌幅%": round(second_half_dd, 2),
        "年化波动%": round(vol, 1),
        "最大单日跌%": round(worst_day, 2),
        "见底前20日%": round(last20, 2) if pd.notna(last20) else None,
        "交易日数": n,
        "破年线占比%": round(float(np.mean(below_ma)) * 100, 1)
        if len(below_ma) else None,
    })
    return b2


def main():
    cfg = load_config()
    setup_logger("bear", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"
    outdir.mkdir(parents=True, exist_ok=True)

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 130)
    print("  A股历史熊市拆解（2005-2026）")
    print("=" * 130)
    print()

    # 用上证指数（历史最长）+ 中证全指
    targets = [
        ("000001.SH", "上证指数"),
        ("399106.SZ", "深证综指"),
        ("000300.SH", "沪深300"),
        ("000852.SH", "中证1000"),
    ]

    all_bears = []
    for code, name in targets:
        d = get_index(pro, code, "20050101", "20261231")
        if d is None or len(d) < 500:
            print(f"  {name} 数据不足")
            continue
        print(f"【{name}】{d['date'].min().date()} ~ {d['date'].max().date()} "
              f"({len(d)} 天)")
        bears = find_bears(d, min_dd=0.20, min_days=60)
        print(f"  识别到 {len(bears)} 轮熊市")
        print()
        for b in bears:
            b2 = analyze_bear(d, b)
            b2["指数"] = name
            all_bears.append(b2)
            print(f"    {b2['peak_date'].date()} → {b2['trough_date'].date()}  "
                  f"{b2['dd%']:>7.1f}%  {b2['months']:>5.1f}月  "
                  f"({b2['days']}天)  反弹确认 {b2['recover_date'].date()}")
        print()

    B = pd.DataFrame(all_bears)
    if len(B) == 0:
        print("未识别到熊市")
        return 1

    # ---- 按指数看
    print("=" * 130)
    print("  一、各指数熊市统计")
    print("=" * 130)
    print()
    g = B.groupby("指数").agg(
        轮数=("dd%", "size"),
        平均跌幅=("dd%", "mean"),
        最大跌幅=("dd%", "min"),
        平均月数=("months", "mean"),
        最长月数=("months", "max"),
    ).round(1)
    print(g.to_string())
    print()

    # ---- 上证指数详情（最有代表性）
    SH = B[B["指数"] == "上证指数"].copy()
    if len(SH):
        print("=" * 130)
        print("  二、上证指数历次熊市详情")
        print("=" * 130)
        print()
        cols = ["peak_date", "trough_date", "peak", "trough", "dd%", "months",
                "days", "前半段跌幅%", "后半段跌幅%", "年化波动%",
                "最大单日跌%", "见底前20日%", "破年线占比%"]
        cols = [c for c in cols if c in SH.columns]
        disp = SH[cols].copy()
        for c in ("peak_date", "trough_date"):
            disp[c] = pd.to_datetime(disp[c]).dt.strftime("%Y-%m-%d")
        print(disp.to_string(index=False))
        print()

    # ---- 统计规律
    print("=" * 130)
    print("  三、统计规律")
    print("=" * 130)
    print()
    print(f"  总体（{len(B)} 轮熊市，4个指数）:")
    print(f"    平均跌幅: {B['dd%'].mean():.1f}%   中位: {B['dd%'].median():.1f}%")
    print(f"    平均时长: {B['months'].mean():.1f} 月   中位: {B['months'].median():.1f} 月")
    print(f"    最深: {B['dd%'].min():.1f}%   最长: {B['months'].max():.1f} 月")
    print()
    # 分档
    for lo, hi in [(-30, 0), (-40, -30), (-50, -40), (-100, -50)]:
        sub = B[(B["dd%"] > lo) & (B["dd%"] <= hi)]
        if len(sub):
            print(f"    跌幅 {hi}% ~ {lo}%: {len(sub)} 轮  "
                  f"平均 {sub['months'].mean():.1f} 月")
    print()
    # 见底前是否加速
    if "见底前20日%" in B.columns:
        acc = B["见底前20日%"].dropna()
        if len(acc):
            print(f"  见底前20日表现: 平均 {acc.mean():.1f}%  中位 {acc.median():.1f}%")
            print(f"    其中跌幅>10%（加速下跌）: {int((acc<-10).sum())}/{len(acc)}")
            print(f"    其中跌幅>15%（恐慌）    : {int((acc<-15).sum())}/{len(acc)}")
            print(f"    → 突然加速下跌常出现在见底前")
    print()
    # 节奏
    if "前半段跌幅%" in B.columns:
        fh = B["前半段跌幅%"].dropna()
        sh = B["后半段跌幅%"].dropna()
        print(f"  跌幅节奏:")
        print(f"    前半段平均: {fh.mean():.1f}%")
        print(f"    后半段平均: {sh.mean():.1f}%")
        print()

    # ---- 保存
    B.to_csv(outdir / "bear_history.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/bear_history.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
