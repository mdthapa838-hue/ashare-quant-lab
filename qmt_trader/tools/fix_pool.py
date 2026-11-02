# -*- coding: utf-8 -*-
"""
股池偏差修复与重跑

问题：
  之前的审计池 611 只 = universe.json 的位置 0~740 段，
  系统性偏向 603/605/688 板块（Tushare 按 ts_code 升序返回）。
  这不是随机抽样，导致结论有偏。

修复：
  从 universe.json 的 2168 只中**分层随机抽样**，保证板块比例与总体一致，
  重新下载数据并重跑信号审计。
"""
from __future__ import annotations

import os
import sys
import json
import time
import glob
import random
from pathlib import Path
from collections import Counter

import pandas as pd
import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import load_config
from core.logger import setup_logger, get_logger
from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound

LOG = get_logger("poolfix")


def board(c: str) -> str:
    if c.startswith("688"):
        return "科创板"
    if c.startswith(("300", "301")):
        return "创业板"
    if c.startswith(("8", "4", "9")):
        return "北交所"
    if c.startswith(("600", "601", "603", "605")):
        return "沪主板"
    if c.startswith(("000", "001", "002", "003")):
        return "深主板"
    return "其他"


def main():
    cfg = load_config()
    setup_logger("poolfix", log_dir=None, level="WARNING", console=True)

    cache = Path(cfg.get("datasource.cache_dir"))
    state = cache.parent / "state"
    uni = json.loads((state / "universe.json").read_text(encoding="utf-8"))
    codes = uni["codes"]

    print("=" * 100)
    print("  股池偏差修复")
    print("=" * 100)
    print()

    # ---- 总体板块分布
    total_board = Counter(board(c) for c in codes)
    print(f"universe.json 共 {len(codes)} 只，板块分布:")
    for k, v in total_board.most_common():
        print(f"    {k:<8} {v:>5} 只  ({v/len(codes)*100:>5.1f}%)")
    print()

    # ---- 分层抽样：按板块比例抽取
    TARGET = 700          # 目标样本量
    rng = random.Random(20261014)
    by_board = {}
    for c in codes:
        by_board.setdefault(board(c), []).append(c)

    sample = []
    for b, lst in by_board.items():
        cnt = max(1, round(len(lst) / len(codes) * TARGET))
        cnt = min(cnt, len(lst))
        sample.extend(rng.sample(lst, cnt))
    sample = sorted(set(sample))

    samp_board = Counter(board(c) for c in sample)
    print(f"分层随机抽样 {len(sample)} 只，板块分布:")
    for k in total_board:
        s = samp_board.get(k, 0)
        t = total_board[k]
        if t:
            print(f"    {k:<8} {s:>5} 只   (总体占比 {t/len(codes)*100:>5.1f}%  "
                  f"样本占比 {s/len(sample)*100:>5.1f}%)")
    print()

    # ---- 同时输出一版纯随机抽样（对照）
    sample_rnd = sorted(rng.sample(codes, min(TARGET, len(codes))))
    print(f"对照：纯随机抽样 {len(sample_rnd)} 只")
    print()

    # ---- 检查本地已有缓存
    have = set()
    for f in glob.glob(str(cache / "kline" / "*.csv")):
        stem = os.path.basename(f)[:-4]
        for suf in ("_qfq", "_hfq", "_bfq"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        if stem.isdigit() and len(stem) == 6:
            have.add(stem)
    print(f"本地已有缓存: {len(have)} 只")
    print()

    # ---- 保存样本清单
    out = cache.parent / "output"
    out.mkdir(parents=True, exist_ok=True)
    p1 = out / "pool_stratified.csv"
    pd.DataFrame({"ts_code": sample, "board": [board(c) for c in sample],
                  "cached": [c[:6] in have for c in sample]}).to_csv(
        p1, index=False, encoding="utf-8-sig")
    p2 = out / "pool_random.csv"
    pd.DataFrame({"ts_code": sample_rnd, "board": [board(c) for c in sample_rnd],
                  "cached": [c[:6] in have for c in sample_rnd]}).to_csv(
        p2, index=False, encoding="utf-8-sig")
    print(f"[输出] 分层样本: {p1}")
    print(f"[输出] 随机样本: {p2}")
    print()

    # ---- 说明修复后的做法
    print("=" * 100)
    print("  修复方案")
    print("=" * 100)
    print()
    print("  1) 用「分层随机抽样」重建审计池，板块比例对齐总体")
    print("  2) 下载全部样本的K线（覆盖完整历史）")
    print("  3) 重跑信号审计")
    print("  4) 与旧池结果对比，量化偏差幅度")
    print()
    print(f"  建议样本量: {len(sample)} 只（占总体 {len(sample)/len(codes)*100:.0f}%）")
    print(f"  当前已有缓存: {len(have)} 只，其中在样本内: "
          f"{len([c for c in sample if c[:6] in have])} 只")
    print()


if __name__ == "__main__":
    main()
