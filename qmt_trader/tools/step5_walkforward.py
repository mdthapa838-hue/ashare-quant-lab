# -*- coding: utf-8 -*-
"""
步骤5：Walk-forward 滚动验证

相比单次时间切分（前70%/后30%）更严格：
  - 多个滚动窗口
  - 每次用「过去 N 年」训练，在「接下来 M 月」测试
  - 参数冻结（不在测试段调参）
  - 报告每个窗口的表现 + 汇总

同时输出：
  - 各窗口的 Sharpe 稳定性
  - 参数敏感性（因子阈值在训练段选出，测试段固定）
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
from step3_engine import load_all_kline, DynamicUniverse, DynBacktest, metrics
from test_factors import add_factors


def main():
    cfg = load_config()
    setup_logger("wf", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.2)

    print("=" * 120)
    print("  步骤5：Walk-forward 滚动验证")
    print("=" * 120)
    print()

    data = load_all_kline(cache, include_delisted=True)
    print(f"[数据] {len(data)} 只")

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
            d["prev_close"] = d["close"].shift(1)
            d["open_chg_pct"] = (d["open"] / d["prev_close"] - 1) * 100
            prepared[c6] = d
        except Exception:
            pass
    print(f"[计算] {len(prepared)} 只  {time.time()-t0:.0f}s")
    print()

    uni = DynamicUniverse(cache, cap_min=30, cap_max=80, exclude_bj=True)
    if not uni.available():
        print("动态池不可用")
        return 1
    print(f"[股池] {len(uni.snapshots)} 个快照")
    print()

    B0 = ["cond_position", "cond_streak", "cond_ma60_down"]

    # ---- Walk-forward 窗口
    # 2015-2026：训练3年 + 测试1年，滚动
    windows = []
    for start_year in range(2015, 2025):
        # 训练窗：start_year-3 ~ start_year-1
        # 测试窗：start_year ~ start_year+1月上界
        tr_lo = f"{start_year - 3}-01-01"
        tr_hi = f"{start_year - 1}-12-31"
        te_lo = f"{start_year}-01-01"
        te_hi = f"{start_year}-12-31"
        if pd.Timestamp(te_lo) > pd.Timestamp("2026-10-14"):
            continue
        if pd.Timestamp(tr_lo) < pd.Timestamp("2010-01-01"):
            continue
        windows.append((tr_lo, tr_hi, te_lo, te_hi))

    print(f"[窗口] {len(windows)} 个 walk-forward 窗口")
    for w in windows[:3]:
        print(f"  训练 {w[0]}~{w[1]}   测试 {w[2]}~{w[3]}")
    print("  ...")
    for w in windows[-2:]:
        print(f"  训练 {w[0]}~{w[1]}   测试 {w[2]}~{w[3]}")
    print()

    # ---- 每个窗口：训练段选阈值，测试段验证
    print("=" * 120)
    print("  Walk-forward 结果")
    print("=" * 120)
    print()

    def slice_data(lo, hi):
        out = {}
        for c, d in prepared.items():
            m = (d["date"] >= pd.Timestamp(lo)) & (d["date"] <= pd.Timestamp(hi))
            t = d[m].reset_index(drop=True)
            if len(t) > 100:
                out[c] = t
        return out

    rows = []
    for wi, (tr_lo, tr_hi, te_lo, te_hi) in enumerate(windows):
        tr_data = slice_data(tr_lo, tr_hi)
        te_data = slice_data(te_lo, te_hi)
        if not tr_data or not te_data:
            continue

        # 训练段：选距MA60阈值（20分位）
        vals = []
        for c, d in tr_data.items():
            m = d[B0].all(axis=1).values
            for k in np.where(m)[0]:
                v = d["f_dist_ma60"].iloc[k]
                if pd.notna(v):
                    vals.append(v)
        if len(vals) < 40:
            continue
        thr = float(pd.Series(vals).quantile(0.20))

        # 基准 B0（训练段 & 测试段）
        for seg, dd in [("训练", tr_data), ("测试", te_data)]:
            # B0
            bt = DynBacktest(cfg.as_dict(), uni, min_list_days=120)
            eq, tr = bt.run(dd, B0, verbose=False)
            m = metrics(eq, tr, f"{wi}|{seg}|B0")
            # B0+因子
            btf = DynBacktest(cfg.as_dict(), uni, min_list_days=120)
            btf.set_factor("f_dist_ma60", "low", thr)
            eqf, trf = btf.run(dd, B0, verbose=False)
            mf = metrics(eqf, trf, f"{wi}|{seg}|B0+距MA60")

            rows.append({
                "窗口": wi, "段": seg,
                "训练起点": tr_lo, "训练终点": tr_hi,
                "测试起点": te_lo, "测试终点": te_hi,
                "阈值": round(thr, 2),
                "配置": "B0",
                "交易": m.get("交易笔数", 0),
                "CAGR%": m.get("CAGR%"),
                "Sharpe": m.get("Sharpe"),
                "回撤%": m.get("最大回撤%"),
                "胜率%": m.get("胜率%"),
            })
            rows.append({
                "窗口": wi, "段": seg,
                "训练起点": tr_lo, "训练终点": tr_hi,
                "测试起点": te_lo, "测试终点": te_hi,
                "阈值": round(thr, 2),
                "配置": "B0+距MA60(20%)",
                "交易": mf.get("交易笔数", 0),
                "CAGR%": mf.get("CAGR%"),
                "Sharpe": mf.get("Sharpe"),
                "回撤%": mf.get("最大回撤%"),
                "胜率%": mf.get("胜率%"),
            })
        print(f"  窗口{wi}: 阈值={thr:.2f}  完成", flush=True)

    R = pd.DataFrame(rows)
    print()

    if len(R):
        # 只看测试段
        TE = R[R["段"] == "测试"]
        print("=" * 120)
        print("  各窗口测试段表现")
        print("=" * 120)
        piv = TE.pivot_table(index="窗口", columns="配置",
                             values=["CAGR%", "Sharpe", "交易"],
                             aggfunc="first")
        print(piv.to_string())
        print()

        print("=" * 120)
        print("  汇总（测试段）")
        print("=" * 120)
        for cfgname in TE["配置"].unique():
            sub = TE[TE["配置"] == cfgname]
            c = sub["CAGR%"].dropna()
            s = sub["Sharpe"].dropna()
            n = sub["交易"].sum()
            if len(c):
                print(f"  {cfgname:<20} 窗口数{len(c)}  "
                      f"平均CAGR {c.mean():>6.2f}%  "
                      f"中位 {c.median():>6.2f}%  "
                      f"最差 {c.min():>6.2f}%  "
                      f"最好 {c.max():>6.2f}%  "
                      f"正收益 {int((c>0).sum())}/{len(c)}  "
                      f"总交易 {n}")
                print(f"  {'':<20} 平均Sharpe {s.mean():.2f}  中位 {s.median():.2f}  "
                      f"  正Sharpe {int((s>0).sum())}/{len(s)}")
        print()

        # 保存
        outdir = cache.parent / "output"
        stamp = time.strftime("%Y%m%d_%H%M")
        R.to_csv(outdir / f"walkforward_{stamp}.csv", index=False,
                 encoding="utf-8-sig")
        print(f"[输出] {outdir}/walkforward_{stamp}.csv")

    return 0


if __name__ == "__main__":
    sys.exit(main())
