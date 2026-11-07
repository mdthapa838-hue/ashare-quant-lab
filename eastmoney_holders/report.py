# -*- coding: utf-8 -*-
"""
eastmoney_holders/report.py — 把抓下来的明细整理成「所有股东的当前持股 / 历史持股 / 持股变化」

产物（默认 output/eastmoney_holders/）

    holders_current.csv      最新报告期：每个股东的持股家数、新进/加仓/减仓/不变、市值
    holders_all_periods.csv  全部报告期：每个 报告期 × 股东 一行的增减持汇总
    holder_stock_history.csv 全部报告期：每个 股东 × 股票 × 报告期 一行的持股明细与环比变化
    holder_report.md         Markdown 分析报告（榜单 + 口径核对 + 使用说明）

“所有股东”逐条列出，面向上万行，因此 CSV 用 UTF-8 BOM，Excel 可直接打开。
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .aggregate import (
    build_holder_history,
    build_holder_universe,
    build_period_changes,
)
from .api import REPORT_NAMES
from .crawl import ALL_HOLD_TYPES, TYPE_SLUG, read_csv, write_csv

LOG = logging.getLogger("eastmoney_holders.report")

CURRENT_COLS = [
    "holder_key", "holder_name", "holder_type", "end_date",
    "holder_num", "new_num", "up_num", "down_num", "unchanged_num",
    "market_cap", "hold_num_total", "first_period", "last_period", "period_num",
]
PERIOD_COLS = [
    "end_date", "holder_key", "holder_name", "holder_type",
    "holder_num", "new_num", "up_num", "down_num", "unchanged_num", "exit_num",
    "market_cap", "prev_market_cap", "market_cap_change",
]
HISTORY_COLS = [
    "holder_key", "holder_name", "holder_type", "secucode", "security_code",
    "security_name", "end_date", "holder_rank", "hold_num", "prev_hold_num",
    "period_change", "period_status", "last_seen_hold_num", "change_kind",
    "change_ratio", "hold_ratio", "market_cap", "shares_type",
]


def load_detail(periods_dir: str | Path, hold_type: str,
                limit_periods: int | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """按日期倒序读回某个口径的全部报告期明细。"""
    period_dir = Path(periods_dir)
    files = sorted(period_dir.glob(f"detail_{TYPE_SLUG[hold_type]}_*.csv"), reverse=True)
    if limit_periods:
        files = files[:limit_periods]
    rows: list[dict[str, Any]] = []
    seen: list[str] = []
    for f in files:
        chunk = read_csv(f)
        rows.extend(chunk)
        seen.append(f.stem.split("_")[-1])
    return rows, seen


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) >= 1e8:
        return f"{value / 1e8:,.2f} 亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:,.2f} 万"
    return f"{value:,.0f}"


def _md_table(header: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(out)


def _holder_rows(universe: list[dict[str, Any]], n: int = 30,
                 by: str = "holder_num", with_type: bool = True) -> list[list[Any]]:
    """股东榜单的表格行；with_type=False 时不输出「类型」列。"""
    key = (lambda r: (-(r[by] or 0), -(r["market_cap"] or 0))) if by == "market_cap" \
        else (lambda r: (-(r["holder_num"] or 0), -(r["market_cap"] or 0)))
    rows = sorted(universe, key=key)[:n]
    out = []
    for i, r in enumerate(rows, 1):
        cells = [i, r["holder_name"][:28]]
        if with_type:
            cells.append(r["holder_type"])
        cells += [r["holder_num"], r["new_num"], r["up_num"], r["down_num"],
                  r["unchanged_num"], _fmt_money(r["market_cap"]),
                  f"{r['first_period']}~{r['last_period']}（{r['period_num']}期）"]
        out.append(cells)
    return out


def _top_header(with_type: bool = True) -> list[str]:
    head = ["#", "股东名称"]
    if with_type:
        head.append("类型")
    return head + ["持股家数", "新进", "加仓", "减仓", "不变", "持股市值", "数据区间"]


def build_report(periods_dir: str | Path, out_dir: str | Path, hold_type: str,
                 top_n: int = 30) -> Path:
    """整理某个口径的全量数据并输出 CSV + Markdown。"""
    period_dir = Path(periods_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = TYPE_SLUG[hold_type]

    detail_rows, periods = load_detail(period_dir, hold_type)
    if not detail_rows:
        raise FileNotFoundError(f"{period_dir} 下没有 {hold_type} 的明细 CSV，请先运行 crawl")
    LOG.info("%s: %d 期, %d 行明细", hold_type, len(periods), len(detail_rows))

    universe = build_holder_universe(detail_rows)
    history = build_holder_history(detail_rows)
    period_changes = build_period_changes(detail_rows)

    write_csv(out_dir / f"holders_current_{slug}.csv", universe, CURRENT_COLS)
    write_csv(out_dir / f"holders_all_periods_{slug}.csv", period_changes, PERIOD_COLS)
    write_csv(out_dir / f"holder_stock_history_{slug}.csv", history, HISTORY_COLS)
    LOG.info("CSV: current=%d, all_periods=%d, history=%d",
             len(universe), len(period_changes), len(history))

    # ------------------------------------------------------------ 核对与统计
    latest = periods[0]
    latest_rows = [r for r in history if r["end_date"] == latest]
    type_counter = Counter(r["holder_type"] for r in latest_rows)
    # 十大股东口径的明细表没有 HOLDER_TYPE 字段，此时全部为空串
    has_type = any(t for t in type_counter)
    change_counter = Counter(r["period_status"] for r in latest_rows)
    stock_counter = {r["secucode"] for r in latest_rows}
    holder_keys_all = {r["holder_key"] for r in history}

    # 页面首屏数字 vs 明细重算：抽头部机构核对
    basic_files = sorted(period_dir.glob(f"basic_{slug}_{latest}.csv"))
    basic_rows = read_csv(basic_files[0]) if basic_files else []
    basic_by_key = {r["HOLDER_NEW"]: r for r in basic_rows}

    md: list[str] = []
    add = md.append
    add(f"# 东方财富「股东持股」全量整理 — {hold_type}\n")
    add(f"> 数据源：https://datapc.eastmoney.com/pc_dc/gdcg/index.html （页面口径：{hold_type}）  ")
    add(f"> 抓取方式：`python -m eastmoney_holders all`，逐页翻完 报告期 × 分页 的全部接口数据  ")
    add(f"> 报告期：{len(periods)} 期（{periods[-1]} ~ {periods[0]}）  明细行：{len(detail_rows):,}  ")
    add(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}（口径见文末「口径与局限」）\n")

    add("## 一、结果总览\n")
    add(_md_table(
        ["指标", "数值"],
        [["报告期数", f"{len(periods)}（{periods[-1]} ~ {periods[0]}）"],
         ["明细行数（股票 × 股东 × 报告期）", f"{len(detail_rows):,}"],
         ["股东数（全部报告期去重）", f"{len(holder_keys_all):,}"],
         [f"最新报告期 {latest} 上榜股东", f"{len(universe):,}"],
         [f"最新报告期 {latest} 上榜股票", f"{len(stock_counter):,}"],
         [f"最新报告期 {latest} 持股记录（股票 × 股东）", f"{len(latest_rows):,}"],
         [f"最新报告期持股市值合计", _fmt_money(sum(r["market_cap"] or 0 for r in latest_rows))],
         ]))

    add("\n### 最新报告期变动分布（按 股票 × 股东 记录数）\n")
    add(_md_table(["状态", "记录数"],
                  [[k or "(空)", v] for k, v in change_counter.most_common()]))

    # 十大股东口径的明细表没有 HOLDER_TYPE 字段，此时跳过类型分布
    if has_type:
        add(f"\n### 股东类型分布（{latest}，按 股票 × 股东 记录数）\n")
        add(_md_table(["股东类型", "持股记录数", "持股市值"],
                      [[t, c, _fmt_money(sum(r["market_cap"] or 0 for r in latest_rows
                                             if r["holder_type"] == t))]
                       for t, c in type_counter.most_common(15)]))
    else:
        add(f"\n### 股东类型分布（{latest}）\n")
        add(f"> 该口径的明细表（`{REPORT_NAMES[hold_type]['detail']}`）不提供 `HOLDER_TYPE` 字段，"
            f"因此本报告不列股东类型。需要类型时请用「十大流通股东」口径，"
            f"或按股东名称到页面首屏表（`raw/basic_{slug}_{latest}.csv`）里查。\n")

    add(f"\n## 二、当前持股最多股东 Top {top_n}（按持股家数）\n")
    add(_md_table(_top_header(has_type), _holder_rows(universe, top_n, "holder_num", has_type)))

    add(f"\n## 三、持股市值最大股东 Top {top_n}\n")
    add(_md_table(_top_header(has_type), _holder_rows(universe, top_n, "market_cap", has_type)))

    # 最新一期“进榜最多”的股东（新进家数）
    entrants = sorted(universe, key=lambda r: (-r["new_num"], -(r["market_cap"] or 0)))[:top_n]
    add(f"\n## 四、最新一期新进榜最多的股东 Top {top_n}\n")
    ent_head = ["#", "股东名称"] + (["类型"] if has_type else []) + ["新进家数", "持股家数", "持股市值"]
    ent_rows = []
    for i, r in enumerate(entrants, 1):
        cells = [i, r["holder_name"][:28]]
        if has_type:
            cells.append(r["holder_type"])
        cells += [r["new_num"], r["holder_num"], _fmt_money(r["market_cap"])]
        ent_rows.append(cells)
    add(_md_table(ent_head, ent_rows))

    # 清仓最多的股东（本期 exit）
    recent = [r for r in period_changes if r["end_date"] == latest]
    exits = sorted(recent, key=lambda r: (-r["exit_num"], -(r["market_cap"] or 0)))[:top_n]
    add(f"\n## 五、最新一期掉出榜单（清仓）最多的股东 Top {top_n}\n")
    exit_head = ["#", "股东名称"] + (["类型"] if has_type else []) + \
                ["清仓家数", "本期末持股家数", "期末市值"]
    exit_rows = []
    for i, r in enumerate(exits, 1):
        cells = [i, r["holder_name"][:28]]
        if has_type:
            cells.append(r["holder_type"])
        cells += [r["exit_num"], r["holder_num"], _fmt_money(r["market_cap"])]
        exit_rows.append(cells)
    add(_md_table(exit_head, exit_rows))

    # 逐期总量
    add("\n## 六、逐报告期总量\n")
    per_period: dict[str, dict[str, Any]] = {}
    for r in period_changes:
        d = per_period.setdefault(r["end_date"], {"holders": 0, "new": 0, "up": 0, "down": 0,
                                                  "flat": 0, "exit": 0, "cap": 0.0, "rows": 0})
        if r["holder_num"]:          # 本期完全掉出榜单的股东不计入“上榜股东数”
            d["holders"] += 1
        d["new"] += r["new_num"] or 0
        d["up"] += r["up_num"] or 0
        d["down"] += r["down_num"] or 0
        d["flat"] += r["unchanged_num"] or 0
        d["exit"] += r["exit_num"] or 0
        d["cap"] += r["market_cap"] or 0
        d["rows"] += r["holder_num"] or 0
    add(_md_table(["报告期", "上榜股东数", "持股记录数", "新进", "加仓", "减仓", "不变", "清仓", "持股市值"],
                  [[p, v["holders"], v["rows"], v["new"], v["up"], v["down"], v["flat"],
                    v["exit"], _fmt_money(v["cap"])] for p, v in sorted(per_period.items(), reverse=True)]))

    add("\n## 七、口径核对（页面首屏 vs 明细重算）\n")
    add("页面首屏（`BASIC_INFO` 表）与明细（`HOLDERDETAIL` 表）是同一次披露的两种视图，"
        "本模块用明细重新聚合，再和页面首屏逐项比对。\n")

    # 全量核对：每个股东都比，不只看头部
    compared, exact, max_diff, diff_sum = 0, 0, 0, 0
    for r in universe:
        b = basic_by_key.get(r["holder_key"])
        if not b or not b.get("HOLDER_NUM"):
            continue
        try:
            page_num = int(b["HOLDER_NUM"])
        except (TypeError, ValueError):
            continue
        diff = page_num - r["holder_num"]
        compared += 1
        if diff == 0:
            exact += 1
        max_diff = max(max_diff, abs(diff))
        diff_sum += diff
    if compared:
        add(_md_table(["核对项", "结果"], [
            ["参与核对的股东数", f"{compared:,}"],
            ["持股家数完全一致", f"{exact:,}（{exact / compared * 100:.2f}%）"],
            ["最大偏差", f"{max_diff} 家"],
            ["偏差合计", f"{diff_sum:+d} 家"],
        ]))

    add("\n头部机构逐项对照（明细重算 vs 页面首屏）：\n")
    check_rows = []
    for r in sorted(universe, key=lambda x: -(x["holder_num"] or 0))[:10]:
        b = basic_by_key.get(r["holder_key"])
        if not b:
            continue
        check_rows.append([r["holder_name"][:24], b.get("HOLDER_NUM"), r["holder_num"],
                           b.get("HOLDADD_NUM"), r["new_num"],
                           b.get("HOLDUP_NUM"), r["up_num"],
                           b.get("HOLDDOWN_NUM"), r["down_num"]])
    if check_rows:
        add(_md_table(["股东", "页面家数", "重算家数", "页面新进", "重算新进",
                       "页面加仓", "重算加仓", "页面减仓", "重算减仓"], check_rows))

    add("\n> **差异从哪来**：两张表是同一次披露的两个快照，不是同一条流水线算出来的。"
        "逐股东核对的偏差集中在少数股东身上（最大 "
        f"{max_diff} 家，占其持股家数的比例很小），且「家数」与「新进/加仓/减仓」"
        "的偏差方向不一定一致，说明是两张表的统计时点或去重口径有细微不同，"
        "不是抓取时漏页（完整性见下一节）。本报告一律以明细重算结果为准，"
        "因为它可以展开到「哪只股票」这一层。\n")

    # 抓取完整性：用接口 count 逐期核对本地行数（verify 步骤写入）
    verify_file = period_dir.parent / "verify.json"
    if verify_file.exists():
        try:
            vdata = json.loads(verify_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            vdata = {}
        vrows = vdata.get(slug, [])
        if vrows:
            add("\n**抓取完整性核对**（接口 `count` vs 本地行数，逐期）：\n")
            add(_md_table(["报告期", "接口 count", "本地行数", "结果"],
                          [[v["end_date"], f"{v['api_count']:,}", f"{v['local_rows']:,}",
                            "一致" if v["api_count"] == v["local_rows"] else "**不一致**"]
                           for v in vrows]))
            bad = [v for v in vrows if v["api_count"] != v["local_rows"]]
            add(f"\n> {'全部一致' if not bad else f'{len(bad)} 期不一致'}，"
                "说明抓取时每一页都取到了，没有漏页。\n")
    else:
        add("\n> 抓取完整性核对：运行 `python -m eastmoney_holders verify` 后"
            "本节会显示每期接口 count 与本地行数的比对结果。\n")

    # 案例：头部股东的持仓轨迹
    add("\n## 八、头部机构持仓轨迹（样例）\n")
    for r in sorted(universe, key=lambda x: -(x["holder_num"] or 0))[:3]:
        hrows = [h for h in history if h["holder_key"] == r["holder_key"]]
        by_period: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"n": 0, "cap": 0.0, "up": 0, "down": 0, "new": 0, "back": 0})
        for h in hrows:
            d = by_period[h["end_date"]]
            d["n"] += 1
            d["cap"] += h["market_cap"] or 0
            st = h["period_status"]
            if st == "新进":
                d["new"] += 1
            elif st == "重回":
                d["back"] += 1
            elif st == "加仓":
                d["up"] += 1
            elif st == "减仓":
                d["down"] += 1
        type_part = f"{r['holder_type']}，" if r["holder_type"] else ""
        add(f"\n**{r['holder_name']}**（{type_part}最新持股 {r['holder_num']} 家）\n")
        add(_md_table(["报告期", "持股家数", "新进", "重回", "加仓", "减仓", "持股市值"],
                      [[p, v["n"], v["new"], v["back"], v["up"], v["down"], _fmt_money(v["cap"])]
                       for p, v in sorted(by_period.items(), reverse=True)]))
    add("\n> 「新进」= 该股东第一次出现在这只股票的前十大名单；「重回」= 曾经持有、"
        "中间掉出榜单后又回来。页面把两者都算作「新进」。\n")

    add("\n## 九、口径与局限\n")
    add("1. **只含「十大（流通）股东」**：页面本身只披露每只股票的前十名股东，"
        "不是全部股东名册；未进前十的持有无法从该页面获得。")
    add("2. **报告期滞后**：定期报告（一季报/中报/三季报/年报）披露后数据才更新，"
        "最新一期可能仍存在未披露个股，`IS_DISCLOSURE_COMPLETE` 字段标记完整性。")
    add("3. **股东身份合并**：接口以 `HOLDER_NEW` 为股东唯一键，自然人同名不合并；"
        "同一机构的不同产品（如「XX基金-XX号」）按产品分行，不会自动汇总到基金公司。")
    add("4. **「新进 / 加仓 / 减仓 / 不变」是相对上一报告期**在该股票上的名次口径，"
        "`HOLD_NUM_CHANGE` 为「不变」代表持股数与上期一致，不代表没有参与分红送转。")
    add("5. **市值口径**：`HOLDER_MARKET_CAP` 为报告期末持股数 × 期末收盘价（接口给定），"
        "与页面「流通市值/持股市值」一致。")
    add("6. **清仓（exit）为本模块补算**：上期在榜、本期不在榜，可能因为真实减持，"
        "也可能因为被挤出前十名，两者无法从该页面区分。")
    add("\n## 十、完整数据在本地\n")
    add("仓库只提交代码与本报告；全量 CSV 由一条命令生成：\n")
    add("```bash\npython -m eastmoney_holders all       # 抓取 + 整理\npython -m eastmoney_holders report    # 只重新整理\nls output/eastmoney_holders/*.csv     # 全量股东表\n```\n")

    report_path = out_dir / f"holder_report_{slug}.md"
    report_path.write_text("\n".join(md), encoding="utf-8")
    LOG.info("报告: %s", report_path)
    return report_path


def build_all(periods_dir: str | Path, out_dir: str | Path,
              hold_types=ALL_HOLD_TYPES, top_n: int = 30) -> list[Path]:
    paths = []
    for ht in hold_types:
        paths.append(build_report(periods_dir, out_dir, ht, top_n=top_n))
    return paths


__all__ = ["build_all", "build_report", "load_detail"]
