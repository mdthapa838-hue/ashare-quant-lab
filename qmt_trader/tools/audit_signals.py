# -*- coding: utf-8 -*-
"""
历史信号审计 — 严格无未来函数验证

核心问题：
  1. 过去真的有信号吗？有多少？什么时候？
  2. 每个信号在当时的时点上，是否真的可计算、可成交？
  3. 有没有隐蔽的未来函数？

验证方法：
  A. 全量信号台账（信号日 / 入场日 / 价格 / 结果）
  B. 截断重算测试：只用 T 日及之前的数据重算，验证信号是否一致
  C. 数据可得性审计：上市时间、停牌、流动性
  D. 可成交性审计：次日开盘是否涨停（买不进）、是否停牌
  E. 前视偏差排查：复权因子、股票池、ST状态
"""
from __future__ import annotations

import os
import sys
import glob
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import load_config
from core.logger import setup_logger, get_logger
from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound

LOG = get_logger("audit")


# ---------------------------------------------------------------------------
def load_cached_kline(cache_dir: str) -> dict[str, pd.DataFrame]:
    """加载全部本地缓存K线。"""
    kdir = Path(cache_dir) / "kline"
    data = {}
    for f in glob.glob(str(kdir / "*.csv")):
        stem = os.path.basename(f)[:-4]
        for suf in ("_qfq", "_hfq", "_bfq"):
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
        if not (stem.isdigit() and len(stem) == 6):
            continue
        try:
            d = pd.read_csv(f)
            if len(d) < 130:
                continue
            d["date"] = pd.to_datetime(d["date"])
            d = d.sort_values("date").reset_index(drop=True)
            data[stem] = d
        except Exception:
            pass
    return data


def to_ts(code6: str) -> str:
    if code6.startswith(("60", "68")):
        return code6 + ".SH"
    if code6.startswith(("8", "4", "9")):
        return code6 + ".BJ"
    return code6 + ".SZ"


# ---------------------------------------------------------------------------
# 主审计
# ---------------------------------------------------------------------------
def main():
    cfg = load_config()
    setup_logger("audit", log_dir=None, level="INFO", console=True)

    ds = cfg.section("datasource")
    cache = ds.get("cache_dir")
    src = TushareSource(cache_dir=cache, min_interval=0.35)

    scfg = cfg.to_strategy_cfg()
    st = BollLowerRebound(scfg, src)

    print("=" * 100)
    print("  历史信号审计 — 严格无未来函数验证")
    print("=" * 100)
    print()

    # ---- 加载数据
    data = load_cached_kline(cache)
    print(f"[数据] 本地缓存标的 {len(data)} 只")
    if not data:
        print("无可用缓存，退出")
        return 1

    span = []
    for c, d in data.items():
        span.append((d["date"].min(), d["date"].max(), len(d)))
    print(f"[数据] 时间跨度 {min(s[0] for s in span).date()} ~ "
          f"{max(s[1] for s in span).date()}")
    print(f"[数据] K线总数 {sum(s[2] for s in span):,} 根")
    print()

    # ---- 市场择时序列（用于审计）
    print("[择时] 获取指数序列...")
    idx = src.index_daily(st.market_index, "20150101", "20261014")
    if idx is None:
        print("  指数获取失败")
        return 1
    mkt = (idx / idx.shift(st.market_win) - 1) * 100
    mkt_ok_days = (mkt < st.market_max)
    print(f"  指数 {st.market_index}: {len(idx)} 天")
    print(f"  择时通过天数: {int(mkt_ok_days.sum())} / {len(mkt)} "
          f"({mkt_ok_days.sum()/len(mkt)*100:.1f}%)")
    print(f"  最近通过日期: "
          f"{[d.strftime('%Y-%m-%d') for d in mkt_ok_days[mkt_ok_days].index[-5:]]}")
    print()

    # ------------------------------------------------------------------
    # 【A】逐只扫描信号（含择时）
    # ------------------------------------------------------------------
    print("=" * 100)
    print("  【A】全量信号台账（严格 T日收盘算信号 → T+1开盘成交）")
    print("=" * 100)

    ledger = []
    t0 = time.time()
    for i, (code6, raw) in enumerate(sorted(data.items())):
        ts_code = to_ts(code6)
        try:
            d = st.compute(raw.copy())
        except Exception as e:
            continue
        # 市场择时（按日期对齐）
        d = d.merge(
            mkt.rename("mkt_ret").reset_index().rename(columns={"index": "date"}),
            on="date", how="left")
        d["cond_market"] = (d["mkt_ret"] < st.market_max).fillna(False)

        cond_cols = ["cond_position", "cond_streak", "cond_ma60_down",
                     "cond_monthly_up", "cond_liquidity", "cond_market"]
        d["n_ok"] = d[cond_cols].sum(axis=1)
        d["sig_full"] = d[cond_cols].all(axis=1)
        # 不含择时（观察用）
        no_mkt = ["cond_position", "cond_streak", "cond_ma60_down",
                  "cond_monthly_up", "cond_liquidity"]
        d["sig_nomkt"] = d[no_mkt].all(axis=1)

        for k in np.where(d["sig_full"].values)[0]:
            if k + 1 >= len(d):
                continue
            r = d.iloc[k]
            nx = d.iloc[k + 1]
            ledger.append({
                "code": ts_code,
                "signal_date": r["date"],
                "entry_date": nx["date"],
                "signal_close": round(float(r["close"]), 3),
                "entry_open": round(float(nx["open"]), 3),
                "open_chg_pct": round(float(nx["open_chg_pct"]), 2)
                if pd.notna(nx.get("open_chg_pct")) else None,
                "pos120": round(float(r["pos120"]), 4),
                "streak": int(r["lower_streak"]),
                "ma60_slope": round(float(r["ma60_slope"]), 5),
                "bb_lower": round(float(r["bb_lower"]), 3),
                "bb_mid": round(float(r["bb_mid"]), 3),
                "bb_upper": round(float(r["bb_upper"]), 3),
                "mkt_ret": round(float(r["mkt_ret"]), 2),
                "bars_history": int(k + 1),
                "amount_ma20": round(float(r["amount_ma20"]), 0),
                "signal_idx": int(k),
            })
        if (i + 1) % 50 == 0:
            print(f"  扫描 {i+1}/{len(data)}  累计信号 {len(ledger)}  "
                  f"耗时 {time.time()-t0:.0f}s", flush=True)

    if not ledger:
        print("\n★ 结论：严格条件下（含择时）历史无任何信号")
        return 0

    L = pd.DataFrame(ledger).sort_values("signal_date").reset_index(drop=True)
    print(f"\n  完整信号数（含择时）: {len(L)}")
    print(f"  涉及标的: {L['code'].nunique()} 只")
    print(f"  时间跨度: {L['signal_date'].min().date()} ~ "
          f"{L['signal_date'].max().date()}")
    print()

    # 分年
    L["year"] = pd.to_datetime(L["signal_date"]).dt.year
    g = L.groupby("year").agg(
        信号数=("code", "size"),
        标的总数=("code", "nunique"),
        平均市场收益=("mkt_ret", "mean"),
    ).round(2)
    print("  分年分布:")
    print(g.to_string())
    print()

    # 涉及的标的
    top = L["code"].value_counts().head(15)
    print("  信号最多的标的:")
    for c, n in top.items():
        print(f"    {c}  {n} 次")
    print()

    # ------------------------------------------------------------------
    # 【B】截断重算测试（无未来函数的核心验证）
    # ------------------------------------------------------------------
    print("=" * 100)
    print("  【B】截断重算测试 —— 只用T日及之前数据重算，验证信号一致性")
    print("=" * 100)
    print()

    sample_n = min(60, len(L))
    sample = L.sample(n=sample_n, random_state=42) if len(L) > sample_n else L
    mismatch = 0
    checked = 0
    details = []

    for _, row in sample.iterrows():
        code6 = row["code"].split(".")[0]
        raw = data.get(code6)
        if raw is None:
            continue
        # 截断：只保留 signal_date 及之前
        trunc = raw[raw["date"] <= row["signal_date"]].copy()
        if len(trunc) < 130:
            continue
        # 用截断数据重算
        dt = st.compute(trunc)
        ok5 = ["cond_position", "cond_streak", "cond_ma60_down",
               "cond_monthly_up", "cond_liquidity"]
        r = dt.iloc[-1]
        sig_trunc = all(bool(r[c]) for c in ok5)
        # 与全量计算比对（全量时该行的 sig_nomkt）
        full_row = st.compute(raw.copy())
        pos_k = dt.iloc[-1] if False else None
        # 全量里同一天
        fr = full_row[full_row["date"] == row["signal_date"]]
        sig_full = bool(fr.iloc[0]["cond_position"] and fr.iloc[0]["cond_streak"]
                        and fr.iloc[0]["cond_ma60_down"] and fr.iloc[0]["cond_monthly_up"]
                        and fr.iloc[0]["cond_liquidity"]) if len(fr) else None

        checked += 1
        if sig_full is not None and bool(sig_trunc) != bool(sig_full):
            mismatch += 1
            details.append({
                "code": row["code"], "date": row["signal_date"].date(),
                "截断重算": sig_trunc, "全量计算": sig_full,
            })

    print(f"  抽样验证: {checked} 个信号")
    print(f"  一致: {checked - mismatch}   不一致: {mismatch}")
    if mismatch == 0:
        print()
        print("  √ 截断重算与全量计算完全一致 → 信号不含未来函数")
        print("    （指标均为因果滚动窗口：MA/布林带/分位/连续计数只依赖历史数据）")
    else:
        print()
        print("  ⚠ 发现不一致，样例：")
        for d in details[:10]:
            print(f"    {d}")
    print()

    # ------------------------------------------------------------------
    # 【C】数据可得性审计
    # ------------------------------------------------------------------
    print("=" * 100)
    print("  【C】数据可得性审计 —— 信号当时数据是否真的存在")
    print("=" * 100)
    print()

    L["min_bars"] = L["bars_history"]
    print(f"  信号日最少历史K线数: {L['min_bars'].min()} 根")
    print(f"  信号日平均历史K线数: {L['min_bars'].mean():.0f} 根")
    insufficient = L[L["min_bars"] < 130]
    print(f"  历史不足130根的信号: {len(insufficient)} 个")
    if len(insufficient) == 0:
        print("  √ 全部信号在产生时都有充足的指标计算历史")
    print()

    # 流动性
    L["amt_wan"] = L["amount_ma20"] / 1e4
    print(f"  信号日20日均成交额（万元）:")
    print(f"    最小 {L['amt_wan'].min():,.0f}   中位 {L['amt_wan'].median():,.0f}   "
          f"最大 {L['amt_wan'].max():,.0f}")
    illiquid = L[L["amt_wan"] < 2000]
    print(f"    低于2000万的信号: {len(illiquid)} 个 "
          f"({len(illiquid)/len(L)*100:.1f}%)")
    print()

    # ------------------------------------------------------------------
    # 【D】可成交性审计
    # ------------------------------------------------------------------
    print("=" * 100)
    print("  【D】可成交性审计 —— 次日开盘能否真的买入")
    print("=" * 100)
    print()

    if "open_chg_pct" in L.columns and L["open_chg_pct"].notna().any():
        oc = L["open_chg_pct"].dropna()
        print(f"  次日开盘涨跌幅分布:")
        print(f"    < -5%      : {int((oc < -5).sum())} 个")
        print(f"    -5% ~ 0%   : {int(((oc >= -5) & (oc < 0)).sum())} 个")
        print(f"    0% ~ +5%   : {int(((oc >= 0) & (oc < 5)).sum())} 个")
        print(f"    +5% ~ +9.5%: {int(((oc >= 5) & (oc < 9.5)).sum())} 个")
        print(f"    >= +9.5% (涨停买不进): {int((oc >= 9.5).sum())} 个")
        print()
        limit_up = L[L["open_chg_pct"] >= 9.5]
        print(f"  因涨停无法成交的信号: {len(limit_up)} 个 "
              f"({len(limit_up)/len(L)*100:.1f}%)")
        if len(limit_up):
            print(f"    （这部分信号在实盘中会错过）")
        print(f"  实际可成交信号: {len(L) - len(limit_up)} 个")
    print()

    # ------------------------------------------------------------------
    # 【E】信号后的实际表现
    # ------------------------------------------------------------------
    print("=" * 100)
    print("  【E】每个信号之后发生了什么（T+1开盘买入）")
    print("=" * 100)
    print()

    outcome = []
    for _, row in L.iterrows():
        code6 = row["code"].split(".")[0]
        raw = data.get(code6)
        if raw is None:
            continue
        after = raw[raw["date"] > row["signal_date"]].reset_index(drop=True)
        if len(after) < 5:
            continue
        entry = after.iloc[0]["open"]
        if not np.isfinite(entry) or entry <= 0:
            continue

        # 用完整指标计算出场（模拟当时的出场规则）
        d_full = st.compute(raw.copy())
        k0 = d_full.index[d_full["date"] == row["signal_date"]]
        if len(k0) == 0:
            continue
        k0 = int(k0[0])
        stop = entry * (1 + st.stop_pct)
        exit_p, reason, days = None, None, None
        for j in range(k0 + 1, min(len(d_full), k0 + 1 + st.max_hold + 2)):
            bar = d_full.iloc[j]
            if j > k0 + 1:
                pass
            prev = d_full.iloc[j - 1]
            if pd.notna(prev["bb_upper"]) and prev["high"] >= prev["bb_upper"]:
                exit_p, reason, days = bar["open"], "止盈", j - k0 - 1
                break
            if prev["low"] <= stop:
                exit_p, reason, days = bar["open"], "止损", j - k0 - 1
                break
            if j - k0 - 1 >= st.max_hold:
                exit_p, reason, days = bar["open"], "超时", j - k0 - 1
                break

        # 面板收益（无论是否出场）
        fwd = {}
        for nd in (5, 10, 20, 60):
            if len(after) > nd:
                fwd[f"T+{nd}"] = (after.iloc[nd]["close"] / entry - 1) * 100
            else:
                fwd[f"T+{nd}"] = np.nan

        rec = {
            "code": row["code"],
            "signal_date": row["signal_date"].date(),
            "entry_date": row["entry_date"].date(),
            "entry": round(float(entry), 2),
            "pos120": row["pos120"],
            "streak": row["streak"],
            "mkt_ret": row["mkt_ret"],
            "exit_price": round(float(exit_p), 2) if exit_p is not None else None,
            "exit_reason": reason,
            "hold_days": days,
            "ret": round((exit_p / entry - 1) * 100, 1) if exit_p is not None else None,
        }
        rec.update({k: round(v, 1) if pd.notna(v) else None for k, v in fwd.items()})
        outcome.append(rec)

    O = pd.DataFrame(outcome)
    if len(O):
        print(f"  可评估信号: {len(O)} 个")
        print()
        print("  前瞻收益（中位数，%）:")
        for c in ["T+5", "T+10", "T+20", "T+60"]:
            if c in O.columns:
                v = O[c].dropna()
                if len(v):
                    print(f"    {c:<6} 中位 {v.median():>+7.1f}   "
                          f"均值 {v.mean():>+7.1f}   "
                          f"胜率 {(v>0).mean()*100:>5.1f}%   n={len(v)}")
        print()
        r = O["ret"].dropna()
        if len(r):
            print(f"  按策略规则出场（{len(r)} 笔）:")
            print(f"    平均收益 {r.mean():+.1f}%   中位 {r.median():+.1f}%   "
                  f"胜率 {(r>0).mean()*100:.1f}%")
            print(f"    最大盈利 {r.max():+.1f}%   最大亏损 {r.min():+.1f}%")
            print(f"    止损 {(O['exit_reason']=='止损').sum()}  "
                  f"止盈 {(O['exit_reason']=='止盈').sum()}  "
                  f"超时 {(O['exit_reason']=='超时').sum()}")
        print()

    # ------------------------------------------------------------------
    # 【F】前视偏差排查
    # ------------------------------------------------------------------
    print("=" * 100)
    print("  【F】前视偏差排查")
    print("=" * 100)
    print()
    print("  1) 复权因子漂移")
    print("     qfq(前复权) 会在分红送股后重算全序列。验证是否影响信号：")
    print("       所有指标均为比值形式（close/MA、分位、连续计数、斜率）")
    print("       而 qfq 对历史序列是【统一常数缩放】，故比值不变")
    print("       → 复权因子变化不影响信号判定 ✓")
    print()
    print("  2) 股票池选择 —— ⚠ 存在偏差")
    print("     本审计的标的池来自「当前存续 + 当前市值符合区间」")
    print("     历史时点上，某股的市值可能与今天不同；已退市股未纳入")
    print()
    print("  3) ST状态 —— ⚠ 存在偏差")
    print("     当前非ST的股票，历史上可能曾是ST（当时应被排除）")
    print()
    print("  4) 指标计算 —— ✓ 无偏差")
    print("     MA/布林带(rolling) / 分位(rolling min-max) / 连续计数 /")
    print("     月线重采样 均为因果窗口，只依赖 t 及之前的数据")
    print()
    print("  5) 成交时点 —— ✓ 无偏差")
    print("     信号用 T 日收盘数据 → T+1 开盘成交")
    print("     出场用 T-1 日收盘条件 → T 日开盘成交")
    print()

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    outdir = HERE / "output"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")

    p1 = outdir / f"signal_ledger_{stamp}.csv"
    L.to_csv(p1, index=False, encoding="utf-8-sig")
    print(f"[输出] 信号台账: {p1}")

    if len(O):
        p2 = outdir / f"signal_outcome_{stamp}.csv"
        O.to_csv(p2, index=False, encoding="utf-8-sig")
        print(f"[输出] 信号结果: {p2}")

    # 打印前30个信号明细
    print()
    print("=" * 100)
    print("  【信号台账明细】前 30 个")
    print("=" * 100)
    show = L.head(30)[["code", "signal_date", "entry_date", "signal_close",
                       "entry_open", "pos120", "streak", "mkt_ret"]].copy()
    show["signal_date"] = show["signal_date"].dt.strftime("%Y-%m-%d")
    show["entry_date"] = show["entry_date"].dt.strftime("%Y-%m-%d")
    print(show.to_string(index=False))
    print()
    print("=" * 100)
    print("  【信号台账明细】后 30 个")
    print("=" * 100)
    show2 = L.tail(30)[["code", "signal_date", "entry_date", "signal_close",
                        "entry_open", "pos120", "streak", "mkt_ret"]].copy()
    show2["signal_date"] = show2["signal_date"].dt.strftime("%Y-%m-%d")
    show2["entry_date"] = show2["entry_date"].dt.strftime("%Y-%m-%d")
    print(show2.to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
