# -*- coding: utf-8 -*-
"""
step8b_download_union.py — 下载并集缺口的K线

目标：把 2015-2026 曾进入 30-80亿 的股票全部补齐（3109只）
  使用 Tushare daily + adj_factor，前复权
  分页拉取（规避单次429行上限）
"""
from __future__ import annotations

import os
import sys
import time
import glob
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def fetch_one(pro, ts_code: str, start: str, end: str, kdir: Path) -> tuple:
    """下载单只，返回 (ts_code, status, rows)"""
    c6 = ts_code.split(".")[0]
    p = kdir / f"{c6}_qfq.csv"
    if p.exists() and p.stat().st_size > 1000:
        return (ts_code, "skip", 0)

    # 分页拉取 daily
    frames = []
    cur_end = pd.Timestamp(end)
    start_ts = pd.Timestamp(start)
    guard = 0
    while cur_end >= start_ts and guard < 15:
        guard += 1
        seg_start = max(start_ts, cur_end - pd.Timedelta(days=650))
        got = None
        for attempt in range(3):
            try:
                got = pro.daily(ts_code=ts_code,
                                start_date=seg_start.strftime("%Y%m%d"),
                                end_date=cur_end.strftime("%Y%m%d"))
                break
            except Exception as e:
                msg = str(e)
                if "每分钟" in msg or "频率" in msg:
                    time.sleep(10 * (attempt + 1))
                else:
                    time.sleep(1.2 * (attempt + 1))
        if got is None or len(got) == 0:
            break
        frames.append(got)
        earliest = pd.to_datetime(got["trade_date"].min(), format="%Y%m%d")
        if earliest <= start_ts:
            break
        cur_end = earliest - pd.Timedelta(days=1)

    if not frames:
        return (ts_code, "nodata", 0)

    dt = pd.concat(frames, ignore_index=True)
    dt = dt.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")

    # 复权因子（分页）
    af_frames = []
    cur_end = pd.Timestamp(end)
    guard = 0
    while cur_end >= start_ts and guard < 15:
        guard += 1
        seg_start = max(start_ts, cur_end - pd.Timedelta(days=650))
        got = None
        for attempt in range(2):
            try:
                got = pro.adj_factor(ts_code=ts_code,
                                     start_date=seg_start.strftime("%Y%m%d"),
                                     end_date=cur_end.strftime("%Y%m%d"))
                break
            except Exception:
                time.sleep(1.0 * (attempt + 1))
        if got is None or len(got) == 0:
            break
        af_frames.append(got)
        earliest = pd.to_datetime(got["trade_date"].min(), format="%Y%m%d")
        if earliest <= start_ts:
            break
        cur_end = earliest - pd.Timedelta(days=1)

    if af_frames:
        afdf = pd.concat(af_frames, ignore_index=True)
        afdf = afdf.drop_duplicates(subset=["trade_date"])
        dt = dt.merge(afdf[["trade_date", "adj_factor"]], on="trade_date", how="left")
        dt = dt.sort_values("trade_date")
        latest = dt["adj_factor"].iloc[-1]
        if pd.notna(latest) and latest > 0:
            dt["adj_factor"] = dt["adj_factor"] / latest
            for col in ("open", "high", "low", "close", "pre_close"):
                if col in dt.columns:
                    dt[col] = dt[col] * dt["adj_factor"]

    out = pd.DataFrame({
        "date": pd.to_datetime(dt["trade_date"], format="%Y%m%d"),
        "open": dt["open"].astype(float),
        "high": dt["high"].astype(float),
        "low": dt["low"].astype(float),
        "close": dt["close"].astype(float),
        "volume": dt["vol"].astype(float) * 100.0,
        "amount": dt["amount"].astype(float) * 1000.0,
    }).sort_values("date").reset_index(drop=True)

    try:
        out.to_csv(p, index=False, encoding="utf-8-sig")
    except Exception:
        return (ts_code, "savefail", 0)
    return (ts_code, "ok", len(out))


def worker(ts_codes: list, kdir: Path, tag: str = ""):
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    ok = skip = nodata = fail = 0
    t0 = time.time()
    fails = []
    for i, tc in enumerate(ts_codes):
        try:
            _, st, n = fetch_one(pro, tc, "20100101", "20261231", kdir)
            if st == "ok":
                ok += 1
            elif st == "skip":
                skip += 1
            else:
                nodata += 1
                fails.append(tc)
        except Exception:
            fail += 1
            fails.append(tc)
        if (i + 1) % 100 == 0:
            print(f"  [{tag}] {i+1}/{len(ts_codes)}  新增{ok} 跳过{skip} "
                  f"无数据{nodata} 失败{fail}  {time.time()-t0:.0f}s", flush=True)
    return ok, skip, nodata, fail, fails


def main():
    cfg = load_config()
    setup_logger("u8", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    need = pd.read_csv(outdir / "universe_need.csv", dtype={"ts_code": str})
    codes = need["ts_code"].dropna().tolist()
    codes = [c for c in codes if isinstance(c, str) and "." in c]
    print(f"待下载: {len(codes)} 只")
    print()

    kdir = cache / "kline"
    kdir.mkdir(exist_ok=True)

    # 分片（避免单进程太久）
    N_SHARD = 4
    shards = [codes[i::N_SHARD] for i in range(N_SHARD)]
    print(f"分 {N_SHARD} 片，每片约 {len(shards[0])} 只")
    print()

    total = [0, 0, 0, 0]
    all_fails = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=N_SHARD) as ex:
        futs = {ex.submit(worker, s, kdir, f"S{i+1}"): i for i, s in enumerate(shards)}
        for f in as_completed(futs):
            try:
                ok, sk, nd, fa, fs = f.result()
                total[0] += ok
                total[1] += sk
                total[2] += nd
                total[3] += fa
                all_fails.extend(fs)
            except Exception as e:
                print(f"  分片失败: {str(e)[:60]}")

    print()
    print(f"完成: 新增{total[0]} 跳过{total[1]} 无数据{total[2]} 失败{total[3]}  "
          f"总耗时 {time.time()-t0:.0f}s")

    if all_fails:
        pd.DataFrame({"ts_code": all_fails}).to_csv(
            outdir / "union_failures.csv", index=False, encoding="utf-8-sig")
        print(f"[输出] 失败清单: {len(all_fails)} 条")

    n = len(list(kdir.glob("*.csv")))
    print(f"kline 目录现有: {n} 只")
    return 0


if __name__ == "__main__":
    sys.exit(main())
