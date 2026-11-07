# -*- coding: utf-8 -*-
"""
eastmoney_holders/screen.py — 按股东类型筛选「重仓股」

用法示例：

    python -m eastmoney_holders screen --holder-type 全国社保基金

输出（默认 output/eastmoney_holders/screen/）：

    screen_<type>_<date>.csv   每只股票一行：持股市值、持股家数、合计股数、占流通比、环比
    screen_<type>_<date>.md    同一份内容的 Markdown 榜单

「重仓」的排序口径：默认按该类股东在该股票上的**合计持股市值**降序，
也可以用 --sort holders（按持有的组合家数）或 --sort ratio（按占流通股比例）。
"""
from __future__ import annotations

import csv
import logging

from pathlib import Path
from typing import Any, Iterable

from .aggregate import to_number
from .crawl import TYPE_SLUG

LOG = logging.getLogger("eastmoney_holders.screen")

COLUMNS = [
    "security_code", "security_name", "secucode", "end_date",
    "market_cap", "hold_num_total", "ratio_of_float",
    "portfolio_num", "portfolios",
    "change_status", "hold_num_change", "hold_num_change_pct",
    "new_portfolio_num", "add_num", "cut_num", "flat_num", "exit_portfolio_num",
    "prev_market_cap", "prev_hold_num_total", "prev_portfolio_num",
]


def _change_kind(raw: Any) -> str:
    v = str(raw or "").strip()
    if v in ("新进", "不变", "加仓", "减仓"):
        return v
    n = to_number(v)
    if n is None:
        return ""
    if n > 0:
        return "加仓"
    if n < 0:
        return "减仓"
    return "不变"


def _load(period_dir: Path, slug: str, end_date: str) -> list[dict[str, str]]:
    path = period_dir / f"detail_{slug}_{end_date}.csv"
    if not path.exists():
        raise FileNotFoundError(f"缺少 {path}，请先运行 crawl")
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _aggregate(rows: Iterable[dict[str, str]], holder_type: str) -> dict[str, dict[str, Any]]:
    """按股票汇总某类股东的持仓。

    同一只股票可能有多只社保组合同时进入前十大，因此这里做三件事：
    合计市值/股数/占比、记录组合名（按 HOLDER_NEW 去重，避免同一组合的
    简称与全称被算成两个），以及统计每个组合自身的加减仓动作。
    """
    by_stock: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("HOLDER_TYPE") != holder_type:
            continue
        code = r.get("SECUCODE") or ""
        d = by_stock.setdefault(code, {
            "secucode": code,
            "security_code": r.get("SECURITY_CODE") or "",
            "security_name": r.get("SECURITY_NAME_ABBR") or "",
            "end_date": (r.get("END_DATE") or "")[:10],
            "market_cap": 0.0,
            "hold_num_total": 0.0,
            "ratio_of_float": 0.0,
            "_names": {},
            "new_portfolio_num": 0, "add_num": 0, "cut_num": 0, "flat_num": 0,
        })
        d["market_cap"] += to_number(r.get("HOLDER_MARKET_CAP")) or 0.0
        d["hold_num_total"] += to_number(r.get("HOLD_NUM")) or 0.0
        d["ratio_of_float"] += to_number(r.get("FREE_HOLDNUM_RATIO")) or 0.0
        key = r.get("HOLDER_NEW") or r.get("HOLDER_NAME") or ""
        name = r.get("HOLDER_NAME") or ""
        if name:
            d["_names"].setdefault(key, name)   # 同一组合只记一次
        kind = _change_kind(r.get("HOLD_NUM_CHANGE"))
        if kind == "新进":
            d["new_portfolio_num"] += 1
        elif kind == "加仓":
            d["add_num"] += 1
        elif kind == "减仓":
            d["cut_num"] += 1
        elif kind == "不变":
            d["flat_num"] += 1

    for d in by_stock.values():
        names = sorted(d.pop("_names").values())
        d["portfolios"] = ";".join(names)
        d["portfolio_num"] = len(names)
        d["market_cap"] = round(d["market_cap"], 2)
        d["hold_num_total"] = round(d["hold_num_total"], 2)
        d["ratio_of_float"] = round(d["ratio_of_float"], 4)

    return by_stock


def screen(period_dir: str | Path, out_dir: str | Path, holder_type: str,
           end_date: str, hold_type: str = "十大流通股东",
           prev_end_date: str | None = None, sort_by: str = "market_cap",
           top_n: int = 50) -> Path:
    """筛选某类股东的重仓股，输出 CSV + Markdown。"""
    period_dir = Path(period_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = TYPE_SLUG[hold_type]

    cur = _aggregate(_load(period_dir, slug, end_date), holder_type)
    if not cur:
        raise ValueError(f"{end_date} 没有 {holder_type} 的记录")

    prev: dict[str, dict[str, Any]] = {}
    if prev_end_date:
        try:
            prev = _aggregate(_load(period_dir, slug, prev_end_date), holder_type)
        except FileNotFoundError:
            prev = {}

    for code, d in cur.items():
        p = prev.get(code)
        if p is None:
            # 上期没有该股票 → 本期新进
            d["change_status"] = "新进" if prev else ""
            d["hold_num_change"] = d["hold_num_total"] if prev else None
            d["hold_num_change_pct"] = None
            d["prev_market_cap"] = None
            d["prev_hold_num_total"] = None
            d["prev_portfolio_num"] = None
            d["exit_portfolio_num"] = 0
            continue
        # 用「合计股数」判断加减仓：个别组合新进但别的组合减得更多时，
        # 该股票整体仍是减持，不能只看单一组合的 HOLD_NUM_CHANGE。
        delta = d["hold_num_total"] - p["hold_num_total"]
        d["hold_num_change"] = round(delta, 2)
        d["hold_num_change_pct"] = round(delta / p["hold_num_total"] * 100, 2) if p["hold_num_total"] else None
        d["change_status"] = ("加仓" if delta > 0 else "减仓" if delta < 0 else "不变")
        d["prev_market_cap"] = round(p["market_cap"], 2)
        d["prev_hold_num_total"] = round(p["hold_num_total"], 2)
        d["prev_portfolio_num"] = p["portfolio_num"]
        d["exit_portfolio_num"] = 0

    # 上期持有、本期掉出前十大流通股东名单的组合数
    if prev:
        for code, p in prev.items():
            if code in cur:
                cur[code]["exit_portfolio_num"] = max(
                    p["portfolio_num"] - cur[code]["portfolio_num"], 0)

    sort_keys = {
        "market_cap": lambda d: (-(d["market_cap"] or 0), d["security_code"]),
        "holders": lambda d: (-d["portfolio_num"], -(d["market_cap"] or 0)),
        "ratio": lambda d: (-(d["ratio_of_float"] or 0), -(d["market_cap"] or 0)),
    }
    rows = sorted(cur.values(), key=sort_keys.get(sort_by, sort_keys["market_cap"]))

    csv_path = out_dir / f"screen_{holder_type}_{end_date}.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for d in rows:
            w.writerow(d)
    LOG.info("写入 %s（%d 只股票）", csv_path, len(rows))

    # ------------------------------------------------------------ Markdown
    def money(v):
        return "-" if v is None else f"{v / 1e8:,.2f} 亿"

    def pct(v):
        return "-" if v is None else f"{v:+.1f}%"

    head = ["#", "代码", "名称", "持股市值", "组合数", "合计持股(万股)",
            "占流通股", "环比(股)", "环比幅度", "状态"]
    body = []
    total_stocks = len(rows)
    total_cap = sum(d["market_cap"] for d in rows)
    total_hold = sum(d["hold_num_total"] for d in rows)
    all_names = {n for d in rows for n in d["portfolios"].split(";") if n}

    for i, d in enumerate(rows[:top_n], 1):
        body.append([
            i, d["security_code"], d["security_name"], money(d["market_cap"]),
            d["portfolio_num"], f"{d['hold_num_total'] / 1e4:,.1f}",
            f"{d['ratio_of_float']:.2f}%",
            f"{d['hold_num_change']:+,.0f}" if d["hold_num_change"] is not None else "-",
            pct(d["hold_num_change_pct"]), d["change_status"],
        ])

    status_all = {
        "新进": sum(1 for d in rows if d["change_status"] == "新进"),
        "加仓": sum(1 for d in rows if d["change_status"] == "加仓"),
        "减仓": sum(1 for d in rows if d["change_status"] == "减仓"),
        "不变": sum(1 for d in rows if d["change_status"] == "不变"),
    }

    md = [f"# {holder_type} 重仓股 — {end_date}（{hold_type}口径）\n",
          f"> 数据源：东方财富数据中心（本仓库 eastmoney_holders 抓取）  ",
          f"> 口径：{holder_type}共有 {len(all_names)} 个组合出现在 {total_stocks} 只股票的"
          f"前十大流通股东名单里，合计持股市值 {money(total_cap)}、合计持股 "
          f"{total_hold / 1e8:.2f} 亿股。  "]
    if prev:
        md.append(f"> 与 {prev_end_date} 相比：新进 {status_all['新进']} 只、"
                  f"加仓 {status_all['加仓']} 只、减仓 {status_all['减仓']} 只、"
                  f"不变 {status_all['不变']} 只。\n")
    else:
        md.append("\n")

    sort_label = {"market_cap": "合计持股市值", "holders": "持有组合数",
                  "ratio": "占流通股比例"}[sort_by]
    md.append(f"\n## Top {min(top_n, total_stocks)}（按{sort_label}排序）\n")
    md.append("| " + " | ".join(head) + " |")
    md.append("|" + "|".join(["---"] * len(head)) + "|")
    for r in body:
        md.append("| " + " | ".join(str(c) for c in r) + " |")

    md.append("\n## 口径说明\n")
    md.append("1. **「重仓」= 进入某只股票的前十大流通股东名单**。页面只披露前十大，"
              "未进前十的持仓不在此列，因此这是「可见重仓」，不是社保全部持仓。")
    md.append("2. **环比按合计股数判断**：同一只股票可能有多只社保组合同时上榜，"
              "个别组合新进、其他组合减得更多时，该股票整体仍是减持；"
              "只看单一组合的「新进/加仓」会得出相反的结论。")
    md.append("3. **市值变化含股价涨跌**：`market_cap` 是期末持股数 × 期末收盘价，"
              "因此市值下降不等于减持，判断增减持请看「环比股数/环比」。")
    md.append("4. **组合去重**：同一组合可能同时以简称和全称出现（如"
              "「社保基金2103组合」与「国泰基金管理有限公司-社保基金2103组合」），"
              "本表按股东唯一编码去重。")
    md.append("5. **「基本养老基金」不在此表**：那是养老金组合，社保与养老分开统计；"
              "需要时用 `--holder-type 基本养老基金` 单独筛。")

    md_path = out_dir / f"screen_{holder_type}_{end_date}.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    LOG.info("写入 %s", md_path)
    return csv_path


__all__ = ["COLUMNS", "screen"]
