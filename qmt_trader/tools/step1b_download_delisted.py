# -*- coding: utf-8 -*-
"""
下载退市股历史K线（修正版）

上一次失败原因：
  1. 部分北交所代码（833xxx/832xxx）在 daily 接口返回空
  2. `use_cache=False` 时仍走缓存路径，重复失败
  3. 失败后无重试

本版：
  - 去掉 use_cache 参数干扰，直连接口
  - 失败重试 3 次，递增等待
  - 记录每只的失败原因
"""
from __future__ import annotations

import os
import sys
import time
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger, get_logger

LOG = get_logger("dl2")


def main():
    cfg = load_config()
    setup_logger("dl2", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    lst = pd.read_csv(cache.parent / "output" / "delisted_to_download.csv",
                      dtype={"ts_code": str})
    codes = lst["ts_code"].tolist()
    kdir = cache / "kline_delisted"
    kdir.mkdir(parents=True, exist_ok=True)

    print(f"待下载: {len(codes)} 只")
    print()

    ok = skip = fail = 0
    fails = []
    t0 = time.time()
    last = 0.0

    for i, ts_code in enumerate(codes):
        c6 = ts_code.split(".")[0]
        if not c6.isdigit():
            # T600018.SH 这种旧代码，跳过
            fails.append({"ts_code": ts_code, "reason": "invalid_code"})
            fail += 1
            continue
        p = kdir / f"{c6}_qfq.csv"
        if p.exists() and p.stat().st_size > 1000:
            skip += 1
            continue

        got = None
        for attempt in range(3):
            gap = time.time() - last
            if gap < 0.35:
                time.sleep(0.35 - gap)
            try:
                last = time.time()
                d = pro.daily(ts_code=ts_code, start_date="20100101",
                              end_date="20261231")
                if d is not None and len(d) >= 30:
                    got = d
                    break
            except Exception as e:
                msg = str(e)
                if "每分钟" in msg or "频率" in msg:
                    time.sleep(12 * (attempt + 1))
                else:
                    time.sleep(1.5 * (attempt + 1))

        if got is None:
            fail += 1
            fails.append({"ts_code": ts_code, "reason": "no_data"})
        else:
            try:
                # 复权
                af = pro.adj_factor(ts_code=ts_code, start_date="20100101",
                                    end_date="20261231")
                dt = got.sort_values("trade_date").copy()
                if af is not None and len(af):
                    dt = dt.merge(af[["trade_date", "adj_factor"]],
                                  on="trade_date", how="left")
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
                out.to_csv(p, index=False, encoding="utf-8-sig")
                ok += 1
            except Exception as e:
                fail += 1
                fails.append({"ts_code": ts_code, "reason": f"save:{str(e)[:40]}"})

        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(codes)}  新增{ok} 跳过{skip} 失败{fail}  "
                  f"{time.time()-t0:.0f}s", flush=True)

    print()
    print(f"完成: 新增{ok} 跳过{skip} 失败{fail}  总耗时{time.time()-t0:.0f}s")

    if fails:
        F = pd.DataFrame(fails)
        F.to_csv(cache.parent / "output" / "delisted_failures.csv",
                 index=False, encoding="utf-8-sig")
        print(f"[输出] 失败清单: {len(F)} 条")
        # 按原因统计
        print()
        print("  失败原因分布:")
        for k, v in F["reason"].value_counts().items():
            print(f"    {k}: {v}")

    # 统计已下载
    n = len(list(kdir.glob("*.csv")))
    print()
    print(f"kline_delisted 目录现有: {n} 只")
    return 0


if __name__ == "__main__":
    sys.exit(main())
