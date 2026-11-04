# -*- coding: utf-8 -*-
"""
tushare_trader — 基于 Tushare 的 A 股量化交易系统

模块组成
--------
    credentials.py  Tushare token 解析（环境变量 / 文件，绝不入库）
    data.py         数据源：日线/复权/指数/每日指标/交易日历，带缓存限频
    indicators.py   技术指标
    strategy.py     布林带下轨反弹策略（唯一的策略实现）
    costs.py        A 股交易成本模型
    broker.py       模拟盘经纪商（下单/成交/持仓/结算，状态持久化）
    backtest.py     组合级事件驱动回测（与模拟盘共用策略与成本）
    runner.py       每日任务编排
    cli.py          命令行入口

快速开始
--------
    export TUSHARE_TOKEN=你的token
    python -m tushare_trader doctor
    python -m tushare_trader prepare
    python -m tushare_trader scan
"""
from __future__ import annotations

__version__ = "1.0.0"

from .backtest import Backtester, BacktestResult, performance
from .broker import PaperBroker
from .config import Config, load_config
from .costs import CostModel
from .credentials import TokenNotFound, mask, resolve_token
from .data import TushareData
from .strategy import BollBandStrategy, compute_indicators

__all__ = [
    "__version__",
    "Backtester",
    "BacktestResult",
    "performance",
    "PaperBroker",
    "Config",
    "load_config",
    "CostModel",
    "TokenNotFound",
    "mask",
    "resolve_token",
    "TushareData",
    "BollBandStrategy",
    "compute_indicators",
]
