# -*- coding: utf-8 -*-
"""
tools/scan_fast.py — 快速扫描（仅用本地缓存池）

解决 Tushare 限频导致的扫描过慢问题：
  - 只扫描 data_cache/kline 中已有缓存的标的
  - 跳过网络请求，纯本地计算

用法：
    python tools/scan_fast.py               # 用全部缓存标的
    python tools/scan_fast.py --limit 500   # 限制数量
"""
from __future__ import annotations

import os
import sys
import glob
import time
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import pandas as pd
from core.config import load_config
from core.logger import setup_logger
from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound


def main():
    limit = 0
    if "--limit" in sys.argv:
        i = sys.argv.index("--limit")
        if i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    cfg = load_config()
    setup_logger("scan", log_dir=None, level="WARNING", console=False)

    ds = cfg.section("datasource")
    cache = ds.get("cache_dir")
    src = TushareSource(cache_dir=cache, min_interval=0.35)

    scfg = cfg.to_strategy_cfg()
    # 关闭择时（用于观察形态）
    scfg_obs = {**scfg, "entry": {**scfg["entry"], "use_market_filter": False}}
    st_o = BollLowerRebound(scfg_obs, src)
    st_f = BollLowerRebound(scfg, src)

    # 缓存标的（兼容 {code}.csv 与 {code}_qfq.csv 两种命名）
    kdir = Path(cache) / "kline"
    codes6 = []
    for f in glob.glob(str(kdir / "*.csv")):
        stem = os.path.basename(f)[:-4]
        # 去掉复权后缀
        for suf in ("_qfq", "_hfq", "_bfq"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
                break
        if stem.isdigit() and len(stem) == 6:
            codes6.append(stem)
    codes6 = sorted(set(codes6))
    if limit:
        codes6 = codes6[:limit]

    # 转 ts_code
    def to_ts(c):
        return c + (".SH" if c.startswith(("60", "68")) else
                    ".BJ" if c.startswith(("8", "4", "9")) else ".SZ")
    codes = [to_ts(c) for c in codes6]

    print(f"本地缓存标的: {len(codes)} 只")

    passed, mret = st_f.market_ok()
    print(f"择时: {'通过' if passed else '未通过'}  市场60日 {mret:.2f}%  "
          f"(门槛 {st_f.market_max}%)")
    print()

    t0 = time.time()
    print("=" * 100)
    print("  扫描（本地缓存，4个个股条件）")
    print("=" * 100)
    sig = st_o.scan(codes, market_passed=True)
    print(f"完整信号: {len(sig)} 只   耗时 {time.time()-t0:.0f}s")

    if len(sig):
        cols = ["code", "date", "close", "bb_lower", "bb_mid", "bb_upper",
                "pos120", "streak", "ma60_slope%", "monthly_up", "conditions"]
        cols = [c for c in cols if c in sig.columns]
        print()
        print(sig[cols].to_string(index=False))
        out = HERE / "output" / f"scan_fast_{pd.Timestamp.now():%Y%m%d_%H%M}.csv"
        out.parent.mkdir(exist_ok=True)
        sig.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n已保存: {out}")
    else:
        print("无完整信号")

    # 条件统计
    print()
    print("=" * 100)
    print("  各条件通过情况")
    print("=" * 100)
    cond_cols = ["cond_position", "cond_streak", "cond_ma60_down",
                 "cond_monthly_up", "cond_j_low", "cond_liquidity"]
    cnt = {c: 0 for c in cond_cols}
    n_scan = 0
    near = []
    for code in codes:
        try:
            df = st_o.get_kline(code)
            if df is None or len(df) < 130:
                continue
            d = st_o.compute(df)
            if len(d) == 0:
                continue
            r = d.iloc[-1]
            n_scan += 1
            for c in cond_cols:
                cnt[c] += int(bool(r[c]))
            n_ok = sum(int(bool(r[c])) for c in cond_cols)
            if n_ok >= 5:
                near.append({
                    "code": code,
                    "close": round(float(r["close"]), 2),
                    "bb_lower": round(float(r["bb_lower"]), 2) if pd.notna(r["bb_lower"]) else None,
                    "bb_mid": round(float(r["bb_mid"]), 2) if pd.notna(r["bb_mid"]) else None,
                    "pos120": round(float(r["pos120"]), 3) if pd.notna(r["pos120"]) else None,
                    "streak": int(r["lower_streak"]),
                    "ma60_slope%": round(float(r["ma60_slope"]) * 100, 2)
                    if pd.notna(r["ma60_slope"]) else None,
                    "monthly_up": bool(r.get("monthly_trend_up", False)),
                    "conditions": f"{n_ok}/6",
                })
        except Exception:
            pass

    if n_scan:
        print(f"  已扫描 {n_scan} 只")
        for c in cond_cols:
            v = cnt[c]
            print(f"    {c:<20} {v:>5} 只  ({v/n_scan*100:>5.1f}%)")

    if near:
        nd = pd.DataFrame(near)
        nd["n_ok"] = nd["conditions"].str[0].astype(int)
        nd = nd.sort_values(["n_ok", "pos120"], ascending=[False, True]).drop(columns=["n_ok"])
        print()
        print(f"  接近满足（≥5/6）: {len(nd)} 只")
        print(nd.head(40).to_string(index=False))
        out2 = HERE / "output" / f"scan_near_{pd.Timestamp.now():%Y%m%d_%H%M}.csv"
        out2.parent.mkdir(parents=True, exist_ok=True)
        nd.to_csv(out2, index=False, encoding="utf-8-sig")
        print(f"\n已保存: {out2}")

    print()
    print("-" * 100)
    if not passed:
        print(f"  择时未通过（{mret:.2f}% vs {st_f.market_max}%）→ 正式流程空仓")
        print(f"  距门槛还差 {st_f.market_max - mret:.2f}pct")
    print(f"  本轮统计仅供观察，非交易信号")
    return 0


if __name__ == "__main__":
    sys.exit(main())
