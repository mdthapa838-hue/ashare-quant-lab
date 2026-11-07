# -*- coding: utf-8 -*-
"""
eastmoney_holders/cli.py — 命令行入口

用法
----
    python -m eastmoney_holders crawl     # 抓全部报告期 × 全部口径的全部分页
    python -m eastmoney_holders report    # 只做整理（读已抓取的 CSV）
    python -m eastmoney_holders all       # crawl + report
    python -m eastmoney_holders dates     # 只看报告期列表

常用参数
--------
    --out DIR             产物目录，默认 output/eastmoney_holders
    --type free|total|both  页面口径：十大流通股东 / 十大股东 / 两者
    --periods N           只抓最近 N 个报告期（默认全部 20 个）
    --with-change-rate    额外抓「持股平均涨幅 / 上涨比例」（最新期默认会抓）
    --refresh             忽略已有 CSV，重新抓
    --top N               报告里榜单条数，默认 30
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .api import HOLD_TYPE_FREE, HOLD_TYPE_TOTAL, EastmoneyClient, EastmoneyError
from .crawl import ALL_HOLD_TYPES, TYPE_SLUG, crawl
from .report import build_all

LOG = logging.getLogger("eastmoney_holders.cli")
DEFAULT_OUT = Path("output") / "eastmoney_holders"


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _hold_types(value: str) -> tuple[str, ...]:
    if value == "free":
        return (HOLD_TYPE_FREE,)
    if value == "total":
        return (HOLD_TYPE_TOTAL,)
    return ALL_HOLD_TYPES


def _client(args) -> EastmoneyClient:
    kwargs = dict(min_interval=args.interval, retry=args.retry, page_size=args.page_size)
    if args.base_url:
        kwargs["base_url"] = args.base_url
    return EastmoneyClient(**kwargs)


def cmd_dates(args) -> int:
    client = _client(args)
    for ht in _hold_types(args.type):
        rows = client.report_dates(ht)
        print(f"\n=== {ht}（{len(rows)} 期）===")
        for r in rows:
            print(f"  {r['END_DATE'][:10]}  {r['REPORT_NAME']:<10} 完整={r.get('IS_DISCLOSURE_COMPLETE')}")
    return 0


def cmd_crawl(args) -> int:
    summary = crawl(_client(args), args.out, hold_types=_hold_types(args.type),
                    limit_periods=args.periods, with_change_rate=args.with_change_rate,
                    refresh=args.refresh)
    t = summary["totals"]
    print(f"\n抓取完成：明细 {t['detail_rows']:,} 行，{t['requests']} 次请求，{t['retries']} 次重试")
    return 0


def cmd_report(args) -> int:
    paths = build_all(Path(args.out) / "raw", args.out,
                      hold_types=_hold_types(args.type), top_n=args.top)
    for p in paths:
        print(f"报告: {p}")
    out = Path(args.out)
    for p in sorted(out.glob("*.csv")):
        print(f"  {p}  ({p.stat().st_size / 1e6:.1f} MB)")
    return 0


def cmd_screen(args) -> int:
    from .screen import screen

    period_dir = Path(args.out) / "raw"
    # 默认最新期 = raw 目录里 detail 文件最大的日期
    end_date = args.period
    prev = args.prev_period
    if end_date is None:
        files = sorted(period_dir.glob(f"detail_{TYPE_SLUG[_hold_types(args.type)[0]]}_*.csv"),
                       reverse=True)
        if not files:
            print("[错误] 还没有抓取数据，请先运行 crawl", file=sys.stderr)
            return 2
        end_date = files[0].stem.split("_")[-1]
        prev = prev if prev is not None else (
            files[1].stem.split("_")[-1] if len(files) > 1 else None)
    path = screen(period_dir, Path(args.out) / "screen", args.holder_type, end_date,
                  hold_type=_hold_types(args.type)[0], prev_end_date=prev,
                  sort_by=args.sort, top_n=args.top)
    print(f"结果: {path}")
    print(f"榜单: {path.with_suffix('.md')}")
    return 0


def cmd_verify(args) -> int:
    from .verify import verify

    result = verify(_client(args), args.out, hold_types=_hold_types(args.type))
    bad = [r for rows in result.values() for r in rows if not r["ok"]]
    total = sum(len(rows) for rows in result.values())
    for rows in result.values():
        for r in rows:
            if not r["ok"]:
                print(f"  [不一致] {r['end_date']}: 接口 {r['api_count']} / 本地 {r['local_rows']}")
    print(f"核对完成：{total} 期，{len(bad)} 期不一致")
    return 1 if bad else 0


def cmd_all(args) -> int:
    rc = cmd_crawl(args)
    if rc:
        return rc
    return cmd_report(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m eastmoney_holders",
        description="抓取东方财富「股东持股」页面全部分页，整理股东当前/历史持股与变化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("command", nargs="?", default="all",
                   choices=["all", "crawl", "report", "dates", "verify", "screen"],
                   help="要执行的步骤，默认 all")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help="产物目录")
    p.add_argument("--type", default="both", choices=["free", "total", "both"],
                   help="页面口径：free=十大流通股东, total=十大股东, both=两者")
    p.add_argument("--periods", type=int, default=None, help="只抓最近 N 个报告期")
    p.add_argument("--top", type=int, default=30, help="报告榜单条数")
    p.add_argument("--page-size", type=int, default=2000, help="单页条数（服务端上限 2500）")
    p.add_argument("--interval", type=float, default=0.25, help="两次请求最小间隔秒数")
    p.add_argument("--retry", type=int, default=4, help="失败重试次数")
    p.add_argument("--base-url", default=None, help="接口地址（默认东方财富数据中心）")
    p.add_argument("--holder-type", default="全国社保基金",
                   help="screen 要筛的股东类型（如 全国社保基金 / 基本养老基金 / QFII）")
    p.add_argument("--period", default=None, help="screen 的报告期，默认最新一期")
    p.add_argument("--prev-period", default=None, help="screen 用于对比的上一期，默认次新一期")
    p.add_argument("--sort", default="market_cap", choices=["market_cap", "holders", "ratio"],
                   help="screen 排序：持股市值 / 持股家数 / 占流通股比例")
    p.add_argument("--with-change-rate", action="store_true",
                   help="额外抓每个报告期的「持股平均涨幅/上涨比例」")
    p.add_argument("--refresh", action="store_true", help="忽略已有 CSV 重新抓")
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    handlers = {"dates": cmd_dates, "crawl": cmd_crawl, "report": cmd_report,
                "verify": cmd_verify, "screen": cmd_screen, "all": cmd_all}
    try:
        return handlers[args.command](args)
    except EastmoneyError as e:
        print(f"[错误] 接口调用失败：{e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n[中断] 已保留已完成的分页文件", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


__all__ = ["build_parser", "main", "TYPE_SLUG"]
