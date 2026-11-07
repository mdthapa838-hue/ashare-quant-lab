# -*- coding: utf-8 -*-
"""
eastmoney_holders/crawl.py — 抓取页面的全部分页并落盘

抓取范围
--------
页面有两个口径（十大流通股东 / 十大股东），每个口径都有 20 个报告期
（2021年报 ~ 2026三季报），每个报告期的股东明细约 5~5.7 万行，按 2000 行/页
需要 29 页左右。本模块把「全部分页」都翻完，并按 报告期 × 口径 存成 CSV。

落盘结构（默认 output/eastmoney_holders/）

    raw/dates_free.json / dates_total.json          报告期列表
    raw/detail_free_2026-09-30.csv                  股东明细（股票 × 股东）
    raw/detail_total_2026-09-30.csv
    raw/basic_free_2026-09-30.csv                   页面首屏的股东列表（持股家数/新进/加仓/减仓）
    raw/basic_total_2026-09-30.csv
    raw/changerate_free_2026-09-30.csv              （可选）持股平均涨幅 / 上涨比例

每个 CSV 都是接口原样字段，便于复核；二次整理见 aggregate.py。
"""
from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .api import (
    COLUMNS,
    HOLD_TYPE_FREE,
    HOLD_TYPE_TOTAL,
    EastmoneyClient,
    EastmoneyError,
    REPORT_NAMES,
)

LOG = logging.getLogger("eastmoney_holders.crawl")

ALL_HOLD_TYPES = (HOLD_TYPE_FREE, HOLD_TYPE_TOTAL)
TYPE_SLUG = {HOLD_TYPE_FREE: "free", HOLD_TYPE_TOTAL: "total"}


def write_csv(path: Path, rows: Iterable[dict[str, Any]], columns: list[str] | None = None) -> int:
    """写 CSV（UTF-8 BOM，Excel 直接打开不乱码）。返回行数。

    空数据也会写出表头，保证下游读到的列名稳定。
    """
    rows = list(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = list(rows[0].keys()) if rows else []
    if not columns:
        path.write_text("", encoding="utf-8-sig")
        return 0
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in columns})
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return len(rows)


def _detail_columns(hold_type: str) -> list[str]:
    return COLUMNS[REPORT_NAMES[hold_type]["detail"]]


def _basic_columns(hold_type: str) -> list[str]:
    return COLUMNS[REPORT_NAMES[hold_type]["basic_info"]]


def fetch_dates(client: EastmoneyClient, out_dir: Path,
                hold_types: Iterable[str] = ALL_HOLD_TYPES) -> dict[str, list[dict[str, Any]]]:
    """取两个口径各自的报告期列表。"""
    result: dict[str, list[dict[str, Any]]] = {}
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for ht in hold_types:
        rows = client.report_dates(ht)
        slug = TYPE_SLUG[ht]
        write_csv(raw_dir / f"dates_{slug}.csv", rows,
                  ["END_DATE", "REPORT_NAME", "HOLD_TYPE", "IS_DISCLOSURE_COMPLETE"])
        (raw_dir / f"dates_{slug}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        result[ht] = rows
        LOG.info("%s: %d 个报告期 (%s ~ %s)", ht, len(rows),
                 rows[-1]["END_DATE"][:10] if rows else "-",
                 rows[0]["END_DATE"][:10] if rows else "-")
    return result


def crawl_period(client: EastmoneyClient, out_dir: Path, end_date: str, hold_type: str,
                 report_name: str, *, with_basic: bool = True,
                 with_change_rate: bool = False, refresh: bool = False) -> dict[str, Any]:
    """抓取一个 报告期 × 口径 的全部分页。"""
    slug = TYPE_SLUG[hold_type]
    detail_path = out_dir / "raw" / f"detail_{slug}_{end_date}.csv"
    stats: dict[str, Any] = {"hold_type": hold_type, "end_date": end_date,
                             "report_name": report_name, "detail_rows": 0, "pages": 0}
    if detail_path.exists() and not refresh:
        with open(detail_path, "r", encoding="utf-8-sig", newline="") as f:
            stats["detail_rows"] = sum(1 for _ in f) - 1
        stats["cached"] = True
        LOG.info("跳过（已存在）: %s", detail_path.name)
        return stats

    LOG.info("抓取 %s %s（%s）…", hold_type, end_date, report_name)
    rows = client.fetch_all(
        REPORT_NAMES[hold_type]["detail"],
        columns=",".join(_detail_columns(hold_type)),
        filter_=f"(END_DATE='{end_date}')(IS_REPORT=\"1\")",
        sort_columns="SECUCODE,HOLDER_RANK,HOLDER_NAME", sort_types="1,1,1",
    )
    n = write_csv(detail_path, rows, _detail_columns(hold_type))
    stats.update(detail_rows=n, pages=(n + client.page_size - 1) // client.page_size,
                 cached=False, columns=_detail_columns(hold_type))
    LOG.info("  -> %d 行, %d 页, 写入 %s", n, stats["pages"], detail_path.name)

    if with_basic:
        basic_path = out_dir / "raw" / f"basic_{slug}_{end_date}.csv"
        brows = client.holder_list(end_date, hold_type)
        write_csv(basic_path, brows, _basic_columns(hold_type))
        stats["basic_rows"] = len(brows)
        LOG.info("  -> 股东列表 %d 行", len(brows))

    if with_change_rate:
        cr_path = out_dir / "raw" / f"changerate_{slug}_{end_date}.csv"
        try:
            crows = client.holder_change_rate(end_date, hold_type)
        except EastmoneyError as e:
            # 平均涨幅只是锦上添花，抓不到不影响股东持股主表
            LOG.warning("  -> 持股平均涨幅抓取失败（跳过）: %s", e)
            stats["changerate_rows"] = 0
        else:
            write_csv(cr_path, crows, COLUMNS[REPORT_NAMES[hold_type]["changerate"]])
            stats["changerate_rows"] = len(crows)
            LOG.info("  -> 平均涨幅 %d 行", len(crows))
    return stats


def crawl(client: EastmoneyClient, out_dir: str | Path, *,
          hold_types: Iterable[str] = ALL_HOLD_TYPES,
          limit_periods: int | None = None,
          with_change_rate: bool = False,
          refresh: bool = False) -> dict[str, Any]:
    """抓全部报告期 × 全部口径的全部分页。"""
    out_dir = Path(out_dir)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    dates = fetch_dates(client, out_dir, hold_types)
    summary: dict[str, Any] = {"base_url": client.base_url, "periods": {}, "totals": {}}
    total_rows = 0
    for ht in hold_types:
        slug = TYPE_SLUG[ht]
        periods = [r["END_DATE"][:10] for r in dates[ht]]
        if limit_periods:
            periods = periods[:limit_periods]
        summary["periods"][slug] = []
        for i, end_date in enumerate(periods, 1):
            rpt = next((r["REPORT_NAME"] for r in dates[ht] if r["END_DATE"][:10] == end_date), "")
            # 平均涨幅只在最新报告期抓，其余报告期可加 --with-change-rate 打开
            want_cr = with_change_rate or (i == 1)
            st = crawl_period(client, out_dir, end_date, ht, rpt,
                              with_basic=True, with_change_rate=want_cr, refresh=refresh)
            summary["periods"][slug].append(st)
            total_rows += st.get("detail_rows", 0)
    summary["totals"] = {
        "detail_rows": total_rows,
        "requests": client.requests,
        "retries": client.retries,
    }
    (out_dir / "crawl_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("抓取完成：%d 行明细，%d 次请求，%d 次重试", total_rows, client.requests, client.retries)
    return summary


def read_csv(path: str | Path) -> list[dict[str, str]]:
    """读回落盘的 CSV（全部为字符串，空串表示空值）。"""
    path = Path(path)
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


__all__ = [
    "ALL_HOLD_TYPES",
    "TYPE_SLUG",
    "EastmoneyError",
    "crawl",
    "crawl_period",
    "fetch_dates",
    "read_csv",
    "write_csv",
]
