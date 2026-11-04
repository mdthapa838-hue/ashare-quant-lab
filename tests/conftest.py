# -*- coding: utf-8 -*-
"""pytest 公共夹具。

所有单元测试都离线运行（不访问 Tushare）。需要真实接口的测试
标记为 @pytest.mark.network，默认跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tushare_trader.config import load_config  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "network: 需要真实 Tushare 接口（默认跳过）")


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m") and "network" in config.getoption("-m"):
        return
    skip = pytest.mark.skip(reason="需要网络/真实 token，使用 -m network 运行")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def cfg():
    return load_config(ROOT / "config" / "system.yaml")


@pytest.fixture
def synthetic_bars():
    """确定性 K 线：可复现，无需网络。"""
    def _make(n: int = 400, start: str = "2022-01-03", seed: int = 7,
              drift: float = 0.0002, vol: float = 0.02) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        dates = pd.bdate_range(start, periods=n)
        rets = rng.normal(drift, vol, n)
        close = 20 * np.exp(np.cumsum(rets))
        open_ = close * (1 + rng.normal(0, 0.004, n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
        volume = rng.integers(1_000_000, 8_000_000, n).astype(float)
        return pd.DataFrame({
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": volume * close,
        })
    return _make
