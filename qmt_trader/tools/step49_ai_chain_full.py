# -*- coding: utf-8 -*-
"""
step49_ai_chain_full.py — AI 产业链全景分析

分层框架（上游→中游→下游）：
  U1 上游·算力芯片设计    （GPU/ASIC/CPU/FPGA）
  U2 上游·芯片制造封测    （代工/封测/设备/材料）
  U3 上游·存储            （HBM/DRAM/NAND/模组）
  U4 上游·光芯片/光器件    （激光器/光芯片）
  M1 中游·光模块          （800G/1.6T）
  M2 中游·PCB/CCL/铜连接   
  M3 中游·服务器/整机      （AI服务器/液冷）
  M4 中游·交换机/网络设备
  M5 中游·散热/电源
  D1 下游·IDC/算力租赁
  D2 下游·云/运营商
  D3 下游·AI应用/大模型
  P1 派生·电力/能源
  P2 派生·铜/金属/材料

每个标的算：
  beta（对龙头）、alpha、R²、条件收益、牛市段/熊市段表现
"""
from __future__ import annotations

import sys
import time
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

OUT = HERE / "output"

# ============================================================
# 分层标的表（人工整理，基于公开产业链认知）
# ============================================================
CHAIN = {
    "U1 上游·算力芯片设计": [
        ("688041.SH", "海光信息"), ("688256.SH", "寒武纪"),
        ("300474.SZ", "景嘉微"), ("603893.SH", "瑞芯微"),
        ("688521.SH", "芯原股份"), ("688008.SH", "澜起科技"),
        ("300223.SZ", "北京君正"), ("688385.SH", "复旦微电"),
        ("002049.SZ", "紫光国微"), ("688018.SH", "乐鑫科技"),
    ],
    "U2 上游·制造/封测/设备": [
        ("688981.SH", "中芯国际"), ("688347.SH", "华虹公司"),
        ("600584.SH", "长电科技"), ("002185.SZ", "华天科技"),
        ("688362.SH", "甬矽电子"), ("002156.SZ", "通富微电"),
        ("002371.SZ", "北方华创"), ("688012.SH", "中微公司"),
        ("603501.SH", "韦尔股份"), ("688200.SH", "华峰测控"),
    ],
    "U3 上游·存储": [
        ("002049.SZ", "紫光国微"), ("603986.SH", "兆易创新"),
        ("300782.SZ", "卓胜微"), ("688123.SH", "聚辰股份"),
        ("002156.SZ", "通富微电"), ("300223.SZ", "北京君正"),
        ("688766.SH", "普冉股份"), ("301308.SZ", "江波龙"),
        ("688110.SH", "东芯股份"), ("300346.SZ", "南大光电"),
    ],
    "U4 上游·光芯片/光器件": [
        ("300502.SZ", "新易盛"), ("300308.SZ", "中际旭创"),
        ("002281.SZ", "光迅科技"), ("300394.SZ", "天孚通信"),
        ("688498.SH", "源杰科技"), ("300620.SZ", "光库科技"),
        ("688195.SH", "腾景科技"), ("002970.SZ", "锐明技术"),
    ],
    "M1 中游·光模块/光通信": [
        ("300308.SZ", "中际旭创"), ("300502.SZ", "新易盛"),
        ("002281.SZ", "光迅科技"), ("300394.SZ", "天孚通信"),
        ("300570.SZ", "太辰光"), ("688048.SH", "长光华芯"),
        ("301205.SZ", "联特科技"), ("002937.SZ", "兴瑞科技"),
    ],
    "M2 中游·PCB/CCL": [
        ("002463.SZ", "沪电股份"), ("002916.SZ", "深南电路"),
        ("300476.SZ", "胜宏科技"), ("002938.SZ", "鹏鼎控股"),
        ("688183.SH", "生益电子"), ("600183.SH", "生益科技"),
        ("300232.SZ", "洲明科技"), ("002913.SZ", "奥士康"),
    ],
    "M3 中游·服务器/整机": [
        ("000977.SZ", "浪潮信息"), ("603019.SH", "中科曙光"),
        ("000938.SZ", "紫光股份"), ("002261.SZ", "拓维信息"),
        ("000066.SZ", "中国长城"), ("300474.SZ", "景嘉微"),
        ("688169.SH", "石头科技"), ("300634.SZ", "彩讯股份"),
    ],
    "M4 中游·交换机/网络": [
        ("000063.SZ", "中兴通讯"), ("300628.SZ", "亿联网络"),
        ("002396.SZ", "星网锐捷"), ("300921.SZ", "南凌科技"),
        ("688100.SH", "威胜信息"), ("300310.SZ", "宜通世纪"),
    ],
    "M5 中游·散热/电源": [
        ("002837.SZ", "英维克"), ("300593.SZ", "新雷能"),
        ("300232.SZ", "洲明科技"), ("002273.SZ", "水晶光电"),
        ("300602.SZ", "飞荣达"), ("688559.SH", "海目星"),
    ],
    "D1 下游·IDC/算力租赁": [
        ("300383.SZ", "光环新网"), ("603881.SH", "数据港"),
        ("300738.SZ", "奥飞数据"), ("603990.SH", "麦迪科技"),
        ("000034.SZ", "神州数码"), ("300442.SZ", "润泽科技"),
        ("600588.SH", "用友网络"), ("300468.SZ", "四方精创"),
    ],
    "D2 下游·云/运营商": [
        ("600050.SH", "中国联通"), ("601728.SH", "中国电信"),
        ("600941.SH", "中国移动"), ("000063.SZ", "中兴通讯"),
        ("600100.SH", "同方股份"), ("600718.SH", "东软集团"),
    ],
    "D3 下游·AI应用/大模型": [
        ("002230.SZ", "科大讯飞"), ("300229.SZ", "拓尔思"),
        ("688111.SH", "金山办公"), ("300418.SZ", "昆仑万维"),
        ("300496.SZ", "中科创达"), ("688327.SH", "云从科技"),
        ("300271.SZ", "华宇软件"), ("002405.SZ", "四维图新"),
        ("300454.SZ", "深信服"), ("688787.SH", "海天瑞声"),
    ],
    "P1 派生·电力/能源": [
        ("600886.SH", "国投电力"), ("000883.SZ", "湖北能源"),
        ("601985.SH", "中国核电"), ("600900.SH", "长江电力"),
        ("000539.SZ", "粤电力A"), ("600011.SH", "华能国际"),
    ],
    "P2 派生·铜/材料": [
        ("601899.SH", "紫金矿业"), ("600362.SH", "江西铜业"),
        ("000630.SZ", "铜陵有色"), ("601600.SH", "中国铝业"),
        ("603993.SH", "洛阳钼业"), ("002171.SZ", "楚江新材"),
    ],
}


def main():
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 142)
    print("  AI 产业链全景分析")
    print("=" * 142)
    print()

    # 去重
    seen = {}
    for g, items in CHAIN.items():
        for c, n in items:
            if c not in seen:
                seen[c] = n
    uniq = sorted(seen.items())
    print(f"  分层数: {len(CHAIN)}")
    print(f"  去重后标的: {len(uniq)} 只")
    print()
    for g, items in CHAIN.items():
        cs = list(dict.fromkeys([c for c, _ in items]))
        print(f"  {g:<26}{len(cs):>3} 只")
    print()

    # ---- 拉数据
    print("[1] 拉取日线（2024-01-01 ~ 2026-10-16）...")
    t0 = time.time()
    px = {}
    fail = []
    for c, n in uniq:
        try:
            d = pro.daily(ts_code=c, start_date="20240101",
                          end_date="20261016")
            if d is None or len(d) < 100:
                fail.append((c, n, "数据不足"))
                continue
            d = d.copy()
            d["date"] = pd.to_datetime(d["trade_date"], format="%Y%m%d")
            d = d.sort_values("date").reset_index(drop=True)
            d["pct"] = pd.to_numeric(d["pct_chg"], errors="coerce") / 100
            d["close"] = pd.to_numeric(d["close"], errors="coerce")
            d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
            px[c] = d
        except Exception as e:
            fail.append((c, n, str(e)[:30]))
        time.sleep(0.14)
    print(f"    成功 {len(px)}/{len(uniq)}  ({time.time()-t0:.0f}s)")
    if fail:
        print(f"    失败 {len(fail)}:")
        for c, n, r in fail[:12]:
            print(f"      {c} {n}: {r}")
    print()

    # 名称映射 + 层级映射
    name_of = {c: n for c, n in uniq}
    grp_of = {}
    for g, items in CHAIN.items():
        for c, n in items:
            grp_of.setdefault(c, []).append(g)

    # 收益矩阵
    R = pd.DataFrame({c: d.set_index("date")["pct"] for c, d in px.items()})
    R = R.sort_index()
    # 价格上涨（用于累计收益）
    CL = pd.DataFrame({c: d.set_index("date")["close"] for c, d in px.items()})
    CL = CL.sort_index()

    Rc = R.dropna(how="all").dropna()
    print(f"  共同交易日: {len(Rc)}")
    print(f"  范围: {Rc.index.min().date()} ~ {Rc.index.max().date()}")
    print()

    # ---- 龙头组合
    l0 = [c for c in ["688041.SH", "688256.SH", "300474.SZ"]
          if c in Rc.columns]
    leader = Rc[l0].mean(axis=1)
    print(f"[2] 龙头组合（等权）: {'、'.join([name_of.get(c,c) for c in l0])}")
    print(f"    区间累计: {((1+leader).prod()-1)*100:+.1f}%")
    print(f"    日均 {leader.mean()*100:+.3f}%  日波动 {leader.std()*100:.3f}%")
    print()

    # 市场基准（全样本等权）
    mkt = Rc.mean(axis=1)
    print(f"    市场等权 {((1+mkt).prod()-1)*100:+.1f}%")
    print()

    # ---- 逐标的指标
    print("[3] 计算各标的指标...")
    rows = []
    for c in Rc.columns:
        y = Rc[c]
        # beta/alpha vs 龙头
        cov = np.cov(y, leader)
        b = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else np.nan
        a = y.mean() - b * leader.mean()
        yhat = a + b * leader
        ss_res = ((y - yhat) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else np.nan
        # beta vs 市场
        covm = np.cov(y, mkt)
        bm = covm[0, 1] / covm[1, 1] if covm[1, 1] > 0 else np.nan
        # 累计收益
        cum = (1 + y).prod() - 1
        # 波动率
        vol = y.std() * np.sqrt(250)
        # 条件收益
        up = leader > 0
        dn = leader <= 0
        mu = y[up].mean() * 100
        md = y[dn].mean() * 100

        rows.append({
            "代码": c[:6], "名称": name_of.get(c, c),
            "层": grp_of.get(c, ["?"])[0],
            "beta_龙头": round(b, 3) if pd.notna(b) else None,
            "alpha日%": round(a * 100, 4),
            "年化alpha%": round(a * 250 * 100, 2),
            "R2": round(r2, 3) if pd.notna(r2) else None,
            "beta_市场": round(bm, 3) if pd.notna(bm) else None,
            "累计收益%": round(cum * 100, 1),
            "年化波动%": round(vol * 100, 1),
            "龙头涨时%": round(mu, 3),
            "龙头跌时%": round(md, 3),
            "条件差": round(mu - md, 3),
        })
    A = pd.DataFrame(rows)
    A = A.sort_values(["层", "累计收益%"], ascending=[True, False])

    print(f"    完成 {len(A)} 只")
    print()

    # ---- 分层汇总
    print("=" * 142)
    print("  一、★ 分层汇总")
    print("=" * 142)
    print()
    GS = []
    for g in CHAIN.keys():
        s = A[A["层"] == g]
        if len(s) == 0:
            continue
        GS.append({
            "层级": g, "标的数": len(s),
            "平均beta": round(s["beta_龙头"].mean(), 3),
            "平均R2": round(s["R2"].mean(), 3),
            "平均累计%": round(s["累计收益%"].mean(), 1),
            "中位累计%": round(s["累计收益%"].median(), 1),
            "平均波动%": round(s["年化波动%"].mean(), 1),
            "平均条件差": round(s["条件差"].mean(), 3),
            "平均年化alpha%": round(s["年化alpha%"].mean(), 1),
        })
    G = pd.DataFrame(GS)
    print(f"  {'层级':<26}{'数':>4}{'beta':>8}{'R2':>7}"
          f"{'平均累计%':>11}{'中位累计%':>11}{'波动%':>8}"
          f"{'条件差':>9}{'年化α%':>10}")
    print("  " + "-" * 96)
    for _, r in G.iterrows():
        print(f"  {r['层级']:<26}{r['标的数']:>4}{r['平均beta']:>8.3f}"
              f"{r['平均R2']:>7.3f}{r['平均累计%']:>11.1f}"
              f"{r['中位累计%']:>11.1f}{r['平均波动%']:>8.1f}"
              f"{r['平均条件差']:>9.3f}{r['平均年化alpha%']:>10.1f}")
    print()
    print(f"  龙头组合累计: {((1+leader).prod()-1)*100:+.1f}%")
    print(f"  市场等权累计: {((1+mkt).prod()-1)*100:+.1f}%")
    print()

    # ---- 弹性排序
    print("=" * 142)
    print("  二、★ 按累计收益排序（看哪一层弹性最大）")
    print("=" * 142)
    print()
    top = A.sort_values("累计收益%", ascending=False).head(25)
    print(f"  {'代码':<8}{'名称':<11}{'层':<24}{'累计%':>9}"
          f"{'beta':>8}{'R2':>7}{'波动%':>8}{'条件差':>9}")
    print("  " + "-" * 90)
    for _, r in top.iterrows():
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['层'])[:23]:<24}{r['累计收益%']:>9.1f}"
              f"{r['beta_龙头']:>8.3f}{r['R2']:>7.3f}"
              f"{r['年化波动%']:>8.1f}{r['条件差']:>9.3f}")
    print()

    print("=" * 142)
    print("  三、★ 按累计收益排序（最差的 20 只）")
    print("=" * 142)
    print()
    bot = A.sort_values("累计收益%").head(20)
    print(f"  {'代码':<8}{'名称':<11}{'层':<24}{'累计%':>9}"
          f"{'beta':>8}{'R2':>7}{'条件差':>9}")
    print("  " + "-" * 82)
    for _, r in bot.iterrows():
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['层'])[:23]:<24}{r['累计收益%']:>9.1f}"
              f"{r['beta_龙头']:>8.3f}{r['R2']:>7.3f}"
              f"{r['条件差']:>9.3f}")
    print()

    # ---- 风险收益比
    print("=" * 142)
    print("  四、★ 风险收益比（累计收益 / 年化波动）")
    print("=" * 142)
    print()
    A2 = A.copy()
    A2["收益风险比"] = (A2["累计收益%"] / A2["年化波动%"]).round(3)
    t2 = A2.sort_values("收益风险比", ascending=False).head(20)
    print(f"  {'代码':<8}{'名称':<11}{'层':<24}{'收益风险比':>11}"
          f"{'累计%':>9}{'波动%':>8}")
    print("  " + "-" * 74)
    for _, r in t2.iterrows():
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['层'])[:23]:<24}{r['收益风险比']:>11.3f}"
              f"{r['累计收益%']:>9.1f}{r['年化波动%']:>8.1f}")
    print()

    # ---- 保存
    A.to_csv(OUT / "ai_chain_stocks.csv", index=False,
             encoding="utf-8-sig")
    G.to_csv(OUT / "ai_chain_groups.csv", index=False,
             encoding="utf-8-sig")
    Rc.to_csv(OUT / "ai_chain_returns.csv", encoding="utf-8-sig")
    print(f"[输出] {OUT}/ai_chain_stocks.csv  ({len(A)})")
    print(f"[输出] {OUT}/ai_chain_groups.csv  ({len(G)})")
    print(f"[输出] {OUT}/ai_chain_returns.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
