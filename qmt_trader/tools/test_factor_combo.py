# -*- coding: utf-8 -*-
"""
因子组合与样本外验证

单一因子测试发现「距MA60乖离% 低位20分位」效果显著：
  交易 323  CAGR 7.93%→17.60%  Sharpe 0.35→0.75  回撤 -60.38%→-32.94%

但必须排查过拟合，本测试做三件事：
  1. 因子相关性检查（是否与 B0 条件重复）
  2. 多因子组合（2-4 个因子叠加）
  3. 样本外验证（前 70% / 后 30% 分别检验）
  4. 参数敏感性（不同分位阈值）
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
from test_factors import add_factors, FACTORS


def main():
    cfg = load_config()
    setup_logger("combo", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.25)

    samp = pd.read_csv(cache.parent / "output" / "pool_stratified.csv",
                       dtype={"ts_code": str})
    codes = sorted(set(samp["ts_code"].str[:6].tolist()))

    print("=" * 118)
    print("  因子组合与样本外验证")
    print("=" * 118)
    print()

    # ---- 载入
    data = {}
    t0 = time.time()
    for i, c6 in enumerate(codes):
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

    # ---- 1. 因子相关性（在信号样本上）
    print("=" * 118)
    print("  一、因子相关性检查（信号样本内）")
    print("=" * 118)
    print()
    keys = [k[0] for k in FACTORS]
    recs = []
    for c, d in data.items():
        m = d[B0].all(axis=1).values
        for k in np.where(m)[0]:
            if k + 1 >= len(d):
                continue
            row = {f: d[f].iloc[k] for f in keys if f in d.columns}
            # 同时记录 B0 各条件的原始指标值，用于判断因子是否与条件重复
            for extra in ("pos120", "lower_streak", "ma60_slope"):
                if extra in d.columns:
                    row[f"b0_{extra}"] = d[extra].iloc[k]
            recs.append(row)
    SIG = pd.DataFrame(recs)
    print(f"信号样本 {len(SIG)} 个")
    print()

    # 关注因子与 B0 原始指标的相关性
    focus = ["f_dist_ma60", "f_ret20", "f_ret60", "f_bb_width", "f_amt20",
             "f_ma20_slope", "f_atr_pct", "f_price", "f_dist_ma120", "f_high_dist"]
    b0cols = ["b0_pos120", "b0_lower_streak", "b0_ma60_slope"]
    corr = SIG[focus + b0cols].corr()
    print("  因子 vs B0原始指标 的相关系数:")
    print(corr.loc[focus, b0cols].round(3).to_string())
    print()
    print("  说明: |r| > 0.7 表示高度重复，|r| < 0.4 表示提供新信息")
    print()

    # ---- 2. 单因子参数敏感性
    print("=" * 118)
    print("  二、关键因子的分位敏感性（判断是否过拟合）")
    print("=" * 118)
    print()
    for fcol, fname in [("f_dist_ma60", "距MA60乖离%"),
                        ("f_ret20", "20日跌幅%"),
                        ("f_ret60", "60日跌幅%"),
                        ("f_bb_width", "布林带宽%"),
                        ("f_amt20", "20日均成交额")]:
        vals = SIG[fcol].dropna()
        print(f"  【{fname}】信号分布分位:")
        line = "    "
        for q in [0.05, 0.1, 0.2, 0.3, 0.4, 0.5]:
            line += f" {int(q*100):>3}%={vals.quantile(q):>9.3f}"
        print(line)
        print()

    # ---- 3. 组合测试
    print("=" * 118)
    print("  三、因子组合测试")
    print("=" * 118)
    print()

    # 基准
    bt0 = SimpleBacktest(cfg.as_dict(), None)
    eq0, tr0 = bt0.run(data, B0, use_market=False)
    m0 = metrics(eq0, tr0, "基准B0")
    print(f"  基准 B0: 交易 {m0.get('交易笔数')}  CAGR {m0.get('CAGR%')}%  "
          f"Sharpe {m0.get('Sharpe')}  回撤 {m0.get('最大回撤%')}%  "
          f"胜率 {m0.get('胜率%')}%")
    print()

    combos = [
        ("B0 + 距MA60(20%)", [("f_dist_ma60", "low", 0.20)]),
        ("B0 + 距MA60(30%)", [("f_dist_ma60", "low", 0.30)]),
        ("B0 + 距MA60(40%)", [("f_dist_ma60", "low", 0.40)]),
        ("B0 + 距MA60(50%)", [("f_dist_ma60", "low", 0.50)]),
        ("B0 + 距MA60(20%) + 量", [("f_dist_ma60", "low", 0.20),
                                   ("f_amt20", "low", 0.50)]),
        ("B0 + 距MA60(20%) + 带宽", [("f_dist_ma60", "low", 0.20),
                                     ("f_bb_width", "high", 0.50)]),
        ("B0 + 距MA60(20%) + 20日跌", [("f_dist_ma60", "low", 0.20),
                                       ("f_ret20", "low", 0.50)]),
        ("B0 + 距MA60(30%) + 量(40%)", [("f_dist_ma60", "low", 0.30),
                                        ("f_amt20", "low", 0.40)]),
        ("B0 + 距MA60(30%) + 带宽(60%)", [("f_dist_ma60", "low", 0.30),
                                          ("f_bb_width", "high", 0.60)]),
        ("B0 + 距MA60(30%)+量(40%)+带宽(60%)",
         [("f_dist_ma60", "low", 0.30), ("f_amt20", "low", 0.40),
          ("f_bb_width", "high", 0.60)]),
        ("三因子(20%/30%/70%)",
         [("f_dist_ma60", "low", 0.20), ("f_amt20", "low", 0.30),
          ("f_bb_width", "high", 0.70)]),
    ]

    results = []
    for label, specs in combos:
        cols = []
        for fcol, direction, q in specs:
            if fcol not in SIG.columns:
                continue
            vals = SIG[fcol].dropna()
            if len(vals) < 50:
                continue
            thr = float(vals.quantile(q))
            col = f"_c_{fcol}_{direction}_{int(q*100)}"
            for c, d in data.items():
                if fcol not in d.columns:
                    d[col] = False
                    continue
                x = d[fcol]
                d[col] = (x <= thr) if direction == "low" else (x >= thr)
                d[col] = d[col].fillna(False)
            cols.append(col)

        try:
            btc = SimpleBacktest(cfg.as_dict(), None)
            eqc, trc = btc.run(data, B0 + cols, use_market=False)
            mc = metrics(eqc, trc, label)
            n = mc.get("交易笔数", 0)
            rec = {
                "组合": label, "条件数": len(B0) + len(cols), "交易数": n,
                "CAGR%": mc.get("CAGR%"), "Sharpe": mc.get("Sharpe"),
                "回撤%": mc.get("最大回撤%"), "Calmar": mc.get("Calmar"),
                "胜率%": mc.get("胜率%"), "平均单笔%": mc.get("平均单笔%"),
                "盈亏比": mc.get("盈亏比"),
                "ΔCAGR": round((mc.get("CAGR%") or 0) - (m0.get("CAGR%") or 0), 2),
            }
            results.append(rec)
            print(f"  {label:<38} 交易{n:>4}  CAGR {mc.get('CAGR%'):>6.2f}%  "
                  f"Sharpe {mc.get('Sharpe'):>5.2f}  回撤 {mc.get('最大回撤%'):>7.2f}%  "
                  f"胜率 {mc.get('胜率%'):>5.1f}%")
        except Exception as e:
            print(f"  {label:<38} 失败: {str(e)[:50]}")
        finally:
            for d in data.values():
                for col in cols:
                    if col in d.columns:
                        del d[col]
    print()

    R = pd.DataFrame(results)
    if len(R):
        print("  按 Calmar 排序:")
        print(R.sort_values("Calmar", ascending=False)[
            ["组合", "交易数", "CAGR%", "Sharpe", "回撤%", "Calmar", "胜率%", "ΔCAGR"]
        ].to_string(index=False))
    print()

    # ---- 4. 样本外验证（对最优组合）
    print("=" * 118)
    print("  四、样本外验证（前70% / 后30%）")
    print("=" * 118)
    print()

    # 选定组合：距MA60(30%) + 量(40%) + 带宽(60%)（保守版）
    ospecs = [("f_dist_ma60", "low", 0.30), ("f_amt20", "low", 0.40),
              ("f_bb_width", "high", 0.60)]
    names = {"f_dist_ma60": "距MA60", "f_amt20": "成交额", "f_bb_width": "带宽"}
    print(f"  验证组合: B0 + {' + '.join(names[f] for f, _, _ in ospecs)}")
    print()

    cols = []
    for fcol, direction, q in ospecs:
        vals = SIG[fcol].dropna()
        thr = float(vals.quantile(q))
        col = f"_o_{fcol}"
        for c, d in data.items():
            x = d[fcol]
            d[col] = ((x <= thr) if direction == "low" else (x >= thr)).fillna(False)
        cols.append(col)

    for seg, (lo, hi) in [("全区间", (None, None)),
                          ("前70%", (None, "2023-01-01")),
                          ("后30%", ("2023-01-01", None))]:
        dd = {}
        for c, d in data.items():
            t = d
            if lo := lo:
                t = t[t["date"] >= pd.Timestamp(lo)]
            if hi := hi:
                t = t[t["date"] <= pd.Timestamp(hi)]
            if len(t) > 150:
                dd[c] = t.reset_index(drop=True)
        if not dd:
            continue
        for lab, conds in [("B0", B0), ("B0+3因子", B0 + cols)]:
            bts = SimpleBacktest(cfg.as_dict(), None)
            eqs, trs = bts.run(dd, conds, use_market=False)
            ms = metrics(eqs, trs, f"{seg}|{lab}")
            print(f"  {seg:<8} {lab:<10} 交易 {ms.get('交易笔数',0):>4}  "
                  f"CAGR {ms.get('CAGR%')}%  Sharpe {ms.get('Sharpe')}  "
                  f"回撤 {ms.get('最大回撤%')}%  胜率 {ms.get('胜率%')}%")
        print()

    for d in data.values():
        for col in cols:
            if col in d.columns:
                del d[col]

    # ---- 输出
    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    if len(R):
        R.to_csv(outdir / f"factor_combo_{stamp}.csv", index=False, encoding="utf-8-sig")
        print(f"[输出] {outdir}/factor_combo_{stamp}.csv")
    corr.round(4).to_csv(outdir / f"factor_corr_{stamp}.csv", encoding="utf-8-sig")
    print(f"[输出] {outdir}/factor_corr_{stamp}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
