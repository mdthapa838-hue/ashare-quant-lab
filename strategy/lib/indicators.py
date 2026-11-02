# -*- coding: utf-8 -*-
"""
indicators.py — 技术指标计算库

所有指标均为纯函数，输入 DataFrame，输出 Series/DataFrame。
参数显式传入，不使用全局状态。

指标清单：
  bollinger()      布林带（中轨/上轨/下轨/带宽/%B）
  moving_average() 简单移动平均
  ma_slope()       均线斜率（百分比变化）
  kdj()            KDJ 随机指标
  atr()            平均真实波幅
  position()       区间相对位置分位
  streak()         连续满足条件的天数
  resample_ma()    周线/月线均线（重采样）
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
        mid       = MA(period)
        upper     = mid + num_std * std(period)
        lower     = mid - num_std * std(period)
        bandwidth = (upper - lower) / mid
        pct_b     = (close - lower) / (upper - lower)
    """
    mid = close.rolling(period, min_periods=period).mean()
    # 使用总体标准差 (ddof=0)，与常见行情软件一致
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
    """
    均线斜率：当前值相对 window 期前的百分比变化。

    window 单位为「该序列的周期数」。
    日线 MA60 的 window=20 表示比较 20 个交易日前。
    月线 MA6 的 window=1 表示比较上个月。
    """
    return ma / ma.shift(window) - 1.0


def kdj(high: pd.Series, low: pd.Series, close: pd.Series,
        n: int = 9, k_smooth: int = 3, d_smooth: int = 3) -> pd.DataFrame:
    """
    KDJ 随机指标。

    RSV = (C - LLV(L,n)) / (HHV(H,n) - LLV(L,n)) * 100
    K   = SMA(RSV, k_smooth, 1)   等价于 ewm(alpha=1/k_smooth)
    D   = SMA(K, d_smooth, 1)
    J   = 3K - 2D
    """
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
    连续满足条件的计数。

    每次条件由 False 变 True 时重新从 1 计数；
    条件为 False 时记为 0。
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
       反例：数据截断到 2024-02-05 时，若把末行当作月末，
       就会用 2024-02 的部分月收盘（18.67）替代 2024-01 的
       完整月收盘（21.93），导致同一日期在截断/全量下结果不一致。

    Parameters
    ----------
    close : pd.Series
        日线收盘价，索引须为 DatetimeIndex。
    freq : str
        'W' 周线 / 'ME' 月线（pandas 2.x 用 'ME' 表示月末）。
    ma_window : int
        均线周期（周线用 20，月线用 6）。
    slope_window : int
        斜率比较窗口（周期数）。
    calendar : array-like, optional
        交易日历（全部交易日）。提供时用它判定周期末；
        省略时退化为用数据本身判定（仅适用于数据已覆盖全部历史的情形）。

    Returns
    -------
    DataFrame indexed like `close` with columns:
        ma, slope, trend_up, period_close
    trend_up = (参考周期收盘 > 均线) | (均线斜率 > 0)
    """
    PERIOD = "M" if str(freq).upper().startswith("M") else "W"
    idx = pd.DatetimeIndex(close.index)
    period_key = idx.to_period(PERIOD)

    # 每个周期的最后一个交易日的收盘价（仅用于「已完成周期」）
    period_close = close.groupby(period_key).last()

    # ---- 周期末判定：优先用交易日历
    if calendar is not None and len(calendar):
        cal = pd.DatetimeIndex(pd.to_datetime(list(calendar))).unique().sort_values()
        cpk = cal.to_period(PERIOD)
        period_last_day = pd.Series(cal.values, index=cpk).groupby(level=0).max()
    else:
        tmp = pd.DataFrame({"d": idx, "p": period_key})
        period_last_day = tmp.groupby("p")["d"].max()

    pld = pd.Series(period_last_day.reindex(period_key).values, index=idx)
    # t 已到达（或超过）本期最后交易日 → 本期已完成
    at_period_end = pd.Series(
        pd.to_datetime(idx.values) >= pd.to_datetime(pld.values), index=idx)

    # 参考周期：周期末用当前周期，否则退回上一个已完成周期
    ref_key = pd.Series(period_key, index=idx)
    prev_key = pd.Series((period_key - 1).values, index=idx)
    ref_key = ref_key.where(at_period_end.values, prev_key.values)

    # 用「已完成周期」序列算均线与斜率
    pma = period_close.rolling(ma_window, min_periods=ma_window).mean()
    pslope = pma / pma.shift(slope_window) - 1.0

    # 映射回日线
    ok = ref_key.isin(period_close.index)
    mapped_ma = pd.Series(np.nan, index=idx)
    mapped_slope = pd.Series(np.nan, index=idx)
    mapped_pc = pd.Series(np.nan, index=idx)
    mapped_ma.loc[ok.values] = pma.reindex(ref_key[ok.values].values).values
    mapped_slope.loc[ok.values] = pslope.reindex(ref_key[ok.values].values).values
    mapped_pc.loc[ok.values] = period_close.reindex(ref_key[ok.values].values).values

    # trend_up：参考周期收盘在均线上方，或均线本身在上升
    ptrend = (mapped_pc > mapped_ma) | (mapped_slope > 0)
    ptrend = ptrend.fillna(False).astype(bool)

    return pd.DataFrame({
        "ma": mapped_ma.values,
        "slope": mapped_slope.values,
        "trend_up": ptrend.values,
        "period_close": mapped_pc.values,
    }, index=close.index)
