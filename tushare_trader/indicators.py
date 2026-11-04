# -*- coding: utf-8 -*-
"""
tushare_trader/indicators.py — 技术指标（系统内自包含副本）

设计说明
--------
这些函数与仓库内 `strategy/lib/indicators.py`（研究/回测版）的算法保持一致，
但本包不依赖 `strategy/` 目录，保证交易系统可以独立部署（`pip install` 后
只依赖 pandas/numpy 即可运行）。

一致性由 `tests/test_indicators.py::test_parity_with_research_lib` 保证：
研究库改动而本副本未同步时，测试会失败。

指标清单：
  bollinger()      布林带（中轨/上轨/下轨/带宽/%B）
  moving_average() 简单移动平均
  ma_slope()       均线斜率（百分比变化）
  kdj()            KDJ 随机指标
  atr()            平均真实波幅
  position()       区间相对位置分位
  streak()         连续满足条件的天数
  resample_ma()    周线/月线均线（重采样，严格无未来函数）
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def moving_average(close: pd.Series, window: int) -> pd.Series:
    """简单移动平均。"""
    return close.rolling(window=window, min_periods=window).mean()


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """
    布林带。

    Returns
    -------
    DataFrame with columns: mid, upper, lower, bandwidth, pct_b
    """
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    denom = (upper - lower).replace(0, np.nan)
    pct_b = (close - lower) / denom
    return pd.DataFrame({
        "mid": mid, "upper": upper, "lower": lower,
        "bandwidth": bandwidth, "pct_b": pct_b,
    })


def ma_slope(ma: pd.Series, window: int = 20) -> pd.Series:
    """均线斜率：当前值相对 window 期前的百分比变化。"""
    return ma / ma.shift(window) - 1.0


def kdj(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 9, k_smooth: int = 3, d_smooth: int = 3) -> pd.DataFrame:
    """KDJ 随机指标。RSV -> K -> D -> J = 3K - 2D。"""
    llv = low.rolling(n, min_periods=n).min()
    hhv = high.rolling(n, min_periods=n).max()
    rng = (hhv - llv).replace(0, np.nan)
    rsv = ((close - llv) / rng * 100).fillna(50.0)
    k = rsv.ewm(alpha=1.0 / k_smooth, adjust=False).mean()
    d = k.ewm(alpha=1.0 / d_smooth, adjust=False).mean()
    j = 3 * k - 2 * d
    return pd.DataFrame({"K": k, "D": d, "J": j})


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """平均真实波幅 (ATR)。"""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def position(close: pd.Series, high: pd.Series, low: pd.Series, window: int = 120) -> pd.Series:
    """
    区间相对位置分位。

    pos = (close - LLV(low, window)) / (HHV(high, window) - LLV(low, window))
    0 = 区间最低，1 = 区间最高。
    """
    llv = low.rolling(window, min_periods=window).min()
    hhv = high.rolling(window, min_periods=window).max()
    rng = (hhv - llv).replace(0, np.nan)
    return (close - llv) / rng


def streak(condition: pd.Series) -> pd.Series:
    """
    连续满足条件的计数：由 False 变 True 时从 1 重新计数，False 记 0。
    """
    cond = condition.fillna(False).astype(bool)
    groups = (cond != cond.shift()).cumsum()
    return cond.groupby(groups).cumsum().astype(int)


def resample_ma(close: pd.Series, freq: str, ma_window: int,
                slope_window: int = 1, calendar=None) -> pd.DataFrame:
    """
    周线/月线均线及其斜率，再映射回原日线索引（**严格无未来函数**）。

    关键设计
    --------
    1) 未结束的周期其「周期末收盘」尚不存在，不可参与均线计算。
       对日线日期 t：仅在 t 已到达该周期最后一个交易日时，才使用当前周期；
       否则退回上一个已完成周期。
    2) 「周期是否结束」必须由**交易日历**判定，不能用数据末行判定。
       否则数据截断到某月中间时，会把部分月收盘误当成完整月收盘。

    Returns
    -------
    DataFrame indexed like `close` with columns: ma, slope, trend_up, period_close
        trend_up = (参考周期收盘 > 均线) | (均线斜率 > 0)
    """
    PERIOD = "M" if str(freq).upper().startswith("M") else "W"
    idx = pd.DatetimeIndex(close.index)
    period_key = idx.to_period(PERIOD)

    period_close = close.groupby(period_key).last()

    if calendar is not None and len(calendar):
        cal = pd.DatetimeIndex(pd.to_datetime(list(calendar))).unique().sort_values()
        cpk = cal.to_period(PERIOD)
        period_last_day = pd.Series(cal.values, index=cpk).groupby(level=0).max()
    else:
        tmp = pd.DataFrame({"d": idx, "p": period_key})
        period_last_day = tmp.groupby("p")["d"].max()

    pld = pd.Series(period_last_day.reindex(period_key).values, index=idx)
    at_period_end = pd.Series(
        pd.to_datetime(idx.values) >= pd.to_datetime(pld.values), index=idx)

    ref_key = pd.Series(period_key, index=idx)
    prev_key = pd.Series((period_key - 1).values, index=idx)
    ref_key = ref_key.where(at_period_end.values, prev_key.values)

    pma = period_close.rolling(ma_window, min_periods=ma_window).mean()
    pslope = pma / pma.shift(slope_window) - 1.0

    ok = ref_key.isin(period_close.index)
    mapped_ma = pd.Series(np.nan, index=idx)
    mapped_slope = pd.Series(np.nan, index=idx)
    mapped_pc = pd.Series(np.nan, index=idx)
    mapped_ma.loc[ok.values] = pma.reindex(ref_key[ok.values].values).values
    mapped_slope.loc[ok.values] = pslope.reindex(ref_key[ok.values].values).values
    mapped_pc.loc[ok.values] = period_close.reindex(ref_key[ok.values].values).values

    ptrend = ((mapped_pc > mapped_ma) | (mapped_slope > 0)).fillna(False).astype(bool)

    return pd.DataFrame({
        "ma": mapped_ma.values,
        "slope": mapped_slope.values,
        "trend_up": ptrend.values,
        "period_close": mapped_pc.values,
    }, index=close.index)
