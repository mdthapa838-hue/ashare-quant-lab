# -*- coding: utf-8 -*-
"""
步骤3：动态股票池 + 退市股完整回测

对比四种口径，量化每项修正的影响：
  A. 静态池（旧）：2026-10-14 市值筛池，无退市股
  B. 静态池 + 退市股
  C. 动态池（每时点重建）
  D. 动态池 + 退市股 + 上市时间过滤（完全修正）

策略：B0 基础三条件（低位 + 连续触轨 + MA60向下）
      以及 B0 + 距MA60乖离因子
"""
from __future__ import annotations

import os
import sys
import time
import json
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
from step3_engine import (load_all_kline, DynamicUniverse, DynBacktest, metrics)
from test_factors import add_factors


def main():
    cfg = load_config()
    setup_logger("dyn", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.2)

    print("=" * 120)
    print("  步骤3：动态股票池 + 退市股 完整回测")
    print("=" * 120)
    print()

    # ---- 载入全部K线（含退市股）
    t0 = time.time()
    data = load_all_kline(cache, include_delisted=True)
    print(f"[数据] 加载 {len(data)} 只（含退市股）  耗时 {time.time()-t0:.0f}s")

    n_delisted = len(list((cache / "kline_delisted").glob("*.csv")))
    print(f"       其中退市股 {n_delisted} 只")

    # 时间范围
    all_first = min(pd.to_datetime(d["date"]).min() for d in data.values())
    all_last = max(pd.to_datetime(d["date"]).max() for d in data.values())
    print(f"       时间跨度 {all_first.date()} ~ {all_last.date()}")
    print()

    # ---- 计算指标 + 因子
    print("[计算] 技术指标与因子...")
    scfg = cfg.to_strategy_cfg()
    scfg["entry"]["use_monthly_filter"] = False
    scfg["entry"]["use_market_filter"] = False
    st = BollLowerRebound(scfg, src)

    prepared = {}
    t0 = time.time()
    for i, (c6, raw) in enumerate(data.items()):
        try:
            if len(raw) < 150:
                continue
            d = st.compute(raw.copy())
            d = add_factors(d)
            # 开盘涨跌幅（涨跌停判断）
            d["prev_close"] = d["close"].shift(1)
            d["open_chg_pct"] = (d["open"] / d["prev_close"] - 1) * 100
            prepared[c6] = d
        except Exception:
            pass
        if (i + 1) % 400 == 0:
            print(f"  {i+1}/{len(data)}  耗时 {time.time()-t0:.0f}s", flush=True)
    print(f"[计算] 有效 {len(prepared)} 只  耗时 {time.time()-t0:.0f}s")
    print()

    # ---- 静态池（模拟旧口径）
    print("[股池] 构建静态池（2026-10-14 市值）...")
    uni = DynamicUniverse(cache, cap_min=30, cap_max=80, exclude_bj=True)
    print(f"       市值快照 {len(uni.snapshots)} 个")
    if uni.snapshots:
        last_sdt, last_sdf = uni.snapshots[-1]
        print(f"       最新快照 {last_sdt.date()}  {len(last_sdf)} 只")
    print()

    B0 = ["cond_position", "cond_streak", "cond_ma60_down"]

    # ---- 因子阈值（用信号分布20分位）
    print("[因子] 计算距MA60乖离阈值...")
    vals = []
    for c6, d in prepared.items():
        m = d[B0].all(axis=1).values
        for k in np.where(m)[0]:
            v = d["f_dist_ma60"].iloc[k]
            if pd.notna(v):
                vals.append(v)
    V = pd.Series(vals)
    THR20 = float(V.quantile(0.20))
    THR30 = float(V.quantile(0.30))
    print(f"       信号样本 {len(V)}  20分位={THR20:.2f}  30分位={THR30:.2f}")
    print()

    # ==================================================================
    print("=" * 120)
    print("  四种口径对比")
    print("=" * 120)
    print()

    results = []

    # A. 静态池（无退市股）
    print("▶ A. 静态池（2026-10-14 市值，无退市股）")
    static_codes = set()
    if uni.snapshots:
        sdt, sdf = uni.snapshots[-1]
        for _, r in sdf.iterrows():
            c6 = r["code6"]
            if c6.startswith(("8", "4", "9")):
                continue
            if 30 <= r["mv_yi"] < 80:
                static_codes.add(c6)
    dataA = {c: d for c, d in prepared.items()
             if c in static_codes and not c.startswith(("8", "4", "9"))}
    uni_static = _StaticUniverse(static_codes)
    btA = DynBacktest(cfg.as_dict(), uni_static, min_list_days=0)
    eqA, trA = btA.run(dataA, B0, verbose=False)
    mA = metrics(eqA, trA, "A.静态池(无退市)")
    results.append(mA)
    print(f"   标的{len(dataA)}  交易{mA.get('交易笔数',0)}  CAGR {mA.get('CAGR%')}%  "
          f"Sharpe {mA.get('Sharpe')}  回撤 {mA.get('最大回撤%')}%")
    print()

    # B. 静态池 + 退市股
    print("▶ B. 静态池 + 退市股")
    delisted = set()
    for f in (cache / "kline_delisted").glob("*.csv"):
        delisted.add(f.stem.split("_")[0])
    dataB = {c: d for c, d in prepared.items()
             if c in static_codes or c in delisted}
    btB = DynBacktest(cfg.as_dict(), uni_static, min_list_days=0)
    eqB, trB = btB.run(dataB, B0, verbose=False)
    mB = metrics(eqB, trB, "B.静态池+退市")
    results.append(mB)
    print(f"   标的{len(dataB)}  交易{mB.get('交易笔数',0)}  CAGR {mB.get('CAGR%')}%  "
          f"Sharpe {mB.get('Sharpe')}  回撤 {mB.get('最大回撤%')}%")
    print()

    # C. 动态池
    print("▶ C. 动态池（每时点重建，含退市股）")
    if not uni.available():
        print("   动态池不可用（快照不足）")
    else:
        btC = DynBacktest(cfg.as_dict(), uni, min_list_days=0)
        eqC, trC = btC.run(prepared, B0, verbose=False)
        mC = metrics(eqC, trC, "C.动态池")
        results.append(mC)
        print(f"   交易{mC.get('交易笔数',0)}  CAGR {mC.get('CAGR%')}%  "
              f"Sharpe {mC.get('Sharpe')}  回撤 {mC.get('最大回撤%')}%  "
              f"平均池{mC.get('平均池大小')}")
        print()

        # D. 动态池 + 上市时间过滤
        print("▶ D. 动态池 + 上市时间过滤（120天）")
        btD = DynBacktest(cfg.as_dict(), uni, min_list_days=120)
        eqD, trD = btD.run(prepared, B0, verbose=False)
        mD = metrics(eqD, trD, "D.动态池+上市过滤")
        results.append(mD)
        print(f"   交易{mD.get('交易笔数',0)}  CAGR {mD.get('CAGR%')}%  "
              f"Sharpe {mD.get('Sharpe')}  回撤 {mD.get('最大回撤%')}%")
        print()

        # E. 动态池 + 上市过滤 + 距MA60因子
        print("▶ E. 动态池 + 上市过滤 + 距MA60乖离(20%)")
        btE = DynBacktest(cfg.as_dict(), uni, min_list_days=120)
        btE.set_factor("f_dist_ma60", "low", THR20)
        eqE, trE = btE.run(prepared, B0, verbose=False)
        mE = metrics(eqE, trE, "E.动态池+上市+距MA60(20%)")
        results.append(mE)
        print(f"   交易{mE.get('交易笔数',0)}  CAGR {mE.get('CAGR%')}%  "
              f"Sharpe {mE.get('Sharpe')}  回撤 {mE.get('最大回撤%')}%  "
              f"胜率 {mE.get('胜率%')}%")
        print()

        # F. 再加单只上限10%
        print("▶ F. E + 单只上限10%")
        cfgF = cfg.as_dict()
        cfgF["strategy"]["portfolio"]["max_position_pct"] = 0.10
        btF = DynBacktest(cfgF, uni, min_list_days=120)
        btF.set_factor("f_dist_ma60", "low", THR20)
        eqF, trF = btF.run(prepared, B0, verbose=False)
        mF = metrics(eqF, trF, "F.E+单只10%")
        results.append(mF)
        print(f"   交易{mF.get('交易笔数',0)}  CAGR {mF.get('CAGR%')}%  "
              f"Sharpe {mF.get('Sharpe')}  回撤 {mF.get('最大回撤%')}%")
        print()

        # 保存最优的明细
        for nm, eq, tr in [("C", eqC, trC), ("D", eqD, trD), ("E", eqE, trE), ("F", eqF, trF)]:
            stamp = time.strftime("%Y%m%d_%H%M")
            outdir = cache.parent / "output"
            eq.to_csv(outdir / f"dyn_equity_{nm}_{stamp}.csv", index=False,
                      encoding="utf-8-sig")
            if len(tr):
                tr.to_csv(outdir / f"dyn_trades_{nm}_{stamp}.csv", index=False,
                          encoding="utf-8-sig")

    # ---- 汇总
    R = pd.DataFrame(results)
    print("=" * 120)
    print("  汇总对比")
    print("=" * 120)
    cols = ["配置", "总收益%", "CAGR%", "Sharpe", "最大回撤%", "Calmar",
            "交易笔数", "胜率%", "平均单笔%", "平均池大小"]
    cols = [c for c in cols if c in R.columns]
    print(R[cols].to_string(index=False))
    print()

    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    R.to_csv(outdir / f"dynamic_pool_compare_{stamp}.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/dynamic_pool_compare_{stamp}.csv")
    return 0


class _StaticUniverse:
    """固定股票池（对照用）。"""

    def __init__(self, codes: set):
        self.codes = codes

    def available(self):
        return True

    def pool_at(self, dt, prices):
        return self.codes


if __name__ == "__main__":
    sys.exit(main())
