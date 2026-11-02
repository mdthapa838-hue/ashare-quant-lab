# -*- coding: utf-8 -*-
"""
step51_ai_universe_full.py — 用概念板块构建完整 AI 产业链股票池

方法：
  ① 从 ths_index 找出全部 AI 相关概念（59+ 个）
  ② 逐个拉 ths_member 得到成分股
  ③ 按概念归类到「上/中/下游」
  ④ 合并去重，得到完整股票池（预计 1500+ 只）
  ⑤ 拉行情，计算 beta/alpha/分段表现
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

# ---- 概念 → 产业链位置 映射
CONCEPT_MAP = {
    # 上游·算力芯片
    "885756.TI": ("U1 上游·芯片设计", "芯片概念"),
    "886042.TI": ("U3 上游·存储", "存储芯片"),
    "885908.TI": ("U2 上游·制造封测", "第三代半导体"),
    "885925.TI": ("U1 上游·芯片设计", "MCU芯片"),
    "885945.TI": ("U1 上游·芯片设计", "汽车芯片"),
    "881121.TI": ("U2 上游·制造封测", "半导体"),
    "884229.TI": ("U2 上游·制造封测", "半导体设备"),
    "884091.TI": ("U2 上游·制造封测", "半导体材料"),
    "700339.TI": ("U2 上游·制造封测", "半导体产品与设备"),
    "700412.TI": ("U2 上游·制造封测", "半导体产品与设备Ⅲ"),
    "700569.TI": ("U2 上游·制造封测", "半导体材料与设备"),
    "700570.TI": ("U2 上游·制造封测", "半导体产品"),
    "861057.TI": ("U2 上游·制造封测", "半导体产品与设备Ⅲ"),
    "861100.TI": ("U2 上游·制造封测", "半导体产品与设备Ⅱ"),
    "861233.TI": ("U2 上游·制造封测", "半导体材料与设备"),
    "861234.TI": ("U2 上游·制造封测", "半导体产品"),
    "871057.TI": ("U2 上游·制造封测", "半导体产品与设备"),
    # 光/CPO
    "886033.TI": ("U4 上游·光芯片器件", "共封装光学(CPO)"),
    # 中游
    "885959.TI": ("M2 中游·PCB/CCL", "PCB概念"),
    "886044.TI": ("M5 中游·液冷/散热", "液冷服务器"),
    # 下游/算力
    "885957.TI": ("D1 下游·算力/IDC", "东数西算(算力)"),
    "886050.TI": ("D1 下游·算力/IDC", "算力租赁"),
    "885887.TI": ("D1 下游·算力/IDC", "数据中心(AIDC)"),
    "883443.TI": ("D1 下游·算力/IDC", "同花顺算力主题精选"),
    "861289.TI": ("D1 下游·算力/IDC", "数据中心REIT"),
    # AI 应用
    "885728.TI": ("D3 下游·AI应用", "人工智能"),
    "886019.TI": ("D3 下游·AI应用", "AIGC概念"),
    "886062.TI": ("D3 下游·AI应用", "多模态AI"),
    "886068.TI": ("D3 下游·AI应用", "AI视频"),
    "886070.TI": ("D3 下游·AI应用", "AI手机"),
    "886071.TI": ("D3 下游·AI应用", "AI PC"),
    "886074.TI": ("D3 下游·AI应用", "AI语料"),
    "886085.TI": ("D3 下游·AI应用", "AI眼镜"),
    "886090.TI": ("D3 下游·AI应用", "智谱AI"),
    "886099.TI": ("D3 下游·AI应用", "AI智能体"),
    "886102.TI": ("D3 下游·AI应用", "中国AI 50"),
    "886108.TI": ("D3 下游·AI应用", "AI应用"),
    # 机器人（派生）
    "885517.TI": ("P3 派生·机器人", "机器人概念"),
    "886069.TI": ("P3 派生·机器人", "人形机器人"),
    "884218.TI": ("P3 派生·机器人", "机器人"),
}


def main():
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 138)
    print("  用概念板块构建完整 AI 产业链股票池")
    print("=" * 138)
    print()

    # ---- 拉概念成分
    print("[1] 拉取概念成分股...")
    t0 = time.time()
    rows = []
    for tc, (layer, cname) in CONCEPT_MAP.items():
        try:
            d = pro.ths_member(ts_code=tc)
            if d is None or len(d) == 0:
                print(f"    {cname}({tc}): 空")
                continue
            n = 0
            for _, r in d.iterrows():
                code = str(r.get("con_code", ""))
                name = str(r.get("con_name", ""))
                if not code or not name:
                    continue
                rows.append({"层级": layer, "概念": cname,
                             "代码": code, "名称": name})
                n += 1
            print(f"    {cname:<22}{n:>5} 只")
        except Exception as e:
            print(f"    {cname}({tc}): 失败 {str(e)[:40]}")
        time.sleep(0.35)
    print(f"    合计 {len(rows):,} 条  ({time.time()-t0:.0f}s)")
    print()

    if not rows:
        print("无数据")
        return 1
    M = pd.DataFrame(rows)
    M["代码6"] = M["代码"].astype(str).str[:6]

    # ---- 去重（一股多层 → 取第一个出现的层，按 U→M→D→P 顺序）
    order = {"U1": 0, "U2": 1, "U3": 2, "U4": 3,
             "M1": 4, "M2": 5, "M3": 6, "M4": 7, "M5": 8,
             "D1": 9, "D2": 10, "D3": 11, "P1": 12, "P2": 13, "P3": 14}
    M["序"] = M["层级"].str[:2].map(lambda x: order.get(x, 99))
    M = M.sort_values(["序", "代码6"])
    M1 = M.drop_duplicates(subset=["代码6"], keep="first")

    print("=" * 138)
    print("  一、股票池规模")
    print("=" * 138)
    print()
    print(f"  原始记录: {len(M):,}")
    print(f"  去重后:   {len(M1):,} 只")
    print()
    print(f"  {'层级':<24}{'标的数':>8}")
    print("  " + "-" * 34)
    for g, s in M1.groupby("层级"):
        print(f"  {g:<24}{len(s):>8}")
    print(f"  {'合计':<24}{len(M1):>8}")
    print()

    # ---- 拉行情
    print("[2] 拉取日线（2024-01-01 ~ 2026-10-16）...")
    print(f"    需拉取 {len(M1):,} 只 —— 预计 {len(M1)*0.3/60:.0f} 分钟")
    print()
    codes = M1["代码"].tolist()
    layer_of = dict(zip(M1["代码6"], M1["层级"]))
    name_of = dict(zip(M1["代码6"], M1["名称"]))

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
                d["close"] = pd.to_numeric(d["close"], errors="coerce")
                px[tc] = d.set_index("date")[["pct", "close"]]
        except Exception:
            pass
        if i % 200 == 0:
            print(f"    {i}/{len(codes)}  成功 {len(px)}  "
                  f"({time.time()-t0:.0f}s)", flush=True)
        time.sleep(0.13)
    print(f"    完成 {len(px):,}/{len(codes):,}  ({time.time()-t0:.0f}s)")
    print()
    if len(px) < 100:
        print("数据太少")
        return 1

    # ---- 收益矩阵
    R = pd.DataFrame({c.split(".")[0]: v["pct"] for c, v in px.items()})
    R = R.sort_index().dropna(how="all")
    # 保留至少 60% 交易日非空的列
    keep = R.notna().mean() >= 0.6
    R = R.loc[:, keep]
    R = R.fillna(0)
    print(f"  收益矩阵: {R.shape[0]} 日 x {R.shape[1]} 只")
    print()

    # 龙头
    l0 = [c for c in ["688041", "688256", "300474"] if c in R.columns]
    leader = R[l0].mean(axis=1)
    mkt = R.mean(axis=1)
    print(f"  龙头: {l0}  累计 {((1+leader).prod()-1)*100:+.1f}%")
    print(f"  市场等权累计 {((1+mkt).prod()-1)*100:+.1f}%")
    print()

    # ---- 分段
    SEG = [("2024牛市", "2024-01-02", "2024-12-31"),
           ("2025震荡", "2025-01-01", "2025-12-31"),
           ("2026熊市", "2026-01-01", "2026-10-16"),
           ("全程", "2024-01-02", "2026-10-16")]

    print("[3] 计算指标...")
    t0 = time.time()
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
        cum = (1 + y).prod() - 1
        vol = y.std() * np.sqrt(250)
        up = leader > 0
        dn = leader <= 0
        rec = {
            "代码": c, "名称": name_of.get(c, "?"),
            "层级": layer_of.get(c, "?"),
            "beta": round(b, 3) if pd.notna(b) else None,
            "年化alpha%": round(a * 250 * 100, 2),
            "R2": round(r2, 3) if pd.notna(r2) else None,
            "累计%": round(cum * 100, 1),
            "波动%": round(vol * 100, 1),
            "条件差": round((y[up].mean() - y[dn].mean()) * 100, 3),
        }
        for lab, s, e in SEG:
            s2, e2 = pd.Timestamp(s), pd.Timestamp(e)
            sub = y.loc[s2:e2]
            rec[lab] = round(((1 + sub).prod() - 1) * 100, 1) if len(sub) else None
        rows2.append(rec)
    A = pd.DataFrame(rows2)
    print(f"    完成  ({time.time()-t0:.0f}s)")
    print()

    # ---- 分层汇总
    print("=" * 138)
    print("  二、★ 分层汇总（完整股票池）")
    print("=" * 138)
    print()
    GS = []
    for g in sorted(A["层级"].unique(),
                    key=lambda x: order.get(x[:2], 99)):
        s = A[A["层级"] == g]
        if len(s) == 0:
            continue
        GS.append({
            "层级": g, "数": len(s),
            "beta": round(s["beta"].mean(), 3),
            "R2": round(s["R2"].mean(), 3),
            "累计%": round(s["累计%"].mean(), 1),
            "中位%": round(s["累计%"].median(), 1),
            "波动%": round(s["波动%"].mean(), 1),
            "条件差": round(s["条件差"].mean(), 3),
            "牛市%": round(s["2024牛市"].mean(), 1),
            "震荡%": round(s["2025震荡"].mean(), 1),
            "熊市%": round(s["2026熊市"].mean(), 1),
            "年化α%": round(s["年化alpha%"].mean(), 1),
        })
    G = pd.DataFrame(GS)
    print(f"  {'层级':<24}{'数':>5}{'beta':>7}{'R2':>7}{'全程%':>9}"
          f"{'牛市%':>9}{'震荡%':>9}{'熊市%':>9}{'波动%':>8}{'条件差':>8}")
    print("  " + "-" * 96)
    for _, r in G.iterrows():
        print(f"  {r['层级']:<24}{r['数']:>5}{r['beta']:>7.3f}"
              f"{r['R2']:>7.3f}{r['累计%']:>9.1f}{r['牛市%']:>9.1f}"
              f"{r['震荡%']:>9.1f}{r['熊市%']:>9.1f}"
              f"{r['波动%']:>8.1f}{r['条件差']:>8.3f}")
    print()
    print(f"  龙头: 牛市 {((1+leader.loc['2024-01-02':'2024-12-31']).prod()-1)*100:+.1f}%  "
          f"震荡 {((1+leader.loc['2025-01-01':'2025-12-31']).prod()-1)*100:+.1f}%  "
          f"熊市 {((1+leader.loc['2026-01-01':'2026-10-16']).prod()-1)*100:+.1f}%")
    print()

    # ---- TOP/BOTTOM
    print("=" * 138)
    print("  三、★ 涨幅 TOP 30（完整池）")
    print("=" * 138)
    print()
    print(f"  {'代码':<8}{'名称':<11}{'层级':<22}{'累计%':>9}{'beta':>7}"
          f"{'R2':>7}{'熊市%':>9}")
    print("  " + "-" * 76)
    for _, r in A.sort_values("累计%", ascending=False).head(30).iterrows():
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['层级'])[:21]:<22}{r['累计%']:>9.1f}"
              f"{r['beta']:>7.3f}{r['R2']:>7.3f}"
              f"{(r['2026熊市'] if r['2026熊市'] is not None else 0):>9.1f}")
    print()

    print("=" * 138)
    print("  四、★ 跌幅 TOP 25（完整池）")
    print("=" * 138)
    print()
    print(f"  {'代码':<8}{'名称':<11}{'层级':<22}{'累计%':>9}{'beta':>7}"
          f"{'熊市%':>9}")
    print("  " + "-" * 68)
    for _, r in A.sort_values("累计%").head(25).iterrows():
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['层级'])[:21]:<22}{r['累计%']:>9.1f}"
              f"{r['beta']:>7.3f}"
              f"{(r['2026熊市'] if r['2026熊市'] is not None else 0):>9.1f}")
    print()

    # ---- 保存
    A.to_csv(OUT / "ai_chain_full_stocks.csv", index=False,
             encoding="utf-8-sig")
    G.to_csv(OUT / "ai_chain_full_groups.csv", index=False,
             encoding="utf-8-sig")
    M1.to_csv(OUT / "ai_chain_full_universe.csv", index=False,
              encoding="utf-8-sig")
    print(f"[输出] {OUT}/ai_chain_full_stocks.csv  ({len(A)})")
    print(f"[输出] {OUT}/ai_chain_full_groups.csv  ({len(G)})")
    print(f"[输出] {OUT}/ai_chain_full_universe.csv  ({len(M1)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
