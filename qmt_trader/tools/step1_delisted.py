# -*- coding: utf-8 -*-
"""
步骤1：获取退市股清单

目标：修正幸存者偏差 —— 当前股池只有存续股票，退市股全部缺失。

方法：
  Tushare stock_basic(list_status='D') 获取已退市股票
  同时统计：退市数量、退市年份分布、退市原因（如有）
"""
from __future__ import annotations

import os
import sys
import json
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import load_config
from core.logger import setup_logger
from core.tushare_source import TushareSource


def main():
    cfg = load_config()
    setup_logger("delisted", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.35)

    outdir = cache.parent / "output"
    outdir.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print("  步骤1：获取退市股清单")
    print("=" * 100)
    print()

    # ---- 已退市
    print("[1] 获取已退市股票 (list_status='D')")
    d = src.stock_basic(list_status="D")
    print(f"    返回 {0 if d is None else len(d)} 条")

    # ---- 暂停上市
    print("[2] 获取暂停上市股票 (list_status='P')")
    p = src.stock_basic(list_status="P")
    print(f"    返回 {0 if p is None else len(p)} 条")

    # ---- 存续
    print("[3] 获取存续股票 (list_status='L')")
    l = src.stock_basic(list_status="L")
    print(f"    返回 {0 if l is None else len(l)} 条")
    print()

    frames = []
    for name, df in [("D", d), ("P", p), ("L", l)]:
        if df is not None and len(df):
            t = df.copy()
            t["list_status"] = name
            frames.append(t)
    if not frames:
        print("未获取到任何数据")
        return 1
    ALL = pd.concat(frames, ignore_index=True)

    # 保存
    p1 = outdir / "stock_basic_all.csv"
    ALL.to_csv(p1, index=False, encoding="utf-8-sig")
    print(f"[输出] {p1}")

    # ---- 退市股分析
    if d is not None and len(d):
        D = d.copy()
        D["list_date"] = pd.to_datetime(D["list_date"], errors="coerce")
        if "delist_date" in D.columns:
            D["delist_date"] = pd.to_datetime(D["delist_date"], errors="coerce")

        print()
        print("=" * 100)
        print("  退市股分析")
        print("=" * 100)
        print()
        print(f"  退市股总数: {len(D)}")
        if "delist_date" in D.columns:
            D["delist_year"] = D["delist_date"].dt.year
            g = D.groupby("delist_year").size()
            print()
            print("  按退市年份分布:")
            for y, n in g.items():
                if pd.notna(y):
                    bar = "█" * min(int(n), 50)
                    print(f"    {int(y)}: {int(n):>4} 只  {bar}")
            print()
            # 生存期
            D["life_years"] = (D["delist_date"] - D["list_date"]).dt.days / 365.25
            L = D["life_years"].dropna()
            if len(L):
                print(f"  上市到退市的生存期（年）:")
                print(f"    最短 {L.min():.1f}  中位 {L.median():.1f}  最长 {L.max():.1f}")
                print(f"    不足5年退市: {int((L < 5).sum())} 只")
                print(f"    不足10年退市: {int((L < 10).sum())} 只")
        print()
        # 板块
        def board(c):
            c = str(c)
            if c.startswith("688"):
                return "科创板"
            if c.startswith(("300", "301")):
                return "创业板"
            if c.startswith(("8", "4")):
                return "北交所"
            if c.startswith(("600", "601", "603", "605")):
                return "沪主板"
            if c.startswith(("000", "001", "002", "003")):
                return "深主板"
            return "其他"
        D["board"] = D["ts_code"].map(board)
        print("  退市股板块分布:")
        for k, v in D["board"].value_counts().items():
            print(f"    {k:<8} {v:>4} 只")
        print()
        # 退市前是否ST
        if "name" in D.columns:
            st = D[D["name"].astype(str).str.contains("ST|退", na=False)]
            print(f"  退市时名称含ST/退: {len(st)} 只 ({len(st)/len(D)*100:.1f}%)")
        print()

        # ---- 关键：有多少退市股曾在 30-80 亿市值区间
        print("=" * 100)
        print("  ⚠ 关键问题：退市股是否曾符合我们的股池条件？")
        print("=" * 100)
        print()
        print("  我们无法直接查历史市值，但可以做时间估算：")
        print(f"    - 退市股 {len(D)} 只")
        if "life_years" in D.columns:
            alive_2015 = D[(D["list_date"] <= "2015-01-01") &
                           (D["delist_date"] >= "2015-01-01")]
            print(f"    - 2015年后仍存续的退市股: {len(alive_2015)} 只")
            print(f"      → 这些股票在 2015-2026 回测区间内曾是可交易标的")
            print(f"      → 当前回测【完全遗漏】了它们")
            alive_2020 = D[(D["list_date"] <= "2020-01-01") &
                           (D["delist_date"] >= "2020-01-01")]
            print(f"    - 2020年后仍存续的退市股: {len(alive_2020)} 只")
        print()

    # ---- 输出可下载清单
    if d is not None and len(d):
        need = d.copy()
        if "delist_date" in need.columns:
            need["delist_date"] = pd.to_datetime(need["delist_date"], errors="coerce")
            need = need[need["delist_date"] >= "2015-01-01"]
        codes = need["ts_code"].tolist()
        p2 = outdir / "delisted_to_download.csv"
        pd.DataFrame({"ts_code": codes}).to_csv(p2, index=False, encoding="utf-8-sig")
        print(f"[输出] 需下载的退市股清单（{len(codes)} 只）: {p2}")
        print()

    print("=" * 100)
    print("  小结")
    print("=" * 100)
    print()
    if d is not None and len(d):
        print(f"  已退市: {len(d)} 只")
    if p is not None and len(p):
        print(f"  暂停上市: {len(p)} 只")
    if l is not None and len(l):
        print(f"  存续: {len(l)} 只")
    print()
    print("  下一步: 下载退市股的历史K线（退市前的数据）")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
