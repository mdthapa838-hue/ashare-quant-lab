# -*- coding: utf-8 -*-
"""模拟盘经纪商：下单、约束、持久化、结算。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tushare_trader.broker import PaperBroker  # noqa: E402
from tushare_trader.costs import CostModel  # noqa: E402


def _bar(open_px: float, chg: float = 0.0) -> pd.Series:
    return pd.Series({"open": open_px, "open_chg_pct": chg,
                      "high": open_px * 1.02, "low": open_px * 0.98,
                      "close": open_px * 1.01})


@pytest.fixture
def broker(tmp_path):
    return PaperBroker(initial_capital=100_000, cost=CostModel(),
                       state_dir=tmp_path)


def test_buy_reduces_cash_by_amount_and_fee(broker):
    t = broker.buy("000001.SZ", _bar(10.0), "2026-01-05", 1000, stop_price=8.8)
    assert t is not None
    assert t.shares == 1000
    expected_fee = broker.cost.buy_cost(10_000)
    assert broker.cash == pytest.approx(100_000 - 10_000 - expected_fee)
    assert "000001.SZ" in broker.positions


def test_buy_rounds_to_lot(broker):
    broker.buy("000001.SZ", _bar(10.0), "2026-01-05", 1050, stop_price=8.8)
    assert broker.positions["000001.SZ"].shares == 1000


def test_buy_respects_limit_up(broker):
    t = broker.buy("000001.SZ", _bar(10.0, chg=10.0), "2026-01-05", 1000, stop_price=8.8)
    assert t is None
    assert broker.cash == 100_000
    assert broker.orders[-1]["status"] == "rejected_limit_up"


def test_buy_never_overspends(broker):
    broker.buy("000001.SZ", _bar(10.0), "2026-01-05", 100_000, stop_price=8.8)
    assert broker.cash >= 0
    pos = broker.positions["000001.SZ"]
    notional = pos.shares * 10.0
    assert notional + broker.cost.buy_cost(notional) <= 100_000 + 1e-6


def test_sell_credits_cash_and_removes_position(broker):
    broker.buy("000001.SZ", _bar(10.0), "2026-01-05", 1000, stop_price=8.8)
    cash_before = broker.cash
    t = broker.sell("000001.SZ", _bar(12.0), "2026-01-06", reason="take_profit")
    assert t is not None
    assert t.shares == 1000
    assert broker.positions == {}
    assert broker.cash == pytest.approx(cash_before + 12_000 - broker.cost.sell_cost(12_000))
    assert broker.trades[-1].reason == "take_profit"


def test_sell_respects_limit_down(broker):
    broker.buy("000001.SZ", _bar(10.0), "2026-01-05", 1000, stop_price=8.8)
    t = broker.sell("000001.SZ", _bar(9.0, chg=-10.0), "2026-01-06")
    assert t is None
    assert "000001.SZ" in broker.positions        # 跌停卖不掉
    assert broker.orders[-1]["status"] == "rejected_limit_down"


def test_sell_missing_position_is_noop(broker):
    assert broker.sell("000002.SZ", _bar(10.0), "2026-01-06") is None


def test_state_persists_across_instances(tmp_path):
    b1 = PaperBroker(initial_capital=50_000, state_dir=tmp_path)
    b1.buy("600000.SH", _bar(8.0), "2026-01-05", 1000, stop_price=7.0)
    cash = b1.cash

    b2 = PaperBroker(initial_capital=50_000, state_dir=tmp_path)
    assert b2.cash == pytest.approx(cash)
    assert "600000.SH" in b2.positions
    assert b2.positions["600000.SH"].shares == 1000
    assert b2.positions["600000.SH"].stop_price == pytest.approx(7.0)
    assert len(b2.trades) == 1


def test_state_file_is_valid_json(broker, tmp_path):
    broker.buy("000001.SZ", _bar(10.0), "2026-01-05", 1000, stop_price=8.8)
    raw = json.loads((tmp_path / PaperBroker.STATE_FILE).read_text(encoding="utf-8"))
    assert raw["positions"]["000001.SZ"]["shares"] == 1000
    assert raw["cash"] > 0


def test_equity_and_summary(broker):
    broker.buy("000001.SZ", _bar(10.0), "2026-01-05", 1000, stop_price=8.8)
    marks = {"000001.SZ": 11.0}
    eq = broker.equity(marks)
    assert eq == pytest.approx(broker.cash + 11_000)
    s = broker.summary(marks)
    assert s["n_positions"] == 1
    assert s["positions"][0]["pnl_pct"] == pytest.approx(10.0, abs=0.01)
    assert s["market_value"] == pytest.approx(11_000)


def test_mark_to_market_records_history(broker):
    broker.buy("000001.SZ", _bar(10.0), "2026-01-05", 1000, stop_price=8.8)
    rec = broker.mark_to_market("2026-01-06", {"000001.SZ": 10.5})
    assert rec["date"] == "2026-01-06"
    assert rec["n_positions"] == 1
    assert len(broker.equity_history) == 1


def test_no_state_dir_does_not_write(tmp_path):
    b = PaperBroker(initial_capital=10_000)
    b.buy("000001.SZ", _bar(5.0), "2026-01-05", 1000, stop_price=4.0)
    assert b.cash < 10_000
    assert not list(tmp_path.glob("*.json"))
