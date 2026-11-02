# -*- coding: utf-8 -*-
"""rebuild_history.py — 业绩预告历史重建（独立于 earnings_watch.py）

用途
----
earnings_watch.py 的 calc_positions() 用运行时实时价计算位置分位，
无法用于历史日回补（会产生「窗口是历史、价格是现在」的时点错配）。
本脚本用本地可得的历史数据独立重建，价格一律取公告日当天收盘。

价格来源优先级
--------------
1. data_cache/kline/{code}_qfq.csv        （QMT 前复权缓存）
2. data_cache/kline_delisted/{code}_qfq.csv（退市股）
3. data/raw/mkdaily_2025.csv + mkdaily_2026.csv（本地日线兜底）

估值来源
--------
data/raw/daily_basic_panel.csv（PE/PB/市值/换手率，按公告日精确取）

用法
----
    python tools/rebuild_history.py                    # 重建全部历史
    python tools/rebuild_history.py --dry-run          # 只检查不写

输出
----
    earnings_watch/history/earnings_YYYYMMDD.csv（覆盖）
    原文件先备份到 earnings_watch/history/_backup_YYYYMMDDHHMM/
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_CACHE = os.path.join(ROOT, "data_cache")
RAW = r"F:\deepseek\股市\1\data\raw"
HIST = os.path.join(ROOT, "earnings_watch", "history")

MIN_GROWTH = 50.0


def load_cache_dir(d):
    idx = {}
    for f in glob.glob(os.path.join(d, "*.csv")):
        code = os.path.basename(f).split("_")[0]
        try:
            x = pd.read_csv(f, usecols=["date", "high", "low", "close"])
        except Exception:
            continue
        x["date"] = x["date"].astype(str).str.replace("-", "", regex=False)
        idx[code] = x.sort_values("date").reset_index(drop=True)
    return idx


def build_sources():
    cache = load_cache_dir(os.path.join(DATA_CACHE, "kline"))
    cache.update(load_cache_dir(os.path.join(DATA_CACHE, "kline_delisted")))

    frames = []
    for f in ["mkdaily_2025.csv", "mkdaily_2026.csv"]:
        p = os.path.join(RAW, f)
        if os.path.exists(p):
            frames.append(pd.read_csv(p, usecols=["ts_code", "trade_date",
                                                  "high", "low", "close"]))
    LD = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(LD):
        LD["trade_date"] = LD["trade_date"].astype(str)
        LD["c6"] = LD["ts_code"].str[:6]
        LD = LD.rename(columns={"trade_date": "date"})

    VB = pd.DataFrame()
    p = os.path.join(RAW, "daily_basic_panel.csv")
    if os.path.exists(p):
        VB = pd.read_csv(p, usecols=["ts_code", "trade_date", "turnover_rate",
                                     "pe", "pb", "total_mv"])
        VB["trade_date"] = VB["trade_date"].astype(str)
        VB["c6"] = VB["ts_code"].str[:6]
        VB = VB.rename(columns={"trade_date": "date"})
    return cache, LD, VB


def series(cache, LD, c6):
    s = cache.get(c6)
    l = LD[LD.c6 == c6][["date", "high", "low", "close"]].sort_values("date").reset_index(drop=True) if len(LD) else pd.DataFrame()
    if s is not None and len(s):
        if len(l):
            return pd.concat([s, l], ignore_index=True).drop_duplicates(
                subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
        return s
    return l if len(l) else None


def positions(cache, LD, c6, asof):
    s = series(cache, LD, c6)
    if s is None:
        return None
    x = s[s["date"] <= asof]
    if len(x) < 20:
        return None
    row = x[x["date"] == asof]
    if len(row) == 0:
        # 公告日可能是周末/节假日/未来日（如 10-17 周六、10-20 次日预告）
        # 此时回退到 asof 之前最近的交易日，并在结果中标出实际使用日
        row = x.tail(1)
        used = str(row["date"].iloc[-1])
    else:
        used = asof
    px = float(row["close"].iloc[-1])
    out = {"close": px, "asof_used": used}
    for n, k in [(20, "p20"), (60, "p60"), (120, "p120")]:
        w = x.tail(n)
        if len(w) < n:
            continue
        hi = w["high"].max()
        lo = w["low"].min()
        if hi > lo:
            out[k] = round((px - lo) / (hi - lo) * 100, 1)
    w = x.tail(120)
    hi = w["high"].max()
    out["dd120"] = round((px / hi - 1) * 100, 1) if hi > 0 else None
    return out


def val(VB, c6, asof):
    if not len(VB):
        return {}
    r = VB[(VB.c6 == c6) & (VB.date == asof)]
    if r.empty:
        return {}
    r = r.iloc[0]
    return {
        "pe": float(r.pe) if pd.notna(r.pe) else None,
        "pb": float(r.pb) if pd.notna(r.pb) else None,
        "mv": float(r.total_mv) / 1e4 if pd.notna(r.total_mv) else None,
        "to": float(r.turnover_rate) if pd.notna(r.turnover_rate) else None,
    }


def classify(g, p20, p60, p120, neg):
    if g is None or pd.isna(g):
        return "快报（需核实）"
    if g < 20:
        return "E 低增/负增长"
    flaw = neg >= 2
    if g >= MIN_GROWTH:
        mid_hi = ((p60 is not None and p60 >= 60) or (p120 is not None and p120 >= 60))
        low = (p20 is not None and p20 < 20)
        if mid_hi and not flaw:
            return "A1 高增+中期高位（优选）"
        if mid_hi and flaw:
            return "A1' 高增+高位但措辞有瑕疵"
        if low:
            return "A2 高增+低位（短期）"
        return "B 高增+中位"
    return "D 中增"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    cache, LD, VB = build_sources()
    print("K线缓存: %d 只 | 本地日线: %d 只 | 估值面板: %d 行"
          % (len(cache), LD.c6.nunique() if len(LD) else 0, len(VB)))

    files = sorted(glob.glob(os.path.join(HIST, "earnings_*.csv")))
    if not files:
        print("没有历史档案")
        return 1

    if not args.dry_run:
        stamp = datetime.now().strftime("%Y%m%d%H%M")
        bak = os.path.join(HIST, "_backup_" + stamp)
        os.makedirs(bak, exist_ok=True)
        for f in files:
            shutil.copy2(f, os.path.join(bak, os.path.basename(f)))
        print("已备份到:", bak)

    stat = {"rows": 0, "rebuilt": 0, "no_price": 0}
    for f in files:
        ymd = os.path.basename(f).replace("earnings_", "").replace(".csv", "")
        rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
        if not rows:
            continue
        cols = list(rows[0].keys())
        for r in rows:
            stat["rows"] += 1
            pos = positions(cache, LD, r["代码"], ymd)
            if pos is None:
                stat["no_price"] += 1
                r["位置20日%"] = r["位置60日%"] = r["位置120日%"] = ""
                r["距120日高%"] = ""
                continue
            v = val(VB, r["代码"], ymd)
            try:
                g = float(r["增速%"]) if r["增速%"] not in ("", None) else None
            except (TypeError, ValueError):
                g = None
            try:
                neg = int(r["负面数"]) if r["负面数"] not in ("", None) else 0
            except (TypeError, ValueError):
                neg = 0
            r["现价"] = "%.2f" % pos["close"]
            r["位置20日%"] = pos.get("p20", "")
            r["位置60日%"] = pos.get("p60", "")
            r["位置120日%"] = pos.get("p120", "")
            r["距120日高%"] = pos.get("dd120", "")
            if v:
                r["PE"] = v["pe"]
                r["PB"] = v["pb"]
                r["总市值亿"] = "%.0f" % v["mv"] if v["mv"] else ""
                r["换手%"] = v["to"]
            r["今日%"] = ""
            r["分组"] = classify(g, pos.get("p20"), pos.get("p60"), pos.get("p120"), neg)
            stat["rebuilt"] += 1

        if not args.dry_run:
            with open(f, "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                for x in rows:
                    w.writerow({k: x.get(k, "") for k in cols})

    print("=" * 60)
    print("总行数 %d | 重算 %d | 无价格 %d" % (stat["rows"], stat["rebuilt"], stat["no_price"]))
    if args.dry_run:
        print("[dry-run] 未写入任何文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
