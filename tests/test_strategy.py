# -*- coding: utf-8 -*-
"""策略与成本模型单元测试（离线）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tushare_trader.costs import CostModel  # noqa: E402
from tushare_trader.strategy import (  # noqa: E402
    BollBandStrategy, compute_indicators, market_return_series,
)


# ---------------------------------------------------------------------------
# 成本模型
# ---------------------------------------------------------------------------
def test_cost_rates():
    c = CostModel(commission=0.00025, stamp_tax=0.0005, slippage=0.00075, min_commission=5)
    assert c.buy_rate == pytest.approx(0.001)
    assert c.sell_rate == pytest.approx(0.0015)
    assert c.round_trip == pytest.approx(0.0025)


def test_min_commission_applies():
    c = CostModel(commission=0.00025, stamp_tax=0.0005, slippage=0.00075, min_commission=5)
    assert c.buy_cost(1000) == pytest.approx(5 + 1000 * 0.00075)   # 小额按最低佣金
    assert c.sell_cost(1_000_000) == pytest.approx(
        1_000_000 * (0.00025 + 0.0005 + 0.00075))


def test_affordability_respects_fees():
    c = CostModel()
    shares = c.affordability(10.0, 10_000)
    assert shares % 100 == 0
    notional = shares * 10.0
    assert notional + c.buy_cost(notional) <= 10_000
    # 再多一手就超预算
    assert (notional + 100 * 10.0) + c.buy_cost(notional + 1000) > 10_000


def test_affordability_edge_cases():
    c = CostModel()
    assert c.affordability(0, 1000) == 0
    assert c.affordability(10, 0) == 0
    assert c.affordability(1000, 500) == 0


# ---------------------------------------------------------------------------
# 指标集成
# ---------------------------------------------------------------------------
def test_compute_indicators_columns(cfg, synthetic_bars):
    df = synthetic_bars(300)
    d = compute_indicators(df, cfg.strategy_cfg)
    for col in ("bb_mid", "bb_upper", "bb_lower", "ma60", "ma60_slope",
                "pos120", "lower_streak", "atr14", "amount_ma20",
                "cond_position", "cond_streak", "cond_ma60_down", "signal"):
        assert col in d.columns, col
    assert len(d) == len(df)
    assert d["signal"].dtype == bool or d["signal"].isin([True, False]).all()


def test_conditions_are_boolean(cfg, synthetic_bars):
    d = compute_indicators(synthetic_bars(300), cfg.strategy_cfg)
    cond_cols = [c for c in d.columns if c.startswith("cond_")]
    assert cond_cols
    for c in cond_cols:
        assert d[c].dtype == bool, c


def test_market_filter_feeds_through(cfg, synthetic_bars):
    """市场择时列必须来自传入的指数收益序列，且严格滞后对齐。"""
    df = synthetic_bars(300)
    idx = pd.Series(np.linspace(100, 60, len(df)), index=df["date"])
    mret = market_return_series(idx, 60)
    d = compute_indicators(df, cfg.strategy_cfg, calendar=list(df["date"]),
                           market_ret=mret)
    # 任一行的 market_ret 必须等于该行日期对应的指数收益
    row = d.iloc[-1]
    assert row["market_ret"] == pytest.approx(mret.loc[row["date"]])


def test_market_ok_threshold(cfg):
    s = BollBandStrategy(cfg.strategy_cfg)
    idx = pd.Series([100.0, 90.0, 70.0],
                    index=pd.to_datetime(["2024-01-02", "2024-02-02", "2024-03-02"]))
    mret = market_return_series(idx, 2)          # 70/100 - 1 = -30%
    passed, cur = s.market_ok(mret)
    assert cur == pytest.approx(-30.0)
    assert passed is True

    idx2 = pd.Series([100.0, 99.0, 98.0],
                     index=pd.to_datetime(["2024-01-02", "2024-02-02", "2024-03-02"]))
    mret2 = market_return_series(idx2, 2)
    passed2, _ = s.market_ok(mret2)
    assert passed2 is False


def test_market_ok_without_index_is_permissive(cfg):
    s = BollBandStrategy(cfg.strategy_cfg)
    passed, cur = s.market_ok(None)
    assert passed is True
    assert cur is None


# ---------------------------------------------------------------------------
# 出场判定与止损
# ---------------------------------------------------------------------------
def test_exit_stop_triggers(cfg):
    s = BollBandStrategy(cfg.strategy_cfg)
    bar = pd.Series({"close": 8.0, "bb_upper": 12.0, "bb_mid": 10.0, "atr14": 0.5, "low": 8.0})
    pos = {"entry_price": 10.0, "stop_price": 8.8, "shares": 100}
    assert s.exit_reason(bar, pos, held_days=1) == "stop"


def test_exit_take_profit_triggers(cfg):
    s = BollBandStrategy(cfg.strategy_cfg)
    bar = pd.Series({"close": 13.0, "bb_upper": 12.0, "bb_mid": 10.0, "atr14": 0.5, "low": 9.0})
    pos = {"entry_price": 10.0, "stop_price": 8.8, "shares": 100}
    assert s.exit_reason(bar, pos, held_days=1) == "take_profit"


def test_exit_timeout(cfg):
    s = BollBandStrategy(cfg.strategy_cfg)
    s.max_hold = 5
    bar = pd.Series({"close": 10.2, "bb_upper": 20.0, "bb_mid": 15.0, "atr14": 0.5, "low": 10.0})
    pos = {"entry_price": 10.0, "stop_price": 8.8, "shares": 100}
    assert s.exit_reason(bar, pos, held_days=5) == "timeout"
    assert s.exit_reason(bar, pos, held_days=4) is None


def test_exit_priority_stop_before_take_profit(cfg):
    """同日既触及止损又触及止盈时，止损优先（保守）。"""
    s = BollBandStrategy(cfg.strategy_cfg)
    bar = pd.Series({"close": 8.0, "bb_upper": 7.0, "bb_mid": 6.0, "atr14": 0.5, "low": 8.0})
    pos = {"entry_price": 10.0, "stop_price": 8.8, "shares": 100}
    assert s.exit_reason(bar, pos, held_days=1) == "stop"


def test_initial_stop_modes(cfg):
    cfg_fixed = {"exit": dict(cfg.section("strategy.exit"), stop_mode="fixed",
                              stop_loss_pct=-0.12)}
    s = BollBandStrategy(cfg_fixed)
    bar = pd.Series({"low": 9.0, "atr14": 0.5})
    assert s.initial_stop(10.0, bar) == pytest.approx(8.8)

    cfg_atr = {"exit": dict(cfg.section("strategy.exit"), stop_mode="atr", atr_mult=2.0)}
    s2 = BollBandStrategy(cfg_atr)
    assert s2.initial_stop(10.0, bar) == pytest.approx(9.0)

    cfg_low = {"exit": dict(cfg.section("strategy.exit"), stop_mode="entry_low")}
    s3 = BollBandStrategy(cfg_low)
    assert s3.initial_stop(10.0, bar) == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# 仓位计算
# ---------------------------------------------------------------------------
def test_position_size_equal_weight(cfg):
    s = BollBandStrategy(cfg.strategy_cfg)     # max_positions=5, cap=25%
    shares = s.position_size(equity=1_000_000, cash=1_000_000, n_held=0, price=10.0)
    assert shares == 20_000                    # 200000 / 10，100 股整数倍
    assert shares % 100 == 0


def test_position_size_limited_by_cash_and_cap(cfg):
    s = BollBandStrategy(cfg.strategy_cfg)
    assert s.position_size(1_000_000, cash=50_000, n_held=0, price=10.0) == 5_000
    # 单只上限 25%，即使槽位更大
    s.max_positions = 2
    s.max_position_pct = 0.25
    assert s.position_size(1_000_000, 1_000_000, 0, 10.0) == 25_000


def test_position_size_zero_guard(cfg):
    s = BollBandStrategy(cfg.strategy_cfg)
    assert s.position_size(1_000_000, 1_000_000, 0, 0.0) == 0
    assert s.position_size(1_000_000, 0, 0, 10.0) == 0
