# -*- coding: utf-8 -*-
"""
step8_universe_union.py — 计算 2015-2026 曾进入 30-80亿 的股票并集

方法：
  已有 47 个季度末全市场市值快照（hist_mktcap/*.csv）。
  对每个快照，取 30-80 亿的股票；
  并集 = 曾经在任一时点进入过该区间的全部股票。

注意：
  快照是季度末采样，会遗漏「区间内停留不足一个季度」的股票。
  但相比只用当前快照，并集已大幅接近真实历史池。

输出：
  output/universe_union.csv     并集清单（含是否已下载）
  output/universe_need.csv      需要下载的清单
"""
from __future__ import annotations

import os
import sys
import glob
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import load_config
from core.logger import setup_logger


def main():
    cfg = load_config()
    setup_logger("union", log_dir=None, level="ERROR", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"
    outdir.mkdir(parents=True, exist_ok=True)

    print("=" * 110)
    print("  计算历史 30-80亿 股票并集")
    print("=" * 110)
    print()

    snaps = sorted(glob.glob(str(cache / "hist_mktcap" / "*.csv")))
    print(f"市值快照: {len(snaps)} 个")

    # 逐快照取 30-80 亿
    rows = []
    for f in snaps:
        dt = pd.Timestamp(os.path.basename(f)[:-4])
        try:
            t = pd.read_csv(f, usecols=lambda c: c in ("ts_code", "total_mv"))
            if "ts_code" not in t.columns or "total_mv" not in t.columns:
                continue
            t = t.copy()
            t["code6"] = t["ts_code"].str[:6]
            t["mv_yi"] = pd.to_numeric(t["total_mv"], errors="coerce") / 1e4
            t = t.dropna(subset=["mv_yi"])
            sub = t[(t["mv_yi"] >= 30) & (t["mv_yi"] < 80)]
            sub = sub[~sub["code6"].str.startswith(("8", "4", "9"))]
            for c6 in sub["code6"].unique():
                rows.append({"date": dt, "code6": c6})
        except Exception as e:
            print(f"  {dt.date()} 读取失败: {str(e)[:40]}")

    LONG = pd.DataFrame(rows)
    print(f"总记录: {len(LONG):,}")
    print()

    # 并集
    union = LONG.groupby("code6").agg(
        出现次数=("date", "size"),
        首次出现=("date", "min"),
        末次出现=("date", "max"),
    ).reset_index().sort_values("首次出现")

    print(f"并集规模: {len(union)} 只（曾经进入过30-80亿）")
    print()
    print("  出现次数分布（在多少个季度末快照中被选中）:")
    vc = union["出现次数"].value_counts().sort_index()
    for k, v in vc.head(15).items():
        print(f"    {k:>2} 次: {v:>5} 只")
    print(f"    ...")
    print(f"    最多 {union['出现次数'].max()} 次（= 全部 {len(snaps)} 个快照）")
    print()

    # 现有K线
    have = set()
    for d in (cache / "kline", cache / "kline_delisted"):
        if not d.is_dir():
            continue
        for f in glob.glob(str(d / "*.csv")):
            stem = os.path.basename(f)[:-4]
            for suf in ("_qfq", "_hfq", "_bfq"):
                if stem.endswith(suf):
                    stem = stem[:-len(suf)]
                    break
            if stem.isdigit() and len(stem) == 6:
                have.add(stem)

    union["已下载"] = union["code6"].isin(have)
    n_have = int(union["已下载"].sum())
    print(f"  已有K线: {n_have} / {len(union)} ({n_have/len(union)*100:.1f}%)")
    print(f"  待下载: {len(union)-n_have} 只")
    print()

    # 待下载清单
    need = union[~union["已下载"]].copy()
    # 补全 ts_code
    def to_ts(c6):
        if c6.startswith(("60", "68")):
            return c6 + ".SH"
        if c6.startswith(("8", "4", "9")):
            return c6 + ".BJ"
        return c6 + ".SZ"
    need["ts_code"] = need["code6"].map(to_ts)

    # 判断是否退市（查 stock_basic_all）
    sb_path = outdir / "stock_basic_all.csv"
    if sb_path.exists():
        sb = pd.read_csv(sb_path, dtype=str)
        dl = set(sb[sb["list_status"] == "D"]["ts_code"].str[:6])
        need["已退市"] = need["code6"].isin(dl)
        print(f"  待下载中已退市: {int(need['已退市'].sum())} 只")
        print(f"  待下载中存续:   {int((~need['已退市']).sum())} 只")
    print()

    union.to_csv(outdir / "universe_union.csv", index=False, encoding="utf-8-sig")
    need.to_csv(outdir / "universe_need.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/universe_union.csv  ({len(union)} 只)")
    print(f"[输出] {outdir}/universe_need.csv   ({len(need)} 只)")
    print()

    # 覆盖率预期
    print("=" * 110)
    print("  预期覆盖率提升")
    print("=" * 110)
    print()
    for i in range(0, len(snaps), max(1, len(snaps) // 6)):
        f = snaps[i]
        dt = pd.Timestamp(os.path.basename(f)[:-4])
        try:
            t = pd.read_csv(f, usecols=lambda c: c in ("ts_code", "total_mv"))
            t = t.copy()
            t["code6"] = t["ts_code"].str[:6]
            t["mv_yi"] = pd.to_numeric(t["total_mv"], errors="coerce") / 1e4
            sub = t[(t["mv_yi"] >= 30) & (t["mv_yi"] < 80)]
            sub = sub[~sub["code6"].str.startswith(("8", "4", "9"))]
            total = len(sub)
            covered = sub["code6"].isin(set(union[union["已下载"]]["code6"])).sum()
            newcov = sub["code6"].isin(set(union["code6"])).sum()
            print(f"  {dt.date()}: 真实池 {total:>5}  "
                  f"当前覆盖 {covered:>4} ({covered/max(total,1)*100:>5.1f}%)  "
                  f"补全后 {newcov:>4} ({newcov/max(total,1)*100:>5.1f}%)")
        except Exception:
            pass
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
