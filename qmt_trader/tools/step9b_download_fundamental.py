# -*- coding: utf-8 -*-
"""
step9b_download_fundamental.py — 批量下载财务数据（PIT 就绪）

下载 fina_indicator（含 ann_date 公告日），保证 PIT 正确。
分片并行，失败重试。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def to_ts(c6: str) -> str:
    if c6.startswith(("60", "68")):
        return c6 + ".SH"
    if c6.startswith(("8", "4", "9")):
        return c6 + ".BJ"
    return c6 + ".SZ"


def worker(codes: list, outdir: Path, tag: str = ""):
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    ok = skip = fail = 0
    t0 = time.time()
    for i, c6 in enumerate(codes):
        p = outdir / f"{c6}.csv"
        if p.exists() and p.stat().st_size > 500:
            skip += 1
            continue
        tc = to_ts(c6)
        got = None
        for attempt in range(3):
            try:
                got = pro.fina_indicator(ts_code=tc, start_date="20140101",
                                         end_date="20261231")
                break
            except Exception as e:
                msg = str(e)
                if "每分钟" in msg or "频率" in msg:
                    time.sleep(9 * (attempt + 1))
                else:
                    time.sleep(1.2 * (attempt + 1))
        if got is not None and len(got):
            try:
                # 去重（Tushare 常有重复行）+ 按公告日排序
                got = got.drop_duplicates(subset=["end_date", "ann_date"])
                got = got.sort_values(["ann_date", "end_date"])
                got.to_csv(p, index=False, encoding="utf-8-sig")
                ok += 1
            except Exception:
                fail += 1
        else:
            fail += 1
        if (i + 1) % 100 == 0:
            print(f"  [{tag}] {i+1}/{len(codes)}  新增{ok} 跳过{skip} "
                  f"失败{fail}  {time.time()-t0:.0f}s", flush=True)
    return ok, skip, fail


def main():
    cfg = load_config()
    setup_logger("f9", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache / "fundamental"
    outdir.mkdir(parents=True, exist_ok=True)

    union = pd.read_csv(cache.parent / "output" / "universe_union.csv",
                        dtype={"code6": str})
    codes = union["code6"].dropna().tolist()
    print(f"目标: {len(codes)} 只")

    have = {f[:-4] for f in os.listdir(outdir) if f.endswith(".csv")}
    todo = [c for c in codes if c not in have]
    print(f"已有 {len(have)}  待下载 {len(todo)}")
    print()

    if not todo:
        print("无需下载")
        return 0

    N_SHARD = 3
    shards = [todo[i::N_SHARD] for i in range(N_SHARD)]
    print(f"分 {N_SHARD} 片，每片约 {len(shards[0])} 只")
    print()

    tot = [0, 0, 0]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=N_SHARD) as ex:
        futs = [ex.submit(worker, s, outdir, f"F{i+1}") for i, s in enumerate(shards)]
        for f in as_completed(futs):
            try:
                a, b, c = f.result()
                tot[0] += a; tot[1] += b; tot[2] += c
            except Exception as e:
                print(f"  分片失败: {str(e)[:60]}")

    print()
    print(f"完成: 新增{tot[0]} 跳过{tot[1]} 失败{tot[2]}  耗时 {time.time()-t0:.0f}s")
    n = len([f for f in os.listdir(outdir) if f.endswith(".csv")])
    print(f"fundamental 目录现有: {n} 只")
    return 0


if __name__ == "__main__":
    sys.exit(main())
