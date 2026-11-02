# -*- coding: utf-8 -*-
"""
B0 策略风控优化测试

B0 基础三条件表现：
  561 笔交易、CAGR 8.04%、Sharpe 0.36、**最大回撤 -60.38%**

回撤太大是致命问题。本测试尝试降低回撤：
  1. 止损收紧/放宽
  2. 持仓数增加（分散）
  3. 加入大盘择时（非急跌，而是「不追涨」型）
  4. 加入个股趋势过滤（MA20/MA60 位置）
  5. 波动率过滤（ATR）

目标：找到「回撤显著降低但收益不明显损失」的配置。
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


def main():
    cfg = load_config()
    setup_logger("risk", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.25)

    samp = pd.read_csv(cache.parent / "output" / "pool_stratified.csv",
                       dtype={"ts_code": str})
    codes = sorted(set(samp["ts_code"].str[:6].tolist()))

    idxs = src.index_daily("000852.SH", "20150101", "20261014")
    mkt60 = (idxs / idxs.shift(60) - 1) * 100 if idxs is not None else None

    scfg = cfg.to_strategy_cfg()

    # ---- 载入 + 计算（含额外过滤列）
    print("计算指标...")
    data = {}
    t0 = time.time()
    for i, c6 in enumerate(codes):
        p = cache / "kline" / f"{c6}_qfq.csv"
        if not p.exists():
            continue
        try:
            raw = pd.read_csv(p)
            if len(raw) < 200:
                continue
            raw["date"] = pd.to_datetime(raw["date"])
            raw = raw.sort_values("date").reset_index(drop=True)
            cc = json.loads(json.dumps(scfg))
            cc["entry"]["use_monthly_filter"] = False
            cc["entry"]["use_market_filter"] = False
            st = BollLowerRebound(cc, src)
            d = st.compute(raw)

            # 附加过滤列
            c = d["close"]
            d["ma20"] = c.rolling(20, min_periods=20).mean()
            d["ma120"] = c.rolling(120, min_periods=120).mean()
            d["above_ma120"] = (c > d["ma120"]).fillna(False)
            d["above_ma20"] = (c > d["ma20"]).fillna(False)
            # 波动率
            tr = pd.concat([
                d["high"] - d["low"],
                (d["high"] - c.shift()).abs(),
                (d["low"] - c.shift()).abs()], axis=1).max(axis=1)
            d["atr_pct"] = (tr.rolling(14, min_periods=14).mean() / c * 100)
            # 长期趋势：120日前的价格
            d["ret250"] = (c / c.shift(250) - 1) * 100

            # 市场择时（多种口径）
            if mkt60 is not None:
                d = d.merge(mkt60.rename("mkt60").reset_index().rename(
                    columns={"index": "date"}), on="date", how="left")
                d["mkt_not_up"] = (d["mkt60"] < 5).fillna(True)      # 不追涨
                d["mkt_down"] = (d["mkt60"] < 0).fillna(False)        # 市场下跌
                d["mkt_deep"] = (d["mkt60"] < -15).fillna(False)
            data[c6] = d
        except Exception:
            pass
        if (i + 1) % 200 == 0:
            print(f"  {i+1}  耗时 {time.time()-t0:.0f}s", flush=True)
    print(f"有效 {len(data)} 只  耗时 {time.time()-t0:.0f}s")
    print()

    B0 = ["cond_position", "cond_streak", "cond_ma60_down"]

    variants = [
        # (标签, 条件, 组合覆盖)
        ("基准 B0", B0, {}),
        ("+ 止损-8%", B0, {"stop": -0.08}),
        ("+ 止损-15%", B0, {"stop": -0.15}),
        ("+ 止损-20%", B0, {"stop": -0.20}),
        ("+ 持仓10只", B0, {"max_pos": 10}),
        ("+ 持仓15只", B0, {"max_pos": 15}),
        ("+ 单只上限10%", B0, {"max_pct": 0.10}),
        ("+ 持仓10只+上限10%", B0, {"max_pos": 10, "max_pct": 0.10}),
        ("+ 站上MA120", B0 + ["above_ma120"], {}),
        ("+ 站上MA20", B0 + ["above_ma20"], {}),
        ("+ 市场不追涨", B0 + ["mkt_not_up"], {}),
        ("+ 市场下跌", B0 + ["mkt_down"], {}),
        ("+ ATR<5%", B0, {"atr_max": 5.0}),
        ("+ 持仓10+上限10+止损15", B0,
         {"max_pos": 10, "max_pct": 0.10, "stop": -0.15}),
    ]

    rows = []
    for label, conds, opts in variants:
        # 应用组合覆盖
        cc = cfg.as_dict()
        if "stop" in opts:
            cc["strategy"]["exit"]["stop_loss_pct"] = opts["stop"]
        if "max_pos" in opts:
            cc["strategy"]["portfolio"]["max_positions"] = opts["max_pos"]
        if "max_pct" in opts:
            cc["strategy"]["portfolio"]["max_position_pct"] = opts["max_pct"]
        if "max_pos" in opts:
            cc["strategy"]["portfolio"]["max_position_pct"] = min(
                cc["strategy"]["portfolio"]["max_position_pct"],
                0.95 / opts["max_pos"])

        bt = SimpleBacktest(cc, mkt60)
        # ATR 过滤：预先把不满足的日期信号抹掉
        if "atr_max" in opts:
            dd = {}
            for c, d in data.items():
                t = d.copy()
                bad = (t["atr_pct"] > opts["atr_max"]).fillna(True)
                for x in B0:
                    t[x] = t[x] & (~bad)
                dd[c] = t
            use_data = dd
        else:
            use_data = data

        eq, tr = bt.run(use_data, conds, use_market=False)
        m = metrics(eq, tr, label)
        # 年化波动
        if len(eq) > 2:
            r = eq["equity"].pct_change().dropna()
            m["年化波动%"] = round(r.std() * np.sqrt(244) * 100, 2)
        rows.append(m)
        print(f"  {label:<26} 交易{m.get('交易笔数',0):>4}  "
              f"CAGR {m.get('CAGR%'):>6.2f}%  Sharpe {m.get('Sharpe')}  "
              f"回撤 {m.get('最大回撤%'):>7.2f}%  "
              f"Calmar {m.get('Calmar')}")

    R = pd.DataFrame(rows)
    print()
    print("=" * 120)
    print("  风控优化结果（按 Calmar 排序）")
    print("=" * 120)
    cols = ["配置", "CAGR%", "Sharpe", "最大回撤%", "Calmar", "年化波动%",
            "交易笔数", "胜率%", "平均单笔%"]
    cols = [c for c in cols if c in R.columns]
    R2 = R.sort_values("Calmar", ascending=False)
    print(R2[cols].to_string(index=False))
    print()

    # ---- 最优配置的分年度
    best = R2.iloc[0]["配置"]
    print("=" * 120)
    print(f"  最优配置分年度: {best}")
    print("=" * 120)
    bo = [v for v in variants if v[0] == best][0]
    cc = cfg.as_dict()
    if "stop" in bo[2]:
        cc["strategy"]["exit"]["stop_loss_pct"] = bo[2]["stop"]
    if "max_pos" in bo[2]:
        cc["strategy"]["portfolio"]["max_positions"] = bo[2]["max_pos"]
        cc["strategy"]["portfolio"]["max_position_pct"] = min(
            cc["strategy"]["portfolio"]["max_position_pct"],
            0.95 / bo[2]["max_pos"])
    bt = SimpleBacktest(cc, mkt60)
    eq, tr = bt.run(data, bo[1], use_market=False)
    if len(tr):
        g = tr.groupby("year").agg(
            笔数=("ret", "size"), 平均=("ret", "mean"),
            胜率=("ret", lambda x: (x > 0).mean()),
            最大亏=("ret", "min"))
        g["平均"] = (g["平均"] * 100).round(2)
        g["胜率"] = (g["胜率"] * 100).round(1)
        g["最大亏"] = (g["最大亏"] * 100).round(1)
        print(g.to_string())
        print(f"\n  正收益年份: {(g['平均']>0).sum()}/{len(g)}")
        print()

    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    R2.to_csv(outdir / f"risk_optimize_{stamp}.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/risk_optimize_{stamp}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
