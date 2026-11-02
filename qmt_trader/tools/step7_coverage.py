# -*- coding: utf-8 -*-
"""
step7_coverage.py — 覆盖率诊断

问题：动态池平均只有 433 只，但真实的 30-80 亿池每年应有 1000-2200 只。
      原因：本地K线只覆盖 1361 只（700分层样本 + 257退市 + 其他），
            而真实的 30-80 亿池每期有 1000+ 只。

本脚本量化覆盖率，为结论定界。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from core.config import load_config
from core.logger import setup_logger
from step3_engine import load_all_kline, DynamicUniverse


def main():
    cfg = load_config()
    setup_logger("cov", log_dir=None, level="ERROR", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))

    data = load_all_kline(cache, include_delisted=True)
    have = set(data.keys())

    uni = DynamicUniverse(cache, 30, 80, exclude_bj=True)

    print("=" * 110)
    print("  覆盖率诊断：本地K线 vs 真实 30-80亿池")
    print("=" * 110)
    print()
    print(f"  本地K线: {len(have)} 只")
    print()
    print(f"  {'时点':<12}{'真实池':>8}{'本地覆盖':>10}{'覆盖率':>9}{'本地总数':>10}")
    print("  " + "-" * 100)

    rows = []
    for sdt, sdf in uni.snapshots[::4]:   # 每4个快照抽样
        in_range = sdf[(sdf["mv_yi"] >= 30) & (sdf["mv_yi"] < 80)]
        covered = in_range[in_range["code6"].isin(have)]
        cov = len(covered) / max(len(in_range), 1) * 100
        rows.append({"时点": sdt.date(), "真实池": len(in_range),
                     "本地覆盖": len(covered), "覆盖率%": round(cov, 1)})
        print(f"  {str(sdt.date()):<12}{len(in_range):>8}{len(covered):>10}{cov:>8.1f}%")

    R = pd.DataFrame(rows)
    print()
    print(f"  平均覆盖率: {R['覆盖率%'].mean():.1f}%")
    print()

    # 关键影响分析
    print("=" * 110)
    print("  对结论的影响")
    print("=" * 110)
    print()
    print("  【可比的部分】")
    print("    - 不同策略之间用**同一股池**，相对排序有效")
    print("    - 无未来函数保证（信号T收盘、成交T+1开盘、市值用当时快照）")
    print("    - 退市股已纳入（257只）")
    print()
    print("  【不可比的部分】")
    print(f"    - 绝对收益受覆盖率限制（平均 {R['覆盖率%'].mean():.1f}%）")
    print("    - 覆盖的样本偏向「2026年仍在30-80亿」的股票")
    print("      即：涨太多离开区间的、跌太多退市的，覆盖率偏低")
    print("      → 这会**高估**表现（幸存者偏差的残余）")
    print()
    print("  【结论口径】")
    print("    - 若某策略在**受限样本**下仍无正收益 → 结论稳健（真实更差）")
    print("    - 若某策略在此样本下有正收益 → 需扩大样本复核")
    print()

    R.to_csv(cache.parent / "output" / "coverage_diag.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] coverage_diag.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
