# -*- coding: utf-8 -*-
"""每日任务编排（离线，用假数据源）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tushare_trader.config import Config  # noqa: E402
from tushare_trader.runner import TradingRunner  # noqa: E402


# ---------------------------------------------------------------------------
# 假数据源：离线、确定性
# ---------------------------------------------------------------------------
def _universe(len_=3):
    return [f"00000{i}.SZ" for i in range(1, len_ + 1)]


class FakeData:
    """模拟 TushareData，返回固定行情。"""

    def __init__(self, codes, n_days=400, signal_on=None):
        self.codes = codes
        self.n_days = n_days
        self.signal_on = signal_on
        self.daily_calls = 0
        self.index_calls = 0

    def trade_calendar(self, start, end):
        return [d.strftime("%Y%m%d")
                for d in pd.bdate_range("2015-01-05", "2035-12-31")
                if start <= d.strftime("%Y%m%d") <= end]

    def build_universe(self, trade_date, **kwargs):
        return list(self.codes)

    def index_daily(self, ts_code, start, end):
        self.index_calls += 1
        dates = pd.bdate_range("2020-01-02", periods=2000)
        dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
        return pd.Series(range(len(dates)), index=dates, dtype=float)

    def daily(self, ts_code, start, end, adj="qfq", use_cache=True):
        self.daily_calls += 1
        dates = pd.bdate_range("2020-01-02", periods=self.n_days + 50)
        dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
        if len(dates) < 30:
            return None
        import numpy as np
        close = 20 + np.arange(len(dates)) * 0.02
        return pd.DataFrame({
            "date": dates, "open": close, "high": close * 1.02,
            "low": close * 0.98, "close": close,
            "volume": 2e6, "amount": close * 2e6,
        })


def _cfg(tmp_path, **over):
    base = {
        "app": {"mode": "simulation"},
        "datasource": {"cache_dir": str(tmp_path / "cache")},
        "universe": {"market_cap_min": 30, "market_cap_max": 80,
                     "exclude_st": True, "exclude_bj": True, "min_avg_amount": 0},
        "strategy": {
            "entry": {"pos120_max": 0.30, "bb_period": 20, "bb_std": 2.0,
                      "streak_min": 2, "ma60_slope_window": 20,
                      "ma60_slope_max": -0.01, "use_monthly_filter": False,
                      "use_market_filter": False, "use_weekly_filter": False,
                      "use_j_filter": False},
            "exit": {"take_profit": "upper", "stop_loss_pct": -0.12,
                     "max_hold_days": 60, "stop_mode": "fixed"},
            "portfolio": {"max_positions": 2, "max_position_pct": 0.5,
                          "initial_capital": 100_000},
            "cost": {"commission": 0.00025, "stamp_tax": 0.0005,
                     "slippage": 0.00075, "min_commission": 5.0},
            "risk": {"sector_concentration_max": 2, "daily_max_orders": 20},
        },
        "constraints": {"no_buy_limit_up": 0.095, "no_sell_limit_down": -0.095},
        "backtest": {"risk_free_rate": 0.02},
        "output": {"dir": str(tmp_path / "output"), "state_dir": str(tmp_path / "state")},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return Config(base)


@pytest.fixture
def runner(tmp_path):
    cfg = _cfg(tmp_path)
    r = TradingRunner(cfg, data=FakeData(_universe()))
    return r


# ---------------------------------------------------------------------------
# 状态持久化
# ---------------------------------------------------------------------------
def test_runner_creates_dirs(tmp_path):
    r = TradingRunner(_cfg(tmp_path), data=FakeData(_universe()))
    assert (tmp_path / "state").is_dir()
    assert (tmp_path / "output").is_dir()


def test_state_roundtrip(runner):
    runner.save_state("x.json", {"a": 1})
    assert runner.load_state("x.json", {}) == {"a": 1}


def test_load_state_missing_returns_default(runner):
    assert runner.load_state("nope.json", {"d": True}) == {"d": True}


def test_load_state_corrupt_returns_default(runner, tmp_path):
    (tmp_path / "state" / "bad.json").write_text("{not json", encoding="utf-8")
    assert runner.load_state("bad.json", []) == []


# ---------------------------------------------------------------------------
# 交易日历
# ---------------------------------------------------------------------------
def test_trade_date_is_recent(runner):
    d = runner.trade_date("20260105")
    assert d is not None and d <= "20260105" and len(d) == 8


def test_previous_trade_date(runner):
    cur = runner.trade_date("20260105")
    prev = runner._previous_trade_date(cur)
    assert prev < cur


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------
def test_prepare_writes_plan_and_universe(runner, tmp_path):
    plan = runner.task_prepare("20260105")
    assert plan["trade_date"] == "20260105"
    assert plan["universe_size"] == 3
    assert "market_passed" in plan and "action" in plan
    saved = json.loads((tmp_path / "state" / "universe.json").read_text(encoding="utf-8"))
    assert saved["codes"] == _universe()
    assert (tmp_path / "state" / "plan.json").exists()


def test_prepare_records_run(runner, tmp_path):
    runner.task_prepare("20260105")
    lines = (tmp_path / "state" / "runs.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["task"] == "prepare"


# ---------------------------------------------------------------------------
# scan / execute / monitor / settle
# ---------------------------------------------------------------------------
def test_scan_requires_universe(runner):
    with pytest.raises(RuntimeError, match="标的池为空"):
        runner.task_scan("20260105")


def test_scan_after_prepare_returns_dataframe(runner):
    runner.task_prepare("20260105")
    sig = runner.task_scan("20260105")
    assert isinstance(sig, pd.DataFrame)


def test_execute_requires_universe(runner):
    with pytest.raises(RuntimeError, match="标的池为空"):
        runner.task_execute("20260105")


def test_execute_dry_run_does_not_trade(runner, tmp_path):
    runner.task_prepare("20260105")
    out = runner.task_execute("20260105", dry_run=True)
    assert "n_signals" in out
    assert not (tmp_path / "state" / "paper_account.json").exists() or \
        not runner._broker().trades


def test_monitor_without_positions(runner):
    out = runner.task_monitor("20260105")
    assert out["n_positions"] == 0 and out["n_sold"] == 0


def test_settle_returns_report(runner):
    rep = runner.task_settle("20260105")
    assert "equity" in rep and "positions" in rep
    assert rep["mode"] == "simulation"


def test_settle_writes_equity_csv(runner, tmp_path):
    runner.task_settle("20260105")
    assert (tmp_path / "output" / "equity.csv").exists()


def test_update_counts(runner):
    runner.task_prepare("20260105")
    out = runner.task_update("20260105")
    assert out["total"] == 3
    assert out["updated"] >= 0


# ---------------------------------------------------------------------------
# 账户
# ---------------------------------------------------------------------------
def test_broker_uses_configured_capital(runner):
    b = runner._broker()
    assert b.initial_capital == 100_000
    assert b.limit_up_pct == pytest.approx(9.5)


def test_broker_state_persists_between_calls(runner, tmp_path):
    b1 = runner._broker()
    b1.buy("000001.SZ",
           pd.Series({"open": 10.0, "open_chg_pct": 0.0}),
           "2026-01-05", 1000, stop_price=8.8)
    b2 = runner._broker()
    assert "000001.SZ" in b2.positions
    assert len(b2.trades) == 1


# ---------------------------------------------------------------------------
# 端到端：信号 -> 建仓 -> 结算
# ---------------------------------------------------------------------------
class CrashData(FakeData):
    """生成「平稳上涨 -> 快速暴跌 -> 反弹」行情，确保产生真实入场信号。"""

    def daily(self, ts_code, start, end, adj="qfq", use_cache=True):
        import numpy as np
        dates = pd.bdate_range("2020-01-02", periods=800)
        dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
        if len(dates) < 30:
            return None
        n = len(dates)
        c1 = np.linspace(20, 24, 500)
        c2 = c1[-1] * np.exp(np.linspace(0, np.log(0.55), 14))
        rest = max(n - len(c1) - len(c2), 1)
        c3 = c2[-1] * np.linspace(1, 1.55, rest)
        close = np.concatenate([c1, c2, c3])[:n]
        return pd.DataFrame({
            "date": dates, "open": close, "high": close * 1.01,
            "low": close * 0.99, "close": close,
            "volume": 2e6, "amount": close * 2e6,
        })


@pytest.fixture
def crash_runner(tmp_path):
    return TradingRunner(_cfg(tmp_path), data=CrashData(_universe(3)))


def test_end_to_end_signal_to_fill_to_settle(crash_runner):
    """完整链路：择时通过 -> 信号 -> T+1 开盘成交 -> 市值结算。"""
    plan = crash_runner.task_prepare("20211215")
    assert plan["market_passed"] is True

    out = crash_runner.task_execute("20211215")
    # 信号来自 20211214（上一交易日），当日成交
    assert out["signal_date"] == "20211214"
    assert out["n_signals"] == 3
    assert out["n_filled"] == 2                 # max_positions=2
    for f in out["fills"]:
        assert f["shares"] % 100 == 0
        assert f["amount"] > 0

    broker = crash_runner._broker()
    assert len(broker.positions) == 2
    assert broker.cash >= 0

    report = crash_runner.task_settle("20211215")
    assert report["n_positions"] == 2
    assert report["equity"] > 0
    # 权益 = 现金 + 市值
    assert report["equity"] == pytest.approx(
        report["cash"] + report["market_value"], rel=1e-6)


def test_execute_is_idempotent_on_full_book(crash_runner):
    """已满仓时不得再买入。"""
    crash_runner.task_prepare("20211215")
    crash_runner.task_execute("20211215")
    n_before = len(crash_runner._broker().positions)
    out = crash_runner.task_execute("20211215")
    assert out["n_filled"] == 0
    assert len(crash_runner._broker().positions) == n_before


def test_execute_skips_when_market_filter_fails(tmp_path):
    """择时未通过时 execute 必须空仓退出。"""
    cfg = _cfg(tmp_path)
    cfg.set("strategy.entry.use_market_filter", True)
    cfg.set("strategy.entry.market_ret_max", -99.0)      # 永不可能满足

    class WeakData(CrashData):
        def index_daily(self, ts_code, start, end):
            dates = pd.bdate_range("2020-01-02", periods=2000)
            dates = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]
            return pd.Series(100.0, index=dates)          # 指数不涨
    r = TradingRunner(cfg, data=WeakData(_universe(3)))
    out = r.task_execute("20211215")
    assert out.get("skipped") is True
    assert "择时" in out["reason"]
    assert r._broker().positions == {}


def test_monitor_sells_on_stop(crash_runner):
    """跌破止损价时必须卖出。"""
    crash_runner.task_prepare("20211215")
    crash_runner.task_execute("20211215")
    b = crash_runner._broker()
    code = next(iter(b.positions))
    b.positions[code].stop_price = 1e9          # 任意当前价都低于止损
    b.save()

    out = crash_runner.task_monitor("20211216")
    assert out["n_sold"] >= 1
    assert code not in crash_runner._broker().positions
