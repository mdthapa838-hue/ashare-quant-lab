# -*- coding: utf-8 -*-
"""
step54_ai_final.py — 基于真实业务的 AI 产业链最终分析

在 step53 基础上：
  ① 收紧 D3 规则（剔除「软件」「系统集成」等宽泛词）
  ② 对纯度 ≥50% 的标的计算 beta/alpha/分段表现
  ③ 与龙头、市场对比
"""
from __future__ import annotations

import io
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
CACHE = HERE / "data_cache" / "mainbz"

# ---- 收紧后的规则
RULES = [
    ("U4 上游·光模块/光器件", [
        "光模块", "光通信收发", "光收发", "光互联", "光器件",
        "光芯片", "光引擎", "有源光缆", "光连接", "光路",
        "光纤连接", "光电子器件", "光电耦合", "AWG", "波分复用",
        "光分路", "光缆", "光纤", "光通信", "光有源", "光无源",
        "激光器", "光放大器",
    ]),
    ("U3 上游·存储", [
        "存储芯片", "存储模组", "DRAM", "FLASH", "闪存",
        "内存", "固态硬盘", "SSD", "内存接口", "eMMC", "UFS",
        "存储产品", "存储器件", "存储类",
    ]),
    ("U2 上游·半导体设备/材料", [
        "半导体设备", "半导体专用设备", "刻蚀", "薄膜沉积",
        "光刻", "离子注入", "清洗设备", "封装设备", "测试设备",
        "探针台", "划片", "半导体材料", "光刻胶", "电子特气",
        "湿电子化学", "抛光液", "靶材", "硅片", "石英",
        "封装基板", "引线框架", "键合丝", "化学机械抛光",
        "功能性湿电子", "半导体硅片", "晶圆制造设备",
    ]),
    ("U1 上游·芯片设计/制造", [
        "芯片", "集成电路", "处理器", "CPU", "GPU", "FPGA",
        "高端处理器", "计算芯片", "智能芯片", "SoC",
        "微处理器", "模拟芯片", "射频芯片", "功率半导体",
        "功率器件", "IGBT", "MOSFET", "晶圆", "晶圆代工",
        "晶圆制造", "封装测试", "封测", "芯片封测", "芯片测试",
        "第三代半导体", "碳化硅", "氮化镓", "半导体",
        "芯片设计", "芯片量产", "半导体功率器件",
        "化合物半导体", "晶圆级封装", "芯粒",
        "LED芯片", "传感器芯片", "电源管理芯片",
        "指纹识别芯片", "触控芯片", "存储类芯片",
    ]),
    ("M2 中游·PCB/覆铜板", [
        "印制电路", "PCB", "覆铜板", "CCL", "线路板",
        "多层板", "HDI", "柔性电路", "FPC", "载板",
        "铜箔", "半固化片", "覆铜", "印刷电路", "电路板",
        "电子电路铜箔", "PCBA",
    ]),
    ("M5 中游·液冷/散热/电源", [
        "液冷", "散热", "热管理", "冷板", "温控",
        "服务器电源", "电源模块", "UPS", "精密空调",
        "机房空调",
    ]),
    ("M3 中游·服务器/整机", [
        "服务器", "AI服务器", "通用服务器", "工作站",
        "计算机整机", "算力设备", "计算产业",
    ]),
    ("M4 中游·交换机/网络", [
        "交换机", "路由器", "网络设备", "通信设备",
        "光网络", "基站", "天线",
    ]),
    ("D1 下游·IDC/算力服务", [
        "IDC", "数据中心", "机柜租赁", "算力服务", "算力",
        "智算", "云计算", "云服务", "托管",
    ]),
    ("D3 下游·AI应用", [
        # ★ 收紧：只保留明确的 AI 相关
        "人工智能", "AI", "大模型", "智能算法",
        "智能驾驶", "智能座舱", "机器视觉", "语音识别",
        "AIoT", "智能应用", "智能体",
    ]),
]

AGG = {"产品", "其他", "其他业务", "其他业务收入", "其他产品",
       "合计特别调整", "合计", "小计", "主营业务", "主营业务收入",
       "营业收入", "分部间抵销", "合并抵消", "内部抵销", "抵消",
       "其他行业", "其他产业"}

EXCLUDE = ["光伏", "太阳能", "电池片", "硅料", "锂电", "正极",
           "负极", "隔膜", "电解液", "医药", "原料药", "制剂",
           "白酒", "食品", "饮料", "房地产", "建筑工程", "水泥",
           "银行", "保险", "证券", "养殖", "饲料", "种业",
           "纺织", "服装", "家纺", "钢铁", "煤炭", "石油",
           "天然气", "汽车整车", "乘用车", "商用车", "售电",
           "火电", "水电", "教育", "旅游", "酒店", "餐饮",
           "传媒", "广告", "影视", "环保", "水务", "燃气",
           "LED封装", "车灯", "家用电器", "百货", "零售"]


def match(items):
    detail = []
    for n, v in items:
        if not n or not v or v <= 0:
            continue
        s = str(n).strip()
        if s in AGG:
            continue
        detail.append((s, v))
    if not detail:
        return None, [], 0.0
    total = sum(v for _, v in detail)
    if total <= 0:
        return None, [], 0.0
    amt = 0.0
    hits = []
    cnt = {}
    for n, v in detail:
        if any(e in n for e in EXCLUDE):
            continue
        for layer, kws in RULES:
            if any(k in n for k in kws):
                amt += v
                hits.append(f"{n}({v/1e8:.1f}亿)")
                cnt[layer] = cnt.get(layer, 0) + v
                break
    purity = amt / total * 100
    main = max(cnt.items(), key=lambda x: x[1])[0] if cnt else None
    return main, hits, purity


def main():
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 142)
    print("  AI 产业链最终分析（基于真实主营业务）")
    print("=" * 142)
    print()

    # ---- 基本信息
    basic = {}
    for st in ("L", "D", "P"):
        try:
            b = pro.stock_basic(exchange="", list_status=st,
                                fields="ts_code,name,industry")
            for _, r in b.iterrows():
                basic[str(r["ts_code"])[:6]] = {
                    "name": r["name"], "ind": r.get("industry", "")}
        except Exception:
            pass

    # ---- 用缓存重建（无需重拉）
    print("[1] 从缓存重建主营构成...")
    rows = []
    files = sorted(CACHE.glob("*.json"))
    print(f"    缓存文件: {len(files):,}")
    for cf in files:
        c6 = cf.stem
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not data:
            continue
        items = [(d.get("bz_item"), d.get("bz_sales", 0) or 0)
                 for d in data]
        layer, hits, purity = match(items)
        if purity <= 0:
            continue
        info = basic.get(c6, {})
        rows.append({
            "代码": c6, "名称": info.get("name", "?"),
            "申万行业": info.get("ind", ""),
            "层级": layer or "未匹配",
            "AI纯度%": round(purity, 1),
            "命中产品": "; ".join(hits[:5]),
        })
    A = pd.DataFrame(rows)
    print(f"    有效: {len(A):,}")
    print()

    # ---- 纯度分布
    print("=" * 142)
    print("  一、★ 真实业务纯度分布")
    print("=" * 142)
    print()
    print(f"  {'纯度':<12}{'标的数':>9}")
    print("  " + "-" * 24)
    for lo, hi in [(0, 10), (10, 30), (30, 50), (50, 70), (70, 101)]:
        m = (A["AI纯度%"] > lo) & (A["AI纯度%"] <= hi)
        print(f"  {lo}~{hi}%{'':<5}{int(m.sum()):>9}")
    print()
    for th in (30, 50, 70, 90):
        print(f"  纯度 ≥ {th}%: {int((A['AI纯度%']>=th).sum()):>5} 只")
    print()

    # ---- 分层
    print("=" * 142)
    print("  二、★ 分层（纯度≥50%）")
    print("=" * 142)
    print()
    C = A[A["AI纯度%"] >= 50].copy()
    print(f"  标的数: {len(C):,}")
    print()
    print(f"  {'层级':<28}{'标的数':>8}{'平均纯度%':>11}")
    print("  " + "-" * 50)
    for g, s in C.groupby("层级"):
        print(f"  {g:<28}{len(s):>8}{s['AI纯度%'].mean():>11.1f}")
    print()

    # ---- 拉行情
    print("[2] 拉取行情...")
    codes = [c + (".SH" if c.startswith(("60", "68", "9"))
                  else ".SZ") for c in C["代码"]]
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
                px[tc[:6]] = d.set_index("date")["pct"]
        except Exception:
            pass
        if i % 100 == 0:
            print(f"    {i}/{len(codes)}  成功 {len(px)}", flush=True)
        time.sleep(0.13)
    print(f"    完成 {len(px):,}/{len(codes):,}  ({time.time()-t0:.0f}s)")
    print()
    R = pd.DataFrame(px).sort_index()
    keep = R.notna().mean() >= 0.6
    R = R.loc[:, keep].fillna(0)
    print(f"  收益矩阵: {R.shape[0]} 日 x {R.shape[1]} 只")
    print()

    l0 = [c for c in ["688041", "688256", "300474"] if c in R.columns]
    leader = R[l0].mean(axis=1)
    mkt = R.mean(axis=1)

    # ---- 指标
    SEG = [("2024牛市", "2024-01-02", "2024-12-31"),
           ("2025震荡", "2025-01-01", "2025-12-31"),
           ("2026熊市", "2026-01-01", "2026-10-16"),
           ("全程", "2024-01-02", "2026-10-16")]
    lay_of = dict(zip(C["代码"], C["层级"]))
    nam_of = dict(zip(C["代码"], C["名称"]))
    pur_of = dict(zip(C["代码"], C["AI纯度%"]))

    rows2 = []
    for c in R.columns:
        y = R[c]
        cov = np.cov(y, leader)
        b = cov[0, 1] / cov[1, 1] if cov[1, 1] > 0 else np.nan
        a = y.mean() - b * leader.mean()
        yh = a + b * leader
        r2 = 1 - ((y - yh) ** 2).sum() / ((y - y.mean()) ** 2).sum()
        up, dn = leader > 0, leader <= 0
        rec = {
            "代码": c, "名称": nam_of.get(c, "?"),
            "层级": lay_of.get(c, "?"), "纯度%": pur_of.get(c),
            "beta": round(b, 3) if pd.notna(b) else None,
            "R2": round(r2, 3),
            "累计%": round(((1 + y).prod() - 1) * 100, 1),
            "波动%": round(y.std() * np.sqrt(250) * 100, 1),
            "条件差": round((y[up].mean() - y[dn].mean()) * 100, 3),
        }
        for lab, s, e in SEG:
            sub = y.loc[pd.Timestamp(s):pd.Timestamp(e)]
            rec[lab] = round(((1 + sub).prod() - 1) * 100, 1) if len(sub) else None
        rows2.append(rec)
    F = pd.DataFrame(rows2)

    # ---- 分层汇总
    print("=" * 142)
    print("  三、★★ 分层汇总（基于真实业务）")
    print("=" * 142)
    print()
    GS = []
    for g, s in F.groupby("层级"):
        if len(s) < 3:
            continue
        GS.append({
            "层级": g, "数": len(s),
            "纯度%": round(s["纯度%"].mean(), 1),
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
    G = pd.DataFrame(GS).sort_values("熊市%", ascending=False)
    print(f"  {'层级':<28}{'数':>4}{'纯度%':>7}{'beta':>7}{'R2':>7}"
          f"{'全程%':>9}{'牛市%':>9}{'震荡%':>9}{'熊市%':>9}{'条件差':>8}")
    print("  " + "-" * 108)
    for _, r in G.iterrows():
        print(f"  {r['层级']:<28}{r['数']:>4}{r['纯度%']:>7.1f}"
              f"{r['beta']:>7.3f}{r['R2']:>7.3f}{r['累计%']:>9.1f}"
              f"{r['牛市%']:>9.1f}{r['震荡%']:>9.1f}{r['熊市%']:>9.1f}"
              f"{r['条件差']:>8.3f}")
    print()
    print(f"  龙头: 牛市 {((1+leader.loc['2024-01-02':'2024-12-31']).prod()-1)*100:+.1f}%  "
          f"震荡 {((1+leader.loc['2025-01-01':'2025-12-31']).prod()-1)*100:+.1f}%  "
          f"熊市 {((1+leader.loc['2026-01-01':'2026-10-16']).prod()-1)*100:+.1f}%  "
          f"全程 {((1+leader).prod()-1)*100:+.1f}%")
    print(f"  市场: 全程 {((1+mkt).prod()-1)*100:+.1f}%")
    print()

    # ---- TOP/BOTTOM
    print("=" * 142)
    print("  四、★ 涨幅 TOP 25（纯度≥50% 池）")
    print("=" * 142)
    print()
    print(f"  {'代码':<8}{'名称':<11}{'层级':<24}{'纯度%':>7}{'累计%':>9}"
          f"{'beta':>7}{'熊市%':>9}")
    print("  " + "-" * 80)
    for _, r in F.sort_values("累计%", ascending=False).head(25).iterrows():
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['层级'])[:23]:<24}{r['纯度%']:>7.1f}"
              f"{r['累计%']:>9.1f}{r['beta']:>7.3f}"
              f"{(r['2026熊市'] or 0):>9.1f}")
    print()

    print("=" * 142)
    print("  五、★ 跌幅 TOP 20（纯度≥50% 池）")
    print("=" * 142)
    print()
    for _, r in F.sort_values("累计%").head(20).iterrows():
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['层级'])[:23]:<24}{r['纯度%']:>7.1f}"
              f"{r['累计%']:>9.1f}{r['beta']:>7.3f}"
              f"{(r['2026熊市'] or 0):>9.1f}")
    print()

    F.to_csv(OUT / "ai_final_stocks.csv", index=False, encoding="utf-8-sig")
    G.to_csv(OUT / "ai_final_groups.csv", index=False, encoding="utf-8-sig")
    A.to_csv(OUT / "ai_final_verified.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {OUT}/ai_final_stocks.csv  ({len(F)})")
    print(f"[输出] {OUT}/ai_final_groups.csv  ({len(G)})")
    print(f"[输出] {OUT}/ai_final_verified.csv  ({len(A)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
