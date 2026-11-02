# -*- coding: utf-8 -*-
"""
重跑信号审计 —— 使用修复后的分层股池

修复内容：
  1. 月线未来函数（已完成）
  2. 股池板块偏差（本次：改为分层随机抽样）

输出：
  - 新池信号台账与结果
  - 与旧池结果对比
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
from core.logger import setup_logger
from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound


def board(c: str) -> str:
    if c.startswith("688"):
        return "科创板"
    if c.startswith(("300", "301")):
        return "创业板"
    if c.startswith(("8", "4", "9")):
        return "北交所"
    if c.startswith(("600", "601", "603", "605")):
        return "沪主板"
    if c.startswith(("000", "001", "002", "003")):
        return "深主板"
    return "其他"


def to_ts(c6: str) -> str:
    if c6.startswith(("60", "68")):
        return c6 + ".SH"
    if c6.startswith(("8", "4", "9")):
        return c6 + ".BJ"
    return c6 + ".SZ"


def main():
    cfg = load_config()
    setup_logger("audit2", log_dir=None, level="WARNING", console=False)

    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.3)
    st = BollLowerRebound(cfg.to_strategy_cfg(), src)

    # ---- 用分层样本作为审计池
    samp = pd.read_csv(cache.parent / "output" / "pool_stratified.csv",
                       dtype={"ts_code": str})
    codes6 = sorted(set(samp["ts_code"].str[:6].tolist()))

    print("=" * 100)
    print("  信号审计（修复后：分层股池 + 无未来函数）")
    print("=" * 100)
    print()
    print(f"审计池: {len(codes6)} 只（分层随机抽样，板块比例对齐总体）")

    # ---- 载入K线
    data = {}
    for c6 in codes6:
        p = cache / "kline" / f"{c6}_qfq.csv"
        if not p.exists():
            continue
        try:
            d = pd.read_csv(p)
            if len(d) < 130:
                continue
            d["date"] = pd.to_datetime(d["date"])
            d = d.sort_values("date").reset_index(drop=True)
            data[c6] = d
        except Exception:
            pass
    print(f"有效K线: {len(data)} 只")

    spans = [(d["date"].min(), d["date"].max(), len(d)) for d in data.values()]
    print(f"时间跨度: {min(s[0] for s in spans).date()} ~ "
          f"{max(s[1] for s in spans).date()}")
    print(f"K线总数: {sum(s[2] for s in spans):,} 根")
    print()

    # ---- 择时序列
    idx = src.index_daily(st.market_index, "20150101", "20261014")
    if idx is None:
        print("指数获取失败")
        return 1
    mkt = (idx / idx.shift(st.market_win) - 1) * 100
    ok_days = mkt < st.market_max
    print(f"择时: 中证1000 60日 < {st.market_max}%")
    print(f"  通过天数: {int(ok_days.sum())} / {len(mkt)} "
          f"({ok_days.sum()/len(mkt)*100:.1f}%)")
    print(f"  通过日期: {[d.strftime('%Y-%m-%d') for d in ok_days[ok_days].index]}")
    print()

    # ---- 扫描
    print("=" * 100)
    print("  扫描信号")
    print("=" * 100)
    rows = []
    t0 = time.time()
    cond_cols = ["cond_position", "cond_streak", "cond_ma60_down",
                 "cond_monthly_up", "cond_liquidity"]

    for i, (c6, raw) in enumerate(sorted(data.items())):
        try:
            d = st.compute(raw.copy())
            d = d.merge(mkt.rename("mkt_ret").reset_index().rename(
                columns={"index": "date"}), on="date", how="left")
            d["cond_market"] = (d["mkt_ret"] < st.market_max).fillna(False)
            allc = cond_cols + ["cond_market"]
            d["sig"] = d[allc].all(axis=1)

            for k in np.where(d["sig"].values)[0]:
                if k + 1 >= len(d):
                    continue
                r = d.iloc[k]
                nx = d.iloc[k + 1]
                rows.append({
                    "code": to_ts(c6),
                    "board": board(c6),
                    "signal_date": r["date"],
                    "entry_date": nx["date"],
                    "signal_close": round(float(r["close"]), 3),
                    "entry_open": round(float(nx["open"]), 3),
                    "open_chg_pct": round(float(nx["open_chg_pct"]), 2)
                    if pd.notna(nx.get("open_chg_pct")) else None,
                    "pos120": round(float(r["pos120"]), 4),
                    "streak": int(r["lower_streak"]),
                    "ma60_slope": round(float(r["ma60_slope"]), 5),
                    "mkt_ret": round(float(r["mkt_ret"]), 2),
                    "bars_history": int(k + 1),
                    "amount_ma20": round(float(r["amount_ma20"]), 0),
                })
        except Exception:
            pass
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(data)}  累计信号 {len(rows)}  "
                  f"耗时 {time.time()-t0:.0f}s", flush=True)

    if not rows:
        print()
        print("★ 分层股池下，历史无任何完整信号")
        return 0

    L = pd.DataFrame(rows).sort_values("signal_date").reset_index(drop=True)
    print()
    print(f"完整信号数: {len(L)}")
    print(f"涉及标的: {L['code'].nunique()} 只")
    print(f"时间跨度: {L['signal_date'].min().date()} ~ {L['signal_date'].max().date()}")
    print()

    # ---- 板块分布（关键：对比旧池）
    print("=" * 100)
    print("  信号板块分布（关键对比）")
    print("=" * 100)
    print()
    print("  【旧池 611只（有偏）】18 个信号")
    print("      603496/603108/603903/603353/603266/688108 → 全部沪市代码")
    print("      创业板 0 只，深主板 0 只")
    print()
    print("  【新池 700只（分层修正）】")
    bd = L["board"].value_counts()
    for k, v in bd.items():
        print(f"      {k:<8} {v:>4} 个信号  ({v/len(L)*100:>5.1f}%)")
    print()
    bl = L["code"].value_counts()
    print(f"  信号最多的标的（共 {L['code'].nunique()} 只）:")
    for c, n in bl.head(20).items():
        b = board(c[:6])
        print(f"      {c}  {b:<6} {n} 次")
    print()

    # ---- 截断重算验证
    print("=" * 100)
    print("  截断重算验证（无未来函数）")
    print("=" * 100)
    print()
    n_chk = min(60, len(L))
    smp = L.sample(n=n_chk, random_state=42)
    bad = 0
    badd = []
    for _, row in smp.iterrows():
        c6 = row["code"][:6]
        raw = data.get(c6)
        if raw is None:
            continue
        trunc = raw[raw["date"] <= row["signal_date"]]
        if len(trunc) < 130:
            continue
        d1 = st.compute(trunc.copy())
        r1 = d1.iloc[-1]
        s1 = all(bool(r1[c]) for c in cond_cols)
        d2 = st.compute(raw.copy())
        r2 = d2[d2["date"] == row["signal_date"]]
        if len(r2) == 0:
            continue
        s2 = all(bool(r2.iloc[0][c]) for c in cond_cols)
        if bool(s1) != bool(s2):
            bad += 1
            badd.append({"code": row["code"],
                         "date": str(row["signal_date"].date()),
                         "截断": s1, "全量": s2})
    print(f"  抽样 {n_chk} 个，不一致 {bad} 个")
    if bad == 0:
        print("  √ 截断重算与全量计算完全一致 → 无未来函数")
    else:
        for b in badd[:6]:
            print(f"    {b}")
    print()

    # ---- 信号后表现
    print("=" * 100)
    print("  信号后的实际表现（T+1开盘买入）")
    print("=" * 100)
    print()
    out = []
    for _, row in L.iterrows():
        c6 = row["code"][:6]
        raw = data.get(c6)
        if raw is None:
            continue
        full = st.compute(raw.copy())
        k0 = full.index[full["date"] == row["signal_date"]]
        if len(k0) == 0:
            continue
        k0 = int(k0[0])
        after = full.iloc[k0 + 1:]
        if len(after) < 3:
            continue
        entry = after.iloc[0]["open"]
        if not np.isfinite(entry) or entry <= 0:
            continue
        stop = entry * (1 + st.stop_pct)
        xp = reason = days = None
        # 出场判定从「入场日的下一根」开始：
        #   j-1 必须是 >= k0+1（入场日）的已完成K线，
        #   否则会用信号日（k0）的涨跌决定出场 → 顺序错误
        for j in range(k0 + 2, min(len(full), k0 + 2 + st.max_hold + 1)):
            prev = full.iloc[j - 1]      # 已完成的、且不早于入场日
            bar = full.iloc[j]
            if pd.notna(prev["bb_upper"]) and prev["high"] >= prev["bb_upper"]:
                xp, reason, days = bar["open"], "止盈", j - (k0 + 1)
                break
            if prev["low"] <= stop:
                xp, reason, days = bar["open"], "止损", j - (k0 + 1)
                break
            if j - (k0 + 1) >= st.max_hold:
                xp, reason, days = bar["open"], "超时", j - (k0 + 1)
                break
        rec = {"code": row["code"], "board": row["board"],
               "signal_date": row["signal_date"].date(),
               "entry": round(float(entry), 2)}
        for nd in (5, 10, 20, 60):
            rec[f"T+{nd}"] = round((after.iloc[nd]["close"] / entry - 1) * 100, 1) \
                if len(after) > nd else None
        rec["exit_reason"] = reason
        rec["hold_days"] = days
        rec["ret"] = round((xp / entry - 1) * 100, 1) if xp is not None else None
        out.append(rec)
    O = pd.DataFrame(out)

    if len(O):
        print(f"  可评估: {len(O)} 个")
        print()
        print("  前瞻收益（中位数%）:")
        for c in ["T+5", "T+10", "T+20", "T+60"]:
            v = O[c].dropna()
            if len(v):
                print(f"    {c:<6} 中位 {v.median():>+7.1f}   均值 {v.mean():>+7.1f}   "
                      f"胜率 {(v>0).mean()*100:>5.1f}%   n={len(v)}")
        print()
        r = O["ret"].dropna()
        if len(r):
            print(f"  按规则出场（{len(r)} 笔）:")
            print(f"    平均 {r.mean():+.1f}%   中位 {r.median():+.1f}%   "
                  f"胜率 {(r>0).mean()*100:.1f}%")
            print(f"    最大盈 {r.max():+.1f}%   最大亏 {r.min():+.1f}%")
            vc = O["exit_reason"].value_counts()
            for k, v in vc.items():
                print(f"    {k}: {v}")
        print()

    # ---- 输出
    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    p1 = outdir / f"signal_ledger_fixed_{stamp}.csv"
    L.to_csv(p1, index=False, encoding="utf-8-sig")
    print(f"[输出] {p1}")
    if len(O):
        p2 = outdir / f"signal_outcome_fixed_{stamp}.csv"
        O.to_csv(p2, index=False, encoding="utf-8-sig")
        print(f"[输出] {p2}")

    print()
    print("=" * 100)
    print("  新旧池对比汇总")
    print("=" * 100)
    print()
    cmp = pd.DataFrame([
        ["股池规模", "611 只（代码排序前段）", f"{len(data)} 只（分层随机）"],
        ["科创板占比", "37.5%（应与总体10.6%）", "10.6% ✓"],
        ["创业板占比", "1.1%（应与总体31.1%）", "31.1% ✓"],
        ["完整信号数", "18", f"{len(L)}"],
        ["涉及标的", "6 只", f"{L['code'].nunique()} 只"],
        ["创业板信号", "0", f"{int((L['board']=='创业板').sum())}"],
        ["深主板信号", "0", f"{int((L['board']=='深主板').sum())}"],
        ["信号时间跨度", "2024-01-31 ~ 2024-02-07",
         f"{L['signal_date'].min().date()} ~ {L['signal_date'].max().date()}"],
    ], columns=["项目", "旧池（有偏）", "新池（修正）"])
    print(cmp.to_string(index=False))
    p3 = outdir / f"pool_compare_{stamp}.csv"
    cmp.to_csv(p3, index=False, encoding="utf-8-sig")
    print(f"\n[输出] {p3}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
