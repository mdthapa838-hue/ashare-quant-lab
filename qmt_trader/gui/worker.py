# -*- coding: utf-8 -*-
"""
gui/worker.py — 后台工作线程

GUI 不能阻塞主线程，所有耗时操作（数据拉取、扫描、下单）都在线程里跑，
通过 Qt 信号回传给界面。
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QThread, Signal

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


class Worker(QThread):
    """
    通用后台任务线程。

    Signals
    -------
    progress(int, str)   进度百分比 + 消息
    log(str, str)        日志（级别, 消息）
    finished_ok(object)  成功完成，携带结果
    failed(str)          失败，携带错误信息
    """
    progress = Signal(int, str)
    log = Signal(str, str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            t0 = time.time()
            result = self.fn(
                progress_cb=lambda p, m="": self.progress.emit(int(p), str(m)),
                log_cb=lambda lvl, msg: self.log.emit(str(lvl), str(msg)),
                *self.args, **self.kwargs)
            self.log.emit("INFO", f"任务完成，耗时 {time.time()-t0:.1f}s")
            self.finished_ok.emit(result)
        except Exception as e:
            tb = traceback.format_exc()
            self.failed.emit(f"{type(e).__name__}: {e}\n\n{tb}")


# ---------------------------------------------------------------------------
# 具体任务
# ---------------------------------------------------------------------------
def task_doctor(progress_cb=None, log_cb=None):
    """环境体检。"""
    from core.tushare_source import resolve_token, TushareSource
    from core.config import load_config
    from core.qmt_trader import detect_userdata, QMTTrader, HAS_XT
    from core.purchasing_power import resolve_purchasing_power

    out = {"deps": [], "tushare": {}, "qmt": {}, "power": {}}
    cfg = load_config()

    # 依赖
    for mod, name in [("pandas", "pandas"), ("numpy", "numpy"),
                      ("tushare", "tushare"), ("xtquant", "xtquant")]:
        try:
            m = __import__(mod)
            out["deps"].append((name, getattr(m, "__version__", "?"), True))
        except ImportError:
            out["deps"].append((name, "未安装", False))
    if progress_cb:
        progress_cb(20, "依赖检查完成")

    # Tushare
    try:
        tok = resolve_token()
        src = TushareSource(token=tok, cache_dir=cfg.get("datasource.cache_dir"),
                            min_interval=0.4)
        cal = src.trade_calendar("20260901", datetime.now().strftime("%Y%m%d"))
        out["tushare"] = {"ok": True, "token_len": len(tok), "cal": len(cal)}
    except Exception as e:
        out["tushare"] = {"ok": False, "err": f"{type(e).__name__}: {e}"}
    if progress_cb:
        progress_cb(50, "Tushare 检查完成")

    # QMT
    try:
        path = cfg.get("qmt.userdata_path") or detect_userdata(cfg.get("qmt.auto_detect_paths"))
        out["qmt"] = {"ok": False, "path": path, "has_xt": HAS_XT}
        if path:
            t = QMTTrader(path, session_id=cfg.get("qmt.session_id", 20261014) + 9,
                          account_id=cfg.get("qmt.account_id", ""), mode="simulation")
            if t.connect():
                asset = t.query_asset()
                pos = t.query_positions()
                out["qmt"].update({"ok": True, "asset": asset, "positions": len(pos)})
                out["power"] = resolve_purchasing_power(
                    asset, mode=cfg.get("strategy.portfolio.purchasing_power_mode", "auto")).__dict__
                t.disconnect()
            else:
                out["qmt"]["err"] = "连接失败（终端未启动或未登录）"
    except Exception as e:
        out["qmt"]["err"] = f"{type(e).__name__}: {e}"
    if progress_cb:
        progress_cb(80, "QMT 检查完成")

    out["config"] = {
        "mode": cfg.mode,
        "market_ret_max": cfg.get("strategy.entry.market_ret_max"),
        "stop_loss": cfg.get("strategy.exit.stop_loss_pct"),
        "max_pos": cfg.get("strategy.portfolio.max_positions"),
        "power_mode": cfg.get("strategy.portfolio.purchasing_power_mode", "auto"),
    }
    if progress_cb:
        progress_cb(100, "体检完成")
    return out


def task_prepare(progress_cb=None, log_cb=None):
    """盘前准备。"""
    from core.config import load_config
    from core.engine import TradingEngine

    cfg = load_config()
    if log_cb:
        log_cb("INFO", "开始盘前准备...")
    if progress_cb:
        progress_cb(10, "加载配置")

    eng = TradingEngine(cfg)
    eng.ensure_source()
    if progress_cb:
        progress_cb(30, "获取交易日与标的池")

    plan = eng.task_prepare()
    if progress_cb:
        progress_cb(100, "完成")
    return plan


def task_scan(progress_cb=None, log_cb=None, use_cache_only: bool = True,
              ignore_market: bool = False):
    """
    扫描信号。

    Parameters
    ----------
    use_cache_only : bool
        True=仅扫本地缓存标的（快）；False=扫全部标的池（慢，受Tushare限频）
    ignore_market : bool
        True=忽略择时（仅观察形态）
    """
    import json
    import glob
    from core.config import load_config
    from core.tushare_source import TushareSource
    from core.strategy import BollLowerRebound

    cfg = load_config()
    ds = cfg.section("datasource")
    cache = ds.get("cache_dir")
    src = TushareSource(cache_dir=cache, min_interval=0.35)

    scfg = cfg.to_strategy_cfg()
    if ignore_market:
        scfg = {**scfg, "entry": {**scfg["entry"], "use_market_filter": False}}
    st = BollLowerRebound(scfg, src)

    # 择时
    passed, mret = st.market_ok()
    if log_cb:
        log_cb("INFO", f"择时: {'通过' if passed else '未通过'}  市场N日 {mret:.2f}%")

    # 标的范围
    if use_cache_only:
        kdir = Path(cache) / "kline"
        codes6 = []
        for f in glob.glob(str(kdir / "*.csv")):
            stem = os.path.basename(f)[:-4]
            for suf in ("_qfq", "_hfq", "_bfq"):
                if stem.endswith(suf):
                    stem = stem[:-len(suf)]
                    break
            if stem.isdigit() and len(stem) == 6:
                codes6.append(stem)
        codes6 = sorted(set(codes6))

        def to_ts(c):
            return c + (".SH" if c.startswith(("60", "68")) else
                        ".BJ" if c.startswith(("8", "4", "9")) else ".SZ")
        codes = [to_ts(c) for c in codes6]
    else:
        state = Path(cache).parent / "state"
        p = state / "universe.json"
        if not p.exists():
            raise RuntimeError("标的池不存在，请先运行「盘前准备」")
        codes = json.loads(p.read_text(encoding="utf-8")).get("codes", [])

    if log_cb:
        log_cb("INFO", f"扫描 {len(codes)} 只...")

    t0 = time.time()
    total = len(codes)
    results = []

    # 用回调做进度
    def cb(p, m):
        if progress_cb:
            progress_cb(int(p), m)

    # 逐只扫描以便报进度
    cond_cols = ["cond_position", "cond_streak", "cond_ma60_down",
                 "cond_monthly_up", "cond_j_low", "cond_liquidity"]
    near = []
    cnt = {c: 0 for c in cond_cols}
    n_ok_scan = 0

    for i, code in enumerate(codes):
        try:
            df = st.get_kline(code)
            if df is None or len(df) < 130:
                continue
            d = st.compute(df)
            if len(d) == 0:
                continue
            r = d.iloc[-1]
            n_ok_scan += 1
            for c in cond_cols:
                cnt[c] += int(bool(r[c]))
            n_ok = sum(int(bool(r[c])) for c in cond_cols)
            rec = {
                "code": code,
                "date": r["date"].strftime("%Y-%m-%d"),
                "close": round(float(r["close"]), 2),
                "bb_lower": round(float(r["bb_lower"]), 2) if pd_notna(r["bb_lower"]) else None,
                "bb_mid": round(float(r["bb_mid"]), 2) if pd_notna(r["bb_mid"]) else None,
                "bb_upper": round(float(r["bb_upper"]), 2) if pd_notna(r["bb_upper"]) else None,
                "pos120": round(float(r["pos120"]), 3) if pd_notna(r["pos120"]) else None,
                "streak": int(r["lower_streak"]),
                "ma60_slope": round(float(r["ma60_slope"]) * 100, 2) if pd_notna(r["ma60_slope"]) else None,
                "monthly_up": bool(r.get("monthly_trend_up", False)),
                "J": round(float(r["J"]), 1) if pd_notna(r.get("J")) else None,
                "n_ok": n_ok,
                "conditions": f"{n_ok}/{len(cond_cols)}",
                "signal": bool(r["signal"]),
            }
            if rec["signal"]:
                results.append(rec)
            elif n_ok >= 5:
                near.append(rec)
        except Exception:
            pass
        if (i + 1) % 10 == 0 or i + 1 == total:
            if progress_cb:
                pct = int((i + 1) / max(total, 1) * 100)
                progress_cb(pct, f"{i+1}/{total}")

    if log_cb:
        log_cb("INFO", f"扫描完成，{n_ok_scan} 只有效，信号 {len(results)} 个，"
                       f"接近 {len(near)} 个，耗时 {time.time()-t0:.1f}s")

    return {
        "market_passed": passed,
        "market_ret": mret,
        "market_threshold": st.market_max,
        "scanned": n_ok_scan,
        "signals": results,
        "near": sorted(near, key=lambda x: (-x["n_ok"], x["pos120"] or 9))[:50],
        "cond_stats": {k: {"count": v, "pct": round(v / max(n_ok_scan, 1) * 100, 1)}
                       for k, v in cnt.items()},
        "ignore_market": ignore_market,
    }


def pd_notna(v):
    try:
        import pandas as pd
        return pd.notna(v)
    except Exception:
        return v is not None


def task_monitor(progress_cb=None, log_cb=None, dry_run: bool = True):
    """持仓监控。"""
    from core.config import load_config
    from core.engine import TradingEngine

    cfg = load_config()
    if log_cb:
        log_cb("INFO", "监控持仓..." + ("（演练）" if dry_run else ""))
    if progress_cb:
        progress_cb(20, "连接交易接口")
    eng = TradingEngine(cfg)
    if progress_cb:
        progress_cb(50, "检查离场条件")
    r = eng.task_monitor_exit(dry_run=dry_run)
    if progress_cb:
        progress_cb(100, "完成")
    return r


def task_entry(progress_cb=None, log_cb=None, dry_run: bool = True):
    """执行建仓。"""
    from core.config import load_config
    from core.engine import TradingEngine

    cfg = load_config()
    if log_cb:
        log_cb("INFO", "执行建仓..." + ("（演练）" if dry_run else ""))
    if progress_cb:
        progress_cb(20, "连接交易接口")
    eng = TradingEngine(cfg)
    if progress_cb:
        progress_cb(50, "扫描信号")
    r = eng.task_execute_entry(dry_run=dry_run)
    if progress_cb:
        progress_cb(100, "完成")
    return r


def task_account(progress_cb=None, log_cb=None):
    """查询账户。"""
    from core.config import load_config
    from core.qmt_trader import QMTTrader, detect_userdata
    from core.purchasing_power import resolve_purchasing_power

    cfg = load_config()
    if progress_cb:
        progress_cb(20, "连接")
    q = cfg.section("qmt")
    path = q.get("userdata_path") or detect_userdata(q.get("auto_detect_paths"))
    if not path:
        raise RuntimeError("未找到 userdata_mini")
    t = QMTTrader(path, session_id=q.get("session_id", 20261014) + 7,
                  account_id=q.get("account_id", ""), mode="simulation")
    if not t.connect():
        raise RuntimeError("miniQMT 连接失败（终端未启动或未登录）")
    if progress_cb:
        progress_cb(50, "查询资产")
    asset = t.query_asset()
    pos = t.query_positions()
    orders = t.query_orders()
    trades = t.query_trades()
    pp = resolve_purchasing_power(
        asset, mode=cfg.get("strategy.portfolio.purchasing_power_mode", "auto"))
    t.disconnect()
    if progress_cb:
        progress_cb(100, "完成")
    return {"asset": asset, "positions": pos, "orders": orders,
            "trades": trades, "power": pp.__dict__}


def task_kline(progress_cb=None, log_cb=None, code: str = "", days: int = 250):
    """获取单只K线（用于绘图）。"""
    from core.config import load_config
    from core.tushare_source import TushareSource
    from core.strategy import BollLowerRebound

    cfg = load_config()
    src = TushareSource(cache_dir=cfg.get("datasource.cache_dir"), min_interval=0.35)
    st = BollLowerRebound(cfg.to_strategy_cfg(), src)
    if progress_cb:
        progress_cb(30, f"加载 {code}")
    df = st.get_kline(code, days=max(days * 2, 500))
    if df is None:
        raise RuntimeError(f"{code} 无数据")
    d = st.compute(df).tail(days)
    if progress_cb:
        progress_cb(100, "完成")
    return {"code": code, "data": d}
