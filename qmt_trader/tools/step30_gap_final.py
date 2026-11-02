# -*- coding: utf-8 -*-
"""
step30_gap_final.py — 底部跳空扫描（最终版，全市场）

设计：
  1. 用 Tushare「按日」接口取目标日+前一日全市场原始行情（精确）
  2. 用本地 qfq 缓存算 130 日位置；缓存缺失的从 Tushare 补 130 日
  3. 分组输出，并附上每只的历史验证数据（同位置分位的历史胜率）
"""
from __future__ import annotations

import os
import sys
import glob
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
    target = "20261015"
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            target = a.split("=")[1]
    LOOKBACK = 130

    cfg = load_config()
    setup_logger("gf", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 125)
    print(f"  底部跳空扫描（最终版）  目标日 {target}")
    print("=" * 125)
    print()

    # ---- 日历
    cal = pro.trade_cal(exchange="SSE", start_date="20260601",
                        end_date="20261231")
    opens = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
    avail = [d for d in opens if d <= target]
    target = avail[-1]
    i = opens.index(target)
    prev = opens[i - 1]
    print(f"  交易日 {target}   前一日 {prev}")
    print()

    # ---- 基础信息
    print("[1] 基础信息...")
    basic = None
    for st in ("L", "D", "P"):
        for attempt in range(3):
            try:
                b = pro.stock_basic(exchange="", list_status=st,
                                    fields="ts_code,symbol,name,industry,market,list_date")
                if b is not None and len(b):
                    b = b.copy()
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
    cutoff = pd.Timestamp(target) - pd.Timedelta(days=200)
    basic = basic[(~bad) & (~bj) & (ld <= cutoff)].copy()
    print(f"    {len(basic)} 只（排除ST/退/北交所/次新）")

    # ---- 原始行情（两日，全市场）
    print("[2] 拉取原始行情...")
    def get_day(d):
        for attempt in range(4):
            try:
                g = pro.daily(trade_date=d)
                if g is not None and len(g):
                    g = g.copy()
                    for c in ("open", "high", "low", "close", "pre_close"):
                        g[c] = pd.to_numeric(g[c], errors="coerce")
                    return g
            except Exception:
                time.sleep(1.2 * (attempt + 1))
        return None

    D0 = get_day(prev)
    D1 = get_day(target)
    if D0 is None or D1 is None:
        print("    行情获取失败")
        return 1
    print(f"    {prev}: {len(D0)} 条   {target}: {len(D1)} 条")

    M = D0[["ts_code", "high", "low", "close"]].rename(
        columns={"high": "y_high", "low": "y_low", "close": "y_close"}
    ).merge(D1[["ts_code", "open", "high", "low", "close"]].rename(
        columns={"open": "t_open", "high": "t_high", "low": "t_low",
                 "close": "t_close"}), on="ts_code", how="inner")
    print(f"    合并: {len(M)} 条")
    print()

    # ---- 跳空筛选（先用原始价，避免复权干扰）
    print("[3] 跳空筛选...")
    M = M.copy()
    M["gap"] = (M["t_low"] / M["y_high"] - 1) * 100
    M["chg"] = (M["t_close"] / M["y_close"] - 1) * 100
    # 真正跳空：今日最低 > 昨日最高
    G = M[(M["t_low"] > M["y_high"]) & (M["gap"] >= 0.2)].copy()
    print(f"    向上跳空（今低>昨高，幅度>=0.2%）: {len(G)} 只")
    print()

    if len(G) == 0:
        print("  今日无向上跳空标的")
        return 0

    # ---- 位置计算
    print("[4] 计算 130 日位置...")
    kdirs = [cache / "kline", cache / "kline_delisted", cache / "kline_full"]
    rows = []
    missed = []
    t0 = time.time()
    for k, tc in enumerate(G["ts_code"].tolist()):
        c6 = tc[:6]
        got = None
        for kd in kdirs:
            p = kd / f"{c6}_qfq.csv"
            if p.exists():
                try:
                    d = pd.read_csv(p, usecols=lambda c: c in
                                    ("date", "open", "high", "low",
                                     "close", "volume"))
                    if len(d) >= LOOKBACK + 5:
                        d = d.copy()
                        d["date"] = pd.to_datetime(d["date"])
                        d = d.sort_values("date").reset_index(drop=True)
                        # 用 130 日窗口
                        d = d[d["date"] <= pd.Timestamp(target)].tail(LOOKBACK)
                        if len(d) >= 60:
                            got = d
                    break
                except Exception:
                    pass
        if got is None:
            missed.append(tc)
            continue
        hi = got["high"].max()
        lo = got["low"].min()
        if not np.isfinite(hi) or hi <= lo:
            continue
        r = G[G["ts_code"] == tc].iloc[0]
        pos = (r["t_close"] - lo) / (hi - lo) * 100
        dd = (r["t_close"] / hi - 1) * 100
        ma20 = got["close"].tail(20).mean()
        ma60 = got["close"].tail(60).mean()
        vol_r = None
        if "volume" in got.columns:
            v = pd.to_numeric(got["volume"], errors="coerce")
            avg = v.tail(20).mean()
            if avg > 0:
                # 用原始量（缓存里是复权后的量与原始量成比例）
                vol_r = None   # 用 Tushare 的量
        info = basic[basic["code6"] == c6]
        rows.append({
            "ts_code": tc, "代码": c6,
            "名称": info["name"].iloc[0] if len(info) else "?",
            "行业": info["industry"].iloc[0] if len(info) else "?",
            "昨收": round(r["y_close"], 2),
            "昨高": round(r["y_high"], 2),
            "今开": round(r["t_open"], 2),
            "今低": round(r["t_low"], 2),
            "今高": round(r["t_high"], 2),
            "今收": round(r["t_close"], 2),
            "跳空%": round(r["gap"], 2),
            "涨跌%": round(r["chg"], 2),
            "位置%": round(pos, 1),
            "距高%": round(dd, 1),
            "距MA20%": round((r["t_close"] / ma20 - 1) * 100, 1),
            "距MA60%": round((r["t_close"] / ma60 - 1) * 100, 1),
            "区间高": round(hi, 2), "区间低": round(lo, 2),
        })
    print(f"    完成 {len(rows)} 只   缓存缺失 {len(missed)} 只  "
          f"({time.time()-t0:.0f}s)")
    print()

    # 补缺失的位置数据
    if missed:
        print(f"[5] 补算 {len(missed)} 只缺失的位置...")
        start = (pd.Timestamp(target) -
                 pd.Timedelta(days=int(LOOKBACK * 2.0))).strftime("%Y%m%d")
        ok = 0
        for tc in missed[:400]:
            got = None
            for attempt in range(2):
                try:
                    got = pro.daily(ts_code=tc, start_date=start,
                                    end_date=target)
                    break
                except Exception:
                    time.sleep(0.5)
            if got is None or len(got) < 60:
                continue
            got = got.copy()
            got["date"] = pd.to_datetime(got["trade_date"], format="%Y%m%d")
            got = got.sort_values("date").tail(LOOKBACK)
            for c in ("high", "low", "close"):
                got[c] = pd.to_numeric(got[c], errors="coerce")
            hi = got["high"].max()
            lo = got["low"].min()
            if not np.isfinite(hi) or hi <= lo:
                continue
            r = G[G["ts_code"] == tc].iloc[0]
            pos = (r["t_close"] - lo) / (hi - lo) * 100
            dd = (r["t_close"] / hi - 1) * 100
            ma20 = got["close"].tail(20).mean()
            ma60 = got["close"].tail(60).mean()
            c6 = tc[:6]
            info = basic[basic["code6"] == c6]
            rows.append({
                "ts_code": tc, "代码": c6,
                "名称": info["name"].iloc[0] if len(info) else "?",
                "行业": info["industry"].iloc[0] if len(info) else "?",
                "昨收": round(r["y_close"], 2),
                "昨高": round(r["y_high"], 2),
                "今开": round(r["t_open"], 2),
                "今低": round(r["t_low"], 2),
                "今高": round(r["t_high"], 2),
                "今收": round(r["t_close"], 2),
                "跳空%": round(r["gap"], 2),
                "涨跌%": round(r["chg"], 2),
                "位置%": round(pos, 1),
                "距高%": round(dd, 1),
                "距MA20%": round((r["t_close"] / ma20 - 1) * 100, 1),
                "距MA60%": round((r["t_close"] / ma60 - 1) * 100, 1),
                "区间高": round(hi, 2), "区间低": round(lo, 2),
            })
            ok += 1
        print(f"    补算成功 {ok} 只")
        print()

    H = pd.DataFrame(rows)
    if len(H) == 0:
        print("  无法计算位置")
        return 1

    # ---- 输出
    print("=" * 125)
    print(f"  全部向上跳空标的（共 {len(H)} 只，按位置分位排序）")
    print("=" * 125)
    print()
    H = H.sort_values("位置%")
    print(f"  {'代码':<8}{'名称':<11}{'行业':<10}{'昨收':>7}{'今开':>7}"
          f"{'今低':>7}{'今收':>7}{'跳空%':>7}{'涨跌%':>8}"
          f"{'位置%':>7}{'距高%':>8}{'距MA60%':>9}")
    print("  " + "-" * 112)
    for _, r in H.iterrows():
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['行业'])[:9]:<10}{r['昨收']:>7.2f}{r['今开']:>7.2f}"
              f"{r['今低']:>7.2f}{r['今收']:>7.2f}{r['跳空%']:>6.2f}%"
              f"{r['涨跌%']:>7.2f}%{r['位置%']:>6.1f}%{r['距高%']:>7.1f}%"
              f"{r['距MA60%']:>8.1f}%")
    print()

    # ---- 分组
    print("=" * 125)
    print("  分组（按回测的胜率排序）")
    print("=" * 125)
    print()
    GRP = [
        ("A组 最优", (H["位置%"] <= 20) & (H["涨跌%"] < 9.5),
         "位置<20% + 非涨停 → 历史20日胜率 61.2%，平均 +6.65%", "★★★"),
        ("B组 次优", (H["位置%"] > 20) & (H["位置%"] <= 30) & (H["涨跌%"] < 9.5),
         "位置20-30% + 非涨停 → 历史20日胜率 56.7%", "★★"),
        ("C组 弹性", (H["位置%"] <= 25) & (H["涨跌%"] >= 9.5),
         "位置<25% + 涨停 → 历史20日胜率 64.5%，但浮亏-12.4%", "★★"),
        ("D组 一般", H["位置%"] > 30,
         "位置>30% → 不算底部，历史胜率<50%", "★"),
    ]
    for name, mask, note, star in GRP:
        sub = H[mask]
        if len(sub) == 0:
            print(f"  【{name}】{star}  无标的")
            print(f"      {note}")
            print()
            continue
        sub = sub.sort_values("位置%")
        print(f"  【{name}】{star}  {len(sub)} 只")
        print(f"      {note}")
        print()
        print(f"      {'代码':<8}{'名称':<11}{'行业':<10}{'今收':>8}"
              f"{'跳空%':>7}{'涨跌%':>8}{'位置%':>7}{'距高%':>8}")
        print("      " + "-" * 72)
        for _, r in sub.iterrows():
            print(f"      {r['代码']:<8}{str(r['名称'])[:10]:<11}"
                  f"{str(r['行业'])[:9]:<10}{r['今收']:>8.2f}"
                  f"{r['跳空%']:>6.2f}%{r['涨跌%']:>7.2f}%"
                  f"{r['位置%']:>6.1f}%{r['距高%']:>7.1f}%")
        print()

    H.to_csv(outdir / f"gap_final_{target}.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/gap_final_{target}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
