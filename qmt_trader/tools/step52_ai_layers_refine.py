# -*- coding: utf-8 -*-
"""
step52_ai_layers_refine.py — 精细化 AI 产业链分层

问题：宽泛概念（"芯片概念"910家、"人工智能"1082家）导致层级污染
解决：用「精准概念」+「排除词」+「申万行业」三重约束重新分层

策略：
  ① 只用精准概念（如 CPO、PCB、存储芯片、液冷服务器、算力租赁）
  ② 加排除词过滤（如"芯片概念"里剔除光伏/家电等）
  ③ 用申万行业补强（如半导体行业）
  ④ 保留宽泛概念但标注为「泛AI」
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

# 精准概念 → 层级（只用细分概念）
PRECISE = {
    # === 上游：光芯片/光器件 ===
    "886033.TI": ("U4 上游·光模块/CPO", "共封装光学(CPO)"),
    # === 上游：存储 ===
    "886042.TI": ("U3 上游·存储", "存储芯片"),
    # === 中游：PCB ===
    "885959.TI": ("M2 中游·PCB/CCL", "PCB概念"),
    # === 中游：液冷 ===
    "886044.TI": ("M5 中游·液冷/散热", "液冷服务器"),
    # === 下游：算力 ===
    "885957.TI": ("D1 下游·算力/IDC", "东数西算(算力)"),
    "886050.TI": ("D1 下游·算力/IDC", "算力租赁"),
    "885887.TI": ("D1 下游·算力/IDC", "数据中心(AIDC)"),
    # === 下游：AI应用（细分） ===
    "886019.TI": ("D3 下游·AI应用", "AIGC概念"),
    "886062.TI": ("D3 下游·AI应用", "多模态AI"),
    "886099.TI": ("D3 下游·AI应用", "AI智能体"),
    "886108.TI": ("D3 下游·AI应用", "AI应用"),
    # === 派生：机器人 ===
    "886069.TI": ("P3 派生·机器人", "人形机器人"),
    # === 芯片设计（用 IS 行业补强，不放泛概念） ===
    # （下面用 SW_MAP 从申万行业补充）
    # === 半导体细分（设备/材料，不含泛芯片） ===
    "884229.TI": ("U2 上游·设备/材料", "半导体设备"),
    "884091.TI": ("U2 上游·设备/材料", "半导体材料"),
}

# 泛概念（单独标注，不混入细分层）
BROAD = {
    "885728.TI": "泛AI·人工智能",
    "885756.TI": "泛AI·芯片概念",
    "885517.TI": "泛AI·机器人概念",
}

# 排除词（用于清洗宽泛概念）
EXCLUDE_KW = ["光伏", "锂电", "锂", "电池", "储能", "新能源",
              "医", "药", "酒", "食品", "银行", "保险", "券商",
              "房地", "建筑", "钢铁", "煤", "石油", "化工",
              "养殖", "种业", "农业", "纺", "服", "家电", "汽车",
              "电力", "环保", "水务", "传媒", "教育", "旅游",
              "纺织", "有色", "非银", "公用"]

# 申万行业映射（用于补强）
SW_MAP = {
    "半导体": "U1 上游·芯片设计",
    "元器件": "U2 上游·设备/材料",
    "通信设备": "U4 上游·光模块/CPO",
    "IT设备": "D1 下游·算力/IDC",
    "软件服务": "D3 下游·AI应用",
    "互联网": "D3 下游·AI应用",
}


def main():
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 138)
    print("  精细化 AI 产业链分层")
    print("=" * 138)
    print()

    # ---- 基础信息
    print("[1] 拉取股票基本信息（含行业）...")
    basic = {}
    for st in ("L", "D", "P"):
        try:
            b = pro.stock_basic(exchange="", list_status=st,
                                fields="ts_code,name,industry,market")
            for _, r in b.iterrows():
                basic[str(r["ts_code"])[:6]] = {
                    "name": r["name"], "ind": r.get("industry", ""),
                    "mkt": r.get("market", ""), "status": st}
        except Exception:
            pass
    print(f"    {len(basic):,} 只")
    print()

    # ---- 拉概念成分
    print("[2] 拉取精准概念成分...")
    rows = []
    for tc, (layer, cname) in PRECISE.items():
        try:
            d = pro.ths_member(ts_code=tc)
            if d is None or len(d) == 0:
                continue
            n = 0
            for _, r in d.iterrows():
                code = str(r.get("con_code", ""))
                if not code:
                    continue
                c6 = code[:6]
                info = basic.get(c6, {})
                nm = info.get("name", str(r.get("con_name", "")))
                rows.append({
                    "层级": layer, "概念": cname, "代码": code,
                    "代码6": c6, "名称": nm,
                    "申万行业": info.get("ind", ""),
                })
                n += 1
            print(f"    {layer[:20]:<22}{cname:<20}{n:>5} 只")
        except Exception as e:
            print(f"    {cname}: 失败 {str(e)[:40]}")
        time.sleep(0.35)
    print(f"    合计 {len(rows):,} 条")
    print()

    M = pd.DataFrame(rows)

    # ---- 排除词清洗
    print("[3] 排除词清洗...")
    before = len(M)
    mask = ~M["名称"].astype(str).str.contains("|".join(EXCLUDE_KW), na=False)
    M = M[mask]
    print(f"    {before:,} → {len(M):,}  (剔除 {before-len(M):,})")
    print()

    # ---- 去重（★ 显式优先级：越"纯"的层越优先）
    # 优先级：光模块 > PCB > 存储 > 设备/材料 > 芯片 > 液冷 > 算力 > AI应用 > 机器人
    LAYER_PRIORITY = {
        "U4 上游·光模块/CPO": 0,
        "M2 中游·PCB/CCL": 1,
        "U3 上游·存储": 2,
        "U2 上游·设备/材料": 3,
        "U1 上游·芯片设计": 4,
        "M5 中游·液冷/散热": 5,
        "D1 下游·算力/IDC": 6,
        "D3 下游·AI应用": 7,
        "P3 派生·机器人": 8,
    }
    order = dict(LAYER_PRIORITY)
    M["序"] = M["层级"].map(lambda x: order.get(x, 99))
    M = M.sort_values(["序", "代码6"])
    M1 = M.drop_duplicates(subset=["代码6"], keep="first")
    # 找出被抢占的（一股多概念）
    multi = M.groupby("代码6").size()
    multi = multi[multi > 1]
    print(f"[4a] 多概念标的: {len(multi):,} 只（按优先级归属）")
    print(f"[4] 去重后: {len(M1):,} 只")
    print()

    print("=" * 138)
    print("  一、分层结构")
    print("=" * 138)
    print()
    print(f"  {'层级':<26}{'标的数':>8}{'占比':>9}")
    print("  " + "-" * 46)
    for g in sorted(M1["层级"].unique(), key=lambda x: order.get(x, 99)):
        s = M1[M1["层级"] == g]
        print(f"  {g:<26}{len(s):>8}{len(s)/len(M1)*100:>8.1f}%")
    print(f"  {'合计':<26}{len(M1):>8}")
    print()

    # ---- 拉行情
    print("[5] 拉取行情（2024-01-01 ~ 2026-10-16）...")
    codes = M1["代码"].tolist()
    layer_of = dict(zip(M1["代码6"], M1["层级"]))
    name_of = dict(zip(M1["代码6"], M1["名称"]))
    ind_of = dict(zip(M1["代码6"], M1["申万行业"]))

    px = {}
    t0 = time.time()
    for i, tc in enumerate(codes, 1):
        try:
            d = pro.daily(ts_code=tc, start_date="20240101",
                          end_date="20261016")
            if d is not None and len(d) >= 100:
                d = d.copy()
                d["date"] = pd.to_datetime(d["trade_date"], format="%Y%m%d")
                d["pct"] = pd.to_numeric(d["pct_chg"], errors="coerce") / 100
                px[tc] = d.set_index("date")["pct"]
        except Exception:
            pass
        if i % 200 == 0:
            print(f"    {i}/{len(codes)}  成功 {len(px)}  "
                  f"({time.time()-t0:.0f}s)", flush=True)
        time.sleep(0.13)
    print(f"    完成 {len(px):,}/{len(codes):,}  ({time.time()-t0:.0f}s)")
    print()

    R = pd.DataFrame({c.split(".")[0]: v for c, v in px.items()})
    R = R.sort_index()
    keep = R.notna().mean() >= 0.6
    R = R.loc[:, keep].fillna(0)
    print(f"  收益矩阵: {R.shape[0]} 日 x {R.shape[1]} 只")
    print()

    l0 = [c for c in ["688041", "688256", "300474"] if c in R.columns]
    leader = R[l0].mean(axis=1)
    mkt = R.mean(axis=1)

    # ---- 指标
    print("[6] 计算指标...")
    SEG = [("2024牛市", "2024-01-02", "2024-12-31"),
           ("2025震荡", "2025-01-01", "2025-12-31"),
           ("2026熊市", "2026-01-01", "2026-10-16"),
           ("全程", "2024-01-02", "2026-10-16")]
    rows2 = []
    for c in R.columns:
        y = R[c]
        cov = np.cov(y, leader)
        b = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else np.nan
        a = y.mean() - b * leader.mean()
        yhat = a + b * leader
        ss_res = ((y - yhat) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
        up, dn = leader > 0, leader <= 0
        rec = {
            "代码": c, "名称": name_of.get(c, "?"),
            "层级": layer_of.get(c, "?"),
            "申万行业": ind_of.get(c, ""),
            "beta": round(b, 3) if pd.notna(b) else None,
            "年化alpha%": round(a * 250 * 100, 2),
            "R2": round(r2, 3) if pd.notna(r2) else None,
            "累计%": round(((1 + y).prod() - 1) * 100, 1),
            "波动%": round(y.std() * np.sqrt(250) * 100, 1),
            "条件差": round((y[up].mean() - y[dn].mean()) * 100, 3),
        }
        for lab, s, e in SEG:
            s2, e2 = pd.Timestamp(s), pd.Timestamp(e)
            sub = y.loc[s2:e2]
            rec[lab] = round(((1 + sub).prod() - 1) * 100, 1) if len(sub) else None
        rows2.append(rec)
    A = pd.DataFrame(rows2)
    print(f"    完成")
    print()

    # ---- 分层汇总
    print("=" * 138)
    print("  二、★ 分层汇总（精细化）")
    print("=" * 138)
    print()
    GS = []
    for g in sorted(A["层级"].unique(), key=lambda x: order.get(x, 99)):
        s = A[A["层级"] == g]
        if len(s) == 0:
            continue
        GS.append({
            "层级": g, "数": len(s),
            "beta": round(s["beta"].mean(), 3),
            "R2": round(s["R2"].mean(), 3),
            "累计%": round(s["累计%"].mean(), 1),
            "中位%": round(s["累计%"].median(), 1),
            "牛市%": round(s["2024牛市"].mean(), 1),
            "震荡%": round(s["2025震荡"].mean(), 1),
            "熊市%": round(s["2026熊市"].mean(), 1),
            "波动%": round(s["波动%"].mean(), 1),
            "条件差": round(s["条件差"].mean(), 3),
        })
    G = pd.DataFrame(GS)
    print(f"  {'层级':<26}{'数':>5}{'beta':>7}{'R2':>7}{'全程%':>9}"
          f"{'牛市%':>9}{'震荡%':>9}{'熊市%':>9}{'波动%':>8}{'条件差':>8}")
    print("  " + "-" * 100)
    for _, r in G.iterrows():
        print(f"  {r['层级']:<26}{r['数']:>5}{r['beta']:>7.3f}"
              f"{r['R2']:>7.3f}{r['累计%']:>9.1f}{r['牛市%']:>9.1f}"
              f"{r['震荡%']:>9.1f}{r['熊市%']:>9.1f}"
              f"{r['波动%']:>8.1f}{r['条件差']:>8.3f}")
    print()
    print(f"  龙头: 牛市 {((1+leader.loc['2024-01-02':'2024-12-31']).prod()-1)*100:+.1f}%  "
          f"震荡 {((1+leader.loc['2025-01-01':'2025-12-31']).prod()-1)*100:+.1f}%  "
          f"熊市 {((1+leader.loc['2026-01-01':'2026-10-16']).prod()-1)*100:+.1f}%  "
          f"全程 {((1+leader).prod()-1)*100:+.1f}%")
    print()

    # ---- 各层代表股
    print("=" * 138)
    print("  三、★ 各层代表股（前8）")
    print("=" * 138)
    print()
    for g in G["层级"]:
        s = A[A["层级"] == g].sort_values("累计%", ascending=False).head(8)
        print(f"  【{g}】{len(A[A['层级']==g])} 只")
        for _, r in s.iterrows():
            print(f"    {r['代码']} {str(r['名称'])[:9]:<10} "
                  f"累计 {r['累计%']:>+8.1f}%  beta {r['beta']:>6.3f}  "
                  f"熊市 {(r['2026熊市'] or 0):>+7.1f}%")
        print()

    A.to_csv(OUT / "ai_refined_stocks.csv", index=False,
             encoding="utf-8-sig")
    G.to_csv(OUT / "ai_refined_groups.csv", index=False,
             encoding="utf-8-sig")
    M1.to_csv(OUT / "ai_refined_universe.csv", index=False,
              encoding="utf-8-sig")
    print(f"[输出] {OUT}/ai_refined_stocks.csv  ({len(A)})")
    print(f"[输出] {OUT}/ai_refined_groups.csv  ({len(G)})")
    print(f"[输出] {OUT}/ai_refined_universe.csv  ({len(M1)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
