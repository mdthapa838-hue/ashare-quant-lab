# -*- coding: utf-8 -*-
"""
tushare_trader/cli.py — 命令行入口

用法：
    python -m tushare_trader doctor             # 环境与 token 体检
    python -m tushare_trader prepare            # 盘前：标的池 + 择时
    python -m tushare_trader scan               # 扫描入场信号（只读）
    python -m tushare_trader execute --dry-run  # 建仓演练
    python -m tushare_trader execute            # 模拟盘建仓
    python -m tushare_trader monitor            # 持仓监控（模拟盘卖出）
    python -m tushare_trader settle             # 收盘结算
    python -m tushare_trader update             # 更新 K 线缓存
    python -m tushare_trader status             # 账户与计划状态
    python -m tushare_trader backtest           # 组合回测
    python -m tushare_trader backtest --limit 80 --start 2021-01-01
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_config
from .credentials import TokenNotFound, mask, resolve_token, token_source


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _print_json(title: str, obj) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


# ---------------------------------------------------------------------------
def cmd_doctor(cfg, args) -> int:
    """环境体检：依赖 / token / 接口连通 / 缓存目录。"""
    ok = True
    print("=" * 68)
    print("  环境体检 (tushare_trader)")
    print("=" * 68)

    print("\n[1] Python 依赖")
    for mod, name, required in [("pandas", "pandas", True),
                                ("numpy", "numpy", True),
                                ("yaml", "PyYAML", True),
                                ("tushare", "tushare", True)]:
        try:
            m = __import__(mod)
            print(f"    OK   {name:<12} {getattr(m, '__version__', '?')}")
        except ImportError:
            print(f"    FAIL {name:<12} 未安装")
            if required:
                ok = False
    try:
        import pandas as pd
        import numpy as np
        major = int(np.__version__.split(".")[0])
        pd_major = int(pd.__version__.split(".")[0])
        if major >= 2 and pd_major < 2:
            print("    WARN pandas<2 与 numpy>=2 二进制不兼容，请 pip install 'numpy<2'")
            ok = False
    except Exception:
        pass

    print("\n[2] Tushare Token")
    try:
        tok = resolve_token(args.token)
        print(f"    OK   token 已获取 ({mask(tok)})，来源 {token_source(args.token)}")
    except TokenNotFound as e:
        print(f"    FAIL {e}")
        return 1

    print("\n[3] 接口连通")
    from .data import TushareData
    data = TushareData(token=tok,
                       cache_dir=cfg.get("datasource.cache_dir"),
                       min_interval=float(cfg.get("datasource.tushare.min_interval", 0.35)))
    cal = data.trade_calendar("20260101", "20261231")
    if cal:
        print(f"    OK   交易日历 {len(cal)} 个交易日，最近 {cal[-1]}")
    else:
        print(f"    FAIL 交易日历为空: {data.last_error}")
        ok = False

    if cal:
        latest = data.latest_trade_date()
        print(f"    OK   最近交易日 {latest}")
        if latest:
            db, used = data.daily_basic_with_fallback(latest)
            if db is not None:
                note = "" if used == latest else f"（当日未发布，用 {used}）"
                print(f"    OK   每日指标 {len(db)} 条{note}")
            else:
                print(f"    FAIL 每日指标不可用: {data.last_error}")
                ok = False
        one = data.stock_basic()
        print(f"    OK   股票列表 {len(one)} 只")

    print("\n[4] 配置")
    print(f"    配置文件   : {cfg.path}")
    print(f"    模式       : {cfg.mode}")
    print(f"    缓存目录   : {cfg.get('datasource.cache_dir')}")
    print(f"    标的池     : 市值 {cfg.get('universe.market_cap_min')}-"
          f"{cfg.get('universe.market_cap_max')} 亿")
    print(f"    择时门槛   : {cfg.get('strategy.entry.market_index')} "
          f"{cfg.get('strategy.entry.market_ret_window')} 日 < "
          f"{cfg.get('strategy.entry.market_ret_max')}%")
    print(f"    止损/止盈  : {cfg.get('strategy.exit.stop_loss_pct')} / "
          f"{cfg.get('strategy.exit.take_profit')}")

    print("\n" + ("体检通过" if ok else "体检发现问题，见上方 FAIL/WARN"))
    return 0 if ok else 1


def cmd_prepare(cfg, args) -> int:
    from .runner import TradingRunner
    runner = TradingRunner(cfg)
    _print_json("盘前准备", runner.task_prepare(args.date))
    return 0


def cmd_scan(cfg, args) -> int:
    from .runner import TradingRunner
    runner = TradingRunner(cfg)
    sig = runner.task_scan(args.date)
    if len(sig) == 0:
        print("无入场信号")
        return 0
    cols = [c for c in ["code", "signal_date", "close", "bb_lower", "pos120",
                        "lower_streak", "ma60_slope_pct", "conditions"]
            if c in sig.columns]
    print(sig[cols].to_string(index=False))
    print(f"\n共 {len(sig)} 个信号，已保存到 {cfg.get('output.dir')}")
    return 0


def cmd_execute(cfg, args) -> int:
    from .runner import TradingRunner
    runner = TradingRunner(cfg)
    _print_json("建仓", runner.task_execute(args.date, dry_run=args.dry_run))
    return 0


def cmd_monitor(cfg, args) -> int:
    from .runner import TradingRunner
    runner = TradingRunner(cfg)
    _print_json("持仓监控", runner.task_monitor(args.date, dry_run=args.dry_run))
    return 0


def cmd_settle(cfg, args) -> int:
    from .runner import TradingRunner
    runner = TradingRunner(cfg)
    _print_json("收盘结算", runner.task_settle(args.date))
    return 0


def cmd_update(cfg, args) -> int:
    from .runner import TradingRunner
    runner = TradingRunner(cfg)
    _print_json("数据更新", runner.task_update(args.date, limit=args.limit or None))
    return 0


def cmd_status(cfg, args) -> int:
    from .runner import TradingRunner
    from .broker import PaperBroker
    from .costs import CostModel
    runner = TradingRunner(cfg)
    broker = runner._broker()
    trade_date = runner.trade_date()
    marks = runner._marks(broker, trade_date) if broker.positions else {}
    _print_json("账户状态", {
        "mode": cfg.mode,
        "trade_date": trade_date,
        "plan": runner.load_state("plan.json", {}),
        "account": broker.summary(marks),
        "last_report": runner.load_state("report.json", {}),
    })
    return 0


def run_backtest(cfg, runner, trade_date: str, codes: list[str],
                 start_date: str, end_date: str = "", verbose: bool = True):
    """执行一次组合回测，返回 (BacktestResult, 输出目录)。

    抽成独立函数，供 `backtest` 与 `all` 两个命令共用。
    """
    import pandas as pd
    from .backtest import Backtester
    from .strategy import compute_indicators, market_return_series

    bt_cfg = dict(cfg.strategy_cfg)
    bt_cfg["backtest"] = {"start_date": start_date,
                          "end_date": end_date or "",
                          "risk_free_rate": 0.02}

    warmup = pd.Timestamp(start_date) - pd.Timedelta(days=500)
    index = cfg.get("strategy.entry.market_index", "000852.SH")
    window = int(cfg.get("strategy.entry.market_ret_window", 60))
    print(f"[data] 加载指数 {index} ...", flush=True)
    idx_close = runner.data.index_daily(index, warmup.strftime("%Y%m%d"), trade_date)
    if idx_close is None:
        raise RuntimeError("指数数据不可用，回测终止")
    mret = market_return_series(idx_close, window)

    cal = [d for d in runner.calendar
           if warmup.strftime("%Y%m%d") <= d <= trade_date]
    print(f"[data] 加载 {len(codes)} 只标的 K 线 ...", flush=True)
    data = {}
    for i, code in enumerate(codes, 1):
        try:
            df = runner.data.daily(code, warmup.strftime("%Y%m%d"), trade_date, adj="qfq")
            if df is None or len(df) < 160:
                continue
            d = compute_indicators(df, cfg.strategy_cfg, calendar=cal, market_ret=mret)
            d["code"] = code
            data[code] = d
        except Exception as e:
            logging.getLogger("backtest").debug(f"{code}: {e}")
        if i % 50 == 0:
            print(f"       {i}/{len(codes)} 成功 {len(data)}", flush=True)

    if not data:
        raise RuntimeError("无可用数据，回测终止")

    print(f"[backtest] 标的 {len(data)} 只，开始回测 ...", flush=True)
    bt = Backtester(data, bt_cfg, index_close=idx_close)
    res = bt.run(verbose=verbose)

    outdir = Path(cfg.get("output.dir")) / f"backtest_{pd.Timestamp.now():%Y%m%d_%H%M%S}"
    outdir.mkdir(parents=True, exist_ok=True)
    res.equity.to_csv(outdir / "equity.csv", index=False, encoding="utf-8-sig")
    if len(res.trades):
        res.trades.to_csv(outdir / "trades.csv", index=False, encoding="utf-8-sig")
    (outdir / "metrics.json").write_text(
        json.dumps(res.metrics, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    return res, outdir


def _print_metrics(res) -> None:
    print("\n" + "=" * 68)
    print("  回测绩效")
    print("=" * 68)
    for k, v in res.metrics.items():
        print(f"  {k:<14}: {v}")


def cmd_backtest(cfg, args) -> int:
    """跑一次组合回测。"""
    from .runner import TradingRunner

    runner = TradingRunner(cfg)
    trade_date = args.date or runner.trade_date()
    if not trade_date:
        print("无法确定最近交易日")
        return 1

    uni = runner.load_state("universe.json", {})
    codes = uni.get("codes", [])
    if not codes:
        print("标的池为空，先执行: python -m tushare_trader prepare")
        return 1
    if args.limit:
        codes = codes[: args.limit]

    try:
        res, outdir = run_backtest(cfg, runner, trade_date, codes,
                                   args.start or "2018-01-01", args.end or "")
    except RuntimeError as e:
        print(str(e))
        return 1
    _print_metrics(res)
    print(f"\n结果已保存: {outdir}")
    return 0


def cmd_all(cfg, args) -> int:
    """
    一条命令跑完整个流程：体检 -> 盘前 -> 扫描 -> 建仓 -> 监控 -> 结算 -> 回测。

    任何一步失败都会带着「第几步失败」的信息退出，便于定位。
    """
    from .runner import TradingRunner

    steps = []
    runner = TradingRunner(cfg)
    trade_date = args.date or runner.trade_date()
    if not trade_date:
        print("无法确定最近交易日")
        return 1

    def step(name, fn):
        print(f"\n{'=' * 68}\n  [{len(steps) + 1}] {name}\n{'=' * 68}", flush=True)
        try:
            out = fn()
            steps.append((name, "OK", out))
            return out
        except Exception as e:
            steps.append((name, "FAIL", str(e)))
            print(f"\n[失败] {name}: {e}", file=sys.stderr)
            raise

    try:
        step("环境体检", lambda: _doctor_quiet(cfg, args))
        plan = step("盘前准备", lambda: runner.task_prepare(trade_date))
        sig = step("扫描信号", lambda: runner.task_scan(trade_date,
                                                        limit=args.limit or None))
        # 注意：scan 显示的是「当日收盘后」的信号，供下一交易日执行；
        # execute 成交的是「上一交易日」的信号。两者日期不同，数量不必相等。
        print(f"  当日收盘信号: {len(sig)} 个（供下一交易日执行）")
        if len(sig):
            print(sig.to_string(index=False))
        exe = step("建仓", lambda: runner.task_execute(trade_date,
                                                  dry_run=args.dry_run,
                                                  limit=args.limit or None))
        print(f"  按上一交易日({exe.get('signal_date', '?')})信号成交: "
              f"{exe.get('n_filled', 0)} 笔 / 信号 {exe.get('n_signals', 0)} 个")
        mon = step("持仓监控", lambda: runner.task_monitor(trade_date,
                                                    dry_run=args.dry_run))
        print(f"  卖出: {mon.get('n_sold', 0)}")
        rep = step("收盘结算", lambda: runner.task_settle(trade_date))
        print(f"  权益: {rep.get('equity')}  收益%: {rep.get('total_return_pct')}")

        if not args.no_backtest:
            uni = runner.load_state("universe.json", {})
            codes = uni.get("codes", [])
            if args.limit:
                codes = codes[: args.limit]
            res, outdir = step("回测", lambda: run_backtest(
                cfg, runner, trade_date, codes,
                args.start or "2018-01-01", args.end or "", verbose=False))
            _print_metrics(res)
            print(f"\n  结果已保存: {outdir}")
    except Exception:
        print("\n流程中断，已完成步骤：")
        for n, st, _ in steps:
            print(f"  [{st}] {n}")
        return 1

    print(f"\n{'=' * 68}")
    print("  全流程完成")
    print(f"{'=' * 68}")
    for n, st, _ in steps:
        print(f"  [{st}] {n}")
    print(f"\n  交易日: {trade_date}")
    print(f"  模式  : {cfg.mode}")
    print(f"  账户  : {cfg.get('output.state_dir')}/paper_account.json")
    return 0


def _doctor_quiet(cfg, args) -> dict:
    """集成流程里的体检：只做关键检查，失败抛异常。"""
    from .credentials import TokenNotFound, resolve_token
    from .data import TushareData
    try:
        tok = resolve_token(args.token)
    except TokenNotFound as e:
        raise RuntimeError(str(e))
    data = TushareData(token=tok,
                       cache_dir=cfg.get("datasource.cache_dir"),
                       min_interval=float(cfg.get("datasource.tushare.min_interval", 0.35)))
    cal = data.trade_calendar("20260101", "20261231")
    if not cal:
        raise RuntimeError(f"交易日历接口不可用: {data.last_error}")
    print(f"  token 与接口 OK，交易日历 {len(cal)} 天")
    return {"calendar_days": len(cal)}


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="tushare_trader",
        description="基于 Tushare 的 A 股量化交易系统（模拟盘 + 回测）",
    )
    ap.add_argument("--config", default=None, help="配置文件路径（默认 config/system.yaml）")
    ap.add_argument("--token", default=None, help="Tushare token（默认读环境变量/文件）")
    ap.add_argument("--log-level", default="INFO")

    sub = ap.add_subparsers(dest="command", required=True)

    def add_date(p):
        p.add_argument("--date", default=None, help="指定交易日 YYYYMMDD（默认最近交易日）")

    p = sub.add_parser("doctor", help="环境与 token 体检")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("prepare", help="盘前准备：标的池 + 择时")
    add_date(p)
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("scan", help="扫描入场信号（只读）")
    add_date(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("execute", help="按上一交易日信号建仓（模拟盘）")
    add_date(p)
    p.add_argument("--dry-run", action="store_true", help="只列出信号，不下单")
    p.set_defaults(func=cmd_execute)

    p = sub.add_parser("monitor", help="持仓监控与卖出")
    add_date(p)
    p.add_argument("--dry-run", action="store_true", help="只输出，不卖出")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("settle", help="收盘结算")
    add_date(p)
    p.set_defaults(func=cmd_settle)

    p = sub.add_parser("update", help="更新 K 线缓存")
    add_date(p)
    p.add_argument("--limit", type=int, default=0, help="只更新前 N 只（调试）")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("status", help="账户与计划状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("backtest", help="组合回测")
    add_date(p)
    p.add_argument("--start", default=None, help="回测开始日期 YYYY-MM-DD")
    p.add_argument("--end", default=None, help="回测结束日期 YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=0, help="只回测前 N 只标的（调试）")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("all", help="一条命令跑完：体检->盘前->扫描->建仓->监控->结算->回测")
    add_date(p)
    p.add_argument("--start", default=None, help="回测开始日期 YYYY-MM-DD（默认 2018-01-01）")
    p.add_argument("--end", default=None, help="回测结束日期 YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=0, help="回测只跑前 N 只标的（调试）")
    p.add_argument("--dry-run", action="store_true", help="建仓与卖出只演练，不改账户")
    p.add_argument("--no-backtest", action="store_true", help="跳过回测，只跑当日流程")
    p.set_defaults(func=cmd_all)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)
    _setup_logging(args.log_level)
    try:
        return args.func(cfg, args)
    except TokenNotFound as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"\n[错误] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
