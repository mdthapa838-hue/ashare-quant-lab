# -*- coding: utf-8 -*-
"""
eastmoney_holders/verify.py — 核对本地抓取是否完整

抓完每一期后，再用一个只取 1 行的请求问服务端「这一期到底有多少行」（`count`），
和本地 CSV 的行数比对。两者一致才说明分页没有漏。

结果写到 `<out_dir>/verify.json`，report 步骤会把它渲染进 Markdown。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .api import EastmoneyClient, REPORT_NAMES
from .crawl import ALL_HOLD_TYPES, TYPE_SLUG, read_csv

LOG = logging.getLogger("eastmoney_holders.verify")


def count_local(path: Path) -> int:
    """本地 CSV 的数据行数（不含表头）。"""
    if not path.exists():
        return 0
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return max(sum(1 for _ in f) - 1, 0)


def verify(client: EastmoneyClient, out_dir: str | Path,
           hold_types=ALL_HOLD_TYPES) -> dict[str, list[dict[str, Any]]]:
    """逐期比对「接口 count」与「本地行数」。"""
    out_dir = Path(out_dir)
    raw = out_dir / "raw"
    result: dict[str, list[dict[str, Any]]] = {}
    for ht in hold_types:
        slug = TYPE_SLUG[ht]
        rows: list[dict[str, Any]] = []
        files = sorted(raw.glob(f"detail_{slug}_*.csv"), reverse=True)
        for f in files:
            end_date = f.stem.split("_")[-1]
            local = count_local(f)
            api_count = client.count(REPORT_NAMES[ht]["detail"],
                                     filter_=f"(END_DATE='{end_date}')(IS_REPORT=\"1\")")
            ok = api_count == local
            rows.append({"end_date": end_date, "api_count": api_count,
                         "local_rows": local, "ok": ok})
            LOG.info("%s %s: 接口 %d / 本地 %d %s", ht, end_date, api_count, local,
                     "OK" if ok else "不一致")
        result[slug] = rows
    path = out_dir / "verify.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = sum(1 for rows in result.values() for r in rows if not r["ok"])
    LOG.info("核对完成：%d 期不一致，结果写入 %s", bad, path)
    return result


__all__ = ["count_local", "verify"]
