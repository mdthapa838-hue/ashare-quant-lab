# -*- coding: utf-8 -*-
"""
step31_realtime_gap.py — 当日实时跳空扫描

数据源：腾讯批量行情（一次800只，含当日实时开/高/低/现价）
      备选：新浪批量

用法：
    python tools/step31_realtime_gap.py              # 扫描当前时点
    python tools/step31_realtime_gap.py --gap 0.5    # 最小跳空幅度

输出：
    当日向上跳空标的（含位置分位、分组）
"""
from __future__ import annotations

import os
import re
import sys
import time
import glob
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger

LOOKBACK = 130


def fetch_tencent(codes: list, batch: int = 600) -> dict:
    """
    腾讯批量行情。

    字段（v_ 格式，~ 分隔）：
      0 未知 1 名称 2 代码 3 现价 4 昨收 5 今开 6 成交量(手)
      7 外盘 8 内盘 9-28 五档买卖
      29 时间 30 涨跌 31 涨跌% 32 最高 33 最低
    """
    out = {}
    hdr = {"User-Agent": "Mozilla/5.0"}
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        try:
            r = requests.get(url, headers=hdr, timeout=15)
            r.encoding = "gbk"
            for line in r.text.strip().split("\n"):
                m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
                if not m:
                    continue
                k, v = m.group(1), m.group(2)
                if not v:
                    continue
                parts = v.split("~")
                if len(parts) < 34:
                    continue
                try:
                    out[k] = {
                        "name": parts[1],
                        "code": parts[2],
                        "price": float(parts[3]) if parts[3] else np.nan,
                        "pre_close": float(parts[4]) if parts[4] else np.nan,
                        "open": float(parts[5]) if parts[5] else np.nan,
                        "time": parts[30] if len(parts) > 30 else "",
                        "high": float(parts[33]) if parts[33] else np.nan,
                        "low": float(parts[34]) if len(parts) > 34 and parts[34] else np.nan,
                    }
                except Exception:
                    continue
        except Exception as e:
            print(f"    批次 {i//batch+1} 失败: {str(e)[:50]}")
        time.sleep(0.15)
    return out


def main():
    gap_min = 0.2
    for a in sys.argv[1:]:
        if a.startswith("--gap="):
            gap_min = float(a.split("=")[1])

    cfg = load_config()
    setup_logger("rt", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    now = datetime.now()
    print("=" * 125)
    print(f"  当日实时跳空扫描   {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 125)
    print()

    # ---- 全市场代码（用本地缓存文件列表，最全且含退市）
    codes = []
    for d in ("kline", "kline_delisted", "kline_full"):
        for f in glob.glob(str(cache / d / "*.csv")):
            stem = os.path.basename(f)[:-4]
            for suf in ("_qfq", "_hfq", "_bfq"):
                if stem.endswith(suf):
                    stem = stem[:-len(suf)]
                    break
            if stem.isdigit() and len(stem) == 6:
                codes.append(stem)
    codes = sorted(set(codes))
    # 转腾讯格式
    def to_tx(c6):
        if c6.startswith(("60", "68", "9")):
            return "sh" + c6
        if c6.startswith(("83", "87", "43", "92")):
            return "bj" + c6
        return "sz" + c6
    tx = [to_tx(c) for c in codes]
    print(f"[1] 代码池: {len(tx)} 只")

    # ---- 实时行情
    print("[2] 拉取实时行情（腾讯批量）...")
    t0 = time.time()
    RT = fetch_tencent(tx)
    print(f"    获取 {len(RT):,} 只  ({time.time()-t0:.0f}s)")

    # 数据时间
    times = [v["time"] for v in RT.values() if v.get("time")]
    if times:
        print(f"    行情时间: {max(set(times), key=times.count)}")
    print()

    if len(RT) < 1000:
        print("  实时数据不足，可能网络受限")
        return 1

    # ---- 前一交易日（从全市场取众数，避免样本偏差）
    print("[3] 读取前一日数据（本地缓存）...")
    from collections import Counter
    dcnt = Counter()
    for c6 in codes:
        for d in ("kline", "kline_delisted", "kline_full"):
            p = cache / d / f"{c6}_qfq.csv"
            if not p.exists():
                continue
            try:
                x = pd.read_csv(p, usecols=["date"])
                dcnt[str(pd.to_datetime(x["date"]).max().date())] += 1
            except Exception:
                pass
            break
    if not dcnt:
        print("    无法确定前一日")
        return 1
    ydate = pd.Timestamp(max(dcnt, key=dcnt.get))
    print(f"    本地最新日期（众数）: {ydate.date()}   覆盖 {dcnt[str(ydate.date())]} 只")
    top3 = sorted(dcnt.items(), key=lambda x: -x[1])[:3]
    print(f"    分布前3: {top3}")
    today = pd.Timestamp(now.date())
    if ydate >= today:
        print(f"    ⚠ 本地缓存已含今日，跳空判断可能不准")
    print()

    # ---- 逐只扫描（需要前一日高 + 130日位置）
    print("[4] 扫描跳空...")
    t0 = time.time()
    rows = []
    checked = 0
    for k, rt in RT.items():
        c6 = rt["code"]
        if not c6:
            continue
        # 排除 ST/退
        nm = rt["name"]
        if "ST" in nm or "退" in nm:
            continue
        if c6.startswith(("83", "87", "43", "92", "9")):
            continue
        # 今日数据有效性
        o, h, l, p = rt["open"], rt["high"], rt["low"], rt["price"]
        if not all(np.isfinite(x) and x > 0 for x in (o, h, l, p)):
            continue
        # 本地历史
        hist = None
        for d in ("kline", "kline_delisted", "kline_full"):
            fp = cache / d / f"{c6}_qfq.csv"
            if fp.exists():
                try:
                    x = pd.read_csv(fp, usecols=lambda c: c in
                                    ("date", "open", "high", "low", "close"))
                    if len(x) < 60:
                        break
                    x = x.copy()
                    x["date"] = pd.to_datetime(x["date"])
                    hist = x.sort_values("date").reset_index(drop=True)
                except Exception:
                    pass
                break
        if hist is None or len(hist) < 60:
            continue
        # 前一日高（用本地最新一根，排除今日）
        h2 = hist[hist["date"] < today]
        if len(h2) < 60:
            continue
        y_high = float(h2["high"].iloc[-1])
        y_close = float(h2["close"].iloc[-1])
        if not (np.isfinite(y_high) and y_high > 0):
            continue
        # 跳空：今日最低 > 昨日最高
        # 注意：本地是前复权价，实时是原始价 → 需换算
        # 用比例：实时昨收 / 本地昨收 = 换算系数
        ratio = rt["pre_close"] / y_close if y_close > 0 else np.nan
        if not np.isfinite(ratio) or ratio <= 0:
            continue
        y_high_adj = y_high * ratio
        if l <= y_high_adj:
            continue
        gap = (l / y_high_adj - 1) * 100
        if gap < gap_min:
            continue
        # 位置（用本地历史 + 换算后的今日价）
        seg = h2.tail(LOOKBACK)
        hi = float(seg["high"].max()) * ratio
        lo = float(seg["low"].min()) * ratio
        if not (np.isfinite(hi) and hi > lo):
            continue
        pos = (p - lo) / (hi - lo) * 100
        dd = (p / hi - 1) * 100
        ma20 = float(seg["close"].tail(20).mean()) * ratio
        ma60 = float(seg["close"].tail(60).mean()) * ratio
        chg = (p / rt["pre_close"] - 1) * 100 if rt["pre_close"] > 0 else np.nan
        checked += 1
        rows.append({
            "代码": c6, "名称": nm,
            "昨收": round(rt["pre_close"], 2),
            "今开": round(o, 2),
            "今低": round(l, 2),
            "今高": round(h, 2),
            "现价": round(p, 2),
            "跳空%": round(gap, 2),
            "涨跌%": round(chg, 2) if np.isfinite(chg) else None,
            "位置%": round(pos, 1),
            "距高%": round(dd, 1),
            "距MA20%": round((p / ma20 - 1) * 100, 1),
            "距MA60%": round((p / ma60 - 1) * 100, 1),
        })
    print(f"    扫描 {checked} 只，命中 {len(rows)} 只  ({time.time()-t0:.0f}s)")
    print()

    if not rows:
        print("  当前时点无向上跳空标的")
        return 0

    H = pd.DataFrame(rows).sort_values("位置%")
    print("=" * 125)
    print(f"  当日跳空标的（{len(H)} 只，按位置分位排序）")
    print("=" * 125)
    print()
    print(f"  {'代码':<8}{'名称':<11}{'昨收':>7}{'今开':>7}{'今低':>7}"
          f"{'现价':>7}{'跳空%':>7}{'涨跌%':>8}{'位置%':>7}"
          f"{'距高%':>8}{'距MA60%':>9}")
    print("  " + "-" * 100)
    for _, r in H.iterrows():
        ch = f"{r['涨跌%']:.2f}" if pd.notna(r["涨跌%"]) else "—"
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}{r['昨收']:>7.2f}"
              f"{r['今开']:>7.2f}{r['今低']:>7.2f}{r['现价']:>7.2f}"
              f"{r['跳空%']:>6.2f}%{ch:>7}%{r['位置%']:>6.1f}%"
              f"{r['距高%']:>7.1f}%{r['距MA60%']:>8.1f}%")
    print()

    # 分组
    print("=" * 125)
    print("  分组")
    print("=" * 125)
    print()
    def grp(r):
        if r["位置%"] <= 20 and (r["涨跌%"] or 0) < 9.5:
            return "A组 底部+非涨停 ★★★"
        if 20 < r["位置%"] <= 30 and (r["涨跌%"] or 0) < 9.5:
            return "B组 次优 ★★"
        if r["位置%"] <= 25 and (r["涨跌%"] or 0) >= 9.5:
            return "C组 底部+涨停 ★★"
        return "D组 非底部 ★"
    H["分组"] = H.apply(grp, axis=1)
    order = ["A组 底部+非涨停 ★★★", "B组 次优 ★★",
             "C组 底部+涨停 ★★", "D组 非底部 ★"]
    for g in order:
        sub = H[H["分组"] == g]
        if len(sub) == 0:
            print(f"  【{g}】无")
            continue
        sub = sub.sort_values("位置%")
        print(f"  【{g}】{len(sub)} 只")
        print(f"    {'代码':<8}{'名称':<11}{'现价':>8}{'跳空%':>7}"
              f"{'涨跌%':>8}{'位置%':>7}{'距高%':>8}")
        print("    " + "-" * 62)
        for _, r in sub.iterrows():
            ch = f"{r['涨跌%']:.2f}" if pd.notna(r["涨跌%"]) else "—"
            print(f"    {r['代码']:<8}{str(r['名称'])[:10]:<11}"
                  f"{r['现价']:>8.2f}{r['跳空%']:>6.2f}%{ch:>7}%"
                  f"{r['位置%']:>6.1f}%{r['距高%']:>7.1f}%")
        print()

    stamp = now.strftime("%Y%m%d_%H%M")
    H.to_csv(outdir / f"gap_realtime_{stamp}.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/gap_realtime_{stamp}.csv")
    print(f"        （数据时点 {max(set(times), key=times.count) if times else '?'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
