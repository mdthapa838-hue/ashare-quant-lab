# -*- coding: utf-8 -*-
"""
step47_leader_beta.py — 「龙头是alpha，产业链是beta」假设检验

用户假设：
  ① 炒AI叙事时，龙头（英伟达）的业绩预期差先被炒
  ② 龙头确定性增长 → 炒供应商/周边
  ③ 还会炒下游（算力租赁）和派生（电力）
  ④ ★ 但只有龙头涨时产业链才涨；龙头不涨则产业链熄火
  ⑤ → 英伟达是产业链的 alpha，配套公司是 beta

要检验的：
  A. 相关性：产业链 vs 龙头的 beta 系数（真实回归 beta）
  B. 领先滞后：龙头是否领先产业链（格兰杰式检验）
  C. 条件收益：龙头上涨/下跌时，产业链的表现
  D. 超额收益：产业链是否有独立于龙头的 alpha
  E. A股映射：英伟达产业链在A股的对应标的
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

OUT = HERE / "output"


def main():
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 140)
    print("  「龙头是 alpha，产业链是 beta」假设检验")
    print("=" * 140)
    print()
    print("  用户假设：只有龙头涨，产业链才涨；龙头不涨则产业链熄火")
    print("  → 龙头 = alpha，配套 = beta")
    print()
    print("  说明：Tushare 不提供美股日线（英伟达 NVDA）")
    print("        → 用「A股AI产业链龙头」+「A股配套」做同构检验")
    print("        → 另用费城半导体指数（SOX）代理，但 Tushare 也无")
    print()

    # ---- A股 AI 产业链分组
    print("=" * 140)
    print("  一、A股 AI 算力产业链分层")
    print("=" * 140)
    print()
    groups = {
        "L0 龙头（对标英伟达）": [
            ("688041.SH", "海光信息"), ("688256.SH", "寒武纪"),
            ("300474.SZ", "景嘉微"),
        ],
        "L1 芯片制造/封测": [
            ("688981.SH", "中芯国际"), ("600584.SH", "长电科技"),
            ("002185.SZ", "华天科技"), ("688362.SH", "甬矽电子"),
        ],
        "L2 服务器/整机": [
            ("000977.SZ", "浪潮信息"), ("603019.SH", "中科曙光"),
            ("000938.SZ", "紫光股份"), ("002413.SZ", "雷科防务"),
        ],
        "L3 光模块/光通信": [
            ("300308.SZ", "中际旭创"), ("300502.SZ", "新易盛"),
            ("002281.SZ", "光迅科技"), ("300394.SZ", "天孚通信"),
        ],
        "L4 PCB/铜连接": [
            ("002463.SZ", "沪电股份"), ("002916.SZ", "深南电路"),
            ("300476.SZ", "胜宏科技"), ("002938.SZ", "鹏鼎控股"),
        ],
        "L5 算力租赁/IDC": [
            ("603019.SH", "中科曙光"), ("000063.SZ", "中兴通讯"),
            ("300383.SZ", "光环新网"), ("603881.SH", "数据港"),
            ("300738.SZ", "奥飞数据"),
        ],
        "L6 电力/散热/配套": [
            ("600886.SH", "国投电力"), ("000883.SZ", "湖北能源"),
            ("002837.SZ", "英维克"), ("300593.SZ", "新雷能"),
            ("300272.SZ", "开能健康"),
        ],
    }
    for g, items in groups.items():
        print(f"  【{g}】{len(items)} 只")
        print(f"    " + "、".join([f"{n}({c[:6]})" for c, n in items]))
    print()

    # ---- 拉数据
    print("[1] 拉取日线（2024-01 ~ 2026-10）...")
    t0 = time.time()
    allc = []
    for g, items in groups.items():
        for c, n in items:
            allc.append((c, n, g))
    # 去重（中科曙光出现在两组）
    seen = set()
    uniq = []
    for c, n, g in allc:
        if c in seen:
            continue
        seen.add(c)
        uniq.append((c, n, g))
    print(f"    唯一标的: {len(uniq)} 只")

    px = {}
    for c, n, g in uniq:
        try:
            d = pro.daily(ts_code=c, start_date="20240101",
                          end_date="20261016")
            if d is None or len(d) < 100:
                print(f"    {c} {n}: 数据不足")
                continue
            d = d.copy()
            d["date"] = pd.to_datetime(d["trade_date"], format="%Y%m%d")
            d = d.sort_values("date").reset_index(drop=True)
            d["pct"] = pd.to_numeric(d["pct_chg"], errors="coerce") / 100
            px[c] = d.set_index("date")["pct"]
        except Exception as e:
            print(f"    {c} {n}: 失败 {str(e)[:30]}")
        time.sleep(0.15)
    print(f"    成功 {len(px)}/{len(uniq)}  ({time.time()-t0:.0f}s)")
    print()
    if len(px) < 5:
        print("数据不足")
        return 1

    X = pd.DataFrame(px).dropna(how="all")
    X = X.dropna()
    print(f"    共同交易日: {len(X)}")
    print(f"    范围: {X.index.min().date()} ~ {X.index.max().date()}")
    print()

    # 名称映射
    name_of = {c: n for c, n, g in uniq}
    grp_of = {c: g for c, n, g in uniq}

    # ---- 二、龙头定义与 beta 回归
    print("=" * 140)
    print("  二、★ Beta 回归：产业链 = β × 龙头 + α？")
    print("=" * 140)
    print()
    # 龙头 = 海光+寒武纪+景嘉微 等权
    l0 = [c for c, n, g in uniq if g.startswith("L0")]
    l0 = [c for c in l0 if c in X.columns]
    if not l0:
        print("无 L0 标的")
        return 1
    leader = X[l0].mean(axis=1)
    print(f"  龙头组合（等权）: {len(l0)} 只 → "
          f"{'、'.join([name_of.get(c,c) for c in l0])}")
    print(f"    日均收益 {leader.mean()*100:+.3f}%   "
          f"日波动 {leader.std()*100:.3f}%")
    print()

    rows = []
    for c in X.columns:
        if c in l0:
            continue
        y = X[c]
        # 单变量回归 y = a + b*leader
        cov = np.cov(y, leader)
        b = cov[0, 1] / cov[1, 1]
        a = y.mean() - b * leader.mean()
        # R²
        yhat = a + b * leader
        ss_res = ((y - yhat) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
        corr = y.corr(leader)
        rows.append({
            "代码": c[:6], "名称": name_of.get(c, c),
            "层级": grp_of.get(c, "")[:2],
            "beta": round(b, 3),
            "alpha(日%)": round(a * 100, 4),
            "R²": round(r2, 3),
            "相关系数": round(corr, 3),
            "年化alpha%": round(a * 250 * 100, 2),
        })
    B = pd.DataFrame(rows).sort_values("beta", ascending=False)
    print(f"  {'代码':<8}{'名称':<11}{'层':<5}{'beta':>8}{'alpha(日%)':>12}"
          f"{'R²':>8}{'相关':>8}{'年化alpha%':>12}")
    print("  " + "-" * 78)
    for _, r in B.iterrows():
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}{r['层级']:<5}"
              f"{r['beta']:>8.3f}{r['alpha(日%)']:>12.4f}"
              f"{r['R²']:>8.3f}{r['相关系数']:>8.3f}"
              f"{r['年化alpha%']:>12.2f}")
    print()

    # ---- 三、条件收益：龙头上涨/下跌时
    print("=" * 140)
    print("  三、★ 条件收益：龙头涨时 vs 龙头跌时")
    print("=" * 140)
    print()
    up = leader > 0
    dn = leader <= 0
    print(f"  龙头上涨天数: {int(up.sum())}  下跌天数: {int(dn.sum())}")
    print()
    print(f"  {'名称':<11}{'层':<5}{'龙头涨时%':>12}{'龙头跌时%':>12}"
          f"{'差':>9}{'跟涨概率':>10}{'跟跌概率':>10}")
    print("  " + "-" * 72)
    cond = []
    for c in X.columns:
        if c in l0:
            continue
        y = X[c]
        mu = y[up].mean() * 100
        md = y[dn].mean() * 100
        pg = (y[up] > 0).mean() * 100
        pl = (y[dn] > 0).mean() * 100
        # 龙头涨且该股也涨的比例
        both = ((leader > 0) & (y > 0)).sum() / max((leader > 0).sum(), 1) * 100
        cond.append({"名称": name_of.get(c, c), "层": grp_of.get(c, "")[:2],
                     "涨时": mu, "跌时": md, "差": mu - md,
                     "跟涨": both})
        print(f"  {str(name_of.get(c,c))[:10]:<11}{grp_of.get(c,'')[:2]:<5}"
              f"{mu:>12.3f}{md:>12.3f}{mu-md:>9.3f}"
              f"{both:>9.1f}%{pg:>9.1f}%")
    print()

    # ---- 四、领先滞后
    print("=" * 140)
    print("  四、★ 领先滞后：龙头是否领先产业链？")
    print("=" * 140)
    print()
    print("  方法：计算 corr(产业链_t, 龙头_{t-k})，k=0..3")
    print("       若 k>0 的相关性最高 → 龙头领先（用户假设成立）")
    print()
    lag_rows = []
    for c in X.columns:
        if c in l0:
            continue
        y = X[c]
        best_k, best_r = 0, -9
        rs = {}
        for k in range(0, 4):
            r = y.corr(leader.shift(k))
            rs[k] = r
            if pd.notna(r) and r > best_r:
                best_r, best_k = r, k
        # 龙头领先 vs 同步
        r0 = rs.get(0, np.nan)
        r1 = rs.get(1, np.nan)
        lag_rows.append({
            "名称": name_of.get(c, c), "层": grp_of.get(c, "")[:2],
            "r(k=0同步)": round(r0, 3) if pd.notna(r0) else None,
            "r(k=1)": round(r1, 3) if pd.notna(r1) else None,
            "r(k=2)": round(rs.get(2, np.nan), 3) if pd.notna(rs.get(2)) else None,
            "r(k=3)": round(rs.get(3, np.nan), 3) if pd.notna(rs.get(3)) else None,
            "最佳k": best_k, "最佳r": round(best_r, 3),
            "判定": ("同步" if best_k == 0 else f"龙头领先{best_k}天"),
        })
    L = pd.DataFrame(lag_rows)
    C_K0, C_K1, C_K2, C_K3 = "r(k=0同步)", "r(k=1)", "r(k=2)", "r(k=3)"
    print(f"  {'名称':<11}{'层':<5}{'k=0':>8}{'k=1':>8}{'k=2':>8}{'k=3':>8}"
          f"{'最佳k':>7}{'  判定':<14}")
    print("  " + "-" * 72)
    for _, r in L.iterrows():
        def ff(v):
            return f"{v:.3f}" if v is not None and pd.notna(v) else "—"
        print(f"  {str(r['名称'])[:10]:<11}{r['层']:<5}"
              f"{ff(r[C_K0]):>8}{ff(r[C_K1]):>8}{ff(r[C_K2]):>8}"
              f"{ff(r[C_K3]):>8}{r['最佳k']:>7}  {r['判定']:<14}")
    print()
    nsync = int((L["最佳k"] == 0).sum())
    nlead = int((L["最佳k"] > 0).sum())
    print(f"  统计: 同步 {nsync} 只   龙头领先 {nlead} 只")
    print()

    # ---- 五、Alpha 检验
    print("=" * 140)
    print("  五、★ Alpha 检验：产业链有独立超额收益吗？")
    print("=" * 140)
    print()
    B["abs_alpha"] = B["年化alpha%"].abs()
    sig = B[B["年化alpha%"].abs() > 10]
    print(f"  年化 alpha 绝对值 > 10% 的: {len(sig)} / {len(B)}")
    print()
    if len(sig):
        print(f"  {'名称':<11}{'层':<5}{'beta':>8}{'年化alpha%':>12}{'R²':>8}")
        print("  " + "-" * 46)
        for _, r in sig.sort_values("年化alpha%", ascending=False).iterrows():
            print(f"  {str(r['名称'])[:10]:<11}{r['层级']:<5}"
                  f"{r['beta']:>8.3f}{r['年化alpha%']:>12.2f}{r['R²']:>8.3f}")
    print()
    print(f"  平均 R²: {B['R²'].mean():.3f}")
    print(f"  → R² 越高说明「被龙头解释得越多」（越像 beta）")
    print(f"  → R² 越低说明有独立行情（可能有 alpha）")
    print()
    hi_r2 = B[B["R²"] >= 0.3]
    lo_r2 = B[B["R²"] < 0.15]
    print(f"  高 R²(≥0.3): {len(hi_r2)} 只 → 强 beta")
    if len(hi_r2):
        print(f"    {'、'.join(hi_r2['名称'].head(10).tolist())}")
    print(f"  低 R²(<0.15): {len(lo_r2)} 只 → 弱相关")
    if len(lo_r2):
        print(f"    {'、'.join(lo_r2['名称'].head(10).tolist())}")
    print()

    B.to_csv(OUT / "leader_beta.csv", index=False, encoding="utf-8-sig")
    L.to_csv(OUT / "leader_lag.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(cond).to_csv(OUT / "leader_cond.csv", index=False,
                              encoding="utf-8-sig")
    print(f"[输出] {OUT}/leader_beta.csv")
    print(f"[输出] {OUT}/leader_lag.csv")
    print(f"[输出] {OUT}/leader_cond.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
