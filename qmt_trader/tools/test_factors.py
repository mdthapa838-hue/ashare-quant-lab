# -*- coding: utf-8 -*-
"""
因子测试 —— 在 B0 基础上测试额外因子的边际贡献

B0 基准 = cond_position(低位) + cond_streak(连续触轨) + cond_ma60_down(MA60向下)
        561 笔交易、CAGR 8.04%、Sharpe 0.36、回撤 -60.38%

测试方法：
  对每个候选因子，在信号日（T日收盘）取值，按分位阈值过滤信号，
  重新跑组合回测，观察 CAGR / Sharpe / 回撤 / 胜率 的变化。

因子分类：
  A 量能类：缩量、量比、成交额趋势
  B 超跌类：乖离率、短期跌幅、连跌天数、距高点
  C 形态类：布林带宽、ATR、收盘位置、跳空
  D 估值类：PB、PE、换手率（来自 daily_basic）

严格无未来函数：因子只用到信号日及之前的数据。
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


# ---------------------------------------------------------------------------
# 因子计算（全部因果，只用历史数据）
# ---------------------------------------------------------------------------
def add_factors(d: pd.DataFrame) -> pd.DataFrame:
    c, h, l, o, v = d["close"], d["high"], d["low"], d["open"], d["volume"]
    amt = d["amount"]

    # --- A 量能类
    v5 = v.rolling(5, min_periods=5).mean()
    v20 = v.rolling(20, min_periods=20).mean()
    d["f_vol_ratio_5_20"] = v5 / v20.replace(0, np.nan)          # 缩量程度
    d["f_vol_ratio_1_20"] = v / v20.replace(0, np.nan)           # 当日量比
    a5 = amt.rolling(5, min_periods=5).mean()
    a20 = amt.rolling(20, min_periods=20).mean()
    d["f_amt_ratio_5_20"] = a5 / a20.replace(0, np.nan)

    # --- B 超跌类
    ma60 = c.rolling(60, min_periods=60).mean()
    ma120 = c.rolling(120, min_periods=120).mean()
    d["f_dist_ma60"] = (c / ma60 - 1) * 100                      # 距MA60乖离%
    d["f_dist_ma120"] = (c / ma120 - 1) * 100
    d["f_ret20"] = (c / c.shift(20) - 1) * 100
    d["f_ret60"] = (c / c.shift(60) - 1) * 100
    d["f_ret120"] = (c / c.shift(120) - 1) * 100
    hi120 = h.rolling(120, min_periods=120).max()
    d["f_high_dist"] = (c / hi120 - 1) * 100                     # 距120日高点%
    # 连跌天数
    down = (c < c.shift(1)).fillna(False)
    d["f_down_streak"] = down.groupby((down != down.shift()).cumsum()).cumsum().astype(int)

    # --- C 形态类
    bb_w = (d["bb_upper"] - d["bb_lower"]) / d["bb_mid"].replace(0, np.nan)
    d["f_bb_width"] = bb_w * 100                                 # 布林带宽%
    rng = (h - l).replace(0, np.nan)
    d["f_close_pos"] = ((c - l) / rng * 100).fillna(50)          # 收盘在当日位置
    d["f_upper_shadow"] = ((h - np.maximum(c, o)) / rng * 100).fillna(0)
    d["f_lower_shadow"] = ((np.minimum(c, o) - l) / rng * 100).fillna(0)
    # 近20日向下跳空次数
    gap = ((l < c.shift(1) * 0.99)).fillna(False).astype(int)
    d["f_gap_down_20"] = gap.rolling(20, min_periods=20).sum()
    # 波动
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean()
    d["f_atr_pct"] = (atr / c * 100)
    # MA20斜率
    ma20 = c.rolling(20, min_periods=20).mean()
    d["f_ma20_slope"] = (ma20 / ma20.shift(10) - 1) * 100

    # --- D 结构类
    d["f_amt20"] = a20 / 1e4                                     # 20日均额（万元）
    d["f_price"] = c                                           # 绝对价格
    return d


FACTORS = [
    # (列名, 中文名, 方向说明)
    ("f_vol_ratio_5_20", "缩量程度(5日/20日量)", "越小越缩量"),
    ("f_vol_ratio_1_20", "当日量比(量/20日均量)", "越小越缩量"),
    ("f_amt_ratio_5_20", "成交额趋势(5日/20日)", "越小越缩量"),
    ("f_dist_ma60", "距MA60乖离%", "越负越超跌"),
    ("f_dist_ma120", "距MA120乖离%", "越负越超跌"),
    ("f_ret20", "20日跌幅%", "越负跌幅越大"),
    ("f_ret60", "60日跌幅%", "越负跌幅越大"),
    ("f_ret120", "120日跌幅%", "越负跌幅越大"),
    ("f_high_dist", "距120日高点%", "越负跌得越多"),
    ("f_down_streak", "连跌天数", "越大越弱"),
    ("f_bb_width", "布林带宽%", "越小越收敛"),
    ("f_close_pos", "收盘位置(0下影-100上影)", "越大越强"),
    ("f_upper_shadow", "上影线占比%", "越小抛压小"),
    ("f_lower_shadow", "下影线占比%", "越大买盘强"),
    ("f_gap_down_20", "近20日跳空下跌次数", "越小越稳"),
    ("f_atr_pct", "ATR/价格%", "越小越稳"),
    ("f_ma20_slope", "MA20斜率%", "越大越强"),
    ("f_amt20", "20日均成交额(万元)", "越大流动性好"),
    ("f_price", "股价", "越低越便宜"),
]


def main():
    cfg = load_config()
    setup_logger("fac", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.25)

    samp = pd.read_csv(cache.parent / "output" / "pool_stratified.csv",
                       dtype={"ts_code": str})
    codes = sorted(set(samp["ts_code"].str[:6].tolist()))

    print("=" * 118)
    print("  因子测试：在 B0 基础上寻找有价值的额外因子")
    print("=" * 118)
    print()
    print(f"股池: {len(codes)} 只（分层随机）")
    print(f"基准: B0 = 低位 + 连续触轨 + MA60向下")
    print()

    # ---- 加载数据 + 计算指标 + 因子
    print("计算指标与因子...")
    t0 = time.time()
    data = {}
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
            st = BollLowerRebound(cc, src)
            d = st.compute(raw)
            d = add_factors(d)
            data[c6] = d
        except Exception:
            pass
        if (i + 1) % 200 == 0:
            print(f"  {i+1}  耗时 {time.time()-t0:.0f}s", flush=True)
    print(f"有效 {len(data)} 只  耗时 {time.time()-t0:.0f}s")
    print()

    B0 = ["cond_position", "cond_streak", "cond_ma60_down"]

    # ---- 基准
    print("▶ 基准 B0")
    bt = SimpleBacktest(cfg.as_dict(), None)
    eq0, tr0 = bt.run(data, B0, use_market=False)
    m0 = metrics(eq0, tr0, "基准B0")
    base_cagr = m0.get("CAGR%", 0) or 0
    print(f"   交易 {m0.get('交易笔数')}  CAGR {base_cagr}%  "
          f"Sharpe {m0.get('Sharpe')}  回撤 {m0.get('最大回撤%')}%  "
          f"胜率 {m0.get('胜率%')}%")
    print()

    # ---- 收集信号日的因子值（用于分位阈值）
    print("收集信号日因子值...")
    keys = [k[0] for k in FACTORS]
    recs = []
    for c, d in data.items():
        m = d[B0].all(axis=1).values
        for k in np.where(m)[0]:
            if k + 1 >= len(d):
                continue
            row = {"code": c}
            for f in keys:
                row[f] = d[f].iloc[k] if f in d.columns else np.nan
            recs.append(row)
    SIG = pd.DataFrame(recs)
    print(f"  信号样本 {len(SIG)} 个")
    print()

    # ---- 逐因子测试
    print("=" * 118)
    print("  因子边际贡献测试（阈值取信号分布分位）")
    print("=" * 118)
    print()

    results = []
    QS = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    for fcol, fname, fdesc in FACTORS:
        if fcol not in SIG.columns:
            continue
        vals = SIG[fcol].dropna()
        if len(vals) < 50:
            continue
        qs = {q: float(vals.quantile(q)) for q in QS}
        row_best = None

        for direction, qsel in [("低位", [0.2, 0.4, 0.6]),
                                ("高位", [0.4, 0.6, 0.8])]:
            for q in qsel:
                thr = qs[q]
                col = f"_t_{fcol}_{direction}_{int(q*100)}"
                # 构造条件列
                for c, d in data.items():
                    if fcol not in d.columns:
                        d[col] = False
                        continue
                    x = d[fcol]
                    d[col] = (x <= thr) if direction == "低位" else (x >= thr)
                    d[col] = d[col].fillna(False)
                try:
                    btf = SimpleBacktest(cfg.as_dict(), None)
                    eqf, trf = btf.run(data, B0 + [col], use_market=False)
                    mf = metrics(eqf, trf, f"{fname}|{direction}{int(q*100)}")
                    n = mf.get("交易笔数", 0)
                    if n < 30:
                        continue
                    dcagr = (mf.get("CAGR%") or 0) - base_cagr
                    dsh = (mf.get("Sharpe") or 0) - (m0.get("Sharpe") or 0)
                    ddd = (mf.get("最大回撤%") or 0) - (m0.get("最大回撤%") or 0)
                    rec = {
                        "因子": fname, "方向": direction, "分位": int(q * 100),
                        "阈值": round(thr, 4),
                        "交易数": n,
                        "CAGR%": mf.get("CAGR%"),
                        "ΔCAGR": round(dcagr, 2),
                        "Sharpe": mf.get("Sharpe"),
                        "ΔSharpe": round(dsh, 2),
                        "回撤%": mf.get("最大回撤%"),
                        "Δ回撤": round(ddd, 2),
                        "胜率%": mf.get("胜率%"),
                        "平均单笔%": mf.get("平均单笔%"),
                    }
                    results.append(rec)
                    if row_best is None or dcagr > row_best["ΔCAGR"]:
                        row_best = rec
                except Exception:
                    pass
                finally:
                    for d in data.values():
                        if col in d.columns:
                            del d[col]

        if row_best:
            print(f"  {fname:<26} 最佳: {row_best['方向']}{row_best['分位']}分位  "
                  f"交易{row_best['交易数']:>4}  ΔCAGR {row_best['ΔCAGR']:>+6.2f}pct  "
                  f"ΔSharpe {row_best['ΔSharpe']:>+5.2f}  "
                  f"Δ回撤 {row_best['Δ回撤']:>+6.2f}pct")

    R = pd.DataFrame(results)
    print()
    print("=" * 118)
    print("  Top 25 因子组合（按 ΔCAGR 排序）")
    print("=" * 118)
    cols = ["因子", "方向", "分位", "交易数", "CAGR%", "ΔCAGR", "Sharpe", "ΔSharpe",
            "回撤%", "Δ回撤", "胜率%"]
    cols = [c for c in cols if c in R.columns]
    if len(R):
        print(R.sort_values("ΔCAGR", ascending=False).head(25)[cols].to_string(index=False))
    print()

    print("=" * 118)
    print("  Top 15（按 ΔSharpe 排序）")
    print("=" * 118)
    if len(R):
        print(R.sort_values("ΔSharpe", ascending=False).head(15)[cols].to_string(index=False))
    print()

    print("=" * 118)
    print("  Top 15（按 回撤改善 排序，即 Δ回撤 最大）")
    print("=" * 118)
    if len(R):
        print(R.sort_values("Δ回撤", ascending=False).head(15)[cols].to_string(index=False))
    print()

    # ---- 输出
    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    if len(R):
        p = outdir / f"factor_test_{stamp}.csv"
        R.to_csv(p, index=False, encoding="utf-8-sig")
        print(f"[输出] {p}")
    SIG.to_csv(outdir / f"signal_factors_{stamp}.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/signal_factors_{stamp}.csv")

    return 0


if __name__ == "__main__":
    sys.exit(main())
