# -*- coding: utf-8 -*-
"""
step12_industry.py — 行业分类数据（用于中性化）

数据源：
  Tushare index_classify  SW2021 一级行业（31个）
  Tushare index_member_all 每个行业的成分股（含 in_date/out_date）

用途：
  - 行业中性化：因子在池内**去行业均值**，排除行业beta
  - 分析：哪些行业贡献了超额收益

输出：
  data_cache/industry/sw_l1.csv        31个一级行业
  data_cache/industry/sw_members.csv   全部成分股（含进出日期）
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def main():
    cfg = load_config()
    setup_logger("ind", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache / "industry"
    outdir.mkdir(parents=True, exist_ok=True)

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    # ---- 1. 一级行业
    print("=" * 100)
    print("  获取申万一级行业（SW2021）")
    print("=" * 100)
    l1 = None
    for attempt in range(3):
        try:
            l1 = pro.index_classify(level="L1", src="SW2021")
            break
        except Exception as e:
            print(f"  重试 {attempt+1}: {str(e)[:50]}")
            time.sleep(2)
    if l1 is None or len(l1) == 0:
        print("获取失败")
        return 1
    print(f"  一级行业: {len(l1)} 个")
    print()
    for _, r in l1.iterrows():
        print(f"    {r['index_code']}  {r['industry_name']}")
    l1.to_csv(outdir / "sw_l1.csv", index=False, encoding="utf-8-sig")
    print()

    # ---- 2. 各行业成分股
    print("=" * 100)
    print("  获取各行业成分股（含进出日期）")
    print("=" * 100)
    print()
    frames = []
    for i, (_, r) in enumerate(l1.iterrows()):
        code = r["index_code"]
        got = None
        for attempt in range(3):
            try:
                got = pro.index_member_all(l1_code=code)
                break
            except Exception as e:
                msg = str(e)
                if "每分钟" in msg or "频率" in msg:
                    time.sleep(8 * (attempt + 1))
                else:
                    time.sleep(1.5 * (attempt + 1))
        if got is not None and len(got):
            got = got.copy()
            got["l1_code"] = code
            got["l1_name"] = r["industry_name"]
            frames.append(got)
            print(f"  {r['industry_name']:<12} {len(got):>5} 只")
        else:
            print(f"  {r['industry_name']:<12} 失败")
        time.sleep(0.35)

    if not frames:
        print("无成分股数据")
        return 1
    M = pd.concat(frames, ignore_index=True)
    print()
    print(f"  合计 {len(M)} 条记录")
    print(f"  涉及股票 {M['ts_code'].nunique()} 只")
    print()

    # ---- 3. 处理进出日期
    for c in ("in_date", "out_date"):
        if c in M.columns:
            M[c] = pd.to_datetime(M[c].astype(str), format="%Y%m%d", errors="coerce")

    M.to_csv(outdir / "sw_members.csv", index=False, encoding="utf-8-sig")

    # ---- 4. 生成「股票 -> 当前行业」映射（用于快速查）
    cur = M[M.get("is_new", 1) == 1] if "is_new" in M.columns else M
    cur = cur.drop_duplicates(subset=["ts_code"], keep="first")
    mp = cur[["ts_code", "l1_code", "l1_name"]].copy()
    mp["code6"] = mp["ts_code"].str[:6]
    mp.to_csv(outdir / "code_industry.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] 当前行业映射: {len(mp)} 只")
    print()

    # ---- 5. 与我们的股池对照
    union = cache.parent / "output" / "universe_union.csv"
    if union.exists():
        U = pd.read_csv(union, dtype={"code6": str})
        have = set(mp["code6"])
        cov = U["code6"].isin(have).sum()
        print(f"  并集 {len(U)} 只中，有行业分类: {cov} ({cov/len(U)*100:.1f}%)")
        # 行业分布
        sub = U[U["code6"].isin(have)].merge(
            mp[["code6", "l1_name"]], on="code6", how="left")
        print()
        print("  并集的行业分布（前15）:")
        for k, v in sub["l1_name"].value_counts().head(15).items():
            print(f"    {k:<14} {v:>5} 只")
    print()
    print(f"[输出] {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
