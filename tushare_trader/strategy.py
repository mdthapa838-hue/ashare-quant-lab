# -*- coding: utf-8 -*-
"""
tushare_trader/strategy.py — 布林带下轨反弹策略

策略逻辑（与 `strategy/README.md` 的回测结论一致）：

    在「长期趋势向上 + 中期下跌 + 短期超卖」的位置买入布林带下轨，
    持有至布林带上轨或止损离场。

入场条件（全部满足才出信号）：
  1. 市场择时   指数 N 日收益 < threshold（只在市场急跌时出手）
  2. 相对位置   120 日区间分位 < pos120_max
  3. 短期超卖   连续 >= streak_min 天收盘价 <= 布林带下轨
  4. 中期下跌   MA60 的 N 日斜率 < ma60_slope_max
  5. 长期趋势   月线收盘 > 月线 MA，或月线 MA 向上
  6. 流动性     20 日均成交额 >= min_avg_amount

出场条件（先触发先执行）：
  * 止损       收盘价 <= 入场价 * (1 + stop_loss_pct)  或 ATR/入场日最低价
  * 止盈       收盘价 >= 布林带上轨（或中轨，可配置）
  * 时限       持有 >= max_hold_days 个交易日

无未来函数
----------
所有指标只使用当日及之前的数据；信号在 T 日收盘后生成，T+1 开盘执行。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from . import indicators as ind

LOG = logging.getLogger("tushare_trader.strategy")

# 计算指标需要的最小 K 线数量（MA60 斜率 20 日 + 120 日分位）
MIN_BARS = 160


def compute_indicators(df: pd.DataFrame, cfg: dict,
                       calendar: list | None = None,
                       market_ret: pd.Series | None = None) -> pd.DataFrame:
    """
    为单只股票计算指标与条件列。

    Parameters
    ----------
    df : DataFrame
        原始 K 线（date/open/high/low/close/volume/amount）。
    cfg : dict
        策略配置（含 entry/universe 节）。
    calendar : list, optional
        交易日历，用于月线/周线「周期是否结束」的严格判定。
    market_ret : Series, optional
        指数 N 日收益（按日期索引），用于择时过滤。

    Returns
    -------
    DataFrame（等长，附加指标列与 cond_* 条件列）
    """
    entry = cfg.get("entry", {})
    uni = cfg.get("universe", {})
    out = df.copy().sort_values("date").reset_index(drop=True)

    close, high, low, open_ = out["close"], out["high"], out["low"], out["open"]

    # ---- 布林带
    bb = ind.bollinger(close, entry.get("bb_period", 20), entry.get("bb_std", 2.0))
    out["bb_mid"] = bb["mid"]
    out["bb_upper"] = bb["upper"]
    out["bb_lower"] = bb["lower"]
    out["bb_pct_b"] = bb["pct_b"]

    # ---- 均线与斜率
    ma60 = ind.moving_average(close, 60)
    out["ma60"] = ma60
    out["ma60_slope"] = ind.ma_slope(ma60, entry.get("ma60_slope_window", 20))

    # ---- 相对位置 / 连续触轨
    out["pos120"] = ind.position(close, high, low, 120)
    out["lower_streak"] = ind.streak((close <= out["bb_lower"]).fillna(False))

    # ---- KDJ / ATR（备用条件）
    kk = ind.kdj(high, low, close)
    out["J"] = kk["J"]
    out["atr14"] = ind.atr(high, low, close, 14)

    # ---- 开盘涨跌幅（涨跌停约束）
    out["prev_close"] = close.shift(1)
    out["open_chg_pct"] = (open_ / out["prev_close"] - 1.0) * 100

    # ---- 流动性
    out["amount_ma20"] = out["amount"].rolling(20, min_periods=20).mean()

    # ---- 月线趋势（无未来函数）
    if entry.get("use_monthly_filter", True):
        need = int(entry.get("monthly_ma", 6))
        if len(out) > need * 21:
            m = ind.resample_ma(out.set_index("date")["close"], "ME", need,
                                entry.get("monthly_slope_window", 1), calendar=calendar)
            out["monthly_trend_up"] = m["trend_up"].fillna(False).values.astype(bool)
            out["monthly_slope"] = m["slope"].values
        else:
            out["monthly_trend_up"] = False
            out["monthly_slope"] = np.nan
    else:
        out["monthly_trend_up"] = True
        out["monthly_slope"] = np.nan

    # ---- 周线趋势（默认关闭）
    if entry.get("use_weekly_filter", False):
        w = ind.resample_ma(out.set_index("date")["close"], "W", 20, 4, calendar=calendar)
        out["weekly_trend_up"] = w["trend_up"].fillna(False).values.astype(bool)
    else:
        out["weekly_trend_up"] = True

    # ---- 市场择时
    if entry.get("use_market_filter", True) and market_ret is not None:
        mr = market_ret.reindex(out["date"].values, method="ffill")
        out["market_ret"] = mr.values
    else:
        out["market_ret"] = -999.0

    # ---- 条件列
    out["cond_position"] = (out["pos120"] < float(entry.get("pos120_max", 0.30))).fillna(False)
    out["cond_streak"] = (out["lower_streak"] >= int(entry.get("streak_min", 2))).fillna(False)
    out["cond_ma60_down"] = (
        out["ma60_slope"] < float(entry.get("ma60_slope_max", -0.01))).fillna(False)
    out["cond_monthly_up"] = out["monthly_trend_up"].astype(bool)
    out["cond_market_crash"] = (
        out["market_ret"] < float(entry.get("market_ret_max", -20.0))).fillna(False)
    out["cond_liquidity"] = (
        out["amount_ma20"] >= float(uni.get("min_avg_amount", 2000)) * 1e4).fillna(False)
    if entry.get("use_j_filter", False):
        out["cond_j_low"] = (out["J"] < float(entry.get("j_max", 13))).fillna(False)
    else:
        out["cond_j_low"] = True

    cond_cols = [c for c in out.columns if c.startswith("cond_")]
    out["n_ok"] = out[cond_cols].sum(axis=1).astype(int)
    out["n_tot"] = len(cond_cols)
    out["signal"] = out[cond_cols].all(axis=1).astype(bool)
    return out


def market_return_series(index_close: pd.Series, window: int) -> pd.Series:
    """指数 N 日收益率（%）。"""
    return (index_close / index_close.shift(int(window)) - 1.0) * 100.0


class BollBandStrategy:
    """布林带下轨反弹策略。"""

    def __init__(self, cfg: dict, calendar: list | None = None):
        self.cfg = cfg
        self.entry = cfg.get("entry", {})
        self.exit_cfg = cfg.get("exit", {})
        self.portfolio = cfg.get("portfolio", {})
        self.universe = cfg.get("universe", {})
        self.calendar = calendar or []

        self.pos_max = float(self.entry.get("pos120_max", 0.30))
        self.streak_min = int(self.entry.get("streak_min", 2))
        self.ma60_max = float(self.entry.get("ma60_slope_max", -0.01))
        self.market_win = int(self.entry.get("market_ret_window", 60))
        self.market_max = float(self.entry.get("market_ret_max", -20.0))
        self.market_index = self.entry.get("market_index", "000852.SH")

        self.take_profit = self.exit_cfg.get("take_profit", "upper")
        self.stop_pct = float(self.exit_cfg.get("stop_loss_pct", -0.12))
        self.max_hold = int(self.exit_cfg.get("max_hold_days", 60))
        self.stop_mode = self.exit_cfg.get("stop_mode", "fixed")
        self.atr_mult = float(self.exit_cfg.get("atr_mult", 2.0))

        self.max_positions = int(self.portfolio.get("max_positions", 5))
        self.max_position_pct = float(self.portfolio.get("max_position_pct", 0.25))
        self.initial_capital = float(self.portfolio.get("initial_capital", 1_000_000))

    # ------------------------------------------------------------ 择时
    def market_ok(self, market_ret: pd.Series | None,
                  as_of: pd.Timestamp | None = None) -> tuple[bool, float | None]:
        """市场择时是否通过，返回 (通过?, 当前收益%)。"""
        if not self.entry.get("use_market_filter", True):
            return True, None
        if market_ret is None or len(market_ret) == 0:
            # 无指数数据时保守放行，由日志提示
            LOG.warning("缺少指数数据，择时过滤被跳过")
            return True, None
        s = market_ret.dropna()
        if as_of is not None:
            s = s[s.index <= pd.Timestamp(as_of)]
        if len(s) == 0:
            return False, None
        cur = float(s.iloc[-1])
        return bool(cur < self.market_max), cur

    # ------------------------------------------------------------ 筛选
    def screen(self, data: pd.DataFrame) -> dict | None:
        """
        判定最新一根 K 线是否满足入场条件。

        Returns
        -------
        dict 信号详情，或 None（不满足）
        """
        if data is None or len(data) < MIN_BARS:
            return None
        row = data.iloc[-1]
        cond_cols = [c for c in data.columns if c.startswith("cond_")]
        n_ok = int(sum(bool(row[c]) for c in cond_cols))
        if not bool(row["signal"]):
            return None
        return {
            "code": row.get("code"),
            "signal_date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            "close": round(float(row["close"]), 2),
            "bb_lower": self._round(row["bb_lower"]),
            "bb_mid": self._round(row["bb_mid"]),
            "bb_upper": self._round(row["bb_upper"]),
            "pos120": self._round(row["pos120"], 3),
            "lower_streak": int(row["lower_streak"]),
            "ma60_slope_pct": self._round(float(row["ma60_slope"]) * 100),
            "monthly_up": bool(row["monthly_trend_up"]),
            "market_ret_pct": self._round(row["market_ret"]),
            "n_ok": n_ok,
            "n_tot": len(cond_cols),
            "conditions": f"{n_ok}/{len(cond_cols)}",
        }

    # ------------------------------------------------------------ 出场
    def exit_reason(self, bar: pd.Series, pos: dict,
                    held_days: int) -> str | None:
        """
        判定当前 K 线是否触发离场（使用当日收盘数据）。

        Parameters
        ----------
        bar : Series
            当日指标行（需含 close/bb_upper/bb_mid/atr14）。
        pos : dict
            持仓信息（entry_price/shares/stop_price）。
        held_days : int
            已持有交易日数。

        Returns
        -------
        'stop' / 'take_profit' / 'timeout' / None
        """
        cur = float(bar["close"])
        stop = float(pos.get("stop_price") or
                     pos["entry_price"] * (1 + self.stop_pct))
        if cur <= stop:
            return "stop"

        tgt_col = "bb_upper" if self.take_profit == "upper" else "bb_mid"
        tgt = bar.get(tgt_col)
        if pd.notna(tgt) and cur >= float(tgt):
            return "take_profit"

        if held_days >= self.max_hold:
            return "timeout"
        return None

    def initial_stop(self, entry_price: float, entry_bar: pd.Series) -> float:
        """根据配置计算入场时的止损价。"""
        if self.stop_mode == "entry_low":
            low = entry_bar.get("low")
            if pd.notna(low) and float(low) > 0:
                return float(low)
        elif self.stop_mode == "atr":
            a = entry_bar.get("atr14")
            if pd.notna(a) and float(a) > 0:
                return float(entry_price - self.atr_mult * float(a))
        return float(entry_price * (1 + self.stop_pct))

    # ------------------------------------------------------------ 仓位
    def position_size(self, equity: float, cash: float, n_held: int,
                      price: float, lot: int = 100) -> int:
        """
        等权仓位：每个槽位 = 总权益 / 最大持仓数，且不超过单只上限。

        Returns
        -------
        int  股数（100 的整数倍，可能为 0）
        """
        if price <= 0 or cash <= 0 or self.max_positions <= 0:
            return 0
        per_slot = equity / self.max_positions
        cap = equity * self.max_position_pct
        budget = min(per_slot, cap, cash)
        return int(budget / price / lot) * lot

    @staticmethod
    def _round(v, nd: int = 2):
        try:
            if pd.isna(v):
                return None
            return round(float(v), nd)
        except (TypeError, ValueError):
            return None
