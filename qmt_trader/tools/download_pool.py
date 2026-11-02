# -*- coding: utf-8 -*-
"""
下载分层样本的K线数据。

用于修复股池偏差：样本按板块比例分层随机抽取，
需下载完整历史（2015-01-01 起）以保证月线指标可计算。
"""
from __future__ import annotations

import os
import sys
import glob
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import load_config
from core.logger import setup_logger, get_logger
from core.tushare_source import TushareSource

LOG = get_logger("dl")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "pool_stratified.csv"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    cfg = load_config()
    setup_logger("dl", log_dir=None, level="WARNING", console=True)

    cache = Path(cfg.get("datasource.cache_dir"))
    srcdir = cache.parent / "output"
    p = srcdir / which
    if not p.exists():
        print(f"样本文件不存在: {p}")
        return 1
    df = pd.read_csv(p, dtype={"ts_code": str})
    codes = df["ts_code"].tolist()
    if limit:
        codes = codes[:limit]

    print(f"待下载: {len(codes)} 只  (来源 {p.name})")

    src = TushareSource(cache_dir=str(cache), min_interval=0.32, retry=3)

    t0 = time.time()
    ok = skip = fail = 0
    fails = []
    for i, ts_code in enumerate(codes):
        code6 = ts_code.split(".")[0]
        kp = cache / "kline" / f"{code6}_qfq.csv"
        # 已有且足够长的跳过
        if kp.exists() and kp.stat().st_size > 2000:
            try:
                d = pd.read_csv(kp, usecols=["date"])
                if len(d) >= 400:
                    skip += 1
                    continue
            except Exception:
                pass
        try:
            d = src.daily(ts_code, "20150101",
                          time.strftime("%Y%m%d"), adj="qfq")
            if d is not None and len(d) >= 200:
                ok += 1
            else:
                fail += 1
                fails.append(ts_code)
        except Exception as e:
            fail += 1
            fails.append(ts_code)
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(codes)}  新增 {ok}  跳过 {skip}  失败 {fail}  "
                  f"耗时 {time.time()-t0:.0f}s", flush=True)

    print()
    print(f"完成: 新增 {ok}  跳过 {skip}  失败 {fail}  总耗时 {time.time()-t0:.0f}s")
    if fails:
        print(f"失败样例: {fails[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
