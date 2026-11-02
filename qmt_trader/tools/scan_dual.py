# -*- coding: utf-8 -*-
"""
tools/scan_dual.py — 双模式扫描

模式A（正式）：含择时过滤。择时未通过时无信号，符合策略设计。
模式B（观察）：忽略择时，仅看4个个股条件（低位/连续触轨/MA60向下/月线向上）
              用于了解「假如择时通过，有多少候选」
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import pandas as pd
from core.config import load_config
from core.logger import setup_logger
from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound


def main():
    cfg = load_config()
    setup_logger("scan", log_dir=None, level="WARNING", console=False)

    ds = cfg.section("datasource")
    src = TushareSource(cache_dir=ds.get("cache_dir"), min_interval=0.35)

    scfg = cfg.to_strategy_cfg()
    # 模式B：关闭择时
    scfg_b = {**scfg, "entry": {**scfg["entry"], "use_market_filter": False}}
    st_b = BollLowerRebound(scfg_b, src)

    # 标的池
    state = Path(ds.get("cache_dir")).parent / "state"
    uni_p = state / "universe.json"
    if not uni_p.exists():
        print("标的池不存在，请先运行 python main.py prepare")
        return 1
    import json
    uni = json.loads(uni_p.read_text(encoding="utf-8"))
    codes = uni.get("codes", [])
    print(f"标的池: {len(codes)} 只  ({uni.get('date')})")

    # 择时
    st_a = BollLowerRebound(scfg, src)
    passed, mret = st_a.market_ok()
    print(f"择时: {'通过' if passed else '未通过'}  市场60日 {mret:.2f}%  "
          f"(门槛 {st_a.market_max}%)")
    print()

    t0 = time.time()
    print("=" * 96)
    print("  模式B：忽略择时，扫描个股形态条件")
    print("=" * 96)
    sig_b = st_b.scan(codes, market_passed=True)
    print(f"完整信号（4条件全满足）: {len(sig_b)} 只   耗时 {time.time()-t0:.0f}s")

    if len(sig_b):
        cols = ["code", "date", "close", "bb_lower", "bb_mid", "bb_upper",
                "pos120", "streak", "ma60_slope%", "monthly_up", "conditions"]
        cols = [c for c in cols if c in sig_b.columns]
        print()
        print(sig_b[cols].to_string(index=False))
        out = HERE / "output" / f"scan_observe_{pd.Timestamp.now():%Y%m%d_%H%M}.csv"
        out.parent.mkdir(exist_ok=True)
        sig_b.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n已保存: {out}")

    # 分条件统计（了解瓶颈在哪）
    print()
    print("=" * 96)
    print("  各条件通过情况（全池统计）")
    print("=" * 96)
    cnt = {}
    n_scanned = 0
    near = []
    for code in codes:
        try:
            df = st_b.get_kline(code)
            if df is None or len(df) < 130:
                continue
            d = st_b.compute(df)
            if len(d) == 0:
                continue
            r = d.iloc[-1]
            n_scanned += 1
            cond_cols = ["cond_position", "cond_streak", "cond_ma60_down",
                         "cond_monthly_up", "cond_j_low", "cond_liquidity"]
            for c in cond_cols:
                cnt[c] = cnt.get(c, 0) + int(bool(r[c]))
            n_ok = int(sum(bool(r[c]) for c in cond_cols))
            if n_ok >= 5:
                near.append({
                    "code": code,
                    "close": round(float(r["close"]), 2),
                    "pos120": round(float(r["pos120"]), 3) if pd.notna(r["pos120"]) else None,
                    "streak": int(r["lower_streak"]),
                    "ma60_slope%": round(float(r["ma60_slope"]) * 100, 2)
                    if pd.notna(r["ma60_slope"]) else None,
                    "monthly_up": bool(r.get("monthly_trend_up", False)),
                    "conditions": f"{n_ok}/{len(cond_cols)}",
                })
        except Exception:
            pass

    if n_scanned:
        print(f"  已扫描 {n_scanned} 只")
        for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
            print(f"    {k:<20} {v:>5} 只  ({v/n_scanned*100:>5.1f}%)")

    if near:
        nd = pd.DataFrame(near).sort_values("conditions", ascending=False)
        print()
        print(f"  接近满足（≥5/6 条件）: {len(nd)} 只")
        print(nd.head(30).to_string(index=False))
        out2 = HERE / "output" / f"scan_near_{pd.Timestamp.now():%Y%m%d_%H%M}.csv"
        nd.to_csv(out2, index=False, encoding="utf-8-sig")
        print(f"\n已保存: {out2}")

    print()
    print("=" * 96)
    print("  结论")
    print("=" * 96)
    if not passed:
        print(f"  ✗ 择时未通过（{mret:.2f}% vs 门槛 {st_a.market_max}%）→ 正式流程应空仓")
        print(f"    距门槛还差 {st_a.market_max - mret:.2f}pct")
    else:
        print(f"  ✓ 择时通过，模式A信号数 = 模式B信号数")
    return 0


if __name__ == "__main__":
    sys.exit(main())
