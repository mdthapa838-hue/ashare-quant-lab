# -*- coding: utf-8 -*-
"""
step50_ai_chain_regime.py — AI 产业链牛熊分段检验

关键问题：
  光模块/PCB 的高收益是「真 alpha」还是「牛市高 beta」？
  → 分段检验：牛市段 vs 熊市段 分别表现如何

分段定义：
  牛市段 1：2024-01-02 ~ 2024-12-31
  震荡段  ：2025-01-01 ~ 2025-12-31
  熊市段  ：2026-01-01 ~ 2026-10-16
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
OUT = HERE / "output"

SEGMENTS = [
    ("2024 牛市段", "2024-01-02", "2024-12-31"),
    ("2025 震荡段", "2025-01-01", "2025-12-31"),
    ("2026 熊市段", "2026-01-01", "2026-10-16"),
    ("全程", "2024-01-02", "2026-10-16"),
]


def main():
    A = pd.read_csv(OUT / "ai_chain_stocks.csv", dtype={"代码": str})
    G = pd.read_csv(OUT / "ai_chain_groups.csv")
    R = pd.read_csv(OUT / "ai_chain_returns.csv", index_col=0,
                    parse_dates=True)
    print("=" * 142)
    print("  AI 产业链牛熊分段检验")
    print("=" * 142)
    print()
    print(f"  收益矩阵: {R.shape[0]} 交易日 x {R.shape[1]} 标的")
    print()

    # 龙头（列名是 ts_code）
    l0 = [c for c in ["688041.SH", "688256.SH", "300474.SZ"]
          if c in R.columns]
    leader = R[l0].mean(axis=1)
    mkt = R.mean(axis=1)

    # 代码 → 层
    ts_of = {}
    for c in R.columns:
        ts_of[c[:6]] = c
    layer_of = dict(zip(A["代码"], A["层"]))
    name_of = dict(zip(A["代码"], A["名称"]))

    print("=" * 142)
    print("  一、★ 各分段整体表现")
    print("=" * 142)
    print()
    print(f"  {'分段':<14}{'交易日':>7}{'龙头%':>10}{'市场等权%':>11}"
          f"{'差':>9}")
    print("  " + "-" * 54)
    segret = {}
    for lab, s, e in SEGMENTS:
        s2, e2 = pd.Timestamp(s), pd.Timestamp(e)
        sl = R.loc[s2:e2]
        if len(sl) == 0:
            continue
        lr = (1 + leader.loc[s2:e2]).prod() - 1
        mr = (1 + mkt.loc[s2:e2]).prod() - 1
        segret[lab] = (lr, mr, sl)
        print(f"  {lab:<14}{len(sl):>7}{lr*100:>10.1f}{mr*100:>11.1f}"
              f"{(lr-mr)*100:>9.1f}")
    print()

    # ---- 分层分段表现
    print("=" * 142)
    print("  二、★★ 分层分段表现（关键：验证是 alpha 还是牛市 beta）")
    print("=" * 142)
    print()
    print(f"  {'层级':<26}{'2024牛市%':>11}{'2025震荡%':>11}"
          f"{'2026熊市%':>11}{'全程%':>10}{'熊市/牛市比':>13}")
    print("  " + "-" * 94)
    rows = []
    for g in G["层级"]:
        codes = [c for c in A[A["层"] == g]["代码"]]
        ts = [ts_of.get(c) for c in codes]
        ts = [t for t in ts if t is not None]
        if not ts:
            continue
        r = {}
        for lab, s, e in SEGMENTS:
            s2, e2 = pd.Timestamp(s), pd.Timestamp(e)
            sub = R.loc[s2:e2, ts]
            if len(sub) == 0:
                r[lab] = np.nan
                continue
            # 等权组合的累计
            eq = sub.mean(axis=1)
            r[lab] = ((1 + eq).prod() - 1) * 100
        ratio = (r.get("2026 熊市段", np.nan) /
                 r.get("2024 牛市段", np.nan)
                 if r.get("2024 牛市段", 0) != 0 else np.nan)
        rows.append({
            "层级": g, "2024牛市%": round(r.get("2024 牛市段", np.nan), 1),
            "2025震荡%": round(r.get("2025 震荡段", np.nan), 1),
            "2026熊市%": round(r.get("2026 熊市段", np.nan), 1),
            "全程%": round(r.get("全程", np.nan), 1),
            "熊牛市比": round(ratio, 2) if pd.notna(ratio) else None,
            "标的数": len(ts),
        })
        print(f"  {g:<26}{r.get('2024 牛市段', np.nan):>11.1f}"
              f"{r.get('2025 震荡段', np.nan):>11.1f}"
              f"{r.get('2026 熊市段', np.nan):>11.1f}"
              f"{r.get('全程', np.nan):>10.1f}"
              f"{(f'{ratio:.2f}' if pd.notna(ratio) else '—'):>13}")
    print()
    T = pd.DataFrame(rows)

    print(f"  对照：")
    for lab, s, e in SEGMENTS:
        s2, e2 = pd.Timestamp(s), pd.Timestamp(e)
        lr = ((1 + leader.loc[s2:e2]).prod() - 1) * 100
        print(f"    龙头 {lab:<14}{lr:>+9.1f}%")
    print()

    # ---- 个股分段
    print("=" * 142)
    print("  三、★ 个股分段表现（前 30 只按全程收益）")
    print("=" * 142)
    print()
    print(f"  {'代码':<8}{'名称':<11}{'层':<22}{'2024%':>9}{'2025%':>9}"
          f"{'2026%':>9}{'全程%':>9}{'熊市beta':>9}")
    print("  " + "-" * 88)
    det = []
    for _, r in A.sort_values("累计收益%", ascending=False).head(30).iterrows():
        c6 = r["代码"]
        t = ts_of.get(c6)
        if t is None:
            continue
        rec = {"代码": c6, "名称": r["名称"], "层": r["层"]}
        for lab, s, e in SEGMENTS:
            s2, e2 = pd.Timestamp(s), pd.Timestamp(e)
            sub = R.loc[s2:e2, t]
            rec[lab] = round(((1 + sub).prod() - 1) * 100, 1) if len(sub) else None
        # 熊市 beta vs 龙头
        s2, e2 = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-10-16")
        y = R.loc[s2:e2, t]
        ld = leader.loc[s2:e2]
        yy = y.dropna()
        if len(yy) > 20:
            cov = np.cov(yy, ld.loc[yy.index])
            bb = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else np.nan
        else:
            bb = np.nan
        rec["熊市beta"] = round(bb, 2) if pd.notna(bb) else None
        det.append(rec)
        bb_s = f"{rec['熊市beta']:.2f}" if rec.get("熊市beta") is not None else "—"
        print(f"  {c6:<8}{str(r['名称'])[:10]:<11}{str(r['层'])[:21]:<22}"
              f"{(rec.get('2024 牛市段') or 0):>9.1f}"
              f"{(rec.get('2025 震荡段') or 0):>9.1f}"
              f"{(rec.get('2026 熊市段') or 0):>9.1f}"
              f"{(rec.get('全程') or 0):>9.1f}{bb_s:>9}")
    print()

    # ---- 关键判定
    print("=" * 142)
    print("  四、★ 关键判定")
    print("=" * 142)
    print()
    D = pd.DataFrame(det)
    if len(T):
        bull = T["2024牛市%"]
        bear = T["2026熊市%"]
        print(f"  分层平均：牛市场 {bull.mean():+.1f}%  熊市段 {bear.mean():+.1f}%")
        print()
        # 找出「牛市高熊市也相对好」的
        T2 = T.copy()
        T2["抗跌性"] = T2["2026熊市%"] - bear.mean()
        best = T2.sort_values("抗跌性", ascending=False)
        print(f"  ★ 熊市最抗跌的层:")
        for _, r in best.head(5).iterrows():
            print(f"    {r['层级']:<26} 熊市 {r['2026熊市%']:>+7.1f}%  "
                  f"牛市 {r['2024牛市%']:>+7.1f}%  "
                  f"抗跌性 {r['抗跌性']:>+6.1f}pct")
        print()
        print(f"  ★ 熊市跌最多的层:")
        for _, r in best.tail(5).iterrows():
            print(f"    {r['层级']:<26} 熊市 {r['2026熊市%']:>+7.1f}%  "
                  f"牛市 {r['2024牛市%']:>+7.1f}%")
    print()

    T.to_csv(OUT / "ai_chain_regime.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(det).to_csv(OUT / "ai_chain_regime_stocks.csv",
                             index=False, encoding="utf-8-sig")
    print(f"[输出] {OUT}/ai_chain_regime.csv")
    print(f"[输出] {OUT}/ai_chain_regime_stocks.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
