# -*- coding: utf-8 -*-
"""
eastmoney_holders — 东方财富「股东持股」页面（datapc.eastmoney.com/pc_dc/gdcg）的数据采集与整理

页面本身是一个 Vue 单页应用，全部数据来自东方财富数据中心接口：

    https://datacenter-web.eastmoney.com/api/data/v1/get

页面上的四张表与接口报表一一对应（口径见 `api.py` 的说明）：

    十大流通股东 / 十大股东       -> RPT_FREEHOLDERS_BASIC_INFO / RPT_HOLDERS_BASIC_INFO
    持股变动（按股东聚合）         -> RPT_FREEHOLDERS_CHANGERATE / RPT_HOLDERS_CHANGERATE
    股东明细（某股东持有哪些股票）  -> RPT_F10_EH_FREEHOLDERS / RPT_F10_EH_HOLDERS
    报告期列表                    -> RPT_F10_EH_HOLDERDATE

用法（命令行）：

    python -m eastmoney_holders crawl        # 抓取全部分页并落盘
    python -m eastmoney_holders report       # 生成股东持股/变动汇总表与分析
    python -m eastmoney_holders all          # crawl + report

产物默认写到 `output/eastmoney_holders/`（已在 .gitignore 中排除）。
"""
from __future__ import annotations

__version__ = "1.0.0"

from .api import (
    DEFAULT_BASE_URL,
    HOLD_TYPE_FREE,
    HOLD_TYPE_TOTAL,
    EastmoneyClient,
    REPORT_NAMES,
)
from .aggregate import build_holder_universe, build_period_changes

__all__ = [
    "__version__",
    "DEFAULT_BASE_URL",
    "EastmoneyClient",
    "HOLD_TYPE_FREE",
    "HOLD_TYPE_TOTAL",
    "REPORT_NAMES",
    "build_holder_universe",
    "build_period_changes",
]
