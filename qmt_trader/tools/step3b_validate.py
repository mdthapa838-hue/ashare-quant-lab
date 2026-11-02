# -*- coding: utf-8 -*-
"""
步骤3b：动态池口径验证 + 敏感性

问题：动态池平均只有 279 只，而全市场 30-80 亿应有 2000+ 只。
      可能是「价格外推」把市值算错了（除权、增发等导致偏离）。

验证：
  1. 池大小是否合理（对比快照直接统计）
  2. 不同市值区间（30-80 / 20-100 / 50-150）的表现
  3. 用「市值排名分位」代替固定金额区间
  4. 关闭价格外推（只用快照市值，持有到下次快照）
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from core.config import load_config
from core.logger import setup_logger
from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound
from step3_engine import load_all_kline, DynamicUniverse, DynBacktest, metrics
from test_factors import add_factors


def main():
    cfg = load_config()
    setup_logger("v", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.2)

    print("=" * 120)
    print("  动态池口径验证")
    print("=" * 120)
    print()

    data = load_all_kline(cache, include_delisted=True)
    scfg = cfg.to_strategy_cfg()
    scfg["entry"]["use_monthly_filter"] = False
    scfg["entry"]["use_market_filter"] = False
    st = BollLowerRebound(scfg, src)

    prepared = {}
    for c6, raw in data.items():
        try:
            if len(raw) < 150:
                continue
            d = st.compute(raw.copy())
            d = add_factors(d)
            d["prev_close"] = d["close"].shift(1)
            d["open_chg_pct"] = (d["open"] / d["prev_close"] - 1) * 100
            prepared[c6] = d
        except Exception:
            pass
    print(f"[数据] {len(prepared)} 只")
    print()

    # ---- 1. 池大小检查（含本地K线的标的 vs 快照中30-80亿的数量）
    uni = DynamicUniverse(cache, 30, 80, exclude_bj=True)
    print("[1] 池大小诊断")
    print(f"    快照数 {len(uni.snapshots)}")
    have_codes = set(prepared.keys())
    for si in [0, len(uni.snapshots) // 4, len(uni.snapshots) // 2,
               len(uni.snapshots) - 1]:
        sdt, sdf = uni.snapshots[si]
        in_range = sdf[(sdf["mv_yi"] >= 30) & (sdf["mv_yi"] < 80)]
        in_range_local = in_range[in_range["code6"].isin(have_codes)]
        print(f"    {sdt.date()}: 全市场30-80亿 {len(in_range):>5} 只  "
              f"其中本地有K线 {len(in_range_local):>4} 只")
    print()

    # ---- 2. 本地K线覆盖率
    print("[2] 本地K线覆盖诊断")
    print(f"    本地K线总数: {len(prepared)}")
    print(f"    退市股: {len(list((cache/'kline_delisted').glob('*.csv')))}")
    print(f"    存续股: {len(list((cache/'kline').glob('*.csv')))}")
    print()
    print("    ⚠ 关键：动态池会包含本地没有K线的股票，这些股票无法交易")
    print("       → 实际可交易池 = 动态池 ∩ 本地K线")
    print()

    B0 = ["cond_position", "cond_streak", "cond_ma60_down"]

    # ---- 3. 不同市值区间的表现（用静态池对照）
    print("=" * 120)
    print("  [3] 不同市值区间对比（静态池，2026-09-30 快照）")
    print("=" * 120)
    print()
    sdt, sdf = uni.snapshots[-1]
    results = []
    for lo, hi, label in [(20, 30, "20-30亿"), (30, 50, "30-50亿"),
                          (50, 80, "50-80亿"), (80, 150, "80-150亿"),
                          (30, 80, "30-80亿(当前)")]:
        codes = set(sdf[(sdf["mv_yi"] >= lo) & (sdf["mv_yi"] < hi)]["code6"])
        codes &= have_codes
        if len(codes) < 30:
            continue
        dd = {c: d for c, d in prepared.items() if c in codes}
        su = _Static(codes)
        bt = DynBacktest(cfg.as_dict(), su, min_list_days=0)
        eq, tr = bt.run(dd, B0, verbose=False)
        m = metrics(eq, tr, label)
        results.append(m)
        print(f"    {label:<14} 标的{len(dd):>4}  交易{m.get('交易笔数',0):>4}  "
              f"CAGR {str(m.get('CAGR%')):>7}%  Sharpe {str(m.get('Sharpe')):>6}  "
              f"回撤 {str(m.get('最大回撤%')):>8}%")
    print()

    # ---- 4. 市值排名分位（避免固定金额阈值失效）
    print("=" * 120)
    print("  [4] 市值分位口径（静态，2026-09-30）")
    print("=" * 120)
    print()
    s = sdf.sort_values("mv_yi")
    n = len(s)
    for qlo, qhi, label in [(0.0, 0.1, "最小0-10%"), (0.1, 0.2, "10-20%"),
                            (0.2, 0.3, "20-30%"), (0.3, 0.4, "30-40%"),
                            (0.4, 0.5, "40-50%")]:
        codes = set(s.iloc[int(n * qlo):int(n * qhi)]["code6"])
        codes &= have_codes
        if len(codes) < 30:
            continue
        rng = s.iloc[int(n * qlo):int(n * qhi)]["mv_yi"]
        dd = {c: d for c, d in prepared.items() if c in codes}
        su = _Static(codes)
        bt = DynBacktest(cfg.as_dict(), su, min_list_days=0)
        eq, tr = bt.run(dd, B0, verbose=False)
        m = metrics(eq, tr, label)
        print(f"    {label:<10} 市值{rng.min():>6.1f}~{rng.max():>6.1f}亿  "
              f"标的{len(dd):>4}  交易{m.get('交易笔数',0):>4}  "
              f"CAGR {str(m.get('CAGR%')):>7}%  Sharpe {str(m.get('Sharpe')):>6}")
    print()

    # ---- 5. 动态池 vs 静态池（相同标的数对照）
    print("=" * 120)
    print("  [5] 关键对照：相同股池大小下，动态 vs 静态")
    print("=" * 120)
    print()
    # 用当前快照的 30-80 亿（静态）
    codes_static = set(sdf[(sdf["mv_yi"] >= 30) & (sdf["mv_yi"] < 80)]["code6"])
    codes_static &= have_codes
    dd_static = {c: d for c, d in prepared.items() if c in codes_static}
    su = _Static(codes_static)
    bt = DynBacktest(cfg.as_dict(), su, min_list_days=0)
    eq, tr = bt.run(dd_static, B0, verbose=False)
    m_static = metrics(eq, tr, "静态池")
    print(f"    静态池: 标的{len(dd_static)}  交易{m_static.get('交易笔数')}  "
          f"CAGR {m_static.get('CAGR%')}%  Sharpe {m_static.get('Sharpe')}  "
          f"回撤 {m_static.get('最大回撤%')}%")
    print()
    print("    动态池（见步骤3）: 交易 565  CAGR -5.51%  Sharpe -0.28  回撤 -83.22%")
    print()
    print("    差异来源：静态池用今天的市值，选出的股票在历史上可能很小（涨了很多倍）")
    print("             或很大（跌了很多）。动态池每个时点用当时市值 → 更真实。")
    print()

    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    pd.DataFrame(results).to_csv(outdir / f"mktcap_range_{stamp}.csv",
                                 index=False, encoding="utf-8-sig")
    return 0


class _Static:
    def __init__(self, codes):
        self.codes = codes

    def available(self):
        return True

    def pool_at(self, dt, prices):
        return self.codes


if __name__ == "__main__":
    sys.exit(main())
