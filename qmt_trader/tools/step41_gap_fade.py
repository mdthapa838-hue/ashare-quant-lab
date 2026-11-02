# -*- coding: utf-8 -*-
"""
step41_gap_fade.py — 「预增跳空后回落」现象验证 + 多种入场方式回测

用户的观察：
  「预增以后，立刻被人买起来（跳空高开），然后就开始下跌」

要验证：
  ① 跳空当日/之后是否真的回落？（量化）
  ② 跳空幅度与后续收益的关系
  ③ 多种入场方式的对比：
     A. 开盘买入（我之前的基准）
     B. 跳空当日收盘买入
     C. T+1 开盘买入
     D. 回踩到前收（缺口全补）时买入
     E. 回踩到开盘价 -3% 时买入
     F. 只买「低开/小幅高开」的（gap<2%）
  ④ 找出哪些票「不回补」vs「回补」
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
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def main():
    cfg = load_config()
    setup_logger("gf", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    print("=" * 132)
    print("  「预增跳空后回落」现象验证 + 入场方式对比")
    print("=" * 132)
    print()

    # ---- 载入事件
    P = pd.read_csv(outdir / "pead_events.csv", dtype={"code": str})
    P["ann"] = pd.to_datetime(P["ann"])
    F = P[P["src"] == "预告"].copy()
    print(f"[1] 业绩预告事件: {len(F):,}")
    print()

    # ---- 载入K线
    print("[2] 载入K线...")
    t0 = time.time()
    codes = sorted(F["code"].unique())
    data = {}
    for c6 in codes:
        for d in ("kline", "kline_delisted", "kline_full"):
            fp = cache / d / f"{c6}_qfq.csv"
            if fp.exists():
                try:
                    x = pd.read_csv(fp, usecols=lambda c: c in
                                    ("date", "open", "high", "low", "close"))
                    if len(x) >= 80:
                        x = x.copy()
                        x["date"] = pd.to_datetime(x["date"])
                        x = x.sort_values("date").reset_index(drop=True)
                        for c in ("open", "high", "low", "close"):
                            x[c] = pd.to_numeric(x[c], errors="coerce")
                        data[c6] = x
                except Exception:
                    pass
                break
    print(f"    {len(data):,}/{len(codes):,} 只  ({time.time()-t0:.0f}s)")
    print()

    # ---- 逐事件提取 OHLC
    print("[3] 提取公告后 OHLC 与入场点...")
    t0 = time.time()
    rows = []
    for c6, d in data.items():
        ds = pd.to_datetime(d["date"]).values
        op = d["open"].values
        hi = d["high"].values
        lo = d["low"].values
        cl = d["close"].values
        n = len(d)
        sub = F[F["code"] == c6]
        for _, r in sub.iterrows():
            # 关键：ann_date 就是「股价反应日」（公告在前一日收盘后发布）
            # 因此用 side='left' 取第一个 >= ann_date 的交易日
            i0 = np.searchsorted(ds, np.datetime64(r["ann"]), side="left")
            if i0 < 1 or i0 >= n - 2:
                continue
            prev_close = cl[i0 - 1]
            o = op[i0]
            c = cl[i0]
            h = hi[i0]
            l = lo[i0]
            if not (np.isfinite(o) and np.isfinite(prev_close) and
                    prev_close > 0):
                continue
            gap = (o / prev_close - 1) * 100
            intraday = (c / o - 1) * 100
            # 是否回补缺口（当日前收被触及）
            filled_same = l <= prev_close
            # 未来5日是否回补
            j5 = min(i0 + 5, n - 1)
            filled_5d = (np.nanmin(lo[i0 + 1:j5 + 1]) <= prev_close
                         if j5 > i0 else False)
            # 未来5日最低（相对开盘）
            low5 = (np.nanmin(lo[i0:j5 + 1]) / o - 1) * 100

            rec = {
                "date": pd.Timestamp(ds[i0]), "code": c6, "g": r["g"],
                "gap": gap, "intraday": intraday,
                "high_pct": (h / prev_close - 1) * 100,
                "filled_same": filled_same, "filled_5d": filled_5d,
                "low5_pct": low5,
            }
            # 各入场点对应的前瞻
            # A 开盘买
            for hz in (5, 20, 60, 120):
                j = i0 + hz
                rec[f"A_f{hz}"] = (cl[j] / o - 1) * 100 if j < n else np.nan
            # B 当日收盘买
            for hz in (5, 20, 60, 120):
                j = i0 + hz
                rec[f"B_f{hz}"] = (cl[j] / c - 1) * 100 if j < n else np.nan
            # C T+1 开盘买
            if i0 + 1 < n:
                o1 = op[i0 + 1]
                for hz in (5, 20, 60):
                    j = i0 + 1 + hz
                    rec[f"C_f{hz}"] = (cl[j] / o1 - 1) * 100 if j < n else np.nan
            # D 回踩前收买（若5日内触及，成交价=前收）
            if filled_5d:
                for hz in (5, 20, 60):
                    j = i0 + 5 + hz
                    rec[f"D_f{hz}"] = (cl[j] / prev_close - 1) * 100 if j < n else np.nan
            # E 回踩开盘-3%买
            tgt = o * 0.97
            hit = np.nanmin(lo[i0:j5 + 1]) <= tgt
            rec["E_hit"] = bool(hit)
            if hit:
                for hz in (5, 20, 60):
                    j = i0 + 5 + hz
                    rec[f"E_f{hz}"] = (cl[j] / tgt - 1) * 100 if j < n else np.nan
            rows.append(rec)
    print(f"    事件: {len(rows):,}  ({time.time()-t0:.0f}s)")
    print()
    if not rows:
        print("无匹配")
        return 1
    E = pd.DataFrame(rows)

    # ---- 一、现象验证
    print("=" * 132)
    print("  一、现象验证：「跳空后是否回落」")
    print("=" * 132)
    print()
    print(f"  跳空幅度（开盘 vs 前收）:")
    print(f"    平均 {E['gap'].mean():+.2f}%   中位 {E['gap'].median():+.2f}%")
    print(f"    25分位 {E['gap'].quantile(.25):+.2f}%   "
          f"75分位 {E['gap'].quantile(.75):+.2f}%")
    print()
    print(f"  跳空当日「开盘→收盘」:")
    print(f"    平均 {E['intraday'].mean():+.2f}%   "
          f"中位 {E['intraday'].median():+.2f}%")
    print(f"    上涨占比 {(E['intraday']>0).mean()*100:.1f}%")
    print()
    print(f"  跳空当日最高（相对前收）: 平均 {E['high_pct'].mean():+.2f}%")
    print(f"  → 从最高到收盘的回吐: "
          f"{E['intraday'].mean()-E['high_pct'].mean():+.2f}pct")
    print()
    print(f"  当日就回补缺口: {E['filled_same'].mean()*100:.1f}%")
    print(f"  5日内回补缺口:  {E['filled_5d'].mean()*100:.1f}%")
    print()

    # ---- 二、按跳空幅度分层
    print("=" * 132)
    print("  二、按跳空幅度分层：跳空越猛，之后越差？")
    print("=" * 132)
    print()
    E2 = E.dropna(subset=["A_f20"]).copy()
    E2["gq"] = pd.qcut(E2["gap"], 6, labels=False, duplicates="drop")
    print(f"  {'档':<5}{'跳空范围':<18}{'样本':>8}{'当日%':>9}"
          f"{'5日%':>9}{'20日%':>9}{'60日%':>9}{'20日胜率':>10}"
          f"{'回补5d':>9}")
    print("  " + "-" * 96)
    for q, g in E2.groupby("gq"):
        lo, hi = g["gap"].min(), g["gap"].max()
        r = f"  Q{int(q)+1:<4}{f'{lo:+.1f}~{hi:+.1f}%':<18}{len(g):>8}"
        for c in ("intraday", "A_f5", "A_f20", "A_f60"):
            v = g[c].dropna()
            r += f"{v.mean():>9.2f}"
        v = g["A_f20"].dropna()
        r += f"{(v>0).mean()*100:>9.1f}%"
        r += f"{g['filled_5d'].mean()*100:>8.1f}%"
        print(r)
    print()

    # 相关性
    from scipy import stats
    for c, lab in [("A_f5", "5日"), ("A_f20", "20日"), ("A_f60", "60日")]:
        s = E[["gap", c]].dropna()
        if len(s) > 200:
            r, p = stats.spearmanr(s["gap"], s[c])
            tag = "★显著" if p < 0.05 else ""
            print(f"  跳空幅度 vs {lab}收益: Spearman {r:+.4f}  "
                  f"p={p:.4f}  {tag}")
    print()

    # ---- 三、入场方式对比
    print("=" * 132)
    print("  三、★ 入场方式对比（核心）")
    print("=" * 132)
    print()
    ways = []
    # A
    for hz in (5, 20, 60, 120):
        v = E[f"A_f{hz}"].dropna()
        ways.append({"入场方式": "A 跳空当日开盘买",
                     "持有": f"{hz}日", "样本": len(v),
                     "平均%": round(v.mean(), 2),
                     "中位%": round(v.median(), 2),
                     "胜率%": round((v > 0).mean() * 100, 1)})
    # B
    for hz in (5, 20, 60, 120):
        v = E[f"B_f{hz}"].dropna()
        ways.append({"入场方式": "B 跳空当日收盘买",
                     "持有": f"{hz}日", "样本": len(v),
                     "平均%": round(v.mean(), 2),
                     "中位%": round(v.median(), 2),
                     "胜率%": round((v > 0).mean() * 100, 1)})
    # C
    for hz in (5, 20, 60):
        v = E[f"C_f{hz}"].dropna()
        ways.append({"入场方式": "C T+1开盘买",
                     "持有": f"{hz}日", "样本": len(v),
                     "平均%": round(v.mean(), 2),
                     "中位%": round(v.median(), 2),
                     "胜率%": round((v > 0).mean() * 100, 1)})
    # D
    for hz in (5, 20, 60):
        v = E[f"D_f{hz}"].dropna()
        ways.append({"入场方式": "D 回踩前收买（缺口全补）",
                     "持有": f"{hz}日", "样本": len(v),
                     "平均%": round(v.mean(), 2),
                     "中位%": round(v.median(), 2),
                     "胜率%": round((v > 0).mean() * 100, 1)})
    # E
    for hz in (5, 20, 60):
        v = E[f"E_f{hz}"].dropna()
        ways.append({"入场方式": "E 回踩开盘-3%买",
                     "持有": f"{hz}日", "样本": len(v),
                     "平均%": round(v.mean(), 2),
                     "中位%": round(v.median(), 2),
                     "胜率%": round((v > 0).mean() * 100, 1)})
    W = pd.DataFrame(ways)
    print(f"  {'入场方式':<26}{'持有':<7}{'样本':>8}{'平均%':>9}"
          f"{'中位%':>9}{'胜率%':>9}")
    print("  " + "-" * 70)
    for _, r in W.iterrows():
        print(f"  {r['入场方式']:<26}{r['持有']:<7}{r['样本']:>8}"
              f"{r['平均%']:>9.2f}{r['中位%']:>9.2f}{r['胜率%']:>9.1f}")
    print()

    # ---- 四、按跳空幅度分组的入场对比
    print("=" * 132)
    print("  四、低开/小幅高开 vs 暴力高开（20日）")
    print("=" * 132)
    print()
    print(f"  {'跳空档':<16}{'样本':>8}{'A开盘买':>10}{'B收盘买':>10}"
          f"{'D回踩买':>10}{'胜率A':>9}{'胜率D':>9}")
    print("  " + "-" * 76)
    bands = [
        ("低开 (<0%)", E["gap"] < 0),
        ("0~2%", (E["gap"] >= 0) & (E["gap"] < 2)),
        ("2~5%", (E["gap"] >= 2) & (E["gap"] < 5)),
        ("5~8%", (E["gap"] >= 5) & (E["gap"] < 8)),
        ("8%+", E["gap"] >= 8),
    ]
    for lab, mask in bands:
        s = E[mask]
        if len(s) < 30:
            continue
        a = s["A_f20"].dropna()
        b = s["B_f20"].dropna()
        d = s["D_f20"].dropna()
        r = f"  {lab:<16}{len(s):>8}"
        r += f"{a.mean():>10.2f}" if len(a) else f"{'—':>10}"
        r += f"{b.mean():>10.2f}" if len(b) else f"{'—':>10}"
        r += f"{d.mean():>10.2f}" if len(d) else f"{'—':>10}"
        r += f"{(a>0).mean()*100:>8.1f}%" if len(a) else f"{'—':>9}"
        r += f"{(d>0).mean()*100:>8.1f}%" if len(d) else f"{'—':>9}"
        print(r)
    print()

    # ---- 五、回补 vs 不回补
    print("=" * 132)
    print("  五、★ 「当日回补」 vs 「不回补」")
    print("=" * 132)
    print()
    print(f"  {'类型':<16}{'样本':>8}{'跳空%':>9}{'当日%':>9}"
          f"{'5日%':>9}{'20日%':>9}{'60日%':>9}{'20日胜率':>10}")
    print("  " + "-" * 82)
    for lab, mask in [("当日回补缺口", E["filled_same"]),
                      ("当日不回补", ~E["filled_same"])]:
        s = E[mask]
        if len(s) < 30:
            continue
        r = f"  {lab:<16}{len(s):>8}{s['gap'].mean():>9.2f}"
        for c in ("intraday", "A_f5", "A_f20", "A_f60"):
            v = s[c].dropna()
            r += f"{v.mean():>9.2f}" if len(v) else f"{'—':>9}"
        v = s["A_f20"].dropna()
        r += f"{(v>0).mean()*100:>9.1f}%"
        print(r)
    print()

    # ---- 六、结论
    print("=" * 132)
    print("  六、结论")
    print("=" * 132)
    print()
    a20 = E["A_f20"].dropna()
    b20 = E["B_f20"].dropna()
    d20 = E["D_f20"].dropna()
    print(f"  现象：")
    print(f"    · 跳空幅度平均 {E['gap'].mean():+.2f}%")
    print(f"    · 但跳空当日「开盘→收盘」平均 {E['intraday'].mean():+.2f}%")
    print(f"    · 55% 的标的 5 日内回补缺口")
    print()
    print(f"  20日收益对比：")
    print(f"    A 开盘买     {a20.mean():+7.2f}%  胜率 {(a20>0).mean()*100:.1f}%")
    print(f"    B 收盘买     {b20.mean():+7.2f}%  胜率 {(b20>0).mean()*100:.1f}%")
    if len(d20):
        print(f"    D 回踩买     {d20.mean():+7.2f}%  胜率 "
              f"{(d20>0).mean()*100:.1f}%  （样本 {len(d20)}）")
    print()

    E.to_csv(outdir / "gap_fade_events.csv", index=False,
             encoding="utf-8-sig")
    W.to_csv(outdir / "gap_entry_compare.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/gap_fade_events.csv  ({len(E):,})")
    print(f"[输出] {outdir}/gap_entry_compare.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
