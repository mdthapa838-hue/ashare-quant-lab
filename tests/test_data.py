# -*- coding: utf-8 -*-
"""数据层（离线，用假 pro_api）与配置/token 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tushare_trader.config import Config, load_config  # noqa: E402
from tushare_trader import credentials as cred  # noqa: E402
from tushare_trader.credentials import (  # noqa: E402
    TokenNotFound, mask, resolve_token, token_source,
)


def _isolate_home(monkeypatch, tmp_path):
    """把 ~/.tushare/token 指向不存在的路径。

    `credentials.HOME_TOKEN` 是模块级常量，导入时就已求值，
    因此必须替换该常量，而不是 patch Path.home。
    """
    monkeypatch.setattr(cred, "HOME_TOKEN", tmp_path / "no_such_home" / "token")
from tushare_trader.data import TushareData  # noqa: E402


# ---------------------------------------------------------------------------
# 假 pro_api
# ---------------------------------------------------------------------------
class FakePro:
    """最小化的 Tushare pro_api 替身。"""

    def __init__(self, frame_by_api: dict | None = None, error: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.frames = frame_by_api or {}
        self.error = error

    def __getattr__(self, name):
        def _fn(**kwargs):
            self.calls.append((name, kwargs))
            if self.error is not None:
                raise self.error
            df = self.frames.get(name)
            return None if df is None else df.copy()
        return _fn


def _daily_frame(n=5, start="2026-01-05"):
    dates = pd.bdate_range(start, periods=n)
    return pd.DataFrame({
        "ts_code": "000001.SZ",
        "trade_date": [d.strftime("%Y%m%d") for d in dates],
        "open": [10.0 + i for i in range(n)],
        "high": [10.5 + i for i in range(n)],
        "low": [9.5 + i for i in range(n)],
        "close": [10.2 + i for i in range(n)],
        "pre_close": [10.0 + i for i in range(n)],
        "vol": [1000.0] * n,
        "amount": [1020.0] * n,
    })


def _cal_frame(n=5, start="2026-01-05"):
    dates = pd.bdate_range(start, periods=n)
    return pd.DataFrame({
        "exchange": "SSE",
        "cal_date": [d.strftime("%Y%m%d") for d in dates],
        "is_open": [1] * n,
        "pretrade_date": [""] * n,
    })


# ---------------------------------------------------------------------------
# token
# ---------------------------------------------------------------------------
def test_resolve_token_explicit_wins(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "envtoken")
    assert resolve_token("explicit") == "explicit"


def test_resolve_token_from_env(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "envtoken123")
    assert resolve_token() == "envtoken123"
    assert token_source() == "env:TUSHARE_TOKEN"


def test_resolve_token_missing_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    _isolate_home(monkeypatch, tmp_path)
    with pytest.raises(TokenNotFound) as ei:
        resolve_token(None, project_root=tmp_path)
    msg = str(ei.value)
    assert "TUSHARE_TOKEN" in msg
    # 错误信息不得包含任何看起来像 token 的长串
    assert "deadbe" not in msg


def test_resolve_token_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    _isolate_home(monkeypatch, tmp_path)
    (tmp_path / ".tushare_token").write_text("filetoken_abc\n", encoding="utf-8")
    assert resolve_token(None, project_root=tmp_path) == "filetoken_abc"


def test_resolve_token_from_file_kv(monkeypatch, tmp_path):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    _isolate_home(monkeypatch, tmp_path)
    (tmp_path / ".tushare_token").write_text(
        "# comment\nTUSHARE_TOKEN=kv_token_123\n", encoding="utf-8")
    assert resolve_token(None, project_root=tmp_path) == "kv_token_123"


def test_mask_hides_token():
    raw = "deadbeef" * 8          # 仅用于测试的假 token，不是真实凭据
    m = mask(raw)
    assert raw not in m
    assert m.startswith(raw[:4]) and m.endswith(raw[-4:])
    assert m.count("*") >= len(raw) - 8


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def test_config_loads_and_validates(cfg):
    assert cfg.mode == "simulation"
    assert cfg.get("strategy.entry.bb_period") == 20
    assert Path(cfg.get("datasource.cache_dir")).is_absolute()


def test_config_rejects_bad_mode():
    c = Config({"app": {"mode": "yolo"}})
    with pytest.raises(ValueError, match="app.mode"):
        c.validate()


def test_config_rejects_positive_stop_loss():
    c = Config({"strategy": {"exit": {"stop_loss_pct": 0.1}}})
    with pytest.raises(ValueError, match="stop_loss_pct"):
        c.validate()


def test_config_dotted_set_get():
    c = Config({})
    c.set("a.b.c", 1)
    assert c.get("a.b.c") == 1
    assert c.get("a.b.missing", "d") == "d"
    assert c.section("a.b") == {"c": 1}


def test_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/system.yaml")


# ---------------------------------------------------------------------------
# 数据层（假接口）
# ---------------------------------------------------------------------------
def test_daily_normalises_columns(tmp_path):
    pro = FakePro({"daily": _daily_frame(), "adj_factor": pd.DataFrame({
        "ts_code": "000001.SZ",
        "trade_date": _daily_frame()["trade_date"],
        "adj_factor": [2.0] * 5,
    })})
    d = TushareData(pro=pro, cache_dir=tmp_path, min_interval=0)
    df = d.daily("000001.SZ", "20260101", "20260110", adj="qfq")
    assert df is not None
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "amount"]
    # 手 -> 股
    assert df["volume"].iloc[0] == pytest.approx(1000.0 * 100)
    # 千元 -> 元
    assert df["amount"].iloc[0] == pytest.approx(1020.0 * 1000)
    # qfq 以后一个因子归一
    assert df["close"].iloc[0] == pytest.approx(10.2)


def test_daily_uses_cache(tmp_path):
    frame = _daily_frame()
    pro = FakePro({"daily": frame, "adj_factor": pd.DataFrame({
        "ts_code": "000001.SZ", "trade_date": frame["trade_date"],
        "adj_factor": [1.0] * len(frame)})})
    d = TushareData(pro=pro, cache_dir=tmp_path, min_interval=0)
    d.daily("000001.SZ", "20260101", "20260110")
    n_calls = len(pro.calls)
    d.daily("000001.SZ", "20260101", "20260110")     # 应命中缓存
    assert len(pro.calls) == n_calls


def test_daily_returns_none_on_api_failure(tmp_path):
    pro = FakePro(error=RuntimeError("接口无权限"))
    d = TushareData(pro=pro, cache_dir=tmp_path, min_interval=0, retry=1)
    assert d.daily("000001.SZ", "20260101", "20260110") is None
    assert d.last_error is not None


def test_trade_calendar_filters_open_days(tmp_path):
    cal = _cal_frame().copy()
    cal.loc[1, "is_open"] = 0
    pro = FakePro({"trade_cal": cal})
    d = TushareData(pro=pro, cache_dir=tmp_path, min_interval=0)
    days = d.trade_calendar("20260101", "20260110")
    assert len(days) == 4
    assert days == sorted(days)
    assert cal.loc[1, "cal_date"] not in days


def test_index_daily_returns_series(tmp_path):
    frame = pd.DataFrame({
        "ts_code": "000852.SH",
        "trade_date": _daily_frame()["trade_date"],
        "close": [7000.0, 7010.0, 7020.0, 7030.0, 7040.0],
    })
    d = TushareData(pro=FakePro({"index_daily": frame}), cache_dir=tmp_path,
                    min_interval=0)
    s = d.index_daily("000852.SH", "20260101", "20260110")
    assert isinstance(s, pd.Series)
    assert len(s) == 5
    assert s.index.is_monotonic_increasing


def test_build_universe_filters(tmp_path):
    basic = pd.DataFrame({
        "ts_code": ["600001.SH", "600002.SH", "600003.SH", "830001.BJ", "600004.SH"],
        "symbol": ["600001", "600002", "600003", "830001", "600004"],
        "name": ["正常A", "*ST股", "正常B", "北交所", "正常C"],
        "market": ["主板"] * 5,
        "list_date": ["20100101"] * 5,
    })
    db = pd.DataFrame({
        "ts_code": basic["ts_code"],
        "total_mv": [500_000, 500_000, 900_000, 500_000, 4_000_000],   # 万元
        "circ_mv": [400_000] * 5,
        "close": [10.0] * 5,
    })
    pro = FakePro({"stock_basic": basic, "daily_basic": db})
    d = TushareData(pro=pro, cache_dir=tmp_path, min_interval=0)
    codes = d.build_universe("20260105", cap_min=30, cap_max=80,
                             exclude_st=True, exclude_bj=True)
    assert codes == ["600001.SH"]     # 50 亿、非 ST、非北交所、非大盘


def test_build_universe_returns_empty_without_data(tmp_path):
    d = TushareData(pro=FakePro({}), cache_dir=tmp_path, min_interval=0)
    assert d.build_universe("20260105") == []


def test_paged_fetch_handles_multiple_pages(tmp_path):
    """每页最多返回 2 行的假接口，验证分页拼装。"""
    calls = {"n": 0}

    class PagedPro:
        def __init__(self):
            self.calls = []

        def daily(self, ts_code, start_date, end_date):
            self.calls.append((start_date, end_date))
            calls["n"] += 1
            # 每次返回 2 个交易日，且早于请求的 end
            end = pd.Timestamp(end_date)
            dates = pd.bdate_range(end - pd.Timedelta(days=4), periods=2)
            return pd.DataFrame({
                "trade_date": [d.strftime("%Y%m%d") for d in dates],
                "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
                "close": [1.0, 2.0], "pre_close": [1.0, 2.0],
                "vol": [10.0, 20.0], "amount": [10.0, 20.0],
            })

    pro = PagedPro()
    d = TushareData(pro=pro, cache_dir=tmp_path, min_interval=0)
    out = d._fetch_paged("daily", "000001.SZ", "20260101", "20260601",
                         ts_code="000001.SZ")
    assert out is not None
    assert len(pro.calls) >= 2
    assert out["trade_date"].is_monotonic_increasing
    assert out["trade_date"].is_unique


def test_cache_does_not_leak_future_data(tmp_path):
    """缓存若含请求终点之后的数据，必须被截断——否则 --date 回放会看到未来。

    这是真实踩到过的缺陷：用近期缓存 + 历史 --date 时，daily() 曾返回
    1096 行终点之后的数据。
    """
    frame = _daily_frame(n=5, start="2026-01-05")          # 缓存覆盖 1/5~1/9
    pro = FakePro({"daily": frame, "adj_factor": pd.DataFrame({
        "ts_code": "000001.SZ", "trade_date": frame["trade_date"],
        "adj_factor": [1.0] * len(frame)})})
    d = TushareData(pro=pro, cache_dir=tmp_path, min_interval=0)
    d.daily("000001.SZ", "20260105", "20260109")           # 先写缓存

    # 再用同一缓存回放更早的区间：终点 1/7
    out = d.daily("000001.SZ", "20260105", "20260107", use_cache=True)
    assert out is not None
    assert out["date"].max() <= pd.Timestamp("2026-01-07"), "缓存泄漏了终点之后的数据"
    assert len(out) == 3


def test_index_cache_does_not_leak_future_data(tmp_path):
    # 需要 >10 行，才会走 index_daily 的缓存分支
    n = 20
    dates = pd.bdate_range("2026-01-05", periods=n)
    frame = pd.DataFrame({
        "ts_code": "000852.SH",
        "trade_date": [d.strftime("%Y%m%d") for d in dates],
        "close": [7000.0 + i for i in range(n)],
    })
    d = TushareData(pro=FakePro({"index_daily": frame}), cache_dir=tmp_path,
                    min_interval=0)
    d.index_daily("000852.SH", "20260105", "20260201")
    s = d.index_daily("000852.SH", "20260105", "20260115", use_cache=True)
    assert len(s) == 9          # 2026-01-05 ~ 01-15 共 9 个交易日
    assert s is not None
    assert s.index.max() <= pd.Timestamp("2026-01-15"), "指数缓存泄漏了终点之后的数据"
