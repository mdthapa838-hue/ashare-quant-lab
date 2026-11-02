# -*- coding: utf-8 -*-
"""
main.py — 命令行入口

用法：
    python main.py doctor              # 环境体检（token/QMT/数据）
    python main.py status              # 查看当前状态
    python main.py prepare             # 盘前准备
    python main.py scan                # 扫描信号（不下单）
    python main.py entry --dry-run     # 建仓演练
    python main.py entry               # 建仓（需 live 模式）
    python main.py monitor --dry-run   # 持仓监控演练
    python main.py monitor             # 持仓监控（需 live 模式）
    python main.py settle              # 收盘结算
    python main.py update-data         # 更新数据
    python main.py backtest            # 回测
    python main.py serve               # 启动定时调度
"""
from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pandas as pd

from core.config import load_config
from core.logger import setup_logger, get_logger


def cmd_doctor(cfg) -> int:
    """环境体检。"""
    print("=" * 70)
    print("  环境体检")
    print("=" * 70)

    ok_all = True

    # 1. 依赖
    print("\n[1] Python 依赖")
    for mod, name in [("pandas", "pandas"), ("numpy", "numpy"),
                      ("yaml", "PyYAML"), ("tushare", "tushare"),
                      ("xtquant", "xtquant(miniQMT)")]:
        try:
            m = __import__(mod)
            v = getattr(m, "__version__", "?")
            print(f"    ✓ {name:<20} {v}")
        except ImportError:
            tag = "✗" if mod != "xtquant" else "○"
            print(f"    {tag} {name:<20} 未安装")
            if mod != "xtquant":
                ok_all = False

    # 2. Tushare token
    print("\n[2] Tushare 凭据")
    try:
        from core.tushare_source import resolve_token, TushareSource
        tok = resolve_token()
        print(f"    ✓ token 获取成功（长度 {len(tok)}）")
        src = TushareSource(token=tok, cache_dir=cfg.get("datasource.cache_dir"),
                            min_interval=0.4)
        cal = src.trade_calendar("20261001", "20261014")
        print(f"    ✓ API 连通，返回 {len(cal)} 个交易日")
    except Exception as e:
        print(f"    ✗ 失败: {type(e).__name__}: {str(e)[:100]}")
        ok_all = False
        src = None

    # 3. miniQMT
    print("\n[3] miniQMT")
    try:
        from core.qmt_trader import detect_userdata, HAS_XT
        print(f"    {'✓' if HAS_XT else '✗'} xtquant 可用: {HAS_XT}")
        paths = cfg.get("qmt.auto_detect_paths", [])
        custom = cfg.get("qmt.userdata_path", "")
        found = detect_userdata(([custom] if custom else []) + list(paths))
        if found:
            print(f"    ✓ userdata_mini: {found}")
            from core.qmt_trader import QMTTrader
            t = QMTTrader(found, session_id=cfg.get("qmt.session_id", 20261014),
                          account_id=cfg.get("qmt.account_id", ""),
                          mode="simulation")
            if t.connect():
                print(f"    ✓ 连接成功")
                a = t.query_asset()
                if a:
                    print(f"      总资产 {a.get('总资产', 0):,.0f}  "
                          f"可用 {a.get('可用资金', 0):,.0f}")
                p = t.query_positions()
                print(f"      持仓 {len(p)} 只")
            else:
                print(f"    ○ 连接失败（QMT 终端可能未启动/未登录）")
            t.disconnect()
        else:
            print(f"    ○ 未找到 userdata_mini 目录")
    except Exception as e:
        print(f"    ○ {type(e).__name__}: {str(e)[:100]}")

    # 4. 配置
    print("\n[4] 配置")
    print(f"    模式        : {cfg.mode}")
    print(f"    缓存目录    : {cfg.get('datasource.cache_dir')}")
    print(f"    日志目录    : {cfg.get('logging.dir')}")
    print(f"    择时门槛    : 指数{cfg.get('strategy.entry.market_ret_window')}日 "
          f"< {cfg.get('strategy.entry.market_ret_max')}%")
    print(f"    止盈/止损    : {cfg.get('strategy.exit.take_profit')} / "
          f"{cfg.get('strategy.exit.stop_loss_pct')*100:.0f}%")
    print(f"    最大持仓    : {cfg.get('strategy.portfolio.max_positions')} 只")
    if cfg.is_live:
        print("    ⚠ 当前为 LIVE 模式，下单将真实执行")

    print("\n" + "=" * 70)
    print("  体检结果: " + ("通过" if ok_all else "存在问题，请检查上面 ✗ 项"))
    print("=" * 70)
    return 0 if ok_all else 1


def cmd_status(cfg) -> int:
    from core.engine import TradingEngine
    eng = TradingEngine(cfg)
    st = eng.status()
    print("=" * 70)
    print("  当前状态")
    print("=" * 70)
    for k, v in st.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for k2, v2 in v.items():
                print(f"      {k2}: {v2}")
        else:
            print(f"  {k}: {v}")
    return 0


def cmd_prepare(cfg) -> int:
    from core.engine import TradingEngine
    eng = TradingEngine(cfg)
    eng.task_prepare()
    return 0


def cmd_scan(cfg) -> int:
    from core.engine import TradingEngine
    eng = TradingEngine(cfg)
    eng.ensure_source()
    uni = eng._load_json("universe.json", {})
    codes = uni.get("codes", [])
    if not codes:
        print("标的池为空，先运行: python main.py prepare")
        return 1
    passed, mret = eng.strategy.market_ok()
    print(f"择时: {'通过' if passed else '未通过'}  (市场收益 {mret}%)")
    sig = eng.strategy.scan(codes, market_passed=passed)
    if len(sig):
        cols = ["code", "date", "close", "bb_lower", "bb_mid", "bb_upper",
                "pos120", "streak", "ma60_slope%", "monthly_up", "conditions"]
        cols = [c for c in cols if c in sig.columns]
        print("\n" + sig[cols].to_string(index=False))
        out = HERE / "output" / f"signals_{pd.Timestamp.now():%Y%m%d}.csv"
        out.parent.mkdir(exist_ok=True)
        sig.to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n已保存: {out}")
    else:
        print("无信号")
    return 0


def cmd_entry(cfg, dry_run: bool) -> int:
    from core.engine import TradingEngine
    eng = TradingEngine(cfg)
    r = eng.task_execute_entry(dry_run=dry_run)
    print(r)
    return 0


def cmd_monitor(cfg, dry_run: bool) -> int:
    from core.engine import TradingEngine
    eng = TradingEngine(cfg)
    r = eng.task_monitor_exit(dry_run=dry_run)
    print(r)
    return 0


def cmd_settle(cfg) -> int:
    from core.engine import TradingEngine
    eng = TradingEngine(cfg)
    r = eng.task_settle()
    print(r)
    return 0


def cmd_update_data(cfg) -> int:
    from core.engine import TradingEngine
    eng = TradingEngine(cfg)
    r = eng.task_update_data()
    print(r)
    return 0


def cmd_backtest(cfg) -> int:
    print("回测请使用 research 版（已验证）:")
    print("  cd F:\\deepseek\\股市\\股市2\\strategy")
    print("  python run_backtest.py")
    return 0


def cmd_serve(cfg) -> int:
    """启动定时调度。"""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
    except ImportError:
        print("需要 apscheduler: pip install apscheduler")
        return 1
    from core.engine import TradingEngine
    eng = TradingEngine(cfg)
    sched = BlockingScheduler(timezone=cfg.get("app.timezone", "Asia/Shanghai"))
    tasks = {
        "prepare": eng.task_prepare,
        "execute_entry": eng.task_execute_entry,
        "monitor_exit": eng.task_monitor_exit,
        "settle": eng.task_settle,
        "update_data": eng.task_update_data,
    }
    jobs = cfg.get("schedule.jobs", [])
    for j in jobs:
        fn = tasks.get(j.get("task"))
        if fn is None:
            continue
        h, m = str(j.get("time", "09:00")).split(":")
        sched.add_job(fn, "cron", hour=int(h), minute=int(m),
                      id=j.get("name"), misfire_grace_time=300)
        print(f"  注册任务: {j.get('time')} {j.get('name')}")
    print("\n调度已启动，Ctrl+C 退出")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n已停止")
    return 0


def main():
    ap = argparse.ArgumentParser(description="布林带下轨反弹策略交易系统")
    ap.add_argument("command", choices=["doctor", "status", "prepare", "scan",
                                        "entry", "monitor", "settle", "update-data",
                                        "backtest", "serve"])
    ap.add_argument("--config", default=str(HERE / "config" / "config.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="演练模式，不实际下单")
    args = ap.parse_args()

    os.chdir(HERE)
    cfg = load_config(args.config)
    setup_logger("qmt_trader", log_dir=cfg.get("logging.dir"),
                 level=cfg.get("logging.level", "INFO"),
                 console=cfg.get("logging.console", True),
                 keep_days=cfg.get("logging.keep_days", 60))

    handlers = {
        "doctor": lambda: cmd_doctor(cfg),
        "status": lambda: cmd_status(cfg),
        "prepare": lambda: cmd_prepare(cfg),
        "scan": lambda: cmd_scan(cfg),
        "entry": lambda: cmd_entry(cfg, args.dry_run),
        "monitor": lambda: cmd_monitor(cfg, args.dry_run),
        "settle": lambda: cmd_settle(cfg),
        "update-data": lambda: cmd_update_data(cfg),
        "backtest": lambda: cmd_backtest(cfg),
        "serve": lambda: cmd_serve(cfg),
    }
    sys.exit(handlers[args.command]())


if __name__ == "__main__":
    main()
