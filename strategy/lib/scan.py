# -*- coding: utf-8 -*-
"""
scan.py — 实时信号扫描

用途：
  每日收盘后运行，输出当前符合入场条件的标的清单。

输出内容：
  1. 今日信号清单（含完整条件快照）
  2. 持仓监控清单（已持仓的出场状态）
  3. 观察清单（接近满足条件的标的）
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.data import DataBundle, normalize_code
from lib.signals import build_signal_column, signal_detail, CONDITION_MAP


def scan_today(bundle: DataBundle, cfg: dict, as_of=None,
               near_threshold: float = 0.8) -> dict:
    """
    扫描最新交易日的信号。

    Parameters
    ----------
    bundle : DataBundle
    cfg : dict
    as_of : str/date, optional
        指定截止日期（默认最新）。
    near_threshold : float
        观察清单阈值：满足条件比例 >= 该值进入观察池。

    Returns
    -------
    dict with keys: signals, near, summary
    """
    active = ["position", "streak", "ma60_down", "monthly_up",
              "market_crash", "j_low", "liquidity"]
    # 只统计真正启用的条件（关闭的条件不参与计数，避免"全通过"的误导）
    disabled = set()
    if not cfg["entry"].get("use_market_filter", True):
        disabled.add("cond_market_crash")
    if not cfg["entry"].get("use_monthly_filter", True):
        disabled.add("cond_monthly_up")
    if not cfg["entry"].get("use_weekly_filter", False):
        disabled.add("cond_weekly_up")
    if not cfg["entry"].get("use_j_filter", False):
        disabled.add("cond_j_low")

    rows, near_rows = [], []
    cutoff = pd.Timestamp(as_of) if as_of else None

    market_ok = True
    if cfg["entry"].get("use_market_filter", True):
        mr = bundle.market_ret
        if mr is not None and len(mr):
            mval = mr.iloc[-1] if cutoff is None else mr.reindex(
                [x for x in mr.index if x <= cutoff])[-1]
            thr = cfg["entry"].get("market_ret_max", -20.0)
            market_ok = bool(mval < thr)

    for code in bundle.codes():
        df = bundle.get(code)
        if df is None or len(df) < 130:
            continue
        d = build_signal_column(df, cfg)
        if cutoff is not None:
            d = d[d["date"] <= cutoff]
        if len(d) == 0:
            continue
        r = d.iloc[-1]
        cond_cols = [c for c in d.columns
                     if c.startswith("cond_") and c not in disabled]
        n_ok = int(r[cond_cols].sum())
        n_tot = len(cond_cols)
        rec = {
            "code": code,
            "date": r["date"].strftime("%Y-%m-%d"),
            "close": round(float(r["close"]), 2),
            "bb_lower": round(float(r["bb_lower"]), 2) if pd.notna(r["bb_lower"]) else None,
            "bb_mid": round(float(r["bb_mid"]), 2) if pd.notna(r["bb_mid"]) else None,
            "bb_upper": round(float(r["bb_upper"]), 2) if pd.notna(r["bb_upper"]) else None,
            "dist_to_lower%": round(float(r["close"] / r["bb_lower"] - 1) * 100, 2)
            if pd.notna(r["bb_lower"]) and r["bb_lower"] > 0 else None,
            "dist_to_mid%": round(float(r["bb_mid"] / r["close"] - 1) * 100, 2)
            if pd.notna(r["bb_mid"]) else None,
            "pos120": round(float(r["pos120"]), 3) if pd.notna(r["pos120"]) else None,
            "lower_streak": int(r["lower_streak"]),
            "ma60_slope%": round(float(r["ma60_slope"]) * 100, 2) if pd.notna(r["ma60_slope"]) else None,
            "monthly_up": bool(r.get("monthly_trend_up", False)),
            "J": round(float(r["J"]), 1) if pd.notna(r.get("J")) else None,
            "market_ret%": round(float(r.get("market_ret", np.nan)), 1)
            if pd.notna(r.get("market_ret")) else None,
            "atr14": round(float(r["atr14"]), 3) if pd.notna(r.get("atr14")) else None,
            "conditions_ok": f"{n_ok}/{n_tot}",
            "n_ok": n_ok, "n_tot": n_tot,
        }
        for c in cond_cols:
            rec[c.replace("cond_", "ok_")] = bool(r[c])
        if bool(r["signal"]):
            rows.append(rec)
        elif n_ok >= n_tot * near_threshold:
            near_rows.append(rec)

    sig = pd.DataFrame(rows)
    near = pd.DataFrame(near_rows)
    if len(near):
        near = near.sort_values("n_ok", ascending=False)

    summary = {
        "扫描日期": (cutoff or pd.Timestamp.now()).strftime("%Y-%m-%d")
        if cutoff else "最新",
        "扫描标的数": len(bundle),
        "市场择时通过": market_ok,
        "市场60日收益%": round(float(bundle.market_ret.iloc[-1]), 1)
        if bundle.market_ret is not None and len(bundle.market_ret) else None,
        "完整信号数": len(sig),
        "观察清单数": len(near),
    }
    return {"signals": sig, "near": near, "summary": summary}


def monitor_positions(bundle: DataBundle, cfg: dict,
                      holdings: list[dict], as_of=None) -> pd.DataFrame:
    """
    监控已持仓标的的出场状态。

    Parameters
    ----------
    holdings : list of dict
        每项含 code, entry_price, entry_date, shares(可选)
    """
    ex = cfg.get("exit", {})
    tp = ex.get("take_profit", "upper")
    stop_pct = ex.get("stop_loss_pct", -0.12)
    max_days = ex.get("max_hold_days", 60)

    rows = []
    for h in holdings:
        code = normalize_code(h["code"])
        df = bundle.get(code)
        if df is None:
            rows.append({"code": code, "状态": "无数据"})
            continue
        d = df
        if as_of is not None:
            d = d[d["date"] <= pd.Timestamp(as_of)]
        if len(d) == 0:
            continue
        r = d.iloc[-1]
        ep = float(h["entry_price"])
        ed = pd.Timestamp(h["entry_date"])
        held = len(d[d["date"] > ed])

        cur = float(r["close"])
        tgt = float(r["bb_upper"]) if tp == "upper" else float(r["bb_mid"])
        stop = ep * (1 + stop_pct)

        pnl = cur / ep - 1
        to_tp = tgt / cur - 1
        to_stop = stop / cur - 1

        status = "持有"
        if cur >= tgt:
            status = "★触及止盈，次日开盘卖出"
        elif cur <= stop:
            status = "★触及止损，次日开盘卖出"
        elif held >= max_days:
            status = "★达到时限，次日开盘卖出"

        rows.append({
            "code": code,
            "入场日": ed.strftime("%Y-%m-%d"),
            "入场价": round(ep, 2),
            "现价": round(cur, 2),
            "浮动盈亏%": round(pnl * 100, 2),
            "持有天数": held,
            "剩余天数": max_days - held,
            "止盈价": round(tgt, 2),
            "距止盈%": round(to_tp * 100, 2),
            "止损价": round(stop, 2),
            "距止损%": round(to_stop * 100, 2),
            "状态": status,
        })
    return pd.DataFrame(rows)
