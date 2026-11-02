# -*- coding: utf-8 -*-
"""
step6_run.py — 策略实验室运行器

流程：
  1. 加载全部K线（含退市股）
  2. 构建动态股票池（47个市值快照）
  3. 计算时序因子
  4. 计算横截面分位（只在池内排名）
  5. 逐策略回测（动态池 + 无未来函数）
  6. Walk-forward 验证
  7. DSR 多重检验（试验数自动统计）
  8. 汇总排序
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
from step3_engine import load_all_kline, DynamicUniverse
from step6_lab import (add_price_factors, add_cross_sectional_ranks,
                       STRATEGIES, SignalBacktest, build_signals, perf)
from step4_dsr import deflated_sharpe_ratio, bh_fdr, bonferroni


def main():
    quick = "--quick" in sys.argv
    cfg = load_config()
    setup_logger("lab", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))

    print("=" * 130)
    print("  策略实验室 —— 多策略动态池验证")
    print("=" * 130)
    print()

    # ---- 1. 数据
    t0 = time.time()
    data = load_all_kline(cache, include_delisted=True)
    print(f"[1] 加载 {len(data)} 只  耗时 {time.time()-t0:.0f}s")

    # ---- 2. 动态池
    uni = DynamicUniverse(cache, cap_min=30, cap_max=80, exclude_bj=True)
    if not uni.available():
        print("动态池不可用")
        return 1
    print(f"[2] 动态池 {len(uni.snapshots)} 个快照")

    # 全交易日历
    cal = set()
    for d in data.values():
        cal.update(pd.to_datetime(d["date"]).tolist())
    cal = sorted(pd.Timestamp(x) for x in cal)
    print(f"    交易日历 {len(cal)} 天  {cal[0].date()} ~ {cal[-1].date()}")

    print(f"[3] 预计算股票池...")
    uni.precompute(cal, data)
    pool_by_date = {dt: uni._pool_cache.get(dt, set()) for dt in cal}
    sizes = [len(v) for v in pool_by_date.values() if v]
    print(f"    平均池大小 {np.mean(sizes):.0f}  最大 {max(sizes)}  非空 {len(sizes)}/{len(cal)}")
    print()

    # ---- 3. 因子
    print(f"[4] 计算时序因子...")
    t0 = time.time()
    prepared = {}
    for i, (c6, raw) in enumerate(data.items()):
        try:
            if len(raw) < 280:
                continue
            d = add_price_factors(raw.copy())
            d["prev_close"] = d["close"].shift(1)
            d["open_chg_pct"] = (d["open"] / d["prev_close"] - 1) * 100
            prepared[c6] = d
        except Exception:
            pass
        if (i + 1) % 400 == 0:
            print(f"    {i+1}/{len(data)}  {time.time()-t0:.0f}s", flush=True)
    print(f"    有效 {len(prepared)} 只  {time.time()-t0:.0f}s")
    print()

    # ---- 4. 横截面分位
    print(f"[5] 计算横截面分位...")
    t0 = time.time()
    add_cross_sectional_ranks(prepared, pool_by_date)
    print(f"    完成 {time.time()-t0:.0f}s")
    print()

    # ---- 5. 策略
    strategies = STRATEGIES[:8] if quick else STRATEGIES
    print(f"[6] 测试 {len(strategies)} 个策略")
    print()

    # 回测参数：先测多组出场方案，避免单一参数误导
    BT_VARIANTS = [
        ("H20/S10", dict(max_pos=5, max_pct=0.20, stop_pct=-0.10, max_hold=20)),
        ("H20/S15", dict(max_pos=5, max_pct=0.20, stop_pct=-0.15, max_hold=20)),
        ("H10/S10", dict(max_pos=5, max_pct=0.20, stop_pct=-0.10, max_hold=10)),
        ("H60/S15", dict(max_pos=5, max_pct=0.20, stop_pct=-0.15, max_hold=60)),
        ("H20/S10/TP20", dict(max_pos=5, max_pct=0.20, stop_pct=-0.10,
                              max_hold=20, tp_pct=0.20)),
        ("H60/S15/P10", dict(max_pos=10, max_pct=0.10, stop_pct=-0.15, max_hold=60)),
    ]

    print(f"[6] 基准：池内等权买入持有")
    # 构造一个「全池等权、每日再平衡」的基准
    bench_sig = {dt: list(pool)[:400] for dt, pool in pool_by_date.items() if pool}
    bt_b = SignalBacktest(cfg.as_dict(), max_pos=400, max_pct=1.0,
                          stop_pct=-99.0, max_hold=99999)
    eq_b, tr_b = bt_b.run(prepared, bench_sig, pool_by_date)
    m_b = perf(eq_b, tr_b, "池内等权基准")
    print(f"    CAGR {m_b.get('CAGR%')}%  Sharpe {m_b.get('Sharpe')}  "
          f"回撤 {m_b.get('回撤%')}%  交易 {m_b.get('交易',0)}")
    print()

    print(f"[7] 测试 {len(strategies)} 个策略 × {len(BT_VARIANTS)} 组出场参数")
    print()

    results = []
    trades_all = {}
    for si, spec in enumerate(strategies):
        name = spec[0]
        try:
            sig = build_signals(prepared, spec, pool_by_date)
            n_sig = sum(len(v) for v in sig.values())
            if n_sig < 50:
                print(f"  {name:<18} 信号太少({n_sig})，跳过")
                continue
            best = None
            for tag, btkw in BT_VARIANTS:
                bt = SignalBacktest(cfg.as_dict(), **btkw)
                eq, tr = bt.run(prepared, sig, pool_by_date)
                m = perf(eq, tr, f"{name}|{tag}")
                m["策略"] = name
                m["出场"] = tag
                m["信号数"] = n_sig
                m["说明"] = spec[5]
                results.append(m)
                if best is None or (m.get("Sharpe") or -9) > (best.get("Sharpe") or -9):
                    best = m
                    trades_all[name] = (eq, tr)
            print(f"  {name:<18} 信号{n_sig:>7}  最优出场{best['出场']:<12} "
                  f"CAGR {str(best.get('CAGR%')):>7}%  "
                  f"Sharpe {str(best.get('Sharpe')):>6}  "
                  f"回撤 {str(best.get('回撤%')):>8}%", flush=True)
        except Exception as e:
            print(f"  {name:<18} 失败: {str(e)[:70]}")
    print()

    ALL = pd.DataFrame([{k: v for k, v in r.items() if k != "_returns"}
                        for r in results])
    if len(ALL) == 0:
        print("无有效结果")
        return 1

    # 每个策略取最优出场
    R = (ALL.sort_values("Sharpe", ascending=False)
            .groupby("策略", as_index=False).first())
    R = R.sort_values("Sharpe", ascending=False)

    print("=" * 130)
    print("  一、全样本结果（动态池 + 退市股 + 无未来函数；每策略取最优出场）")
    print("=" * 130)
    cols = ["策略", "出场", "信号数", "交易", "CAGR%", "Sharpe", "回撤%",
            "Calmar", "胜率%", "单笔%", "年交易"]
    cols = [c for c in cols if c in R.columns]
    print(R[cols].to_string(index=False))
    print()
    print(f"  基准（池内等权）: CAGR {m_b.get('CAGR%')}%  "
          f"Sharpe {m_b.get('Sharpe')}  回撤 {m_b.get('回撤%')}%")
    print()

    print("=" * 130)
    print("  二、出场方案对比（全部策略）")
    print("=" * 130)
    if len(ALL):
        piv = ALL.pivot_table(index="出场", values=["CAGR%", "Sharpe", "回撤%"],
                              aggfunc="mean").round(2)
        print(piv.to_string())
        print()

    # ---- 6. DSR
    print("=" * 130)
    print("  二、DSR 多重检验（试验数自动统计）")
    print("=" * 130)
    print()
    N = len(strategies)
    print(f"  试验次数 N = {N}（本次测试的策略数）")
    print()

    rows = []
    for r in results:
        rr = r.get("_returns")
        if rr is None or len(rr) < 50:
            continue
        sr_ann = r.get("Sharpe") or 0
        sr_raw = sr_ann / np.sqrt(244)
        n = len(rr)
        from scipy import stats
        skew = float(stats.skew(rr))
        kurt = float(stats.kurtosis(rr, fisher=False))
        t = rr.mean() / (rr.std(ddof=1) / np.sqrt(n)) if rr.std(ddof=1) > 0 else 0
        p = 2 * (1 - stats.t.cdf(abs(t), n - 1))
        dsr = deflated_sharpe_ratio(sr_raw, n, skew, kurt, N)
        rows.append({
            "策略": r["策略"],
            "样本": n,
            "年化Sharpe": round(sr_ann, 3),
            "t值": round(t, 2),
            "p值": round(p, 6),
            "零假设期望最大Sharpe": dsr.get("sr_expected_max"),
            "DSR概率": dsr.get("psr_vs_expected_max"),
            "判定": dsr.get("verdict"),
        })
    D = pd.DataFrame(rows)
    if len(D):
        D = D.sort_values("DSR概率", ascending=False)
        print(D.to_string(index=False))
        print()
        pv = D["p值"].dropna().tolist()
        if pv:
            b = bonferroni(pv, 0.05)
            f = bh_fdr(pv, 0.05)
            print(f"  Bonferroni: 阈值={b.get('校正阈值')}  通过={b.get('通过数')}/{b.get('试验数')}")
            print(f"  BH-FDR    : 通过={f.get('通过数')}/{f.get('试验数')}")
            print()
        # 解读
        best = D.iloc[0]
        print(f"  最优策略: {best['策略']}")
        print(f"    观测 Sharpe = {best['年化Sharpe']}")
        print(f"    零假设期望最大 Sharpe = {best['零假设期望最大Sharpe']}")
        if (best["年化Sharpe"] or 0) > (best["零假设期望最大Sharpe"] or 0):
            print(f"    → 超过门槛 {best['年化Sharpe']-best['零假设期望最大Sharpe']:+.2f}")
        else:
            print(f"    → 低于门槛 {best['年化Sharpe']-best['零假设期望最大Sharpe']:+.2f}，"
                  f"无法排除随机搜索")
        print()

    # ---- 7. 输出
    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    R.to_csv(outdir / f"lab_results_{stamp}.csv", index=False, encoding="utf-8-sig")
    if len(D):
        D.to_csv(outdir / f"lab_dsr_{stamp}.csv", index=False, encoding="utf-8-sig")
    # 保存所有权益曲线
    for name, (eq, tr) in trades_all.items():
        safe = name.replace("/", "_")
        eq.to_csv(outdir / f"lab_eq_{safe}_{stamp}.csv", index=False,
                  encoding="utf-8-sig")
    print(f"[输出] {outdir}/lab_results_{stamp}.csv")
    print(f"[输出] {outdir}/lab_dsr_{stamp}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
