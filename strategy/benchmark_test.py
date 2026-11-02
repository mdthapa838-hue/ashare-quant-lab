# -*- coding: utf-8 -*-
"""
benchmark_test.py — 决定性检验：策略 vs 随机入场

原理：
  如果「随机在同样的股票池里择时买入、同样规则卖出」也能获得接近的收益，
  那么策略的「择时能力」就是虚假的，收益来自市场beta或其他因素。

做法：
  1. 打乱信号的日期（保留标的与出场规则），生成随机对照组
  2. 空信号对照（完全随机日期）
  3. 买入持有对照（同池等权）
"""
from __future__ import annotations

import os, sys, glob, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib.data import DataBundle
from lib.signals import collect_signals
from lib.backtest import Backtester, performance

try:
    import yaml
except ImportError:
    yaml = None


def find_cache():
    for b in [os.path.join(HERE, "..", "research", "smallcap_20261014", "out"),
              os.path.join(HERE, "..", "..", "research", "smallcap_20261014", "out")]:
        b = os.path.abspath(b)
        for sub in ("kline_cache", "kline"):
            d = os.path.join(b, sub)
            if os.path.isdir(d) and len(glob.glob(os.path.join(d, "*.csv"))) > 100:
                return b
    raise RuntimeError("no cache")


def main():
    cache = find_cache()
    codes = sorted(os.path.basename(f)[:-4].zfill(6)
                   for f in glob.glob(os.path.join(cache, "kline_cache", "*.csv")))
    cfg = yaml.safe_load(open(os.path.join(HERE, "config", "boll_lower_rebound.yaml"),
                              encoding="utf-8"))
    cfg["entry"]["use_market_filter"] = False
    cfg["entry"]["use_monthly_filter"] = True

    b = DataBundle(cfg, cache, allow_fetch=False)
    b.load_all(codes, verbose=False)
    sig = collect_signals(b, cfg, verbose=False)
    print(f"标的 {len(b)}  真实信号 {len(sig)}")
    if len(sig) == 0:
        print("无信号，退出")
        return

    # 真实策略
    bt = Backtester(b, sig, cfg)
    eq_r, tr_r = bt.run(verbose=False)
    m_r = performance(eq_r, tr_r, cfg, "真实策略")
    print(f"\n真实策略: 交易{m_r.get('交易笔数',0)}  CAGR {m_r['CAGR%']}%  Sharpe {m_r['Sharpe']}")

    # ---------- 随机对照 1：随机抽同样数量的 (股票, 日期) 组合
    print("\n" + "=" * 90)
    print("【随机对照】每个股票在其历史区间内随机取相同数量的日期")
    print("=" * 90)
    rng = np.random.default_rng(42)
    n_sig_per_code = sig.groupby("code").size().to_dict()
    rand_rows = []
    for code, n in n_sig_per_code.items():
        d = b.get(code)
        if d is None or len(d) < 150:
            continue
        # 只取有完整指标的区域
        valid = d.dropna(subset=["bb_upper", "bb_mid", "pos120"])
        if len(valid) < 100:
            continue
        picks = rng.choice(len(valid) - 2, size=min(n, len(valid) - 2), replace=False)
        for p in picks:
            if p + 1 >= len(valid):
                continue
            r = valid.iloc[p]
            nx = valid.iloc[p + 1]
            rand_rows.append({
                "code": code,
                "signal_date": r["date"],
                "entry_date": nx["date"],
                "close_signal": r["close"],
                "open_entry": nx["open"],
                "open_chg": nx.get("open_chg_pct", np.nan),
                "pos120": r["pos120"], "lower_streak": int(r["lower_streak"]),
                "ma60_slope": r["ma60_slope"], "monthly_slope": np.nan,
                "market_ret": r.get("market_ret", np.nan), "J": np.nan,
                "bb_lower": r["bb_lower"], "bb_mid": r["bb_mid"], "bb_upper": r["bb_upper"],
                "atr14": r.get("atr14", np.nan), "signal_idx": int(p),
            })
    rand_sig = pd.DataFrame(rand_rows)
    print(f"随机信号数 {len(rand_sig)}（与真实信号同量级）")
    if len(rand_sig):
        bt2 = Backtester(b, rand_sig, cfg)
        eq_rd, tr_rd = bt2.run(verbose=False)
        m_rd = performance(eq_rd, tr_rd, cfg, "随机入场")
        print(f"随机入场: 交易{m_rd.get('交易笔数',0)}  CAGR {m_rd['CAGR%']}%  "
              f"Sharpe {m_rd['Sharpe']}  胜率{m_rd.get('胜率%',np.nan)}%")
    else:
        m_rd = {"CAGR%": np.nan}

    # ---------- 随机对照 2：全部随机（不限数量），测基准水平
    print("\n" + "=" * 90)
    print("【随机对照2】每只股票随机取 3 个日期（看长期基准）")
    print("=" * 90)
    r2 = []
    for code in codes[:300]:
        d = b.get(code)
        if d is None or len(d) < 200:
            continue
        valid = d.dropna(subset=["bb_upper", "pos120"])
        if len(valid) < 150:
            continue
        picks = rng.choice(len(valid) - 2, size=3, replace=False)
        for p in picks:
            if p + 1 >= len(valid):
                continue
            rr = valid.iloc[p]
            nx = valid.iloc[p + 1]
            r2.append({"code": code, "signal_date": rr["date"], "entry_date": nx["date"],
                       "close_signal": rr["close"], "open_entry": nx["open"],
                       "open_chg": nx.get("open_chg_pct", np.nan),
                       "pos120": rr["pos120"], "lower_streak": int(rr["lower_streak"]),
                       "ma60_slope": rr["ma60_slope"], "monthly_slope": np.nan,
                       "market_ret": rr.get("market_ret", np.nan), "J": np.nan,
                       "bb_lower": rr["bb_lower"], "bb_mid": rr["bb_mid"],
                       "bb_upper": rr["bb_upper"], "atr14": rr.get("atr14", np.nan),
                       "signal_idx": int(p)})
    rs2 = pd.DataFrame(r2)
    if len(rs2):
        bt3 = Backtester(b, rs2, cfg)
        eq3, tr3 = bt3.run(verbose=False)
        m3 = performance(eq3, tr3, cfg, "随机300只")
        print(f"随机入场: 交易{m3.get('交易笔数',0)}  CAGR {m3['CAGR%']}%  "
              f"Sharpe {m3['Sharpe']}  胜率{m3.get('胜率%',np.nan)}%")
    else:
        m3 = {"CAGR%": np.nan}

    # ---------- 买入持有对照
    print("\n" + "=" * 90)
    print("【对照3】池内等权买入持有")
    print("=" * 90)
    d0, d1 = eq_r["date"].iloc[0], eq_r["date"].iloc[-1]
    rets = []
    for code in codes:
        d = b.get(code)
        if d is None:
            continue
        s = d[(d["date"] >= d0) & (d["date"] <= d1)]
        if len(s) < 100:
            continue
        rets.append(s["close"].iloc[-1] / s["close"].iloc[0] - 1)
    rets = np.array(rets)
    yrs = (d1 - d0).days / 365.25
    print(f"  标的数 {len(rets)}  平均总收益 {rets.mean()*100:+.1f}%  "
          f"中位 {np.median(rets)*100:+.1f}%")
    bh_cagr = (1 + np.median(rets)) ** (1 / yrs) - 1
    print(f"  中位年化 {bh_cagr*100:+.2f}%")

    # ---------- 汇总
    print("\n" + "=" * 90)
    print("【汇总对比】")
    print("=" * 90)
    rows = [
        {"方案": "真实策略", "CAGR%": m_r["CAGR%"], "Sharpe": m_r["Sharpe"],
         "最大回撤%": m_r["最大回撤%"], "交易": m_r.get("交易笔数", 0),
         "胜率%": m_r.get("胜率%", np.nan)},
        {"方案": "随机入场(同数量)", "CAGR%": m_rd.get("CAGR%", np.nan),
         "Sharpe": m_rd.get("Sharpe", np.nan), "最大回撤%": m_rd.get("最大回撤%", np.nan),
         "交易": m_rd.get("交易笔数", 0), "胜率%": m_rd.get("胜率%", np.nan)},
        {"方案": "随机入场(300只×3)", "CAGR%": m3.get("CAGR%", np.nan),
         "Sharpe": m3.get("Sharpe", np.nan), "最大回撤%": m3.get("最大回撤%", np.nan),
         "交易": m3.get("交易笔数", 0), "胜率%": m3.get("胜率%", np.nan)},
        {"方案": "池内等权买入持有", "CAGR%": round(bh_cagr * 100, 2), "Sharpe": np.nan,
         "最大回撤%": np.nan, "交易": 0, "胜率%": np.nan},
    ]
    M = pd.DataFrame(rows)
    print(M.to_string(index=False))
    print()
    if m_rd.get("CAGR%") and m_r["CAGR%"]:
        diff = m_r["CAGR%"] - m_rd["CAGR%"]
        print(f"策略 vs 随机入场 差异: {diff:+.2f}pct/年")
        if diff < 5:
            print("⚠ 策略优势不明显：收益可能主要来自市场beta而非择时")
        elif diff < 15:
            print("△ 策略有正向优势，但幅度有限")
        else:
            print("√ 策略相对随机入场有显著优势")

    outdir = os.path.join(HERE, "validation", "bench_" + time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(outdir, exist_ok=True)
    M.to_csv(os.path.join(outdir, "benchmark.csv"), index=False, encoding="utf-8-sig")
    print(f"\n[输出] {outdir}")


if __name__ == "__main__":
    main()
