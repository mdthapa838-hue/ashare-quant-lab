# -*- coding: utf-8 -*-
"""指标计算：正确性 + 与研究库 strategy/lib/indicators.py 的一致性。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tushare_trader import indicators as t_ind  # noqa: E402


def test_bollinger_known_values():
    close = pd.Series([10.0] * 19 + [11.0])
    bb = t_ind.bollinger(close, period=20, num_std=2.0)
    assert bb["mid"].iloc[-1] == pytest.approx(10.05)
    # 总体标准差 ddof=0
    std = np.std([10.0] * 19 + [11.0])
    assert bb["upper"].iloc[-1] == pytest.approx(10.05 + 2 * std)
    assert bb["lower"].iloc[-1] == pytest.approx(10.05 - 2 * std)


def test_bollinger_needs_full_window():
    close = pd.Series(np.arange(1, 11, dtype=float))
    bb = t_ind.bollinger(close, period=20)
    assert bb["mid"].isna().all()


def test_streak_counts_reset():
    cond = pd.Series([False, True, True, False, True, True, True])
    assert t_ind.streak(cond).tolist() == [0, 1, 2, 0, 1, 2, 3]


def test_position_bounds():
    close = pd.Series([5.0, 6.0, 7.0, 8.0, 9.0])
    high = close + 0.5
    low = close - 0.5
    pos = t_ind.position(close, high, low, window=5)
    # 窗口区间 [4.5, 9.5]，收 9.0 -> (9.0-4.5)/(9.5-4.5) = 0.9
    assert pos.iloc[-1] == pytest.approx(0.9)
    assert pd.isna(pos.iloc[3])                    # 窗口未满

    # 收在区间最低/最高时分别趋近 0 / 1
    c2 = pd.Series([9.5, 8.0, 7.0, 6.0, 5.0])
    h2 = pd.Series([9.5, 8.0, 7.0, 6.0, 5.5])
    l2 = pd.Series([9.0, 8.0, 7.0, 6.0, 4.5])
    p2 = t_ind.position(c2, h2, l2, window=5)
    assert p2.iloc[-1] == pytest.approx(0.1)


def test_ma_slope():
    ma = pd.Series([100.0, 101.0, 102.0, 103.0])
    slope = t_ind.ma_slope(ma, window=2)
    assert pd.isna(slope.iloc[1])
    assert slope.iloc[3] == pytest.approx(103.0 / 101.0 - 1)


def test_atr_positive(synthetic_bars):
    df = synthetic_bars(120)
    a = t_ind.atr(df["high"], df["low"], df["close"], 14)
    assert a.dropna().gt(0).all()


def test_kdj_range(synthetic_bars):
    df = synthetic_bars(120)
    k = t_ind.kdj(df["high"], df["low"], df["close"])
    assert k["K"].dropna().between(-5, 105).all()


def test_resample_ma_no_lookahead():
    """月线趋势在数据截断处不得使用未结束月份的数据。"""
    dates = pd.bdate_range("2020-01-01", periods=400)
    close = pd.Series(np.linspace(10, 20, len(dates)), index=dates)
    full = t_ind.resample_ma(close, "ME", 6, 1, calendar=list(dates))
    cut = close.iloc[:300]
    partial = t_ind.resample_ma(cut, "ME", 6, 1, calendar=list(dates))

    # 截断前最后一个日期在两种计算中必须一致（日历相同 -> 结果相同）
    last = cut.index[-1]
    assert full.loc[last, "trend_up"] == partial.loc[last, "trend_up"]


def test_parity_with_research_lib(synthetic_bars):
    """与研究库 indicators.py 的输出必须逐值一致（防止两份实现漂移）。"""
    try:
        sys.path.insert(0, str(ROOT / "strategy"))
        from lib import indicators as r_ind  # type: ignore
    except Exception as e:  # pragma: no cover
        pytest.skip(f"研究库不可导入: {e}")

    df = synthetic_bars(300)
    for period, std in ((20, 2.0), (10, 1.5)):
        a = t_ind.bollinger(df["close"], period, std)
        b = r_ind.bollinger(df["close"], period, std)
        pd.testing.assert_frame_equal(a, b)

    pd.testing.assert_series_equal(
        t_ind.atr(df["high"], df["low"], df["close"], 14),
        r_ind.atr(df["high"], df["low"], df["close"], 14))
    pd.testing.assert_series_equal(
        t_ind.position(df["close"], df["high"], df["low"], 120),
        r_ind.position(df["close"], df["high"], df["low"], 120))
    pd.testing.assert_series_equal(
        t_ind.streak((df["close"] <= t_ind.bollinger(df["close"])["lower"]).fillna(False)),
        r_ind.streak((df["close"] <= r_ind.bollinger(df["close"])["lower"]).fillna(False)))

    close_idx = df.set_index("date")["close"]
    cal = list(df["date"])
    a = t_ind.resample_ma(close_idx, "ME", 6, 1, calendar=cal)
    b = r_ind.resample_ma(close_idx, "ME", 6, 1, calendar=cal)
    pd.testing.assert_frame_equal(a, b)
