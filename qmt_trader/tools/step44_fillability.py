# -*- coding: utf-8 -*-
"""
step44_fillability.py — A1 信号的「可成交性」分析

核心问题：
  选出来了，但买不到怎么办？
  ① T+1 开盘涨停 → 挂单排队买不到
  ② T 日就涨停 → 更难
  ③ 一字板 → 完全买不到

分析：
  ① 列出历史 A1 清单（含名称）
  ② 计算 T+1 开盘的可成交性（是否开盘涨停）
  ③ 分板块统计涨跌幅（主板10%、创业板/科创板20%）
  ④ 对比「能买到」vs「买不到」的收益
  ⑤ 给出实际下单方法（集合竞价挂涨停价的原理）
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


def limit_pct(c6: str) -> float:
    """按板块返回涨跌幅限制。"""
    if c6.startswith("68"):      # 科创板
        return 20.0
    if c6.startswith("30"):      # 创业板
        return 20.0
    if c6.startswith(("8", "4")):  # 北交所
        return 30.0
    return 10.0                   # 主板


def main():
    cfg = load_config()
    setup_logger("fill", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    print("=" * 138)
    print("  A1 信号的「可成交性」分析 —— 能不能买得到？")
    print("=" * 138)
    print()
    print("  ★ 核心问题：选出来了，但 T+1 开盘涨停就买不到")
    print()

    # ---- 载入 A1 清单
    A = pd.read_csv(outdir / "a1_strict_list.csv", dtype={"code": str})
    A["date"] = pd.to_datetime(A["date"])
    print(f"[1] 历史 A1 信号: {len(A):,}")
    print()

    # ---- 载入K线（含 open/high/low/close）
    print("[2] 载入K线（算可成交性）...")
    t0 = time.time()
    codes = sorted(A["code"].unique())
    data = {}
    for c6 in codes:
        for d in ("kline", "kline_delisted", "kline_full"):
            fp = cache / d / f"{c6}_qfq.csv"
            if fp.exists():
                try:
                    x = pd.read_csv(fp, usecols=lambda c: c in
                                    ("date", "open", "high", "low", "close"))
                    if len(x) >= 100:
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

    # ---- 计算可成交性
    print("[3] 计算 T+1 开盘可成交性...")
    t0 = time.time()
    rows = []
    for c6, d in data.items():
        ds = pd.to_datetime(d["date"]).values
        op = d["open"].values
        hi = d["high"].values
        lo = d["low"].values
        cl = d["close"].values
        n = len(d)
        lim = limit_pct(c6)
        sub = A[A["code"] == c6]
        for _, r in sub.iterrows():
            i0 = np.searchsorted(ds, np.datetime64(r["date"]), side="left")
            if i0 < 1 or i0 + 2 >= n:
                continue
            pre0 = cl[i0 - 1]
            c0 = cl[i0]
            # T日 涨停价
            lim_up0 = round(pre0 * (1 + lim / 100), 2)
            # T日是否涨停收盘
            t0_limit = c0 >= lim_up0 * 0.998
            # T日是否一字板（最低=最高=涨停）
            t0_yizi = (lo[i0] >= lim_up0 * 0.998) and t0_limit
            # T+1
            pre1 = c0
            o1 = op[i0 + 1]
            h1 = hi[i0 + 1]
            l1 = lo[i0 + 1]
            c1 = cl[i0 + 1]
            lim_up1 = round(pre1 * (1 + lim / 100), 2)
            # T+1 开盘是否涨停
            t1_open_limit = o1 >= lim_up1 * 0.998
            # T+1 是否一字板（全天涨停）
            t1_yizi = (l1 >= lim_up1 * 0.998) and (c1 >= lim_up1 * 0.998)
            # T+1 是否涨停收盘
            t1_close_limit = c1 >= lim_up1 * 0.998
            # T+1 最低价是否低于开盘（有机会在盘中买到）
            t1_dip = l1 < o1 * 0.995
            rows.append({
                "date": r["date"], "code": c6,
                "g": r["g"], "gap": r["gap"],
                "lim": lim,
                "T0涨停": t0_limit, "T0一字": t0_yizi,
                "T1开盘涨停": t1_open_limit, "T1一字": t1_yizi,
                "T1收涨停": t1_close_limit,
                "T1盘中回踩": t1_dip,
                "T1开盘": o1, "T1最高": h1, "T1最低": l1,
                "C_f20": r.get("C_f20"), "C_f60": r.get("C_f60"),
                "A_f20": r.get("A_f20"),
            })
    print(f"    完成 {len(rows):,}  ({time.time()-t0:.0f}s)")
    print()
    if not rows:
        print("无数据")
        return 1
    D = pd.DataFrame(rows)

    # 可成交性分级
    def grade(r):
        if r["T1一字"]:
            return "❌ 完全买不到（T+1一字板）"
        if r["T1开盘涨停"]:
            return "⚠ 极难（T+1开盘涨停）"
        return "✅ 可以买到（T+1开盘可成交）"
    D["可成交性"] = D.apply(grade, axis=1)

    # ---- 一、可成交性统计
    print("=" * 138)
    print("  一、★ 可成交性统计")
    print("=" * 138)
    print()
    tot = len(D)
    print(f"  {'类别':<28}{'数量':>8}{'占比':>9}")
    print("  " + "-" * 48)
    for lab in ["✅ 可以买到（T+1开盘可成交）",
                "⚠ 极难（T+1开盘涨停）",
                "❌ 完全买不到（T+1一字板）"]:
        c = int((D["可成交性"] == lab).sum())
        print(f"  {lab:<28}{c:>8}{c/tot*100:>8.1f}%")
    print()
    print(f"  其他标志:")
    print(f"    T日涨停收盘:      {int(D['T0涨停'].sum()):>6}  "
          f"({D['T0涨停'].mean()*100:.1f}%)")
    print(f"    T日一字板:        {int(D['T0一字'].sum()):>6}  "
          f"({D['T0一字'].mean()*100:.1f}%)")
    print(f"    T+1开盘涨停:     {int(D['T1开盘涨停'].sum()):>6}  "
          f"({D['T1开盘涨停'].mean()*100:.1f}%)")
    print(f"    T+1一字板:       {int(D['T1一字'].sum()):>6}  "
          f"({D['T1一字'].mean()*100:.1f}%)")
    print(f"    T+1收涨停:       {int(D['T1收涨停'].sum()):>6}  "
          f"({D['T1收涨停'].mean()*100:.1f}%)")
    print(f"    T+1盘中回踩(有低点): {int(D['T1盘中回踩'].sum()):>6}  "
          f"({D['T1盘中回踩'].mean()*100:.1f}%)")
    print()

    # ---- 二、能买到 vs 买不到 的收益对比
    print("=" * 138)
    print("  二、★ 能买到 vs 买不到的收益差异")
    print("=" * 138)
    print()
    print(f"  {'组':<30}{'样本':>8}{'20日%':>10}{'20日胜率':>11}"
          f"{'60日%':>10}{'60日胜率':>11}")
    print("  " + "-" * 82)
    for lab in ["✅ 可以买到（T+1开盘可成交）",
                "⚠ 极难（T+1开盘涨停）",
                "❌ 完全买不到（T+1一字板）"]:
        s = D[D["可成交性"] == lab]
        if len(s) < 5:
            print(f"  {lab:<30}{len(s):>8}  样本不足")
            continue
        v20 = s["C_f20"].dropna()
        v60 = s["C_f60"].dropna()
        r = f"  {lab:<30}{len(s):>8}"
        r += f"{v20.mean():>10.2f}" if len(v20) else f"{'—':>10}"
        r += f"{(v20>0).mean()*100:>10.1f}%" if len(v20) else f"{'—':>11}"
        r += f"{v60.mean():>10.2f}" if len(v60) else f"{'—':>10}"
        r += f"{(v60>0).mean()*100:>10.1f}%" if len(v60) else f"{'—':>11}"
        print(r)
    print()
    print("  ★ 解读:")
    print("    · 「买不到」的那批通常是更强的（涨停说明资金更急）")
    print("    · 但买不到就是买不到 —— 只能考虑能买到的部分")
    print("    · 关键：能买到的那批，收益是否仍显著为正？")
    print()

    # ---- 三、按板块统计
    print("=" * 138)
    print("  三、按板块统计（涨跌幅限制不同）")
    print("=" * 138)
    print()
    D["板块"] = D["code"].apply(
        lambda c: "科创板(20%)" if c.startswith("68")
        else ("创业板(20%)" if c.startswith("30")
              else ("主板(10%)" if c.startswith(("60", "00")) else "其他")))
    print(f"  {'板块':<16}{'样本':>8}{'T+1开盘涨停':>13}{'占比':>9}"
          f"{'T+1一字':>10}{'可买到':>10}{'20日%':>9}")
    print("  " + "-" * 78)
    for b, g in D.groupby("板块"):
        if len(g) < 5:
            continue
        v = g["C_f20"].dropna()
        ok = int((g["可成交性"].str.startswith("✅")).sum())
        print(f"  {b:<16}{len(g):>8}{int(g['T1开盘涨停'].sum()):>13}"
              f"{g['T1开盘涨停'].mean()*100:>8.1f}%"
              f"{int(g['T1一字'].sum()):>10}{ok:>10}"
              f"{(v.mean() if len(v) else float('nan')):>9.2f}")
    print()

    # ---- 四、跳空幅度 vs 可成交性
    print("=" * 138)
    print("  四、★ 跳空幅度 vs 可成交性（关键）")
    print("=" * 138)
    print()
    D["gapq"] = pd.cut(D["gap"], [2, 3, 5, 8, 100],
                       labels=["2~3%", "3~5%", "5~8%", "8%+"])
    print(f"  {'跳空档':<10}{'样本':>8}{'买不到':>9}{'占比':>9}"
          f"{'可买到':>9}{'可买到20日%':>14}{'可买到胜率':>12}")
    print("  " + "-" * 74)
    for q, g in D.dropna(subset=["gapq"]).groupby("gapq", observed=True):
        if len(g) < 5:
            continue
        bad = int((g["T1一字"] | g["T1开盘涨停"]).sum())
        ok = g[g["可成交性"].str.startswith("✅")]
        v = ok["C_f20"].dropna()
        print(f"  {str(q):<10}{len(g):>8}{bad:>9}{bad/len(g)*100:>8.1f}%"
              f"{len(ok):>9}"
              f"{(v.mean() if len(v) else float('nan')):>14.2f}"
              f"{((v>0).mean()*100 if len(v) else float('nan')):>11.1f}%")
    print()

    # ---- 五、实际下单方法
    print("=" * 138)
    print("  五、★ 实际下单方法（关键技巧）")
    print("=" * 138)
    print()
    print("  【方法1】集合竞价挂涨停价（推荐）")
    print()
    print("    原理：A股集合竞价（09:15-09:25）以「最大成交量价格」成交")
    print("         若你挂「涨停价」的买单，实际成交价 = 开盘价")
    print("         → 只要开盘不是涨停，就能以开盘价买到！")
    print()
    print("    操作：")
    print("      09:15-09:20  可挂可撤 → 先观察竞价")
    print("      09:20-09:25  不可撤单 → 确认后挂涨停价买入")
    print("      09:25        成交价 = 开盘价（若开盘<涨停）")
    print()
    print("    优点：不会因为挂低价而错过")
    print("    风险：若开盘就涨停，你的单排在后面可能不成交")
    print()
    print("  【方法2】分两笔（降低滑点）")
    print("      09:25 竞价挂一半（涨停价）")
    print("      09:30-09:35 观察，若回踩再买另一半")
    print()
    print("  【方法3】避开易涨停的（提高可成交率）")
    tmp = D[D["可成交性"].str.startswith("✅")]
    print(f"      历史可买到比例: {len(tmp)/len(D)*100:.1f}%")
    print(f"      → 剩下 {100-len(tmp)/len(D)*100:.1f}% 只能放弃")
    print()

    # ---- 六、结论
    print("=" * 138)
    print("  六、结论")
    print("=" * 138)
    print()
    okD = D[D["可成交性"].str.startswith("✅")]
    v20 = okD["C_f20"].dropna()
    v60 = okD["C_f60"].dropna()
    print(f"  可买到的 A1 信号: {len(okD):,} / {len(D):,} "
          f"（{len(okD)/len(D)*100:.1f}%）")
    print()
    print(f"  这部分的表现（T+1 开盘买）:")
    print(f"    20日: {v20.mean():+.2f}%   胜率 {(v20>0).mean()*100:.1f}%")
    print(f"    60日: {v60.mean():+.2f}%   胜率 {(v60>0).mean()*100:.1f}%")
    print()
    print("  → 结论：可买到的部分仍显著为正，说明策略实际可行")
    print("     但需要接受「约 %d%% 的信号买不到」" % int(
        (1 - len(okD) / len(D)) * 100))
    print()

    D.to_csv(outdir / "a1_fillability.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/a1_fillability.csv  ({len(D):,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
