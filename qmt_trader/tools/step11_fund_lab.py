# -*- coding: utf-8 -*-
"""
step11_fund_lab.py — 基本面策略实验室

与价量实验室同框架，但因子换为 PIT 财务因子。

因子族：
  A 盈利质量：ROE、扣非ROE、ROA、ROIC、毛利率、净利率、ROE稳定性
  B 成长    ：营收增速、净利增速、扣非增速、营业利润增速、8期均值
  C 现金流  ：OCF/净利、OCF/营收、OCF/资产、现金流增速
  D 应计    ：每股应计（低=质量高）
  E 安全    ：资产负债率、流动比率、速动比率、负债率变化
  F 趋势    ：毛利率趋势、ROE同比
  G 周转    ：总资产周转、应收周转

严格 PIT：所有因子按 ann_date 合并到日线。
"""
from __future__ import annotations

import os
import sys
import time
import json
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from core.logger import get_logger

LOG = get_logger("flab")


# ===========================================================================
# 基本面策略定义
# ===========================================================================
# (名称, 因子, 方向, 分位, 附加条件, 说明)
FUND_STRATEGIES = [
    # ---- A 盈利质量
    ("高ROE", "rk_f_roe_ann", "high", 0.20, [], "ROE最高20%"),
    ("高扣非ROE", "rk_f_roe_dt_ann", "high", 0.20, [], "扣非ROE最高20%"),
    ("高ROA", "rk_f_roa_ann", "high", 0.20, [], "ROA最高20%"),
    ("高ROIC", "rk_f_roic_ann", "high", 0.20, [], "ROIC最高20%"),
    ("高毛利率", "rk_grossprofit_margin", "high", 0.20, [], "毛利率最高20%"),
    ("高净利率", "rk_netprofit_margin", "high", 0.20, [], "净利率最高20%"),
    ("ROE稳定", "rk_f_roe_stable", "high", 0.20, [], "ROE均值/标准差最高20%"),
    ("低ROE波动", "rk_f_roe_std_8", "low", 0.20, [], "ROE标准差最低20%"),

    # ---- B 成长
    ("高营收增速", "rk_or_yoy", "high", 0.20, [], "营收增速最高20%"),
    ("高净利增速", "rk_netprofit_yoy", "high", 0.20, [], "净利增速最高20%"),
    ("高扣非增速", "rk_dt_netprofit_yoy", "high", 0.20, [], "扣非净利增速最高20%"),
    ("高营业利润增速", "rk_op_yoy", "high", 0.20, [], "营业利润增速最高20%"),
    ("持续成长", "rk_f_netprofit_yoy_mean_8", "high", 0.20, [],
     "8期平均净利增速最高20%"),

    # ---- C 现金流
    ("现金流质量", "rk_f_ocf_to_ni", "high", 0.20, [], "OCF/净利最高20%"),
    ("高现金流", "rk_ocf_to_or", "high", 0.20, [], "OCF/营收最高20%"),
    ("现金流增速", "rk_ocf_yoy", "high", 0.20, [], "现金流增速最高20%"),

    # ---- D 应计（低=好）
    ("低应计", "rk_f_accrual_ps", "low", 0.20, [], "每股应计最低20%（质量高）"),

    # ---- E 安全
    ("低负债", "rk_debt_to_assets", "low", 0.20, [], "资产负债率最低20%"),
    ("高流动比", "rk_current_ratio", "high", 0.20, [], "流动比率最高20%"),
    ("负债下降", "rk_f_debt_chg", "low", 0.20, [], "负债率同比降幅最大20%"),

    # ---- F 趋势
    ("毛利改善", "rk_f_gm_trend", "high", 0.20, [], "毛利率同比改善最大20%"),
    ("ROE改善", "rk_f_roe_yoy", "high", 0.20, [], "ROE同比改善最大20%"),

    # ---- G 周转
    ("高资产周转", "rk_assets_turn", "high", 0.20, [], "总资产周转最高20%"),

    # ---- 组合（QMJ 框架：质量+成长+安全）
    ("质量组合", "rk_f_roe_ann", "high", 0.30,
     ["rk_f_accrual_ps:low:0.40"], "高ROE + 低应计"),
    ("质量+低负债", "rk_f_roe_ann", "high", 0.30,
     ["rk_debt_to_assets:low:0.40"], "高ROE + 低负债"),
    ("成长+现金流", "rk_netprofit_yoy", "high", 0.30,
     ["rk_f_ocf_to_ni:high:0.40"], "高净利增速 + 高现金流质量"),
    ("GARP组合", "rk_f_roe_ann", "high", 0.30,
     ["rk_netprofit_yoy:high:0.40"], "高ROE + 高成长"),
    ("高质量低估值", "rk_f_roe_ann", "high", 0.30,
     ["rk_f_log_bps:low:0.40"], "高ROE + 低BPS（小盘）"),
]


def build_group_ranks(merged: dict, pool_by_date: dict,
                      factors: list[str]) -> None:
    """
    在池内计算基本面因子的横截面分位。

    与价量实验室同逻辑：按 (date, code) 长表 → groupby(date).rank(pct=True)
    """
    print(f"  [横截面] 构建长表...", flush=True)
    frames = []
    for c6, d in merged.items():
        keep = [c for c in factors if c in d.columns]
        if not keep:
            continue
        t = d[["date"] + keep].copy()
        t["code"] = c6
        frames.append(t)
    if not frames:
        return
    LONG = pd.concat(frames, ignore_index=True)
    del frames
    print(f"  [横截面] {len(LONG):,} 行", flush=True)

    rows = []
    for dt, pool in pool_by_date.items():
        for c in pool:
            rows.append((dt, c))
    POOL = pd.DataFrame(rows, columns=["date", "code"])
    LONG = LONG.merge(POOL, on=["date", "code"], how="inner")
    print(f"  [横截面] 池内 {len(LONG):,} 行", flush=True)

    for i, f in enumerate(factors):
        if f not in LONG.columns:
            continue
        rk = LONG.groupby("date")[f].rank(pct=True, na_option="keep")
        LONG[f"rk_{f}"] = rk.astype(np.float32)
        if (i + 1) % 8 == 0:
            print(f"  [横截面] {i+1}/{len(factors)}", flush=True)

    rank_cols = [c for c in LONG.columns if c.startswith("rk_")]
    for c6, sub in LONG.groupby("code"):
        d = merged.get(c6)
        if d is None:
            continue
        idxs = sub["date"].values
        for col in rank_cols:
            vals = sub[col].values
            d[col] = pd.Series(vals, index=idxs).reindex(d["date"].values).values
    del LONG
    print(f"  [横截面] 完成", flush=True)


def build_signals(merged: dict, spec: tuple) -> dict:
    """生成信号 {date: [codes]}，按因子强度排序。"""
    name, factor, direction, q, extra, desc = spec
    extras = []
    for e in extra or []:
        parts = e.split(":")
        extras.append((parts[0], parts[1], float(parts[2])))

    sig = defaultdict(list)
    for c6, d in merged.items():
        if factor not in d.columns:
            continue
        if direction == "low":
            mask = d[factor] <= q
        else:
            mask = d[factor] >= (1 - q)
        mask = mask.fillna(False)
        for ef, edir, eq_ in extras:
            if ef not in d.columns:
                mask = mask & False
                break
            if edir == "low":
                mask = mask & (d[ef] <= eq_).fillna(False)
            else:
                mask = mask & (d[ef] >= (1 - eq_)).fillna(False)
        idxs = np.where(mask.values)[0]
        if len(idxs) == 0:
            continue
        ds = d["date"].tolist()
        vals = d[factor].values
        for k in idxs:
            sig[pd.Timestamp(ds[k])].append((c6, vals[k]))

    out = {}
    for dt, items in sig.items():
        items.sort(key=lambda x: (x[1] if np.isfinite(x[1]) else 9e9),
                   reverse=(direction != "low"))
        out[dt] = [c for c, _ in items]
    return out
