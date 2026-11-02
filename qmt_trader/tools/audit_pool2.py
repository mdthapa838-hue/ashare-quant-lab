# -*- coding: utf-8 -*-
"""
股池构成深度核查

回答三个问题：
  1. 股池里到底是什么股票？（市值分布/板块分布/代码分布）
  2. 520 只是怎么从全市场 5573 只里选出来的？中间有没有额外筛选？
  3. 是否只用了某一类股票（如超小市值/特定板块）？
"""
from __future__ import annotations

import os
import sys
import glob
import json
from pathlib import Path

import pandas as pd
import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from core.config import load_config
from core.logger import setup_logger

setup_logger("pool2", log_dir=None, level="ERROR", console=False)
cfg = load_config()


def get_pool_codes(d: Path) -> list[str]:
    out = []
    for f in glob.glob(str(d / "*.csv")):
        stem = os.path.basename(f)[:-4]
        for suf in ("_qfq", "_hfq", "_bfq"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        if stem.isdigit() and len(stem) == 6:
            out.append(stem)
    return sorted(set(out))


def main():
    cache = Path(cfg.get("datasource.cache_dir"))
    research = Path(r"F:\deepseek\股市\股市2\research\smallcap_20261014\out")

    pool_a = get_pool_codes(cache / "kline")
    pool_r = get_pool_codes(research / "kline_cache")

    print("=" * 100)
    print("  一、股池规模")
    print("=" * 100)
    print(f"  全市场 A 股（含北交所）        : ~5573 只")
    print(f"  30-80亿市值区间（2026-10-14）  : 2168 只")
    print(f"  审计用池 data_cache/kline      : {len(pool_a)} 只")
    print(f"  研究用池 research/kline_cache  : {len(pool_r)} 只")
    print(f"  两池交集                        : {len(set(pool_a) & set(pool_r))} 只")
    print()

    # ---- 板块分布
    def board(c):
        if c.startswith("688"):
            return "科创板"
        if c.startswith("300") or c.startswith("301"):
            return "创业板"
        if c.startswith(("8", "4")):
            return "北交所"
        if c.startswith(("600", "601", "603", "605")):
            return "沪主板"
        if c.startswith(("000", "001", "002", "003")):
            return "深主板"
        return "其他"

    print("=" * 100)
    print("  二、股池板块分布")
    print("=" * 100)
    for label, pool in [("审计用池", pool_a), ("研究用池", pool_r)]:
        s = pd.Series([board(c) for c in pool]).value_counts()
        print(f"  [{label}]  共 {len(pool)} 只")
        for k, v in s.items():
            print(f"      {k:<8} {v:>5} 只  ({v/len(pool)*100:>5.1f}%)")
        print()

    # ---- 市值分布（用 spot 快照）
    print("=" * 100)
    print("  三、市值分布（对照当前快照）")
    print("=" * 100)
    spot = None
    for name in ("spot_mktcap.csv", "spot_all_sina.csv"):
        p = research / name
        if p.exists():
            spot = pd.read_csv(p)
            print(f"  快照来源: {p.name}  ({len(spot)} 行)")
            break
    if spot is not None:
        # 标准化代码
        if "symbol" in spot.columns:
            spot["code6"] = spot["symbol"].astype(str).str[2:].str.zfill(6)
        elif "代码" in spot.columns:
            spot["code6"] = spot["代码"].astype(str).str.zfill(6)
        else:
            spot["code6"] = spot.iloc[:, 0].astype(str).str.zfill(6)

        cap_col = None
        for c in ("mktcap", "总市值", "total_mv"):
            if c in spot.columns:
                cap_col = c
                break
        if cap_col:
            spot["cap_yi"] = pd.to_numeric(spot[cap_col], errors="coerce")
            if spot["cap_yi"].median() > 1e4:      # 单位是万元
                spot["cap_yi"] = spot["cap_yi"] / 1e4

            for label, pool in [("审计用池", pool_a), ("研究用池", pool_r)]:
                sub = spot[spot["code6"].isin(pool)]
                if len(sub) == 0:
                    print(f"  [{label}] 未能匹配到快照")
                    continue
                c = sub["cap_yi"].dropna()
                print(f"  [{label}] 匹配 {len(sub)} 只")
                print(f"      市值区间: {c.min():.1f} ~ {c.max():.1f} 亿")
                print(f"      中位: {c.median():.1f} 亿   均值: {c.mean():.1f} 亿")
                bins = [0, 20, 30, 50, 80, 100, 200, 99999]
                labs = ["<20亿", "20-30亿", "30-50亿", "50-80亿", "80-100亿",
                        "100-200亿", ">200亿"]
                g = pd.cut(c, bins=bins, labels=labs, right=False).value_counts().sort_index()
                for k, v in g.items():
                    if v > 0:
                        print(f"      {k:<10} {v:>5} 只  ({v/len(c)*100:>5.1f}%)")
                print()

    # ---- 是否只用了某一类
    print("=" * 100)
    print("  四、关键判断")
    print("=" * 100)
    print()
    print("  Q: 股池只用了「尾盘股」吗？")
    print()
    print("  说明：'尾盘股'常被理解为收盘前异动的股票。本股池【不是】按此选的。")
    print()
    print("  股池实际筛选条件（按代码审查）：")
    print("    1. 全市场 A 股（sina 快照 ~5573 只）")
    print("    2. 总市值在 30-80 亿之间")
    print("    3. 名称不含 ST / 退")
    print("    4. 排除北交所（代码 8/4 开头）")
    print("    5. 20日均成交额 >= 2000 万（research 版为隐含条件）")
    print()
    print("  → 这是一个【小盘股池】，不是【尾盘股池】")
    print()

    # ---- 未纳入的部分
    print("=" * 100)
    print("  五、哪些股票被排除了")
    print("=" * 100)
    print()
    if spot is not None and cap_col:
        allc = spot["cap_yi"].dropna()
        # 按代码判断板块
        spot["board"] = spot["code6"].map(board)
        spot["in_pool_r"] = spot["code6"].isin(pool_r)
        print(f"  全市场快照: {len(spot)} 只")
        print()
        print("  按市值区间看，研究用池覆盖情况:")
        bins = [0, 20, 30, 50, 80, 100, 200, 99999]
        labs = ["<20亿", "20-30亿", "30-50亿", "50-80亿", "80-100亿",
                "100-200亿", ">200亿"]
        spot["cap_bin"] = pd.cut(spot["cap_yi"], bins=bins, labels=labs, right=False)
        g = spot.groupby("cap_bin", observed=False).agg(
            总数=("code6", "size"),
            在池中=("in_pool_r", "sum"),
        )
        g["覆盖率%"] = (g["在池中"] / g["总数"] * 100).round(1)
        print(g.to_string())
        print()
        # 非30-80亿的
        out_of_range = spot[(spot["cap_yi"] < 30) | (spot["cap_yi"] >= 80)]
        print(f"  市值不在 30-80 亿的: {len(out_of_range)} 只 "
              f"({len(out_of_range)/len(spot)*100:.1f}%) → 已排除")
        print(f"  市值在 30-80 亿的:   {len(spot)-len(out_of_range)} 只")
        in_range = spot[(spot["cap_yi"] >= 30) & (spot["cap_yi"] < 80)]
        in_pool = in_range[in_range["in_pool_r"]]
        print(f"    其中进入研究用池: {len(in_pool)} 只 "
              f"({len(in_pool)/len(in_range)*100:.1f}%)")
        print(f"    未进入（约 {len(in_range)-len(in_pool)} 只）")
        print()
        # 未进入的原因
        miss = in_range[~in_range["in_pool_r"]]
        if len(miss):
            print("  未进入的标的板块分布:")
            mb = miss["board"].value_counts()
            for k, v in mb.head(8).items():
                print(f"      {k:<8} {v:>5} 只")
            print()
            if "name" in miss.columns:
                st = miss[miss["name"].astype(str).str.contains("ST|退", na=False)]
                print(f"      其中含 ST/退: {len(st)} 只 → 已排除")
            bj = miss[miss["code6"].str.startswith(("8", "4"))]
            print(f"      北交所: {len(bj)} 只 → 已排除")


if __name__ == "__main__":
    main()
