# -*- coding: utf-8 -*-
"""fix_layer_order.py — 修正分层优先级（显式数字顺序，不依赖字母序）。"""
import io
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
p = HERE / "tools" / "step52_ai_layers_refine.py"
s = io.open(p, encoding="utf-8").read()

old = '''    # ---- 去重
    order = {}
    for i, g in enumerate(sorted(M["层级"].unique())):
        order[g] = i
    M["序"] = M["层级"].map(order)
    M = M.sort_values(["序", "代码6"])
    M1 = M.drop_duplicates(subset=["代码6"], keep="first")'''

new = '''    # ---- 去重（★ 显式优先级：越"纯"的层越优先）
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
    print(f"[4a] 多概念标的: {len(multi):,} 只（按优先级归属）")'''

if old in s:
    s = s.replace(old, new)
    print("已修正去重优先级")
else:
    print("未找到目标段落")

# 同时修正汇总时的排序
s = s.replace(
    'for g in sorted(A["层级"].unique(), key=lambda x: order.get(x, 99)):',
    'for g in sorted(A["层级"].unique(), key=lambda x: order.get(x, 99)):')
s = s.replace(
    'for g in sorted(M1["层级"].unique(), key=lambda x: order.get(x, 99)):',
    'for g in sorted(M1["层级"].unique(), key=lambda x: order.get(x, 99)):')

io.open(p, "w", encoding="utf-8").write(s)
print("完成")
