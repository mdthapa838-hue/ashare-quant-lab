# -*- coding: utf-8 -*-
"""
step29_append_daily.py — 用「按日拉取」快速补齐本地K线

pro.daily(trade_date=X) 一次返回全市场（约5565行），
比逐只拉取快 5000 倍。

用途：把最新交易日追加到本地 kline/ 缓存。
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
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def main():
    cfg = load_config()
    setup_logger("app", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    # 找本地最新日期
    kdir = cache / "kline"
    files = glob.glob(str(kdir / "*.csv"))
    print(f"kline 文件: {len(files)}")
    sample = pd.read_csv(files[0], usecols=["date"])
    local_max = pd.to_datetime(sample["date"]).max()
    print(f"本地最新日期: {local_max.date()}")
    print()

    # 交易日历
    cal = pro.trade_cal(exchange="SSE", start_date="20260901",
                        end_date="20261231")
    opens = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
    todo = [d for d in opens if pd.Timestamp(d) > local_max]
    if not todo:
        print("已是最新，无需补齐")
        return 0
    print(f"需补齐: {todo}")
    print()

    # 逐日拉取全市场
    all_rows = []
    for d in todo:
        got = None
        for attempt in range(3):
            try:
                got = pro.daily(trade_date=d)
                break
            except Exception:
                time.sleep(1.0)
        if got is None or len(got) == 0:
            print(f"  {d}: 无数据（可能未收盘）")
            continue
        got = got.copy()
        got["date"] = pd.to_datetime(got["trade_date"], format="%Y%m%d")
        all_rows.append(got)
        print(f"  {d}: {len(got)} 条")
        time.sleep(0.4)

    if not all_rows:
        print("无新数据")
        return 1
    NEW = pd.concat(all_rows, ignore_index=True)
    print()
    print(f"合计 {len(NEW)} 条  股票数 {NEW['ts_code'].nunique()}")
    print()

    # 复权因子（一次拉取全市场）
    af_rows = []
    for d in todo:
        got = None
        for attempt in range(3):
            try:
                got = pro.adj_factor(trade_date=d)
                break
            except Exception:
                time.sleep(1.0)
        if got is not None and len(got):
            got = got.copy()
            got["date"] = pd.to_datetime(got["trade_date"], format="%Y%m%d")
            af_rows.append(got[["ts_code", "date", "adj_factor"]])
            print(f"  adj {d}: {len(got)} 条")
            time.sleep(0.3)
    AF = pd.concat(af_rows, ignore_index=True) if af_rows else None

    # 更新本地文件
    print()
    print("更新本地缓存...")
    t0 = time.time()
    ok = skip = 0
    by_code = dict(tuple(NEW.groupby("ts_code")))
    af_by = dict(tuple(AF.groupby("ts_code"))) if AF is not None else {}

    for i, (tc, newdf) in enumerate(by_code.items()):
        c6 = tc[:6]
        p = kdir / f"{c6}_qfq.csv"
        if not p.exists():
            skip += 1
            continue
        try:
            old = pd.read_csv(p)
            if "date" not in old.columns:
                skip += 1
                continue
            old["date"] = pd.to_datetime(old["date"])
            old_max = old["date"].max()

            nd = newdf.copy()
            nd = nd.rename(columns={"vol": "volume"})
            # 用 adj_factor 做前复权：新数据保持与原文件同比例
            # 原文件是「截至最后一日」的前复权，所以新的因子要除以最新因子
            if tc in af_by:
                af = af_by[tc][["date", "adj_factor"]]
                nd = nd.merge(af, on="date", how="left")
                last_f = nd["adj_factor"].dropna()
                if len(last_f):
                    base = last_f.iloc[-1]
                    if base > 0:
                        nd["adj_factor"] = nd["adj_factor"] / base
                        for c in ("open", "high", "low", "close", "pre_close"):
                            if c in nd.columns:
                                nd[c] = pd.to_numeric(nd[c], errors="coerce") \
                                    * nd["adj_factor"]
            nd = nd[nd["date"] > old_max]
            if len(nd) == 0:
                skip += 1
                continue
            keep = [c for c in old.columns if c in nd.columns]
            if not keep:
                # 列不匹配：用标准列
                out = pd.DataFrame({
                    "date": nd["date"],
                    "open": nd.get("open"), "high": nd.get("high"),
                    "low": nd.get("low"), "close": nd.get("close"),
                    "volume": nd.get("volume"), "amount": nd.get("amount"),
                })
            else:
                out = nd[keep]
            merged = pd.concat([old, out], ignore_index=True)
            merged = merged.drop_duplicates(subset=["date"]).sort_values("date")
            merged.to_csv(p, index=False, encoding="utf-8-sig")
            ok += 1
        except Exception:
            skip += 1
        if (i + 1) % 1000 == 0:
            print(f"  {i+1}/{len(by_code)}  更新 {ok}  跳过 {skip}  "
                  f"{time.time()-t0:.0f}s", flush=True)

    print(f"  完成: 更新 {ok}  跳过 {skip}  {time.time()-t0:.0f}s")
    print()

    # 验证
    files2 = glob.glob(str(kdir / "*.csv"))
    s2 = pd.read_csv(files2[0], usecols=["date"])
    print(f"验证: {os.path.basename(files2[0])} 最新 {pd.to_datetime(s2['date']).max().date()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
