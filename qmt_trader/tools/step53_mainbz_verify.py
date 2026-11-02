# -*- coding: utf-8 -*-
"""
step53_mainbz_verify.py — 基于「主营业务构成」逐股核实 AI 产业链归属

方法（正确做法）：
  ① 从候选池（概念板块 3,239 条）取全部股票
  ② 逐股拉 fina_mainbz（主营业务构成，按产品）
  ③ 用产品名关键词判定「哪些产品属于 AI 产业链」
  ④ 计算 AI 相关业务收入占比（纯度）
  ⑤ 只保留纯度 ≥ 30% 的标的
  ⑥ 按产品名归类到具体层级

这样能剔除：
  · 名字带 AI 但主业无关的（如太极集团）
  · ST/垃圾股（主业萎缩）
  · 概念蹭热度但收入不相关的
"""
from __future__ import annotations

import os
import sys
import time
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

OUT = HERE / "output"
CACHE = HERE / "data_cache" / "mainbz"


# ============================================================
# 产品关键词 → 产业链层级
# 按「具体 → 宽泛」排序，先匹配到的优先
# ============================================================
PRODUCT_RULES = [
    # ---- U4 光模块/光器件 ----
    ("U4 上游·光模块/光器件", [
        "光模块", "光通信收发", "光收发", "光互联", "光器件",
        "光芯片", "光引擎", "有源光缆", "光连接", "光路",
        "光纤连接", "光电子器件", "光电耦合", "AWG", "波分复用",
        "光分路", "光缆", "光纤", "光通信", "光有源", "光无源",
    ]),
    # ---- U3 存储 ----
    ("U3 上游·存储", [
        "存储芯片", "存储模组", "DRAM", "FLASH", "闪存",
        "内存", "固态硬盘", "SSD", "内存接口", "eMMC", "UFS",
        "存储产品", "存储器件",
    ]),
    # ---- U2 半导体设备/材料 ----
    ("U2 上游·半导体设备/材料", [
        "半导体设备", "刻蚀", "薄膜沉积", "光刻", "离子注入",
        "清洗设备", "封装设备", "测试设备", "探针台", "划片",
        "半导体材料", "光刻胶", "电子特气", "湿电子化学",
        "抛光液", "靶材", "硅片", "石英", "封装基板",
        "引线框架", "键合丝",
    ]),
    # ---- U1 芯片设计/制造 ----
    ("U1 上游·芯片设计/制造", [
        "芯片", "集成电路", "处理器", "CPU", "GPU", "FPGA",
        "高端处理器", "计算芯片", "智能芯片", "半导体",
        "ASIC", "SoC", "微处理器", "模拟芯片", "射频芯片",
        "功率半导体", "功率器件", "IGBT", "MOSFET", "晶圆",
        "晶圆代工", "晶圆制造", "封装测试", "封测",
        "第三代半导体", "碳化硅", "氮化镓",
    ]),
    # ---- M2 PCB/CCL ----
    ("M2 中游·PCB/覆铜板", [
        "印制电路", "PCB", "覆铜板", "CCL", "线路板",
        "多层板", "HDI", "柔性电路", "FPC", "载板",
        "铜箔", "半固化片", "覆铜", "印刷电路", "电路板",
    ]),
    # ---- M5 液冷/散热/电源 ----
    ("M5 中游·液冷/散热/电源", [
        "液冷", "散热", "热管理", "冷板", "温控",
        "服务器电源", "电源模块", "UPS", "精密空调",
        "机房空调", "机柜", "机箱",
    ]),
    # ---- M3 服务器/整机 ----
    ("M3 中游·服务器/整机", [
        "服务器", "整机", "计算机整机", "算力设备",
        "AI服务器", "通用服务器", "工作站", "计算产业",
        "存储类、交换类",
    ]),
    # ---- M4 交换机/网络 ----
    ("M4 中游·交换机/网络", [
        "交换机", "路由器", "网络设备", "网络产品",
        "通信设备", "基站", "天线",
    ]),
    # ---- D1 IDC/算力服务 ----
    ("D1 下游·IDC/算力服务", [
        "IDC", "数据中心", "机柜租赁", "算力服务", "算力",
        "云计算", "云服务", "带宽", "托管", "智算",
    ]),
    # ---- D3 AI应用/软件 ----
    ("D3 下游·AI应用/软件", [
        "人工智能", "AI", "大模型", "算法", "智能驾驶",
        "软件", "系统集成", "信息化", "智能应用",
    ]),
]

# 排除词（出现则大幅降权，即使命中关键词）
HARD_EXCLUDE = [
    "光伏", "太阳能", "组件", "硅料", "电池片",
    "锂电", "正极", "负极", "隔膜", "电解液",
    "医药", "原料药", "制剂", "疫苗",
    "白酒", "食品", "饮料",
    "房地产", "建筑工程", "水泥",
    "银行业", "保险", "证券",
    "养殖", "饲料", "种业",
    "纺织", "服装", "家纺",
    "钢铁", "煤炭", "石油", "天然气",
    "汽车整车", "乘用车", "商用车",
    "电力销售", "售电", "火电", "水电",
    "教育", "旅游", "酒店", "餐饮",
    "传媒", "广告", "影视",
    "环保", "水务", "燃气",
]


# 需要剔除的汇总项（与明细并列，会导致分母翻倍）
AGG_ITEMS = {"产品", "其他", "其他业务", "其他业务收入", "其他产品",
             "合计特别调整", "合计", "小计", "合计特别调整项",
             "主营业务", "主营业务收入", "营业收入", "合计项",
             "分部间抵销", "合并抵消", "内部抵销", "抵消"}


def match_layer(items: list) -> tuple:
    """
    对一组产品名，返回 (层级, 匹配的产品, AI相关收入占比)。

    items: [(产品名, 收入), ...]
    ★ 关键：剔除「产品」「其他」等汇总项，否则分母翻倍。
    """
    # 先剔除汇总项
    detail = []
    for name, amt in items:
        if not name or not amt or amt <= 0:
            continue
        nm = str(name).strip()
        if nm in AGG_ITEMS:
            continue
        detail.append((nm, amt))
    if not detail:
        detail = [(str(n), v) for n, v in items if v and v > 0]
    total = sum(v for _, v in detail)
    if total <= 0:
        return None, [], 0.0
    items = detail
    hit_amt = 0.0
    hit_items = []
    layer_cnt = {}
    for name, amt in items:
        if not name or not amt or amt <= 0:
            continue
        nm = str(name)
        # 排除词检查
        if any(e in nm for e in HARD_EXCLUDE):
            continue
        for layer, kws in PRODUCT_RULES:
            if any(k in nm for k in kws):
                hit_amt += amt
                hit_items.append(f"{nm}({amt/1e8:.1f}亿)")
                layer_cnt[layer] = layer_cnt.get(layer, 0) + amt
                break
    purity = hit_amt / total * 100 if total > 0 else 0.0
    # 主层 = 命中金额最大的层
    main_layer = (max(layer_cnt.items(), key=lambda x: x[1])[0]
                  if layer_cnt else None)
    return main_layer, hit_items, purity


def main():
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 140)
    print("  基于「主营业务构成」逐股核实 AI 产业链归属")
    print("=" * 140)
    print()

    CACHE.mkdir(parents=True, exist_ok=True)

    # ---- 候选池
    U = pd.read_csv(OUT / "ai_refined_universe.csv", dtype={"代码6": str})
    codes = sorted(set(U["代码"].tolist()))
    print(f"[1] 候选池: {len(codes):,} 只")
    print()

    # ---- 基本信息
    print("[2] 拉取股票基本信息...")
    basic = {}
    for st in ("L", "D", "P"):
        try:
            b = pro.stock_basic(exchange="", list_status=st,
                                fields="ts_code,name,industry")
            for _, r in b.iterrows():
                basic[str(r["ts_code"])[:6]] = {
                    "name": r["name"], "ind": r.get("industry", ""),
                    "status": st}
        except Exception:
            pass
    print(f"    {len(basic):,} 只")
    print()

    # ---- 逐股拉主营构成
    print("[3] 逐股拉取主营业务构成（fina_mainbz）...")
    print(f"    {len(codes):,} 只 —— 预计 {len(codes)*0.35/60:.0f} 分钟")
    print()
    t0 = time.time()
    rows = []
    miss = []
    for i, tc in enumerate(codes, 1):
        c6 = tc[:6]
        cf = CACHE / f"{c6}.json"
        data = None
        if cf.exists():
            try:
                data = json.loads(cf.read_text(encoding="utf-8"))
            except Exception:
                data = None
        if data is None:
            got = None
            # 试最近4个报告期
            for period in ("20251231", "20250630", "20241231", "20240630"):
                try:
                    d = pro.fina_mainbz(ts_code=tc, period=period, type="P")
                    if d is not None and len(d) > 0:
                        got = d
                        break
                except Exception:
                    time.sleep(0.5)
                time.sleep(0.2)
            if got is None:
                miss.append(c6)
                data = []
            else:
                got = got.copy()
                got["bz_sales"] = pd.to_numeric(got["bz_sales"],
                                                errors="coerce")
                data = got[["end_date", "bz_item", "bz_sales"]].fillna(
                    {"bz_sales": 0}).to_dict("records")
            try:
                cf.write_text(json.dumps(data, ensure_ascii=False),
                              encoding="utf-8")
            except Exception:
                pass
            time.sleep(0.25)
        if data:
            info = basic.get(c6, {})
            items = [(d.get("bz_item"), d.get("bz_sales", 0))
                     for d in data]
            layer, hits, purity = match_layer(items)
            rows.append({
                "代码": tc, "代码6": c6,
                "名称": info.get("name", "?"),
                "申万行业": info.get("ind", ""),
                "状态": info.get("status", ""),
                "报告期": data[0].get("end_date", ""),
                "主营项数": len(data),
                "层级": layer or "未匹配",
                "AI纯度%": round(purity, 1),
                "命中产品": "; ".join(hits[:5]),
                "全部产品": "; ".join(
                    [f"{d.get('bz_item')}({d.get('bz_sales',0)/1e8:.1f}亿)"
                     for d in data][:10]),
            })
        if i % 200 == 0:
            print(f"    {i}/{len(codes)}  有效 {len(rows)}  "
                  f"({time.time()-t0:.0f}s)", flush=True)
    print(f"    完成: 有效 {len(rows):,}  无数据 {len(miss):,}"
          f"  ({time.time()-t0:.0f}s)")
    print()
    if not rows:
        print("无数据")
        return 1
    A = pd.DataFrame(rows)

    # ---- 纯度分布
    print("=" * 140)
    print("  一、★ AI 业务纯度分布（关键）")
    print("=" * 140)
    print()
    print(f"  有效标的: {len(A):,}")
    print()
    bands = [(0, 0), (0, 10), (10, 30), (30, 50), (50, 70), (70, 90), (90, 101)]
    print(f"  {'纯度区间':<14}{'标的数':>9}{'占比':>9}")
    print("  " + "-" * 34)
    for lo, hi in bands:
        if lo == 0 and hi == 0:
            m = A["AI纯度%"] <= 0
            lab = "= 0%"
        else:
            m = (A["AI纯度%"] > lo) & (A["AI纯度%"] <= hi)
            lab = f"{lo}~{hi}%"
        print(f"  {lab:<14}{int(m.sum()):>9}{m.mean()*100:>8.1f}%")
    print()
    print(f"  纯度 ≥ 30%: {int((A['AI纯度%']>=30).sum()):,} 只")
    print(f"  纯度 ≥ 50%: {int((A['AI纯度%']>=50).sum()):,} 只")
    print(f"  纯度 ≥ 70%: {int((A['AI纯度%']>=70).sum()):,} 只")
    print()

    # ---- 分层统计
    print("=" * 140)
    print("  二、★ 匹配到的层级分布")
    print("=" * 140)
    print()
    C = A[A["AI纯度%"] > 0]
    print(f"  {'层级':<28}{'标的数':>8}{'平均纯度%':>11}{'中位纯度%':>11}")
    print("  " + "-" * 60)
    for g, s in C.groupby("层级"):
        print(f"  {g:<28}{len(s):>8}{s['AI纯度%'].mean():>11.1f}"
              f"{s['AI纯度%'].median():>11.1f}")
    print()
    print(f"  未匹配: {int((A['AI纯度%']<=0).sum()):,} 只（主业与 AI 无关）")
    print()

    # ---- 高纯度代表
    print("=" * 140)
    print("  三、★ 高纯度标的（纯度 ≥ 70%）代表")
    print("=" * 140)
    print()
    hi = A[A["AI纯度%"] >= 70].sort_values("AI纯度%", ascending=False)
    print(f"  共 {len(hi):,} 只")
    print()
    print(f"  {'代码':<10}{'名称':<11}{'层级':<26}{'纯度%':>8}"
          f"{'主营':<46}")
    print("  " + "-" * 104)
    for _, r in hi.head(45).iterrows():
        print(f"  {r['代码']:<10}{str(r['名称'])[:10]:<11}"
              f"{str(r['层级'])[:25]:<26}{r['AI纯度%']:>8.1f}"
              f"{str(r['命中产品'])[:44]:<46}")
    print()

    # ---- 反面案例（名字像但主业无关）
    print("=" * 140)
    print("  四、★ 反面案例：概念池里但主业与 AI 无关（纯度=0）")
    print("=" * 140)
    print()
    z = A[A["AI纯度%"] <= 0]
    print(f"  {len(z):,} 只 —— 这些应从池中剔除")
    print()
    print(f"  {'代码':<10}{'名称':<11}{'申万行业':<14}{'主营业务'}")
    print("  " + "-" * 100)
    for _, r in z.head(30).iterrows():
        print(f"  {r['代码']:<10}{str(r['名称'])[:10]:<11}"
              f"{str(r['申万行业'])[:13]:<14}{str(r['全部产品'])[:60]}")
    print()

    A.to_csv(OUT / "ai_mainbz_verified.csv", index=False,
             encoding="utf-8-sig")
    hi.to_csv(OUT / "ai_mainbz_highpurity.csv", index=False,
              encoding="utf-8-sig")
    print(f"[输出] {OUT}/ai_mainbz_verified.csv  ({len(A):,})")
    print(f"[输出] {OUT}/ai_mainbz_highpurity.csv  ({len(hi):,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
