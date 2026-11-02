# -*- coding: utf-8 -*-
"""
步骤2：构建历史时点市值数据（动态股票池基础）

问题：
  当前回测用 2026-10-14 的市值筛选股票池，然后贯穿 2015-2026 全部历史。
  但某股历史市值可能与今天差异巨大（价格涨3倍则历史市值只有1/3）。
  正确做法：每个历史时点用「当时」的市值重建池。

方法：
  Tushare daily_basic 按交易日拉取全市场总市值/流通市值。
  为避免接口压力，按**季度末**采样（约 45 个时点，覆盖 2015-2026）。
  每个时点保存：ts_code, total_mv, circ_mv, close, pe_ttm, pb, turnover_rate

输出：
  data_cache/hist_mktcap/{YYYYMMDD}.csv  每个时点的全市场快照
  data_cache/hist_mktcap_index.csv        汇总索引
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import load_config
from core.logger import setup_logger, get_logger
from core.tushare_source import TushareSource

LOG = get_logger("histmv")


def quarter_ends(start_year: int = 2015, end_year: int = 2026) -> list[str]:
    """生成季度末日期（用每月最后一个交易日近似在拉取时校正）。"""
    out = []
    for y in range(start_year, end_year + 1):
        for m, d in [(3, 31), (6, 30), (9, 30), (12, 31)]:
            out.append(f"{y}{m:02d}{d:02d}")
    # 2026 年只到 10 月
    return [x for x in out if x <= "20261014"]


def main():
    cfg = load_config()
    setup_logger("histmv", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.35, retry=3)

    outdir = cache / "hist_mktcap"
    outdir.mkdir(parents=True, exist_ok=True)

    # 交易日历
    cal = src.trade_calendar("20150101", "20261231")
    cal_set = set(cal)
    print(f"交易日历: {len(cal)} 天  ({cal[0]} ~ {cal[-1]})")

    targets = quarter_ends()
    print(f"目标季度末时点: {len(targets)} 个")
    print()

    # 把目标日期对齐到最近的交易日（向前找）
    aligned = []
    for t in targets:
        # 找 <= t 的最近交易日
        cands = [c for c in cal if c <= t]
        if cands:
            aligned.append((t, cands[-1]))
    print(f"对齐后: {len(aligned)} 个")
    for a, b in aligned[:3]:
        print(f"  {a} -> {b}")
    print("  ...")
    for a, b in aligned[-3:]:
        print(f"  {a} -> {b}")
    print()

    # 拉取
    t0 = time.time()
    ok = skip = fail = 0
    index_rows = []
    for i, (target, actual) in enumerate(aligned):
        p = outdir / f"{actual}.csv"
        if p.exists() and p.stat().st_size > 5000:
            try:
                d = pd.read_csv(p)
                if len(d) > 1000:
                    skip += 1
                    index_rows.append({"target": target, "actual": actual,
                                       "rows": len(d), "status": "cached"})
                    continue
            except Exception:
                pass
        try:
            df = src.daily_basic(actual)
            if df is not None and len(df) > 500:
                df.to_csv(p, index=False, encoding="utf-8-sig")
                ok += 1
                index_rows.append({"target": target, "actual": actual,
                                   "rows": len(df), "status": "fetched"})
            else:
                fail += 1
                index_rows.append({"target": target, "actual": actual,
                                   "rows": 0, "status": "empty"})
        except Exception as e:
            fail += 1
            index_rows.append({"target": target, "actual": actual,
                               "rows": 0, "status": f"err:{str(e)[:30]}"})
        if (i + 1) % 5 == 0:
            print(f"  {i+1}/{len(aligned)}  新增{ok} 缓存{skip} 失败{fail}  "
                  f"{time.time()-t0:.0f}s", flush=True)

    print()
    print(f"完成: 新增{ok} 缓存{skip} 失败{fail}  总耗时{time.time()-t0:.0f}s")

    IDX = pd.DataFrame(index_rows)
    p2 = cache / "hist_mktcap_index.csv"
    IDX.to_csv(p2, index=False, encoding="utf-8-sig")
    print(f"[输出] {p2}")

    # 检查覆盖
    have = IDX[IDX["rows"] > 500]
    print()
    print(f"有效时点: {len(have)}")
    if len(have):
        print(f"  时间范围: {have['actual'].min()} ~ {have['actual'].max()}")
        print(f"  平均股票数: {have['rows'].mean():.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
