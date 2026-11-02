# -*- coding: utf-8 -*-
"""
step28_gap_scan_v2.py — 底部跳空扫描 v2（分组输出）

回测发现的关键区分：
  · 位置<20% + 非涨停 → 20日胜率 61.2%，平均 +6.65%  ★最优
  · 位置<20% + 涨停   → 20日胜率 64.5%，平均 +10.16%，但浮亏 -12.38%

所以分成两组输出：
  A组（推荐）：底部跳空 + 非涨停 → 位置分位 < 20%
  B组（观察）：底部跳空 + 涨停   → 位置分位 < 20%
  C组（备选）：底部跳空 + 距高点 < -20%
"""
from __future__ import annotations

import os
import sys
import glob
import time
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def main():
    target_date = "20261015"
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            target_date = a.split("=")[1]

    cfg = load_config()
    setup_logger("g2", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 120)
    print(f"  底部跳空扫描 v2   目标日 {target_date}")
    print("=" * 120)
    print()

    # 日历
    cal = pro.trade_cal(exchange="SSE", start_date="20260601",
                        end_date="20261231")
    opens = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
    avail = [d for d in opens if d <= target_date]
    if not avail:
        print("无可用交易日")
        return 1
    target_date = avail[-1]
    idx = opens.index(target_date)
    prev_date = opens[idx - 1]
    print(f"  交易日: {target_date}   前一日: {prev_date}")
    print()

    # 基础信息
    print("[1] 股票基础信息...")
    basic = None
    for st in ("L", "D", "P"):
        for attempt in range(3):
            try:
                b = pro.stock_basic(exchange="", list_status=st,
                                    fields="ts_code,symbol,name,area,industry,market,list_date")
                if b is not None and len(b):
                    b["list_status"] = st
                    basic = b if basic is None else pd.concat([basic, b])
                break
            except Exception:
                time.sleep(1.0)
    basic = basic.drop_duplicates(subset=["ts_code"])
    basic["code6"] = basic["ts_code"].str[:6]
    bad = basic["name"].str.contains("ST|退", na=False)
    bj = basic["ts_code"].str.endswith(".BJ")
    ld = pd.to_datetime(basic["list_date"], format="%Y%m%d", errors="coerce")
    cutoff = pd.Timestamp(target_date) - pd.Timedelta(days=200)
    basic = basic[(~bad) & (~bj) & (ld <= cutoff)].copy()
    print(f"    {len(basic)} 只（已排除ST/退/北交所/次新）")
    print()

    # 日线（本地缓存）
    print("[2] 加载日线...")
    kdirs = [cache / "kline", cache / "kline_delisted", cache / "kline_full"]
    data = {}
    for c6 in basic["code6"].tolist():
        for kd in kdirs:
            p = kd / f"{c6}_qfq.csv"
            if not p.exists():
                continue
            try:
                d = pd.read_csv(p, usecols=lambda c: c in
                                ("date", "open", "high", "low", "close", "volume"))
                if len(d) < 150:
                    break
                d = d.copy()
                d["date"] = pd.to_datetime(d["date"])
                d = d.sort_values("date").reset_index(drop=True)
                for c in ("open", "high", "low", "close"):
                    d[c] = pd.to_numeric(d[c], errors="coerce")
                if d["date"].max() >= pd.Timestamp(target_date):
                    data[c6] = d
                break
            except Exception:
                break
    print(f"    有效 {len(data)} 只（本地缓存）")
    print()

    # 扫描：宽松条件，分组
    print("=" * 120)
    print("  扫描结果")
    print("=" * 120)
    print()

    LOOKBACK = 130
    rows = []
    for c6, d in data.items():
        try:
            m = d["date"] == pd.Timestamp(target_date)
            if not m.any():
                continue
            i = int(np.where(m.values)[0][0])
            if i < LOOKBACK + 1:
                continue
            t = d.iloc[i]
            y = d.iloc[i - 1]
            if not (np.isfinite(t["low"]) and np.isfinite(y["high"])):
                continue
            if y["high"] <= 0 or t["low"] <= y["high"]:
                continue
            gap = (t["low"] / y["high"] - 1) * 100
            if gap < 0.2:
                continue
            seg = d.iloc[i - LOOKBACK:i + 1]
            hi = seg["high"].max()
            lo = seg["low"].min()
            if not np.isfinite(hi) or hi <= lo:
                continue
            pos = (t["close"] - lo) / (hi - lo) * 100
            dd = (t["close"] / hi - 1) * 100
            chg = (t["close"] / y["close"] - 1) * 100
            # 只要在底部区域就算
            if not (pos <= 25 or dd <= -18):
                continue
            info = basic[basic["code6"] == c6]
            nm = info["name"].iloc[0] if len(info) else "?"
            ind = info["industry"].iloc[0] if len(info) else "?"
            ma60 = float(d["close"].iloc[i - 59:i + 1].mean())
            ma20 = float(d["close"].iloc[i - 19:i + 1].mean())
            vol_ratio = None
            if "volume" in d.columns:
                v = pd.to_numeric(d["volume"], errors="coerce")
                if i >= 20:
                    avg = v.iloc[i - 19:i + 1].mean()
                    if avg > 0:
                        vol_ratio = float(v.iloc[i] / avg)
            rows.append({
                "代码": c6, "名称": nm, "行业": ind,
                "昨收": round(float(y["close"]), 2),
                "今开": round(float(t["open"]), 2),
                "今低": round(float(t["low"]), 2),
                "今收": round(float(t["close"]), 2),
                "跳空%": round(gap, 2),
                "涨跌%": round(chg, 2),
                "位置%": round(pos, 1),
                "距高%": round(dd, 1),
                "距MA20%": round((t["close"] / ma20 - 1) * 100, 1),
                "距MA60%": round((t["close"] / ma60 - 1) * 100, 1),
                "量比": round(vol_ratio, 2) if vol_ratio else None,
                "涨停": chg >= 9.5,
            })
        except Exception:
            continue

    H = pd.DataFrame(rows)
    if len(H) == 0:
        print("  未找到符合条件的标的")
        return 1

    def show(sub, title, note):
        if len(sub) == 0:
            print(f"  【{title}】无")
            print()
            return
        sub = sub.sort_values("跳空%", ascending=False)
        print(f"  【{title}】{len(sub)} 只   {note}")
        print()
        print(f"    {'代码':<8}{'名称':<11}{'行业':<10}{'昨收':>7}{'今开':>7}"
              f"{'今低':>7}{'今收':>7}{'跳空%':>7}{'涨跌%':>8}"
              f"{'位置%':>7}{'距高%':>8}{'量比':>7}")
        print("    " + "-" * 112)
        for _, r in sub.iterrows():
            qb = f"{r['量比']:.2f}" if pd.notna(r["量比"]) else "—"
            print(f"    {r['代码']:<8}{str(r['名称'])[:10]:<11}"
                  f"{str(r['行业'])[:9]:<10}{r['昨收']:>7.2f}{r['今开']:>7.2f}"
                  f"{r['今低']:>7.2f}{r['今收']:>7.2f}{r['跳空%']:>6.2f}%"
                  f"{r['涨跌%']:>7.2f}%{r['位置%']:>6.1f}%{r['距高%']:>7.1f}%"
                  f"{qb:>7}")
        print()

    # 分组
    A = H[(~H["涨停"]) & (H["位置%"] <= 20)]        # 推荐组
    B = H[(H["涨停"]) & (H["位置%"] <= 25)]         # 观察组
    C = H[(~H["涨停"]) & (H["位置%"] > 20) & (H["位置%"] <= 25)]

    show(A, "A组：底部跳空 + 非涨停 + 位置<20%",
         "← 回测最优（20日胜率61.2%，平均+6.65%）")
    show(B, "B组：底部跳空 + 涨停 + 位置<25%",
         "← 弹性大但浮亏深（20日胜率64.5%，浮亏-12.38%）")
    show(C, "C组：底部跳空 + 非涨停 + 位置20~25%",
         "← 次优（20日胜率56.7%）")

    # 汇总
    print("=" * 120)
    print("  全部命中（按位置分位排序，越靠前越「底部」）")
    print("=" * 120)
    print()
    H2 = H.sort_values("位置%")
    print(f"  {'代码':<8}{'名称':<11}{'行业':<10}{'今收':>7}{'跳空%':>7}"
          f"{'涨跌%':>8}{'位置%':>7}{'距高%':>8}{'距MA60%':>9}{'量比':>7}")
    print("  " + "-" * 92)
    for _, r in H2.iterrows():
        qb = f"{r['量比']:.2f}" if pd.notna(r["量比"]) else "—"
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['行业'])[:9]:<10}{r['今收']:>7.2f}{r['跳空%']:>6.2f}%"
              f"{r['涨跌%']:>7.2f}%{r['位置%']:>6.1f}%{r['距高%']:>7.1f}%"
              f"{r['距MA60%']:>8.1f}%{qb:>7}")
    print()

    H.to_csv(outdir / f"gap_scan_v2_{target_date}.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/gap_scan_v2_{target_date}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
