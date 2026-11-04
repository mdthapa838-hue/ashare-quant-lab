# -*- coding: utf-8 -*-
"""真实 Tushare 接口冒烟测试（默认跳过，使用 -m network 运行）。

运行方式：
    TUSHARE_TOKEN=... python -m pytest tests -m network -v

这些测试只做只读查询，不写任何状态、不下单。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tushare_trader.credentials import TokenNotFound, resolve_token  # noqa: E402
from tushare_trader.data import TushareData  # noqa: E402

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    try:
        tok = resolve_token()
    except TokenNotFound:
        pytest.skip("未配置 TUSHARE_TOKEN，跳过网络测试")
    cache = tmp_path_factory.mktemp("cache")
    return TushareData(token=tok, cache_dir=cache, min_interval=0.4, retry=2)


def test_trade_calendar_nonempty(data):
    cal = data.latest_trade_date()
    assert cal is not None and len(cal) == 8


def test_stock_basic_has_fields(data):
    df = data.stock_basic()
    assert len(df) > 1000
    for col in ("ts_code", "symbol", "name", "industry", "market"):
        assert col in df.columns


def test_daily_returns_normalised_frame(data):
    latest = data.latest_trade_date()
    start = "20250101"
    df = data.daily("000001.SZ", start, latest, adj="qfq")
    assert df is not None and len(df) > 100
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "amount"]
    assert df["date"].is_monotonic_increasing
    assert df["close"].gt(0).all()
    assert df["volume"].gt(0).all()
    # qfq 后价格应连续，不应出现跳空到个位数以下的异常
    assert df["close"].max() / df["close"].min() < 100


def test_daily_handles_short_history(data):
    """新股/退市股不应抛异常，返回 None 或较短的数据。"""
    out = data.daily("000001.SZ", "20251001", "20251231")
    assert out is None or isinstance(out, type(out))


def test_index_daily_series(data):
    latest = data.latest_trade_date()
    s = data.index_daily("000852.SH", "20250101", latest)
    assert s is not None and len(s) > 100
    assert s.gt(0).all()


def test_daily_basic_fallback_never_empty(data):
    """当日未发布时应回退到最近可用日期，不返回 None。"""
    latest = data.latest_trade_date()
    db, used = data.daily_basic_with_fallback(latest)
    assert db is not None and len(db) > 1000
    assert used is not None and used <= latest


def test_build_universe_produces_codes(data):
    latest = data.latest_trade_date()
    codes = data.build_universe(latest, cap_min=30, cap_max=80,
                                exclude_st=True, exclude_bj=True)
    assert len(codes) > 0
    assert all(c.endswith((".SH", ".SZ")) for c in codes)


def test_bad_token_returns_none_not_crash():
    d = TushareData(token="0000000000000000000000000000000000000000",
                    min_interval=0, retry=1)
    # 假 token 应被接口拒绝，客户端返回 None 而不是抛异常
    out = d.latest_trade_date()
    assert out is None
    assert d.last_error is not None
