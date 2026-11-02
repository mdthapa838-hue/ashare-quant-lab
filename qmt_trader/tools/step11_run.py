# -*- coding: utf-8 -*-
"""
step11_run.py — 基本面策略实验室运行器

流程：
  1. 加载K线（4199只，含退市）
  2. 加载 PIT 财务数据（4441只）
  3. 按 ann_date 合并到日线（无未来函数）
  4. 计算基本面因子的横截面分位
  5. 逐策略回测（动态池）
  6. DSR 多重检验
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
from step3_engine import load_all_kline, DynamicUniverse
from step6_lab import SignalBacktest, perf
from step10_pit import (load_one, compute_fundamental_factors,
                        attach_pit_to_daily, FUND_FACTORS)
from step11_fund_lab import FUND_STRATEGIES, build_group_ranks, build_signals
from step4_dsr import deflated_sharpe_ratio, bonferroni, bh_fdr


def main():
    quick = "--quick" in sys.argv
    cfg = load_config()
    setup_logger("frun", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    fund_dir = cache / "fundamental"

    print("=" * 130)
    print("  基本面策略实验室（PIT 财务因子）")
    print("=" * 130)
    print()

    # ---- 1. K线
    t0 = time.time()
    data = load_all_kline(cache, include_delisted=True)
    print(f"[1] K线 {len(data)} 只  {time.time()-t0:.0f}s")

    # ---- 2. 动态池
    uni = DynamicUniverse(cache, cap_min=30, cap_max=80, exclude_bj=True)
    if not uni.available():
        print("动态池不可用")
        return 1
    cal = sorted({pd.Timestamp(x) for d in data.values()
                  for x in pd.to_datetime(d["date"])})
    print(f"[2] 日历 {len(cal)} 天")
    uni.precompute(cal, data)
    pool_by_date = {dt: uni._pool_cache.get(dt, set()) for dt in cal}
    print(f"    动态池 {len(uni.snapshots)} 快照  平均 {np.mean([len(v) for v in pool_by_date.values() if v]):.0f} 只")
    print()

    # ---- 3. PIT 财务数据
    print("[3] 加载 PIT 财务数据...")
    t0 = time.time()
    codes = [c for c in data.keys() if (fund_dir / f"{c}.csv").exists()]
    print(f"    有财务数据的标的: {len(codes)}")
    pit_map = {}
    for i, c6 in enumerate(codes):
        d = load_one(c6, fund_dir)
        if d is None:
            continue
        d = compute_fundamental_factors(d)
        if d is None or len(d) == 0:
            continue
        pit_map[c6] = d
        if (i + 1) % 500 == 0:
            print(f"    {i+1}/{len(codes)}  有效 {len(pit_map)}  {time.time()-t0:.0f}s",
                  flush=True)
    print(f"    有效 PIT: {len(pit_map)} 只  {time.time()-t0:.0f}s")
    print()

    # ---- 4. 合并到日线
    print("[4] 合并 PIT 因子到日线（按公告日）...")
    t0 = time.time()
    merged = {}
    n_ok = 0
    for i, (c6, daily) in enumerate(data.items()):
        pit = pit_map.get(c6)
        if pit is None:
            continue
        d = daily.copy()
        d["prev_close"] = d["close"].shift(1)
        d["open_chg_pct"] = (d["open"] / d["prev_close"] - 1) * 100
        d = attach_pit_to_daily(d, pit, FUND_FACTORS)
        # 有财务覆盖的比例
        n_ok += 1
        merged[c6] = d
        if (i + 1) % 500 == 0:
            print(f"    {i+1}/{len(data)}  合并 {n_ok}  {time.time()-t0:.0f}s", flush=True)
    print(f"    合并完成 {len(merged)} 只  {time.time()-t0:.0f}s")
    print()

    # ---- 5. 横截面分位
    print("[5] 计算横截面分位（池内）...")
    t0 = time.time()
    build_group_ranks(merged, pool_by_date, FUND_FACTORS)
    print(f"    完成 {time.time()-t0:.0f}s")
    print()

    # ---- 6. 策略
    strategies = FUND_STRATEGIES[:8] if quick else FUND_STRATEGIES
    BT_VARIANTS = [
        ("H20/S10", dict(max_pos=5, max_pct=0.20, stop_pct=-0.10, max_hold=20)),
        ("H60/S15", dict(max_pos=5, max_pct=0.20, stop_pct=-0.15, max_hold=60)),
        ("H60/S15/P10", dict(max_pos=10, max_pct=0.10, stop_pct=-0.15, max_hold=60)),
    ]
    print(f"[6] 测试 {len(strategies)} 个基本面策略 × {len(BT_VARIANTS)} 组出场")
    print()

    results = []
    trades_all = {}
    for spec in strategies:
        name = spec[0]
        try:
            sig = build_signals(merged, spec)
            n_sig = sum(len(v) for v in sig.values())
            if n_sig < 50:
                print(f"  {name:<16} 信号太少({n_sig})，跳过")
                continue
            best = None
            for tag, btkw in BT_VARIANTS:
                bt = SignalBacktest(cfg.as_dict(), **btkw)
                eq, tr = bt.run(merged, sig, pool_by_date)
                m = perf(eq, tr, f"{name}|{tag}")
                m["策略"] = name
                m["出场"] = tag
                m["信号数"] = n_sig
                m["说明"] = spec[5]
                results.append(m)
                if best is None or (m.get("Sharpe") or -9) > (best.get("Sharpe") or -9):
                    best = m
                    trades_all[name] = (eq, tr)
            print(f"  {name:<16} 信号{n_sig:>7}  最优{best['出场']:<12} "
                  f"CAGR {str(best.get('CAGR%')):>7}%  "
                  f"Sharpe {str(best.get('Sharpe')):>6}  "
                  f"回撤 {str(best.get('回撤%')):>8}%", flush=True)
        except Exception as e:
            print(f"  {name:<16} 失败: {str(e)[:60]}")
    print()

    ALL = pd.DataFrame([{k: v for k, v in r.items() if k != "_returns"}
                        for r in results])
    if len(ALL) == 0:
        print("无有效结果")
        return 1

    R = (ALL.sort_values("Sharpe", ascending=False)
            .groupby("策略", as_index=False).first()
            .sort_values("Sharpe", ascending=False))

    print("=" * 130)
    print("  一、基本面策略排名（每策略取最优出场）")
    print("=" * 130)
    cols = ["策略", "出场", "信号数", "交易", "CAGR%", "Sharpe", "回撤%",
            "Calmar", "胜率%", "单笔%"]
    cols = [c for c in cols if c in R.columns]
    print(R[cols].to_string(index=False))
    print()

    pos = R[R["CAGR%"] > 0]
    print(f"  正收益策略: {len(pos)}/{len(R)}")
    print()

    # ---- 7. DSR
    print("=" * 130)
    print("  二、DSR 多重检验")
    print("=" * 130)
    print()
    N = len(strategies) * len(BT_VARIANTS)
    print(f"  试验次数 N = {N}")
    print()
    rows = []
    from scipy import stats
    for r in results:
        rr = r.get("_returns")
        if rr is None or len(rr) < 50:
            continue
        sr_ann = r.get("Sharpe") or 0
        sr_raw = sr_ann / np.sqrt(244)
        n = len(rr)
        sk = float(stats.skew(rr))
        ku = float(stats.kurtosis(rr, fisher=False))
        t = rr.mean() / (rr.std(ddof=1) / np.sqrt(n)) if rr.std(ddof=1) > 0 else 0
        p = 2 * (1 - stats.t.cdf(abs(t), n - 1))
        dsr = deflated_sharpe_ratio(sr_raw, n, sk, ku, N)
        rows.append({
            "策略": r["策略"], "出场": r["出场"], "样本": n,
            "年化Sharpe": round(sr_ann, 3), "t值": round(t, 2),
            "p值": round(p, 6),
            "期望最大Sharpe": dsr.get("sr_expected_max"),
            "DSR概率": dsr.get("psr_vs_expected_max"),
            "判定": dsr.get("verdict"),
        })
    D = pd.DataFrame(rows)
    if len(D):
        D = D.sort_values("DSR概率", ascending=False)
        print(D.head(20).to_string(index=False))
        print()
        pv = D["p值"].dropna().tolist()
        if pv:
            b = bonferroni(pv, 0.05)
            f = bh_fdr(pv, 0.05)
            print(f"  Bonferroni: 阈值={b.get('校正阈值')}  通过={b.get('通过数')}/{b.get('试验数')}")
            print(f"  BH-FDR    : 通过={f.get('通过数')}/{f.get('试验数')}")
            print()
        best = D.iloc[0]
        print(f"  最优: {best['策略']} ({best['出场']})")
        print(f"    观测 Sharpe = {best['年化Sharpe']}")
        print(f"    门槛（期望最大）= {best['期望最大Sharpe']}")
        exp_max = best["期望最大Sharpe"] or 0
        obs = best["年化Sharpe"] or 0
        print(f"    → {'超过门槛 ' if obs > exp_max else '低于门槛 '}"
              f"{obs - exp_max:+.2f}")
        print()

    # ---- 8. 输出
    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    R.to_csv(outdir / f"fund_results_{stamp}.csv", index=False, encoding="utf-8-sig")
    if len(D):
        D.to_csv(outdir / f"fund_dsr_{stamp}.csv", index=False, encoding="utf-8-sig")
    for name, (eq, tr) in trades_all.items():
        safe = name.replace("/", "_")
        eq.to_csv(outdir / f"fund_eq_{safe}_{stamp}.csv", index=False,
                  encoding="utf-8-sig")
        if len(tr):
            tr.to_csv(outdir / f"fund_tr_{safe}_{stamp}.csv", index=False,
                      encoding="utf-8-sig")
    print(f"[输出] {outdir}/fund_results_{stamp}.csv")
    print(f"[输出] {outdir}/fund_dsr_{stamp}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
