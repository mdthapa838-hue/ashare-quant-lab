# -*- coding: utf-8 -*-
"""
因子验证 —— 排查过拟合与流动性陷阱

两个必须排查的风险：
  1. 流动性陷阱：「成交额低位」可能选中了难以交易的僵尸股
     - 低位20%分位 = 2110万/日，实盘滑点远高于模型的0.075%
  2. 样本外衰减：三因子在测试段（9.29%）跑输基准（12.02%）
     - 说明因子优势可能只在训练段有效

本测试：
  A. 流动性现实性检查（持仓规模 vs 成交额）
  B. 各组合的样本外表现（前70% / 后30%）
  C. 逐年稳定性
  D. 去掉成交额因子后的表现（规格简化）
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
from eval_filters import SimpleBacktest, metrics
from test_factors import add_factors


def main():
    cfg = load_config()
    setup_logger("verify", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.25)

    samp = pd.read_csv(cache.parent / "output" / "pool_stratified.csv",
                       dtype={"ts_code": str})
    codes = sorted(set(samp["ts_code"].str[:6].tolist()))

    data = {}
    t0 = time.time()
    for c6 in codes:
        p = cache / "kline" / f"{c6}_qfq.csv"
        if not p.exists():
            continue
        try:
            raw = pd.read_csv(p)
            if len(raw) < 300:
                continue
            raw["date"] = pd.to_datetime(raw["date"])
            raw = raw.sort_values("date").reset_index(drop=True)
            cc = json.loads(json.dumps(cfg.to_strategy_cfg()))
            cc["entry"]["use_monthly_filter"] = False
            cc["entry"]["use_market_filter"] = False
            d = BollLowerRebound(cc, src).compute(raw)
            data[c6] = add_factors(d)
        except Exception:
            pass
    print(f"有效 {len(data)} 只  耗时 {time.time()-t0:.0f}s")
    print()

    B0 = ["cond_position", "cond_streak", "cond_ma60_down"]

    # ---- 信号样本 + 因子分位阈值
    keys = ["f_dist_ma60", "f_amt20", "f_bb_width", "f_ret20"]
    recs = []
    for c, d in data.items():
        m = d[B0].all(axis=1).values
        for k in np.where(m)[0]:
            if k + 1 >= len(d):
                continue
            recs.append({f: d[f].iloc[k] for f in keys})
    SIG = pd.DataFrame(recs)
    THR = {f: {q: float(SIG[f].dropna().quantile(q)) for q in (0.2, 0.3, 0.4, 0.6, 0.7, 0.8)}
           for f in keys}

    print("=" * 118)
    print("  一、流动性现实性检查（关键）")
    print("=" * 118)
    print()
    print(f"  股池规则要求 20日均成交额 >= 2000 万")
    print()
    print("  信号样本的成交额分布（万元）:")
    a = SIG["f_amt20"].dropna()
    for q in (0.05, 0.1, 0.2, 0.3, 0.5, 0.75):
        print(f"    {int(q*100):>3}% 分位: {a.quantile(q):>9,.0f} 万")
    print()
    print("  ⚠ 判据：单笔 10 万 / 日成交额 2100 万 = 0.48% 占比")
    print("     若单笔占比 > 1%，实盘冲击成本会显著超过模型的 0.075%")
    print()
    for cap in (100_000, 200_000, 500_000):
        r20 = cap / (a.quantile(0.2) * 1e4) * 100
        r40 = cap / (a.quantile(0.4) * 1e4) * 100
        print(f"    单笔 {cap/1e4:>5.0f} 万: 占20%分位股成交额 {r20:>5.2f}%   "
              f"占40%分位 {r40:>5.2f}%")
    print()

    # ---- 组合定义
    def build_cols(specs, prefix):
        cols = []
        for fcol, direction, q in specs:
            thr = THR[fcol][q]
            col = f"{prefix}_{fcol}_{direction}_{int(q*100)}"
            for c, d in data.items():
                x = d[fcol]
                d[col] = ((x <= thr) if direction == "low" else (x >= thr)).fillna(False)
            cols.append(col)
        return cols

    def clean(cols):
        for d in data.values():
            for col in cols:
                if col in d.columns:
                    del d[col]

    specs_map = {
        "B0": [],
        "B0+距MA60(20%)": [("f_dist_ma60", "low", 0.2)],
        "B0+距MA60(30%)": [("f_dist_ma60", "low", 0.3)],
        "B0+距MA60(20%)+量(40%)": [("f_dist_ma60", "low", 0.2),
                                   ("f_amt20", "low", 0.4)],
        "B0+距MA60(30%)+量(40%)": [("f_dist_ma60", "low", 0.3),
                                   ("f_amt20", "low", 0.4)],
        "B0+距MA60(30%)+量(40%)+带宽(60%)": [("f_dist_ma60", "low", 0.3),
                                             ("f_amt20", "low", 0.4),
                                             ("f_bb_width", "high", 0.6)],
        "B0+距MA60(30%)+带宽(60%)": [("f_dist_ma60", "low", 0.3),
                                     ("f_bb_width", "high", 0.6)],
        "B0+带宽(60%)": [("f_bb_width", "high", 0.6)],
    }

    print("=" * 118)
    print("  二、各组合样本外表现（前70% / 后30%）")
    print("=" * 118)
    print()
    print(f"  {'组合':<36}{'段':<9}{'交易':>6}{'CAGR%':>9}{'Sharpe':>9}{'回撤%':>10}{'胜率%':>8}")
    print("  " + "-" * 112)

    all_rows = []
    for label, specs in specs_map.items():
        cols = build_cols(specs, "_v") if specs else []
        conds = B0 + cols
        for seg, lo, hi in [("全区间", None, None),
                            ("前70%", None, "2023-01-01"),
                            ("后30%", "2023-01-01", None)]:
            dd = {}
            for c, d in data.items():
                t = d
                if lo is not None:
                    t = t[t["date"] >= pd.Timestamp(lo)]
                if hi is not None:
                    t = t[t["date"] <= pd.Timestamp(hi)]
                if len(t) > 150:
                    dd[c] = t.reset_index(drop=True)
            bt = SimpleBacktest(cfg.as_dict(), None)
            eq, tr = bt.run(dd, conds, use_market=False)
            m = metrics(eq, tr, f"{label}|{seg}")
            r = {"组合": label, "段": seg,
                 "交易": m.get("交易笔数", 0), "CAGR%": m.get("CAGR%"),
                 "Sharpe": m.get("Sharpe"), "回撤%": m.get("最大回撤%"),
                 "胜率%": m.get("胜率%"), "Calmar": m.get("Calmar")}
            all_rows.append(r)
            print(f"  {label:<36}{seg:<9}{r['交易']:>6}{str(r['CAGR%']):>9}"
                  f"{str(r['Sharpe']):>9}{str(r['回撤%']):>10}{str(r['胜率%']):>8}")
        if cols:
            clean(cols)
        print()

    R = pd.DataFrame(all_rows)

    # ---- 样本外衰减分析
    print("=" * 118)
    print("  三、样本外衰减分析（关键判断）")
    print("=" * 118)
    print()
    print(f"  {'组合':<36}{'前70%':>9}{'后30%':>9}{'衰减':>9}{'判定':>14}")
    print("  " + "-" * 112)
    for label in specs_map:
        a = R[(R["组合"] == label) & (R["段"] == "前70%")]
        b = R[(R["组合"] == label) & (R["段"] == "后30%")]
        if len(a) and len(b):
            va = a.iloc[0]["CAGR%"]
            vb = b.iloc[0]["CAGR%"]
            if va is None or vb is None:
                continue
            dec = vb - va
            if dec > -3:
                tag = "稳定"
            elif dec > -10:
                tag = "轻微衰减"
            else:
                tag = "★显著衰减"
            print(f"  {label:<36}{va:>9.2f}{vb:>9.2f}{dec:>+9.2f}{tag:>14}")
    print()

    # ---- 与基准对比（后30%）
    print("=" * 118)
    print("  四、后30%相对基准的超额（因子是否真有用）")
    print("=" * 118)
    print()
    base = R[(R["组合"] == "B0") & (R["段"] == "后30%")]
    if len(base):
        bv = base.iloc[0]["CAGR%"]
        print(f"  基准 B0 后30% CAGR: {bv:.2f}%")
        print()
        for label in specs_map:
            if label == "B0":
                continue
            x = R[(R["组合"] == label) & (R["段"] == "后30%")]
            if len(x):
                v = x.iloc[0]["CAGR%"]
                d = v - bv
                tag = "√ 有超额" if d > 2 else ("≈ 无差异" if d > -2 else "✗ 跑输")
                print(f"    {label:<36} {v:>7.2f}%   超额 {d:>+7.2f}pct   {tag}")
    print()

    # ---- 逐年（最优合格组合）
    print("=" * 118)
    print("  五、逐年表现（B0+距MA60(30%)+量(40%)）")
    print("=" * 118)
    print()
    cols = build_cols([("f_dist_ma60", "low", 0.3), ("f_amt20", "low", 0.4)], "_y")
    bt = SimpleBacktest(cfg.as_dict(), None)
    eq, tr = bt.run(data, B0 + cols, use_market=False)
    if len(tr):
        g = tr.groupby("year").agg(
            笔数=("ret", "size"), 平均=("ret", "mean"),
            胜率=("ret", lambda x: (x > 0).mean()),
            最大盈=("ret", "max"), 最大亏=("ret", "min"))
        g["平均"] = (g["平均"] * 100).round(2)
        g["胜率"] = (g["胜率"] * 100).round(1)
        g["最大盈"] = (g["最大盈"] * 100).round(1)
        g["最大亏"] = (g["最大亏"] * 100).round(1)
        print(g.to_string())
        print(f"\n  正收益年份: {(g['平均']>0).sum()}/{len(g)}")
    clean(cols)
    print()

    # ---- 输出
    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    R.to_csv(outdir / f"factor_verify_{stamp}.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/factor_verify_{stamp}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
