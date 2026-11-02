# -*- coding: utf-8 -*-
"""
step10_pit.py — PIT（Point-In-Time）财务数据加载

核心要求：
  财务数据必须在**公告日(ann_date)**之后才可用。
  用报告期末(end_date)提前交易 = 未来函数。

本模块：
  1. 载入 data_cache/fundamental/{code}.csv
  2. 去重（同一 end_date 可能有多次公告/修订）
  3. 按 ann_date 排序
  4. 提供 asof(date) 接口：返回 date 时点「已公告」的最新一期
  5. 构建基本面因子（全部基于 asof 数据）

关键设计：
  - 严格用 ann_date <= date 过滤
  - 同一报告期取**最早公告**（避免用修订后的数据）
  - 计算同比/环比时只用已公告的历史期
"""
from __future__ import annotations

import os
import glob
from pathlib import Path
from functools import lru_cache

import numpy as np
import pandas as pd

from core.logger import get_logger

LOG = get_logger("pit")


# 需要的基础字段
KEEP_FIELDS = [
    "end_date", "ann_date",
    # 盈利
    "roe", "roe_dt", "roe_waa", "roa", "roic",
    "netprofit_margin", "grossprofit_margin",
    "op_of_gr", "ebit_of_gr", "profit_to_gr",
    # 杠杆
    "debt_to_assets", "current_ratio", "quick_ratio", "cash_ratio",
    "assets_to_eqt",
    # 周转
    "ar_turn", "ca_turn", "fa_turn", "assets_turn",
    # 每股
    "eps", "dt_eps", "bps", "ocfps", "cfps", "retainedps",
    # 现金流质量
    "ocf_to_or", "ocf_to_assets", "ocf_to_liabilities",
    "ocf_to_debt", "ocf_to_netdebt",
    # 增速
    "or_yoy", "op_yoy", "ebt_yoy", "netprofit_yoy", "dt_netprofit_yoy",
    "ocf_yoy", "roe_yoy", "bps_yoy", "assets_yoy", "eqt_yoy", "tr_yoy",
    # 规模
    "profit_dedt", "op_income", "ebit", "ebitda", "fcff", "fcfe",
    "total_revenue_ps", "revenue_ps",
    # 费用
    "saleexp_to_gr", "adminexp_of_gr", "finaexp_of_gr", "expense_of_sales",
]


def load_one(c6: str, fund_dir: Path) -> pd.DataFrame | None:
    """载入并清洗单只财务数据。"""
    p = fund_dir / f"{c6}.csv"
    if not p.exists():
        return None
    try:
        d = pd.read_csv(p, low_memory=False)
    except Exception:
        return None
    if len(d) == 0 or "ann_date" not in d.columns:
        return None
    d = d.copy()
    # 日期
    for c in ("ann_date", "end_date"):
        d[c] = pd.to_datetime(d[c].astype(str), format="%Y%m%d", errors="coerce")
    d = d.dropna(subset=["ann_date", "end_date"])
    if len(d) == 0:
        return None

    # 同一报告期可能有多条（初次公告 + 修订）
    # 取**最早公告**的那条（避免用修订后的数据 = PIT）
    d = d.sort_values(["end_date", "ann_date"])
    d = d.drop_duplicates(subset=["end_date"], keep="first")
    d = d.sort_values("ann_date").reset_index(drop=True)

    # 只保留需要的字段（Tushare 返回可能有重复列名，需去重）
    keep = [c for c in KEEP_FIELDS if c in d.columns]
    d = d.loc[:, ~d.columns.duplicated()]
    keep = [c for c in keep if c in d.columns]
    if not keep:
        return None
    d = d[["ann_date", "end_date"] + keep].copy()
    # 数值化（逐列，避免重复列名导致的 DataFrame 返回）
    for c in keep:
        s = d[c]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
            d[c] = s
        d[c] = pd.to_numeric(s, errors="coerce")
    return d


def compute_fundamental_factors(d: pd.DataFrame) -> pd.DataFrame:
    """
    基于 PIT 财务数据计算因子。

    所有指标均只依赖**该期及之前的已公告数据**：
      - 单期指标：直接用该期值
      - 同比：与该期同年同季（4期前）比较（若存在）
      - 趋势：最近 N 期的斜率/变化
    """
    if d is None or len(d) == 0:
        return pd.DataFrame()
    if len(d) < 2:
        return d.copy()

    o = d.copy()
    # 去重列名（Tushare 偶有重复）
    o = o.loc[:, ~o.columns.duplicated()]
    # 保护日期列：重新解析为 datetime（防止被数值化破坏）
    for dc in ("ann_date", "end_date"):
        if dc in o.columns:
            o[dc] = pd.to_datetime(o[dc], errors="coerce")
    o = o.dropna(subset=["ann_date"])

    def col(name):
        """安全取列，重复名时取第一个。"""
        s = o[name]
        return s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s

    # ---- ROE 等（保留原始值；fina_indicator 为累计口径）
    for src, dst in [("roe", "f_roe_ann"), ("roe_dt", "f_roe_dt_ann"),
                     ("roa", "f_roa_ann"), ("roic", "f_roic_ann")]:
        if src in o.columns:
            o[dst] = pd.to_numeric(col(src), errors="coerce")

    # ---- 同比变化（pp）
    for c in ("grossprofit_margin", "netprofit_margin", "op_of_gr"):
        if c in o.columns:
            v = pd.to_numeric(col(c), errors="coerce")
            o[f"{c}_chg"] = v - v.shift(4)

    # ---- 应计项目
    if "eps" in o.columns and "ocfps" in o.columns and "bps" in o.columns:
        eps = pd.to_numeric(col("eps"), errors="coerce")
        ocf = pd.to_numeric(col("ocfps"), errors="coerce")
        bps = pd.to_numeric(col("bps"), errors="coerce").replace(0, np.nan)
        o["f_accrual_ps"] = (eps - ocf) / bps

    # ---- 现金流质量
    if "ocfps" in o.columns and "eps" in o.columns:
        eps = pd.to_numeric(col("eps"), errors="coerce").replace(0, np.nan)
        ocf = pd.to_numeric(col("ocfps"), errors="coerce")
        o["f_ocf_to_ni"] = ocf / eps

    # ---- 盈利稳定性
    if "roe" in o.columns:
        v = pd.to_numeric(col("roe"), errors="coerce")
        o["f_roe_std_8"] = v.rolling(8, min_periods=4).std()
        o["f_roe_mean_8"] = v.rolling(8, min_periods=4).mean()
        o["f_roe_min_8"] = v.rolling(8, min_periods=4).min()
        sd = o["f_roe_std_8"].replace(0, np.nan)
        o["f_roe_stable"] = o["f_roe_mean_8"] / sd

    # ---- 增长稳定性
    for c in ("or_yoy", "netprofit_yoy"):
        if c in o.columns:
            v = pd.to_numeric(col(c), errors="coerce")
            o[f"{c}_std_8"] = v.rolling(8, min_periods=4).std()
            o[f"{c}_mean_8"] = v.rolling(8, min_periods=4).mean()

    # ---- 毛利率趋势
    if "grossprofit_margin" in o.columns:
        v = pd.to_numeric(col("grossprofit_margin"), errors="coerce")
        o["f_gm_trend"] = v - v.shift(4)

    # ---- 负债率变化
    if "debt_to_assets" in o.columns:
        v = pd.to_numeric(col("debt_to_assets"), errors="coerce")
        o["f_debt_chg"] = v - v.shift(4)

    # ---- 费用率
    if "expense_of_sales" in o.columns:
        o["f_expense_ratio"] = pd.to_numeric(col("expense_of_sales"), errors="coerce")

    # ---- 规模代理
    if "bps" in o.columns:
        v = pd.to_numeric(col("bps"), errors="coerce").clip(lower=0)
        o["f_log_bps"] = np.log1p(v)

    # ---- ROE 同比（用 roe_yoy）
    if "roe_yoy" in o.columns:
        o["f_roe_yoy"] = pd.to_numeric(col("roe_yoy"), errors="coerce")

    # ---- 确保所有 FUND_FACTORS 列存在（缺失补 NaN）
    for f in FUND_FACTORS:
        if f not in o.columns:
            o[f] = np.nan

    return o


# 用于测试的基本面因子（横截面排名用）
FUND_FACTORS = [
    # --- 盈利质量
    "f_roe_ann",          # ROE（年化）
    "f_roe_dt_ann",       # 扣非ROE
    "f_roa_ann",          # ROA
    "f_roic_ann",         # ROIC
    "grossprofit_margin", # 毛利率
    "netprofit_margin",   # 净利率
    "f_roe_stable",       # ROE稳定性（均值/标准差）
    "f_roe_std_8",        # ROE波动（越低越好）
    # --- 成长
    "or_yoy",             # 营收增速
    "netprofit_yoy",      # 净利增速
    "dt_netprofit_yoy",   # 扣非净利增速
    "op_yoy",             # 营业利润增速
    "f_or_yoy_mean_8",    # 8期平均营收增速
    "f_netprofit_yoy_mean_8",
    # --- 现金流质量
    "f_ocf_to_ni",        # 经营现金流/净利润
    "ocf_to_or",          # 经营现金流/营收
    "ocf_to_assets",      # 经营现金流/资产
    "ocf_yoy",            # 现金流增速
    # --- 应计（反向）
    "f_accrual_ps",       # 每股应计（越低越好）
    # --- 杠杆/安全
    "debt_to_assets",     # 资产负债率
    "current_ratio",      # 流动比率
    "quick_ratio",        # 速动比率
    "f_debt_chg",         # 负债率变化
    # --- 趋势
    "f_gm_trend",         # 毛利率趋势
    "f_roe_yoy",          # ROE同比变化（从roe_yoy）
    # --- 周转
    "assets_turn",        # 总资产周转
    "ar_turn",            # 应收周转
    # --- 规模
    "f_log_bps",          # BPS对数
]


def build_pit_frame(codes: list, fund_dir: Path,
                    max_workers: int = 1) -> dict:
    """
    为全部股票构建 PIT 财务因子表。

    Returns {code6: DataFrame(ann_date, end_date, <factors>)}
    """
    out = {}
    n = len(codes)
    for i, c6 in enumerate(codes):
        d = load_one(c6, fund_dir)
        if d is None:
            continue
        d = compute_fundamental_factors(d)
        if d is None or len(d) == 0:
            continue
        d["code"] = c6
        out[c6] = d
        if (i + 1) % 500 == 0:
            LOG.info(f"  PIT 载入 {i+1}/{n}  有效 {len(out)}")
    return out


def attach_pit_to_daily(daily: pd.DataFrame, pit: pd.DataFrame,
                        factor_cols: list) -> pd.DataFrame:
    """
    把 PIT 财务因子**按公告日**合并到日线。

    关键：对每个交易日 t，取 ann_date <= t 的**最近一期**财务数据。
    这保证无未来函数。

    Parameters
    ----------
    daily : DataFrame with 'date'
    pit : DataFrame with 'ann_date' + factor_cols
    factor_cols : list of str
        要合并的因子列

    Returns
    -------
    daily 附加了 f_* 列
    """
    if pit is None or len(pit) == 0:
        for c in factor_cols:
            daily[c] = np.nan
        return daily

    cols = [c for c in factor_cols if c in pit.columns]
    if not cols:
        for c in factor_cols:
            daily[c] = np.nan
        return daily

    # merge_asof：按 ann_date 对齐（向后取最近一期）
    left = daily[["date"]].copy().sort_values("date")
    left["_k"] = 0
    right = pit[["ann_date"] + cols].copy().sort_values("ann_date")
    right = right.dropna(subset=["ann_date"])
    if len(right) == 0:
        for c in cols:
            daily[c] = np.nan
        return daily

    merged = pd.merge_asof(left, right, left_on="date", right_on="ann_date",
                           direction="backward")
    merged = merged.drop_duplicates(subset=["date"], keep="last")
    m = dict(zip(merged["date"], [tuple(x) for x in merged[cols].values]))
    for i, c in enumerate(cols):
        daily[c] = daily["date"].map(lambda x, i=i: m.get(x, (None,) * len(cols))[i])
    return daily
