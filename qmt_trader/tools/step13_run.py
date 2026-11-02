# -*- coding: utf-8 -*-
"""
step13_run.py — 行业中性化 + 多因子组合 + 市场中性

对比设计：
  组1 中性与否  ：原因子 vs 行业中性因子（同一批因子）
  组2 多因子组合：单因子 vs 3因子等权 vs 5因子等权
  组3 市场中性  ：多头绝对收益 vs 相对基准的超额收益
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from core.config import load_config
from core.logger import setup_logger
from step3_engine import load_all_kline, DynamicUniverse
from step6_lab import SignalBacktest, perf, build_signals
from step10_pit import (load_one, compute_fundamental_factors,
                        attach_pit_to_daily, FUND_FACTORS)
from step13_neutral import (load_industry_pit, build_neutral_ranks,
                            MarketNeutralBacktest, excess_metrics)
from step4_dsr import deflated_sharpe_ratio


def main():
    quick = "--quick" in sys.argv
    cfg = load_config()
    setup_logger("n", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))

    print("=" * 130)
    print("  行业中性化 + 多因子组合 + 市场中性")
    print("=" * 130)
    print()

    # ---- 数据
    data = load_all_kline(cache, include_delisted=True)
    uni = DynamicUniverse(cache, 30, 80, exclude_bj=True)
    cal = sorted({pd.Timestamp(x) for d in data.values()
                  for x in pd.to_datetime(d["date"])})
    uni.precompute(cal, data)
    pool_by_date = {dt: uni._pool_cache.get(dt, set()) for dt in cal}
    print(f"[1] K线 {len(data)} 只  动态池 {np.mean([len(v) for v in pool_by_date.values() if v]):.0f} 只")

    # 行业 PIT
    _, by_code = load_industry_pit(cache)
    print(f"[2] 行业 PIT: {len(by_code)} 只有归属记录")

    # PIT 财务
    print("[3] 加载 PIT 财务...")
    fund_dir = cache / "fundamental"
    pit_map = {}
    for i, c6 in enumerate(data.keys()):
        p = fund_dir / f"{c6}.csv"
        if not p.exists():
            continue
        d = load_one(c6, fund_dir)
        if d is None:
            continue
        d = compute_fundamental_factors(d)
        if d is None or len(d) == 0:
            continue
        pit_map[c6] = d
    print(f"    {len(pit_map)} 只")

    merged = {}
    for c6, daily in data.items():
        pit = pit_map.get(c6)
        if pit is None:
            continue
        d = daily.copy()
        d["prev_close"] = d["close"].shift(1)
        d["open_chg_pct"] = (d["open"] / d["prev_close"] - 1) * 100
        d = attach_pit_to_daily(d, pit, FUND_FACTORS)
        merged[c6] = d
    print(f"    合并 {len(merged)} 只")
    print()

    # ---- 行业中性化
    print("[4] 行业中性化...")
    t0 = time.time()
    KEY_FACTORS = ["f_roe_ann", "f_roa_ann", "grossprofit_margin",
                   "netprofit_yoy", "or_yoy", "f_ocf_to_ni", "ocf_yoy",
                   "debt_to_assets", "current_ratio", "f_debt_chg",
                   "assets_turn", "f_accrual_ps", "f_roe_stable"]
    build_neutral_ranks(merged, pool_by_date, KEY_FACTORS, by_code)
    print(f"    完成 {time.time()-t0:.0f}s")
    print()

    # ---- 策略定义（用中性化后的 rk_ 列）
    STRATS = [
        ("负债下降", "rk_f_debt_chg", "low", 0.20, []),
        ("高资产周转", "rk_assets_turn", "high", 0.20, []),
        ("现金流增速", "rk_ocf_yoy", "high", 0.20, []),
        ("高流动比", "rk_current_ratio", "high", 0.20, []),
        ("高毛利率", "rk_grossprofit_margin", "high", 0.20, []),
        ("高ROA", "rk_f_roa_ann", "high", 0.20, []),
        ("高营收增速", "rk_or_yoy", "high", 0.20, []),
        ("现金流质量", "rk_f_ocf_to_ni", "high", 0.20, []),
    ]

    BT = dict(max_pos=5, max_pct=0.20, stop_pct=-0.15, max_hold=60)

    print("[5] 单因子回测（行业中性）")
    print()
    results = []
    eqs = {}
    for name, f, d_, q, ex in (STRATS[:5] if quick else STRATS):
        spec = (name, f, d_, q, ex, "")
        sig = build_signals(merged, spec, pool_by_date)
        n_sig = sum(len(v) for v in sig.values())
        if n_sig < 50:
            print(f"  {name:<12} 信号少({n_sig})")
            continue
        bt = SignalBacktest(cfg.as_dict(), **BT)
        eq, tr = bt.run(merged, sig, pool_by_date)
        m = perf(eq, tr, name)
        m["信号数"] = n_sig
        results.append(m)
        eqs[name] = (eq, tr, sig)
        print(f"  {name:<12} 信号{n_sig:>7}  交易{m.get('交易',0):>4}  "
              f"CAGR {str(m.get('CAGR%')):>7}%  Sharpe {str(m.get('Sharpe')):>6}  "
              f"回撤 {str(m.get('回撤%')):>8}%", flush=True)
    print()

    R = pd.DataFrame([{k: v for k, v in r.items() if k != "_returns"}
                      for r in results]).sort_values("Sharpe", ascending=False)
    if len(R):
        print("=" * 130)
        print("  一、单因子（行业中性化后）")
        print("=" * 130)
        cols = ["策略", "信号数", "交易", "CAGR%", "Sharpe", "回撤%", "Calmar",
                "胜率%", "单笔%"]
        cols = [c for c in cols if c in R.columns]
        print(R[cols].to_string(index=False))
        print()

    # ---- 多因子组合
    print("=" * 130)
    print("  二、多因子组合（等权复合分）")
    print("=" * 130)
    print()

    # 选低相关因子
    if len(eqs) >= 3:
        top = list(R.head(6)["策略"])
        print(f"  取前6优质因子: {top}")
        print()

        # 因子相关性（用抽样加速）
        fcols = [s[1] for s in STRATS if s[0] in top]
        sample_codes = list(merged.keys())[:400]
        corr_rows = []
        for i, a in enumerate(fcols):
            for b in fcols[i + 1:]:
                xs, ys = [], []
                for c6 in sample_codes:
                    d = merged.get(c6)
                    if d is None or a not in d.columns or b not in d.columns:
                        continue
                    m = d[[a, b]].dropna()
                    if len(m) > 50:
                        xs.append(m[a].values)
                        ys.append(m[b].values)
                if xs:
                    x = np.concatenate(xs)
                    y = np.concatenate(ys)
                    if len(x) > 200:
                        r = float(np.corrcoef(x, y)[0, 1])
                        corr_rows.append({"因子A": a, "因子B": b,
                                          "相关": round(r, 3)})
        C = pd.DataFrame(corr_rows)
        if len(C):
            print("  因子相关性（|r|>0.5 视为冗余）:")
            print(C.reindex(C["相关"].abs().sort_values(ascending=False).index).head(12).to_string(index=False))
            print()

        # 组合：等权复合分（**横截面**取 top_q，而非时序分位）
        def build_combo(merged, fnames, direction_map, top_q=0.10):
            """
            复合分 = 各因子中性分位的等权平均。
            每个交易日在池内取复合分最高的 top_q 比例。

            与单因子策略口径一致（都是池内横截面选股）。
            """
            # 先汇总每日每只的复合分
            daily = defaultdict(list)
            for c6, d in merged.items():
                cols = [f for f in fnames if f in d.columns]
                if len(cols) < len(fnames):
                    continue
                s = pd.Series(0.0, index=d.index)
                cnt = pd.Series(0.0, index=d.index)
                for f in fnames:
                    v = pd.to_numeric(d[f], errors="coerce")
                    ok = v.notna()
                    if direction_map.get(f) == "low":
                        v = 1 - v
                    s = s.add(v.where(ok, 0.0), fill_value=0.0)
                    cnt = cnt.add(ok.astype(float), fill_value=0.0)
                # 至少一半因子有效
                comp = (s / cnt.replace(0, np.nan)).where(
                    cnt >= len(fnames) * 0.5)
                ds = d["date"].tolist()
                cv = comp.values
                for k in np.where(np.isfinite(cv))[0]:
                    daily[pd.Timestamp(ds[k])].append((c6, cv[k]))
            # 每日取 top_q
            out = {}
            for dt, items in daily.items():
                if not items:
                    continue
                items.sort(key=lambda x: -x[1])
                n = max(1, int(len(items) * top_q))
                out[dt] = [c for c, _ in items[:n]]
            return out

        dir_map = {s[1]: s[2] for s in STRATS}
        combos = []
        # 3因子（相关性最低的3个）
        if len(C) >= 2:
            C2 = C.copy()
            C2["abs"] = C2["相关"].abs()
            C2 = C2.sort_values("abs")
            picked = set()
            for _, rr in C2.iterrows():
                if len(picked) >= 6:
                    break
                picked.add(rr["因子A"])
                picked.add(rr["因子B"])
            pool = list(picked)[:6]
            combos.append((f"3因子等权", pool[:3]))
            combos.append((f"5因子等权", pool[:5]))
            combos.append((f"6因子等权", pool[:6]))
        else:
            combos.append(("3因子等权", [s[1] for s in STRATS[:3]]))

        for cname, fs in combos:
            fs = [f for f in fs if f in dir_map]
            if len(fs) < 3:
                continue
            sig = build_combo(merged, fs, dir_map)
            n_sig = sum(len(v) for v in sig.values())
            bt = SignalBacktest(cfg.as_dict(), **BT)
            eq, tr = bt.run(merged, sig, pool_by_date)
            m = perf(eq, tr, cname)
            m["信号数"] = n_sig
            m["因子"] = ",".join(f.replace("rk_", "") for f in fs)
            results.append(m)
            eqs[cname] = (eq, tr, sig)
            print(f"  {cname:<12} 因子{len(fs)}  信号{n_sig:>7}  交易{m.get('交易',0):>4}  "
                  f"CAGR {str(m.get('CAGR%')):>7}%  Sharpe {str(m.get('Sharpe')):>6}  "
                  f"回撤 {str(m.get('回撤%')):>8}%", flush=True)
        print()

    # ---- 市场中性
    print("=" * 130)
    print("  三、市场中性（超额收益）")
    print("=" * 130)
    print()
    print("  说明：多头组合 vs 池内等权基准，超额 = 多头日收益 - 基准日收益")
    print()

    mn_rows = []
    for name, (eq, tr, sig) in eqs.items():
        try:
            mn = MarketNeutralBacktest(cfg.as_dict(), **BT)
            m = mn.run(merged, sig, pool_by_date)
            em = excess_metrics(m, name)
            mn_rows.append(em)
            print(f"  {name:<12} 多头{m.get('多头CAGR%', em.get('多头CAGR%')):>7}%  "
                  f"基准{em.get('基准CAGR%'):>7}%  "
                  f"超额{em.get('超额CAGR%'):>7}%  "
                  f"超额Sharpe {str(em.get('超额Sharpe')):>6}  "
                  f"超额回撤 {str(em.get('超额最大回撤%')):>8}%", flush=True)
        except Exception as e:
            print(f"  {name:<12} 失败: {str(e)[:50]}")
    print()

    MN = pd.DataFrame(mn_rows)
    if len(MN):
        MN = MN.sort_values("超额Sharpe", ascending=False)
        print("  超额收益排名:")
        cols = ["策略", "多头CAGR%", "基准CAGR%", "超额CAGR%", "超额Sharpe",
                "超额波动%", "超额最大回撤%", "超额胜率%"]
        cols = [c for c in cols if c in MN.columns]
        print(MN[cols].to_string(index=False))
        print()

    # ---- 输出
    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    R.to_csv(outdir / f"neutral_results_{stamp}.csv", index=False,
             encoding="utf-8-sig")
    if len(MN):
        MN.to_csv(outdir / f"market_neutral_{stamp}.csv", index=False,
                  encoding="utf-8-sig")
    if len(C) if 'C' in dir() else False:
        C.to_csv(outdir / f"factor_corr_neu_{stamp}.csv", index=False,
                 encoding="utf-8-sig")
    print(f"[输出] {outdir}/neutral_results_{stamp}.csv")
    print(f"[输出] {outdir}/market_neutral_{stamp}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
