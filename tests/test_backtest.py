# -*- coding: utf-8 -*-
"""回测引擎：成交规则、无未来函数、绩效指标。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tushare_trader.backtest import Backtester, performance  # noqa: E402
from tushare_trader.strategy import compute_indicators  # noqa: E402


def _cfg(**over):
    cfg = {
        "entry": {"pos120_max": 0.30, "bb_period": 20, "bb_std": 2.0,
                  "streak_min": 2, "ma60_slope_window": 20, "ma60_slope_max": -0.01,
                  "use_monthly_filter": False, "use_market_filter": False,
                  "use_weekly_filter": False, "use_j_filter": False},
        "exit": {"take_profit": "upper", "stop_loss_pct": -0.12,
                 "max_hold_days": 60, "stop_mode": "fixed"},
        "portfolio": {"max_positions": 2, "max_position_pct": 0.5,
                      "initial_capital": 100_000},
        "cost": {"commission": 0.00025, "stamp_tax": 0.0005,
                 "slippage": 0.00075, "min_commission": 5.0},
        "risk": {"sector_concentration_max": 2, "daily_max_orders": 20},
        "universe": {"min_avg_amount": 0},
        "constraints": {"no_buy_limit_up": 0.095, "no_sell_limit_down": -0.095},
        "backtest": {"risk_free_rate": 0.02},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = dict(cfg[k], **v)
        else:
            cfg[k] = v
    return cfg


def _series(seed=3, calm=260, crash=14, drop=0.45, bounce=120, rise=0.55,
            start="2022-01-03", scale=1.0):
    """
    构造「平稳上涨 -> 快速暴跌 -> 反弹」的确定性行情。

    快速暴跌（而非缓跌）才能同时满足「收在下轨连续多日」与「MA60 下行」，
    这样测试里的入场/出场路径一定会被走到。
    """
    c1 = np.linspace(20, 24, calm)
    c2 = c1[-1] * np.exp(np.linspace(0, np.log(1 - drop), crash))
    c3 = c2[-1] * np.linspace(1, 1 + rise, bounce)
    close = np.concatenate([c1, c2, c3]) * scale
    dates = pd.bdate_range(start, periods=len(close))
    rng = np.random.default_rng(seed)
    close = close * (1 + rng.normal(0, 0.004, len(close)))
    open_ = close * (1 + rng.normal(0, 0.003, len(close)))
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": np.maximum(open_, close) * 1.010,
        "low": np.minimum(open_, close) * 0.990,
        "close": close,
        "volume": 2e6,
        "amount": close * 2e6,
    })


def _prepared(df, cfg):
    d = compute_indicators(df, cfg, calendar=list(df["date"]))
    d["code"] = "000001.SZ"
    return d


def test_backtest_runs_and_reports_metrics():
    cfg = _cfg()
    df = _prepared(_series(), cfg)
    assert df["signal"].any(), "测试行情必须产生信号，否则回测路径未被覆盖"
    bt = Backtester({"000001.SZ": df}, cfg, index_close=df.set_index("date")["close"])
    res = bt.run()
    assert len(res.equity) == len(bt.calendar)
    assert "总收益%" in res.metrics and "最大回撤%" in res.metrics
    assert res.equity["equity"].gt(0).all()
    assert res.equity["cash"].ge(-1e-6).all()
    assert len(res.trades) > 0, "应产生交易"


def test_no_lookahead_entry_uses_next_open():
    """信号的成交价必须是信号次日开盘价，而不是信号日收盘价。"""
    cfg = _cfg()
    raw = _series()
    df = _prepared(raw, cfg)
    bt = Backtester({"000001.SZ": df}, cfg, index_close=df.set_index("date")["close"])
    res = bt.run()
    assert len(res.trades) > 0, "测试行情必须产生交易"

    sig_idx = {pd.Timestamp(d): i for i, d in enumerate(df["date"])}

    for _, tr in res.trades.iterrows():
        entry_date = pd.Timestamp(tr["entry_date"])
        i = sig_idx[entry_date]
        # (1) 成交价 = 成交日开盘价
        assert tr["entry_price"] == pytest.approx(float(df.iloc[i]["open"]), rel=1e-9)
        # (2) 信号发生在上一交易日
        assert bool(df.iloc[i - 1]["signal"])
        # (3) 成交价绝不等于信号日收盘价（否则就是未来函数）
        assert tr["entry_price"] != pytest.approx(float(df.iloc[i - 1]["close"]))


def test_equity_curve_matches_cash_plus_positions():
    cfg = _cfg()
    df = _prepared(_series(), cfg)
    bt = Backtester({"000001.SZ": df}, cfg, index_close=df.set_index("date")["close"])
    res = bt.run()
    assert res.equity["equity"].ge(res.equity["cash"] - 1e-6).all()


def test_max_positions_respected():
    cfg = _cfg(portfolio={"max_positions": 2, "max_position_pct": 0.5,
                          "initial_capital": 100_000})
    frames = {}
    for i, seed in enumerate(["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"]):
        raw = _series(start="2023-01-03", scale=1 + i * 0.01)
        d = _prepared(raw, cfg)
        d["code"] = seed
        frames[seed] = d
    cal = frames["000001.SZ"].set_index("date")["close"]
    bt = Backtester(frames, cfg, index_close=cal)
    res = bt.run()
    assert res.equity["n_positions"].max() <= 2


def test_limit_up_blocks_entry():
    """开盘涨停（>9.5%）不得成交。

    注意 open_chg_pct 是「当日开盘 / 上一交易日收盘」，所以人为抬高开盘价时
    必须相对「前一交易日收盘」计算，否则不会真的越过涨停阈值。
    """
    cfg = _cfg()
    raw = _series()
    df = _prepared(raw, cfg)
    sig_rows = df.index[df["signal"]].tolist()
    assert sig_rows, "测试行情必须产生信号"

    # 让每个信号日的次日以 +10% 开盘
    for k in sig_rows:
        nxt = k + 1
        if nxt < len(df):
            raw.loc[nxt, "open"] = raw.loc[nxt - 1, "close"] * 1.10

    df = _prepared(raw, cfg)
    blocked = {pd.Timestamp(d) for d in df.loc[df["open_chg_pct"] > 9.5, "date"]}
    assert blocked, "构造失败：没有任何一天越过涨停阈值"

    bt = Backtester({"000001.SZ": df}, cfg, index_close=df.set_index("date")["close"])
    res = bt.run()
    assert len(res.trades) == 0, "涨停日不应成交（信号次日全部涨停）"


def test_costs_reduce_returns():
    """同一行情，含成本收益必须低于零成本。"""
    no_cost = _cfg(cost={"commission": 0, "stamp_tax": 0, "slippage": 0, "min_commission": 0})
    with_cost = _cfg()
    raw = _series()
    res = {}
    for label, c in (("free", no_cost), ("cost", with_cost)):
        df = _prepared(raw.copy(), c)
        bt = Backtester({"000001.SZ": df}, c, index_close=df.set_index("date")["close"])
        res[label] = bt.run()
    assert res["free"].metrics.get("交易笔数", 0) > 0, "测试行情必须产生交易"
    assert res["cost"].metrics["总收益%"] <= res["free"].metrics["总收益%"]


def test_performance_empty_equity():
    m = performance(pd.DataFrame(), pd.DataFrame(), _cfg())
    assert "错误" in m


def test_performance_metrics_are_finite():
    cfg = _cfg()
    df = _prepared(_series(), cfg)
    bt = Backtester({"000001.SZ": df}, cfg, index_close=df.set_index("date")["close"])
    m = bt.run().metrics
    for key in ("CAGR%", "最大回撤%", "Sharpe", "年化波动%"):
        assert key in m
        if m[key] is not None:
            assert np.isfinite(m[key]), key


def test_benchmark_appears_when_index_given():
    cfg = _cfg()
    df = _prepared(_series(), cfg)
    idx = df.set_index("date")["close"]
    m = Backtester({"000001.SZ": df}, cfg, index_close=idx).run().metrics
    assert "基准总收益%" in m and "超额CAGR%" in m
