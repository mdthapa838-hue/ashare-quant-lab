# -*- coding: utf-8 -*-
"""
eastmoney_holders/aggregate.py — 把“股票 × 股东”明细整理成“每个股东”的持股与变动

页面首屏只给到“某个报告期内每个股东持有多少家股票、新进/加仓/减仓多少家”，
要看“某个股东现在持有哪些股票、过去持有哪些股票、每家股票怎么变”，
需要把它自己的股东明细逐个报告期对齐。本模块做三件事：

1. `build_holder_universe`   —— 每个股东的画像：最新报告期的持股家数、加仓/减仓/新进
   （与页面首屏一致，可用来校验），以及它出现过的全部报告期。
2. `build_holder_history`    —— 每个股东 × 每只股票 × 每个报告期的持股明细，含环比变化量。
3. `build_period_changes`    —— 每个报告期相对上一个报告期的增减持汇总（谁新进、谁清仓）。

所有函数只吃 list[dict]，不依赖 pandas，方便离线测试。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

CHANGE_BUY = "加仓"
CHANGE_SELL = "减仓"
CHANGE_NEW = "新进"
CHANGE_FLAT = "不变"
CHANGE_EXIT = "清仓"

#: 接口 HOLD_NUM_CHANGE 的取值约定：'不变' / '新进' / 数字字符串（正=加仓，负=减仓）
SPECIAL_CHANGES = {CHANGE_FLAT, CHANGE_NEW}


def to_number(value: Any) -> float | None:
    """把接口里的数字/数字字符串/空值统一成 float（空返回 None）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s or s in ("-", "--", "null", "None", "nan", "NaN"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def classify_change(raw: Any) -> tuple[str, float | None]:
    """把原始 HOLD_NUM_CHANGE 归一为 (类别, 变化股数)。

    '新进'  -> (新进, None)   本期新出现在十大（流通）股东名单里
    '不变'  -> (不变, 0.0)
    '12345' -> (加仓, 12345.0)
    '-1234' -> (减仓, -1234.0)
    空值    -> ('', None)
    """
    if raw is None:
        return "", None
    s = str(raw).strip()
    if not s:
        return "", None
    if s == CHANGE_FLAT:
        return CHANGE_FLAT, 0.0
    if s == CHANGE_NEW:
        return CHANGE_NEW, None
    num = to_number(s)
    if num is None:
        return s, None
    if num > 0:
        return CHANGE_BUY, num
    if num < 0:
        return CHANGE_SELL, num
    return CHANGE_FLAT, 0.0


def normalize_detail_row(row: dict[str, Any]) -> dict[str, Any]:
    """把接口明细行整理成分析用的扁平行。"""
    kind, delta = classify_change(row.get("HOLD_NUM_CHANGE"))
    hold_num = to_number(row.get("HOLD_NUM"))
    return {
        "end_date": (row.get("END_DATE") or "")[:10],
        "secucode": row.get("SECUCODE") or "",
        "security_code": row.get("SECURITY_CODE") or "",
        "security_name": row.get("SECURITY_NAME_ABBR") or "",
        "holder_key": row.get("HOLDER_NEW") or row.get("HOLDER_NAME") or "",
        "holder_name": row.get("HOLDER_NAME") or "",
        "holder_type": row.get("HOLDER_TYPE") or "",
        "holder_rank": to_number(row.get("HOLDER_RANK")),
        "hold_num": hold_num,
        "change_kind": kind,
        "change_num": delta,
        "change_ratio": to_number(row.get("CHANGE_RATIO")),
        "hold_ratio": to_number(row.get("HOLD_RATIO") if row.get("HOLD_RATIO") is not None
                                else row.get("HOLD_NUM_RATIO")),
        "market_cap": to_number(row.get("HOLDER_MARKET_CAP")),
        "shares_type": row.get("SHARES_TYPE") or "",
    }


def _group_by(rows: Iterable[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


def build_holder_universe(detail_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按股东聚合出“当前持股画像”。

    输入是全部报告期的明细行；输出最新报告期内上榜的每个股东一行，字段含义与页面首屏一致：

        holder_num     最新报告期持股家数
        new_num        新进（在最新报告期首次出现）
        up_num         加仓
        down_num       减仓
        unchanged_num  不变
        market_cap     最新报告期持股市值合计（元）
        first_period / last_period / period_num   出现在哪些报告期

    只在历史报告期出现过、最新报告期已完全掉出榜单的股东不进入本表
    （它们的历史仍保留在 build_holder_history / build_period_changes 里）。
    """
    rows = [normalize_detail_row(r) for r in detail_rows]
    if not rows:
        return []
    latest = max(r["end_date"] for r in rows)
    by_holder = _group_by(rows, "holder_key")

    out: list[dict[str, Any]] = []
    for key, hrows in by_holder.items():
        cur = [r for r in hrows if r["end_date"] == latest]
        if not cur:            # 本期已不在榜
            continue
        periods = sorted({r["end_date"] for r in hrows})
        counter = {CHANGE_NEW: 0, CHANGE_BUY: 0, CHANGE_SELL: 0, CHANGE_FLAT: 0}
        for r in cur:
            if r["change_kind"] in counter:
                counter[r["change_kind"]] += 1
        out.append({
            "holder_key": key,
            "holder_name": cur[0]["holder_name"],
            "holder_type": cur[0]["holder_type"],
            "end_date": latest,
            "holder_num": len(cur),
            "new_num": counter[CHANGE_NEW],
            "up_num": counter[CHANGE_BUY],
            "down_num": counter[CHANGE_SELL],
            "unchanged_num": counter[CHANGE_FLAT],
            "market_cap": round(sum(r["market_cap"] or 0 for r in cur), 2),
            "hold_num_total": round(sum(r["hold_num"] or 0 for r in cur), 2),
            "first_period": periods[0],
            "last_period": periods[-1],
            "period_num": len(periods),
        })
    out.sort(key=lambda r: (-r["holder_num"], -(r["market_cap"] or 0)))
    return out


def build_holder_history(detail_rows: Iterable[dict[str, Any]],
                         holder_keys: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """每个 股东 × 股票 × 报告期 一行的历史明细，附带与上一个报告期的对比。

    增加的字段：
        prev_hold_num       紧邻上一报告期该股东在这只股票上的持股（上期未上榜则空）
        period_change       本期相对上期的股数变化（非连续期为空）
        period_status       新进 / 重回 / 加仓 / 减仓 / 不变 / 持有
        last_seen_hold_num  若中间有缺口，缺口前最近一次的持股数（便于判断“重回”的基数）

    说明：接口的 HOLD_NUM_CHANGE 是相对“上一报告期”的名次口径；若股东中间几期
    掉出前十大，接口会直接给“新进”，这里改标为「重回」并给出回来的基数。
    """
    all_rows = [normalize_detail_row(r) for r in detail_rows]
    if not all_rows:
        return []
    # 报告期序列必须来自“全部股东”，否则筛掉某股东后会把缺口误判成连续期
    periods = sorted({r["end_date"] for r in all_rows})
    period_index = {p: i for i, p in enumerate(periods)}
    rows = all_rows
    if holder_keys is not None:
        wanted = set(holder_keys)
        rows = [r for r in all_rows if r["holder_key"] in wanted]
    if not rows:
        return []

    # (holder, secucode) -> {period: row}
    series: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in rows:
        series[(r["holder_key"], r["secucode"])][r["end_date"]] = r

    out: list[dict[str, Any]] = []
    for (hkey, secu), by_period in series.items():
        for p in sorted(by_period, key=lambda x: period_index[x]):
            r = by_period[p]
            idx = period_index[p] - 1
            prev = by_period.get(periods[idx]) if idx >= 0 else None
            prev_num = prev["hold_num"] if prev else None

            # 缺口前最近一次的持股数（本报告期之前出现过就行）
            last_seen = None
            for j in range(idx, -1, -1):
                earlier = by_period.get(periods[j])
                if earlier is not None:
                    last_seen = earlier["hold_num"]
                    break

            if prev is not None:
                status = r["change_kind"] or "持有"
                if r["hold_num"] is not None and prev_num is not None:
                    delta = r["hold_num"] - prev_num
                else:
                    delta = None
            elif last_seen is not None:
                status = "重回"          # 中间掉出过榜单，与缺口前不可直接比
                delta = None
            else:
                status = CHANGE_NEW      # 该股上是新进，变化量即本期持股数
                delta = r["hold_num"]
            out.append({**r,
                        "prev_hold_num": prev_num,
                        "period_change": delta,
                        "period_status": status,
                        "last_seen_hold_num": last_seen})
    out.sort(key=lambda r: (r["holder_key"], r["secucode"], r["end_date"]))
    return out


def build_period_changes(detail_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个报告期相对上一报告期的增减持汇总。

    返回每个 报告期 × 股东 一行：
        holder_num      该期末持股家数
        new_num / up_num / down_num / unchanged_num / exit_num
        market_cap      该期末持股市值合计
        market_cap_change  与上一报告期相比的市值变化
    其中 exit_num（清仓）是本模块相对页面首屏额外补的：上期持有、本期不在榜单。
    """
    rows = [normalize_detail_row(r) for r in detail_rows]
    if not rows:
        return []
    periods = sorted({r["end_date"] for r in rows})
    by_period: dict[str, list[dict[str, Any]]] = _group_by(rows, "end_date")

    # (holder, secucode) -> 该期持股，用于算清仓
    holdings: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for p in periods:
        holdings[p] = {(r["holder_key"], r["secucode"]): r for r in by_period[p]}

    out: list[dict[str, Any]] = []
    for i, p in enumerate(periods):
        prev_p = periods[i - 1] if i else None
        cur_rows = by_period[p]
        holders = _group_by(cur_rows, "holder_key")
        prev_holdings = holdings.get(prev_p, {}) if prev_p else {}
        prev_by_holder: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for (hk, _secu), r in prev_holdings.items():
            prev_by_holder[hk].append(r)

        all_keys = set(holders) | set(prev_by_holder)
        for hk in all_keys:
            cur = holders.get(hk, [])
            prev = prev_by_holder.get(hk, [])
            counter = {CHANGE_NEW: 0, CHANGE_BUY: 0, CHANGE_SELL: 0, CHANGE_FLAT: 0}
            for r in cur:
                if r["change_kind"] in counter:
                    counter[r["change_kind"]] += 1
            cur_keys = {(r["holder_key"], r["secucode"]) for r in cur}
            exit_num = sum(1 for r in prev if (r["holder_key"], r["secucode"]) not in cur_keys) if prev else 0
            cap = round(sum(r["market_cap"] or 0 for r in cur), 2)
            prev_cap = round(sum(r["market_cap"] or 0 for r in prev), 2) if prev else None
            base = cur[0] if cur else prev[0]
            out.append({
                "end_date": p,
                "holder_key": hk,
                "holder_name": base["holder_name"],
                "holder_type": base["holder_type"],
                "holder_num": len(cur),
                "new_num": counter[CHANGE_NEW],
                "up_num": counter[CHANGE_BUY],
                "down_num": counter[CHANGE_SELL],
                "unchanged_num": counter[CHANGE_FLAT],
                "exit_num": exit_num,
                "market_cap": cap,
                "prev_market_cap": prev_cap,
                "market_cap_change": None if prev_cap is None else round(cap - prev_cap, 2),
            })
    out.sort(key=lambda r: (r["end_date"], -r["holder_num"], -(r["market_cap"] or 0)))
    return out


__all__ = [
    "CHANGE_BUY",
    "CHANGE_EXIT",
    "CHANGE_FLAT",
    "CHANGE_NEW",
    "CHANGE_SELL",
    "build_holder_history",
    "build_holder_universe",
    "build_period_changes",
    "classify_change",
    "normalize_detail_row",
    "to_number",
]
