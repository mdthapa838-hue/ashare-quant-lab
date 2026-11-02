# -*- coding: utf-8 -*-
"""
股池构成审计

回答：
  1. 回测/审计到底用了哪些股票？多少个？
  2. 股池是怎么选出来的？用的是哪一天的市值？
  3. 是否存在「幸存者偏差」——只用了存续到最后的股票？
  4. 股池是静态的还是随历史动态重建的？
  5. 数据时间覆盖如何？有没有股票中途消失？
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

setup_logger("pool", log_dir=None, level="ERROR", console=False)
cfg = load_config()


def scan_dir(kdir: Path, label: str) -> pd.DataFrame:
    """扫描一个K线目录，返回每只股票的时间覆盖。"""
    rows = []
    for f in glob.glob(str(kdir / "*.csv")):
        stem = os.path.basename(f)[:-4]
        for suf in ("_qfq", "_hfq", "_bfq"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        if not (stem.isdigit() and len(stem) == 6):
            continue
        try:
            d = pd.read_csv(f, usecols=lambda c: c in ("date", "close", "amount"))
            if len(d) == 0:
                continue
            d["date"] = pd.to_datetime(d["date"], errors="coerce")
            d = d.dropna(subset=["date"])
            if len(d) == 0:
                continue
            rows.append({
                "code": stem,
                "first": d["date"].min(),
                "last": d["date"].max(),
                "bars": len(d),
                "source": label,
            })
        except Exception:
            pass
    return pd.DataFrame(rows)


def main():
    print("=" * 100)
    print("  股池构成审计")
    print("=" * 100)
    print()

    # ---- 各目录
    cache = Path(cfg.get("datasource.cache_dir"))
    ds_dirs = {
        "审计用池 (data_cache/kline)": cache / "kline",
        "研究用池 (research/kline_cache)":
            Path(r"F:\deepseek\股市\股市2\research\smallcap_20261014\out\kline_cache"),
    }

    pools = {}
    for label, d in ds_dirs.items():
        if d.is_dir():
            df = scan_dir(d, label)
            pools[label] = df
            print(f"[{label}]")
            print(f"  标的数: {len(df)}")
            if len(df):
                print(f"  数据起点: {df['first'].min().date()} ~ {df['first'].max().date()}")
                print(f"  数据终点: {df['last'].min().date()} ~ {df['last'].max().date()}")
                print(f"  K线根数: 最少 {df['bars'].min()}  中位 {int(df['bars'].median())}  "
                      f"最多 {df['bars'].max()}")
            print()

    # ---- 当前全市场标的池（来自 state/universe.json）
    uni_p = cache.parent / "state" / "universe.json"
    if uni_p.exists():
        uni = json.loads(uni_p.read_text(encoding="utf-8"))
        print(f"[当前全市场标的池 (state/universe.json)]")
        print(f"  日期: {uni.get('date')}")
        print(f"  标的数: {len(uni.get('codes', []))}")
        print()

    # ---- 幸存者偏差检查
    print("=" * 100)
    print("  【核心】幸存者偏差检查")
    print("=" * 100)
    print()
    for label, df in pools.items():
        if len(df) == 0:
            continue
        latest = df["last"].max()
        # 数据终点明显早于最新日期的（可能退市/长停）
        stale = df[df["last"] < latest - pd.Timedelta(days=30)]
        # 数据起点很晚的（新上市）
        late = df[df["first"] > df["first"].min() + pd.Timedelta(days=365)]
        print(f"[{label}]")
        print(f"  最新数据日: {latest.date()}")
        print(f"  数据终点早于最新30天以上的: {len(stale)} 只 "
              f"({len(stale)/len(df)*100:.1f}%)")
        if len(stale):
            for _, r in stale.head(10).iterrows():
                print(f"      {r['code']}  终点 {r['last'].date()}  ({r['bars']}根)")
        print(f"  数据起点晚于池内最早的1年以上: {len(late)} 只 "
              f"({len(late)/len(df)*100:.1f}%)")
        print()

    # ---- 股池是怎么来的？—— 审查选池逻辑
    print("=" * 100)
    print("  【关键】股池是如何构建的")
    print("=" * 100)
    print()
    print("  审查代码：core/engine.py 的 task_prepare → discover_universe")
    print()
    print("  实际逻辑：")
    print("    build_universe(trade_date) 使用 **trade_date 当天的** daily_basic")
    print("    → 用「当天市值在 30-80 亿」筛选")
    print("    → 用「当天名称不含 ST」筛选")
    print()

    # 检查 universe.json 是否是单日快照
    if uni_p.exists():
        uni = json.loads(uni_p.read_text(encoding="utf-8"))
        print(f"  universe.json 结构: date={uni.get('date')}, codes={len(uni.get('codes',[]))}")
        print(f"  → 这是 **单日快照**，不是历史动态池")
        print()

    print("  ⚠ 问题：回测/审计时用的是**同一个静态池**贯穿全部历史")
    print("     正确做法：每个历史时点都要用「当时」的市值和 ST 状态重建池")
    print()

    # ---- 市值漂移检查（用价格变化估算）
    print("=" * 100)
    print("  【量化】静态池的历史失真程度")
    print("=" * 100)
    print()
    pool = pools.get("审计用池 (data_cache/kline)")
    if pool is not None and len(pool):
        # 用价格涨跌估算：如果一只股票历史涨了3倍，那么当时市值可能只有现在的1/3
        rows = []
        for _, r in pool.iterrows():
            f = cache / "kline" / f"{r['code']}_qfq.csv"
            if not f.exists():
                continue
            try:
                d = pd.read_csv(f, usecols=lambda c: c in ("date", "close"))
                d["date"] = pd.to_datetime(d["date"])
                d = d.sort_values("date")
                if len(d) < 300:
                    continue
                # 最早一年 vs 最近一年的均价
                early = d.head(250)["close"].mean()
                late = d.tail(250)["close"].mean()
                if early > 0:
                    rows.append({
                        "code": r["code"],
                        "早均价": round(early, 2),
                        "近均价": round(late, 2),
                        "涨跌倍数": round(late / early, 2),
                    })
            except Exception:
                pass
        if rows:
            R = pd.DataFrame(rows)
            print(f"  可估算 {len(R)} 只（用前250日 vs 后250日均价）")
            print(f"  价格涨跌倍数: 最小 {R['涨跌倍数'].min():.2f}  "
                  f"中位 {R['涨跌倍数'].median():.2f}  "
                  f"最大 {R['涨跌倍数'].max():.2f}")
            print()
            # 若价格涨了N倍，当时市值 = 现在市值/N（假设股本不变）
            print("  → 若某股历史价格是现在的 1/3，则当时市值约为现在的 1/3")
            print("     当前 30-80 亿的股票，历史市值可能只有 10-27 亿，")
            print("     即【当时并不在 30-80 亿区间内】→ 静态池存在选择偏差")
            print()
            up = R[R["涨跌倍数"] > 1.5]
            dn = R[R["涨跌倍数"] < 0.67]
            print(f"     价格涨超1.5倍的: {len(up)} 只 → 历史市值 < 当前/1.5")
            print(f"     价格跌超33%的:   {len(dn)} 只 → 历史市值 > 当前/0.67")

    # ---- 结论
    print()
    print("=" * 100)
    print("  结论")
    print("=" * 100)
    print()


if __name__ == "__main__":
    main()
