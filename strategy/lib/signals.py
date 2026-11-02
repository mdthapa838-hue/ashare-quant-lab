# -*- coding: utf-8 -*-
"""
signals.py — 信号生成

职责：
  1. 根据配置在单只股票上生成入场信号
  2. 汇总全池信号为信号表
  3. 提供信号详情（用于人工复核）

设计原则：
  - 信号只依赖当日及之前的数据（无未来函数）
  - 入场在信号次日开盘执行（由回测引擎负责）
  - 每个信号携带完整条件快照，便于审计
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.data import normalize_code


# ---------------------------------------------------------------------------
# 单条件判定（返回布尔 Series）
# ---------------------------------------------------------------------------
def cond_position(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """相对位置低于阈值。"""
    thr = cfg["entry"].get("pos120_max", 0.30)
    return df["pos120"] < thr


def cond_streak(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """连续触轨天数达标。"""
    need = cfg["entry"].get("streak_min", 2)
    return df["lower_streak"] >= need


def cond_ma60_down(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """MA60 斜率向下。"""
    thr = cfg["entry"].get("ma60_slope_max", -0.01)
    return df["ma60_slope"] < thr


def cond_monthly_up(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """月线趋势不走跌（长期趋势过滤）。"""
    if not cfg["entry"].get("use_monthly_filter", True):
        return pd.Series(True, index=df.index)
    return df["monthly_trend_up"].fillna(False)


def cond_weekly_up(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """周线趋势不走跌（默认关闭）。"""
    if not cfg["entry"].get("use_weekly_filter", False):
        return pd.Series(True, index=df.index)
    return df["weekly_trend_up"].fillna(False)


def cond_market_crash(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """市场急跌（择时过滤）。"""
    if not cfg["entry"].get("use_market_filter", True):
        return pd.Series(True, index=df.index)
    thr = cfg["entry"].get("market_ret_max", -20.0)
    return df["market_ret"] < thr


def cond_j_low(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """J 值低位（默认关闭，经测试无边际贡献）。"""
    if not cfg["entry"].get("use_j_filter", False):
        return pd.Series(True, index=df.index)
    thr = cfg["entry"].get("j_max", 13)
    return df["J"] < thr


def cond_liquidity(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """成交额流动性达标。"""
    thr_yuan = cfg["universe"].get("min_avg_amount", 2000) * 1e4
    return df["amount_ma20"] >= thr_yuan


# ---------------------------------------------------------------------------
# 信号合成
# ---------------------------------------------------------------------------
CONDITION_MAP = {
    "position": cond_position,
    "streak": cond_streak,
    "ma60_down": cond_ma60_down,
    "monthly_up": cond_monthly_up,
    "weekly_up": cond_weekly_up,
    "market_crash": cond_market_crash,
    "j_low": cond_j_low,
    "liquidity": cond_liquidity,
}


def build_signal_column(df: pd.DataFrame, cfg: dict,
                        active: list[str] | None = None) -> pd.DataFrame:
    """
    为单只股票计算全部条件列与合成信号。

    Returns
    -------
    DataFrame，附加列：
        cond_<name> : 每个条件的布尔结果
        signal      : 所有激活条件同时满足
        signal_score: 满足的条件数（用于排序/诊断）
    """
    if active is None:
        active = ["position", "streak", "ma60_down", "monthly_up",
                  "market_crash", "j_low", "liquidity"]

    out = df.copy()
    cols = []
    for name in active:
        fn = CONDITION_MAP.get(name)
        if fn is None:
            continue
        c = fn(out, cfg).fillna(False).astype(bool)
        out[f"cond_{name}"] = c
        cols.append(f"cond_{name}")

    out["signal_score"] = out[cols].sum(axis=1) if cols else 0
    out["signal"] = out[cols].all(axis=1) if cols else False
    return out


# ---------------------------------------------------------------------------
# 全池信号收集
# ---------------------------------------------------------------------------
def collect_signals(bundle, cfg: dict, verbose: bool = True) -> pd.DataFrame:
    """
    遍历全部标的，收集入场信号。

    Returns
    -------
    DataFrame with columns:
        code, signal_date, entry_date, price_signal, 
        pos120, lower_streak, ma60_slope, market_ret, monthly_slope,
        bb_lower, bb_mid, bb_upper, atr14
    无未来函数：entry_date = 信号日的下一个交易日。
    """
    rows = []
    codes = bundle.codes()
    for i, code in enumerate(codes):
        df = bundle.get(code)
        if df is None or len(df) < 130:
            continue
        d = build_signal_column(df, cfg)
        idxs = np.where(d["signal"].values)[0]
        for k in idxs:
            if k + 1 >= len(d):
                continue
            r = d.iloc[k]
            nxt = d.iloc[k + 1]
            rows.append({
                "code": code,
                "signal_date": r["date"],
                "entry_date": nxt["date"],
                "close_signal": r["close"],
                "open_entry": nxt["open"],
                "open_chg": nxt.get("open_chg_pct", np.nan),
                "pos120": r["pos120"],
                "lower_streak": int(r["lower_streak"]),
                "ma60_slope": r["ma60_slope"],
                "monthly_slope": r.get("monthly_slope", np.nan),
                "market_ret": r.get("market_ret", np.nan),
                "J": r.get("J", np.nan),
                "bb_lower": r["bb_lower"],
                "bb_mid": r["bb_mid"],
                "bb_upper": r["bb_upper"],
                "atr14": r.get("atr14", np.nan),
                "signal_idx": int(k),
            })
        if verbose and (i + 1) % 100 == 0:
            print(f"  扫描 {i+1}/{len(codes)}  累计信号 {len(rows)}", flush=True)

    sig = pd.DataFrame(rows)
    if len(sig):
        sig["code"] = sig["code"].map(normalize_code)
        sig = sig.sort_values(["signal_date", "code"]).reset_index(drop=True)
    return sig


def signal_detail(bundle, cfg: dict, code: str, date) -> pd.DataFrame:
    """
    返回某只股票在指定日期前后若干行的完整条件快照（用于人工复核）。
    """
    df = bundle.get(code)
    if df is None:
        return pd.DataFrame()
    d = build_signal_column(df, cfg)
    target = pd.Timestamp(date)
    pos = d.index[d["date"] == target]
    if len(pos) == 0:
        return pd.DataFrame()
    i = pos[0]
    lo = max(0, i - 3)
    hi = min(len(d), i + 4)
    keep = ["date", "close", "bb_lower", "bb_mid", "bb_upper", "pos120",
            "lower_streak", "ma60_slope", "monthly_slope", "monthly_trend_up",
            "market_ret", "J", "signal"] + [c for c in d.columns if c.startswith("cond_")]
    keep = [c for c in keep if c in d.columns]
    return d.iloc[lo:hi][keep].copy()
