# -*- coding: utf-8 -*-
"""
eastmoney_holders/api.py — 东方财富数据中心接口封装

只依赖标准库（urllib/json），不需要 requests，方便在任何环境复现。

接口约定
--------
GET https://datacenter-web.eastmoney.com/api/data/v1/get
    reportName   报表名，见 REPORT_NAMES
    columns      ALL 或逗号分隔的字段名（字段名写错会返回 code=9501）
    filter       形如 (END_DATE='2026-09-30')(IS_REPORT="1")
    pageNumber   从 1 开始
    pageSize     单页条数；实测服务端对“股东明细”表单次最多返回 2500 行，
                 请求更大只会拿到 2500 行但 pages 仍按 2500 计算，所以统一用 2000。
    sortColumns  排序字段，如 SECUCODE,HOLDER_RANK,HOLDER_NAME
    sortTypes    对应排序方向，1 升序 -1 降序
    source       WEB

返回 {"code":0,"message":"ok","result":{"pages":N,"count":M,"data":[...]}}

两份口径（页面上的两个下拉项）
------------------------------
    十大流通股东  HOLD_TYPE="十大流通股东"  明细表 RPT_F10_EH_FREEHOLDERS
    十大股东      HOLD_TYPE="十大股东"      明细表 RPT_F10_EH_HOLDERS

「股东明细」表按 (股票, 股东) 逐行记录持有数量，是我们重建“某股东持有哪些股票、
每个报告期怎么变化”的原始数据；页面首屏的“持股家数/新进/加仓/减仓”是根据它聚合出来的。
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

DEFAULT_BASE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

HOLD_TYPE_FREE = "十大流通股东"
HOLD_TYPE_TOTAL = "十大股东"

#: 页面两个口径对应的报表名
REPORT_NAMES = {
    HOLD_TYPE_FREE: {
        "basic_info": "RPT_FREEHOLDERS_BASIC_INFO",   # 股东列表（持股家数/新进/加仓/减仓/市值）
        "changerate": "RPT_FREEHOLDERS_CHANGERATE",   # 股东列表的“持股平均涨幅/上涨比例”
        "detail": "RPT_F10_EH_FREEHOLDERS",           # 股东明细（某股东持有哪些股票）
        "detail_obj": "RPT_F10_EH_FREEHOLDERS",       # 个股十大流通股东（同一张表，按 SECUCODE 过滤）
    },
    HOLD_TYPE_TOTAL: {
        "basic_info": "RPT_HOLDERS_BASIC_INFO",
        "changerate": "RPT_HOLDERS_CHANGERATE",
        "detail": "RPT_F10_EH_HOLDERS",
        "detail_obj": "RPT_F10_EH_HOLDERS",
    },
}

REPORT_DATE_API = "RPT_F10_EH_HOLDERDATE"

#: 各报表可用字段（2026-11 实测）。写错字段名接口直接报 code=9501。
COLUMNS = {
    "RPT_F10_EH_FREEHOLDERS": [
        "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "END_DATE", "REPORT_DATE_NAME",
        "HOLDER_NAME", "HOLDER_NEW", "HOLDER_TYPE", "HOLDER_RANK", "HOLD_NUM",
        "HOLD_NUM_CHANGE", "CHANGE_RATIO", "HOLD_RATIO", "FREE_HOLDNUM_RATIO",
        "HOLDER_MARKET_CAP", "IS_REPORT", "SHARES_TYPE", "NOTICE_DATE", "UPDATE_DATE",
    ],
    "RPT_F10_EH_HOLDERS": [
        "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "END_DATE",
        "HOLDER_NAME", "HOLDER_NEW", "HOLDER_RANK", "HOLD_NUM", "HOLD_NUM_CHANGE",
        "CHANGE_RATIO", "HOLD_NUM_RATIO", "TOTAL_SHARES_NUM", "HOLDER_MARKET_CAP",
        "IS_REPORT", "SHARES_TYPE",
    ],
    "RPT_FREEHOLDERS_BASIC_INFO": [
        "HOLDER_NEW", "HOLDER_NAME", "HOLDER_TYPE", "END_DATE", "HOLDER_NUM",
        "HOLDADD_NUM", "HOLDUP_NUM", "HOLDUNCHANGED_NUM", "HOLDDOWN_NUM",
        "HOLDER_MARKET_CAP", "REPORT_DATE_NAME", "HOLDER_SOURCE", "IS_REPORT",
    ],
    "RPT_HOLDERS_BASIC_INFO": [
        "HOLDER_NEW", "HOLDER_NAME", "HOLDER_TYPE", "END_DATE", "HOLDER_NUM",
        "HOLDADD_NUM", "HOLDUP_NUM", "HOLDUNCHANGED_NUM", "HOLDDOWN_NUM",
        "HOLDER_MARKET_CAP", "REPORT_DATE_NAME", "HOLDER_SOURCE", "IS_REPORT",
    ],
    "RPT_FREEHOLDERS_CHANGERATE": [
        "HOLDER_NEW", "HOLDER_NAME", "END_DATE", "HOLDER_NUM", "AVG_CHANGE_RATE",
        "RISE_RATIO", "REPORT_DATE_NAME",
    ],
    "RPT_HOLDERS_CHANGERATE": [
        "HOLDER_NEW", "HOLDER_NAME", "END_DATE", "HOLDER_NUM", "AVG_CHANGE_RATE",
        "RISE_RATIO", "REPORT_DATE_NAME",
    ],
}

#: 实测单次最多返回 2500 行；用 2000 保证按 pages 翻页不丢数据
DEFAULT_PAGE_SIZE = 2000


class EastmoneyError(RuntimeError):
    """接口返回非 0 或结构异常。"""


@dataclass
class Page:
    rows: list[dict[str, Any]]
    pages: int
    count: int
    page_number: int


class EastmoneyClient:
    """带限频、重试、分页的接口客户端（纯标准库）。"""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, min_interval: float = 0.25,
                 retry: int = 4, timeout: float = 30.0, page_size: int = DEFAULT_PAGE_SIZE,
                 user_agent: str | None = None, sleeper=time.sleep):
        self.base_url = base_url
        self.min_interval = float(min_interval)
        self.retry = int(retry)
        self.timeout = float(timeout)
        self.page_size = int(page_size)
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
        self._sleep = sleeper
        self._last_call = 0.0
        self.requests = 0          # 便于测试与统计
        self.retries = 0

    # ------------------------------------------------------------------ HTTP
    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        gap = time.time() - self._last_call
        if gap < self.min_interval:
            self._sleep(self.min_interval - gap)
        self._last_call = time.time()

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v is not None and v != ""}
        params.setdefault("source", "WEB")
        # 全部参数都做 URL 编码：filter 里的引号若不编码，服务端会直接返回 400
        url = self.base_url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Referer": "https://datapc.eastmoney.com/pc_dc/gdcg/index.html",
            "Accept": "*/*",
        })
        last_err: Exception | None = None
        for attempt in range(self.retry + 1):
            if attempt:
                self.retries += 1
                self._sleep(min(8.0, 0.8 * 2 ** (attempt - 1)) + random.random() * 0.3)
            self._throttle()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                self.requests += 1
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                continue
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                last_err = e
                continue
            code = payload.get("code")
            if code not in (0, "0"):
                # 字段名/过滤条件写错属于调用方 bug，重试没有意义
                raise EastmoneyError(f"接口返回错误 code={code} message={payload.get('message')!r} params={params}")
            return payload
        raise EastmoneyError(f"请求重试 {self.retry} 次仍失败: {last_err} url={url}")

    # --------------------------------------------------------------- 基础调用
    def fetch_page(self, report_name: str, columns: str = "ALL",
                   filter_: str | None = None, page_number: int = 1,
                   page_size: int | None = None, sort_columns: str | None = None,
                   sort_types: str | int | None = None) -> Page:
        params = {
            "reportName": report_name,
            "columns": columns,
            "filter": filter_,
            "pageNumber": page_number,
            "pageSize": page_size if page_size is not None else self.page_size,
            "sortColumns": sort_columns,
            "sortTypes": sort_types,
        }
        payload = self._request(params)
        result = payload.get("result") or {}
        rows = result.get("data") or []
        return Page(rows=rows,
                    pages=int(result.get("pages") or 0),
                    count=int(result.get("count") or 0),
                    page_number=page_number)

    def count(self, report_name: str, filter_: str | None = None) -> int:
        """只问「这个条件下一共有多少行」，用于核对抓取完整性（不拉数据）。"""
        return self.fetch_page(report_name, columns="SECUCODE", filter_=filter_,
                               page_number=1, page_size=1).count

    def fetch_all(self, report_name: str, columns: str = "ALL", filter_: str | None = None,
                  page_size: int | None = None, sort_columns: str | None = None,
                  sort_types: str | int | None = None,
                  max_pages: int | None = None, on_page=None) -> list[dict[str, Any]]:
        """翻完所有分页，返回全部行。"""
        size = page_size or self.page_size
        first = self.fetch_page(report_name, columns, filter_, 1, size, sort_columns, sort_types)
        rows = list(first.rows)
        if on_page:
            on_page(1, first.pages, len(first.rows), first.count)
        pages = first.pages
        if max_pages:
            pages = min(pages, max_pages)
        for p in range(2, pages + 1):
            page = self.fetch_page(report_name, columns, filter_, p, size, sort_columns, sort_types)
            rows.extend(page.rows)
            if on_page:
                on_page(p, pages, len(page.rows), page.count)
            if not page.rows:      # 服务端提前没有数据了，避免空转
                break
        return rows

    # ------------------------------------------------------------- 语义化封装
    def report_dates(self, hold_type: str = HOLD_TYPE_FREE) -> list[dict[str, Any]]:
        """页面「报告期」下拉里的全部报告期，按日期倒序。"""
        return self.fetch_all(
            REPORT_DATE_API,
            columns="END_DATE,REPORT_NAME,HOLD_TYPE,IS_DISCLOSURE_COMPLETE",
            filter_=f'(HOLD_TYPE="{hold_type}")',
            sort_columns="END_DATE", sort_types=-1,
        )

    def holder_list(self, end_date: str, hold_type: str = HOLD_TYPE_FREE) -> list[dict[str, Any]]:
        """某一报告期的股东列表（持股家数 / 新进 / 加仓 / 减仓 / 持股市值）。"""
        rpt = REPORT_NAMES[hold_type]["basic_info"]
        return self.fetch_all(
            rpt,
            columns=",".join(COLUMNS[rpt]),
            filter_=f"(END_DATE='{end_date}')(IS_REPORT=\"1\")",
            sort_columns="HOLDER_NUM,HOLDER_CODE", sort_types="-1,1",
        )

    def holder_change_rate(self, end_date: str, hold_type: str = HOLD_TYPE_FREE) -> list[dict[str, Any]]:
        """股东列表的「持股平均涨幅 / 持股上涨比例」。该表单次只返回 500 行，需要翻页。"""
        rpt = REPORT_NAMES[hold_type]["changerate"]
        return self.fetch_all(
            rpt,
            columns=",".join(COLUMNS[rpt]),
            filter_=f"(END_DATE='{end_date}')",
            page_size=500,
            sort_columns="HOLDER_NUM,HOLDER_CODE", sort_types="-1,1",
        )

    def period_detail(self, end_date: str, hold_type: str = HOLD_TYPE_FREE,
                      include_non_report: bool = False) -> list[dict[str, Any]]:
        """某一报告期的股东明细（股票 × 股东）。

        include_non_report=False 时只保留 IS_REPORT="1" 的定期报告口径，
        与页面表格一致；True 时连同公告日口径（IS_REPORT="0"，例如股东户数公告）一并返回。
        """
        rpt = REPORT_NAMES[hold_type]["detail"]
        filter_ = f"(END_DATE='{end_date}')"
        if not include_non_report:
            filter_ += '(IS_REPORT="1")'
        return self.fetch_all(
            rpt,
            columns=",".join(COLUMNS[rpt]),
            filter_=filter_,
            sort_columns="SECUCODE,HOLDER_RANK,HOLDER_NAME",
            sort_types="1,1,1",
        )

    def stock_detail(self, secucode: str, end_date: str,
                     hold_type: str = HOLD_TYPE_FREE) -> list[dict[str, Any]]:
        """单只股票某一报告期的十大（流通）股东。"""
        rpt = REPORT_NAMES[hold_type]["detail"]
        return self.fetch_all(
            rpt,
            columns=",".join(COLUMNS[rpt]),
            filter_=f"(SECUCODE='{secucode}')(END_DATE='{end_date}')",
            sort_columns="HOLDER_RANK,HOLDER_NAME", sort_types="1,1",
        )

    def holder_history(self, holder_new: str, hold_type: str = HOLD_TYPE_FREE,
                       end_dates: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """某股东的历史持股明细。

        接口里 END_DATE 除了定期报告日还有公告日，调用方一般会传 end_dates 限定范围。
        """
        rpt = REPORT_NAMES[hold_type]["detail"]
        filter_ = f'(HOLDER_NEW="{holder_new}")'
        if end_dates is not None:
            dates = sorted(set(end_dates))
            if not dates:
                return []
            cond = "".join(f"(END_DATE='{d}')" for d in dates)
            filter_ += cond
        return self.fetch_all(
            rpt,
            columns=",".join(COLUMNS[rpt]),
            filter_=filter_,
            sort_columns="END_DATE,SECUCODE", sort_types="-1,1",
        )
