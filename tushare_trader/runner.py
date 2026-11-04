# -*- coding: utf-8 -*-
"""
tushare_trader/runner.py — 每日任务编排（模拟盘 / 信号扫描 / 数据更新）

任务流水线（与 A 股交易时段对应）：

    prepare   盘前：更新标的池、检查市场择时、输出今日计划
    scan      盘中/盘后：扫描入场信号（只读，不下单）
    execute   开盘后：按信号在模拟盘买入（T+1 开盘价成交）
    monitor   尾盘：检查持仓出场条件（模拟盘卖出）
    settle    收盘后：按收盘价结算权益、生成日报
    update    更新本地 K 线缓存

所有状态持久化在 `state/` 与 `data/` 下，进程重启可继续。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from .broker import PaperBroker
from .costs import CostModel
from .data import TushareData
from .strategy import BollBandStrategy, compute_indicators, market_return_series

LOG = logging.getLogger("tushare_trader.runner")

KLINE_START = "20150101"
HISTORY_DAYS = 400          # 指标计算所需的历史长度（自然日）
UNIVERSE_MAX = 800          # 单次扫描的标的数量上限（控制接口调用量）


class TradingRunner:
    """交易任务编排器。"""

    def __init__(self, cfg, data: TushareData | None = None):
        self.cfg = cfg
        self.state_dir = Path(cfg.get("output.state_dir", "state"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = Path(cfg.get("output.dir", "output"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

        cache_dir = cfg.get("datasource.cache_dir", "data/cache")
        self.data = data or TushareData(
            token=cfg.get("datasource.tushare.token_override") or None,
            cache_dir=cache_dir,
            min_interval=float(cfg.get("datasource.tushare.min_interval", 0.35)),
            retry=int(cfg.get("datasource.tushare.retry", 3)),
        )
        self.strategy = BollBandStrategy(cfg.strategy_cfg)
        self._calendar: list[str] | None = None
        # 缓存：指数收益序列 / 交易日历子区间（避免逐只股票重复计算）
        self._market_ret_cache: dict[str, pd.Series | None] = {}
        self._calendar_cache: dict[tuple[str, str], list[str]] = {}

    # ------------------------------------------------------------ 状态
    def _state_path(self, name: str) -> Path:
        return self.state_dir / name

    def load_state(self, name: str, default=None):
        p = self._state_path(name)
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            LOG.warning(f"状态文件 {name} 读取失败: {e}")
            return default

    def save_state(self, name: str, data) -> None:
        p = self._state_path(name)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        tmp.replace(p)

    def _log_run(self, task: str, payload: dict) -> None:
        rec = {"time": datetime.now().isoformat(timespec="seconds"),
               "task": task, "result": payload}
        with open(self._state_path("runs.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    # ------------------------------------------------------------ 数据辅助
    @property
    def calendar(self) -> list[str]:
        if self._calendar is None:
            self._calendar = self.data.trade_calendar("20150101", "20351231")
        return self._calendar

    def trade_date(self, today: str | None = None) -> str | None:
        """最近交易日（YYYYMMDD）。"""
        today = today or datetime.now().strftime("%Y%m%d")
        cal = [d for d in self.calendar if d <= today]
        return cal[-1] if cal else None

    def _previous_trade_date(self, date: str) -> str | None:
        cal = [d for d in self.calendar if d < date]
        return cal[-1] if cal else None

    def market_ret(self, as_of: str) -> pd.Series | None:
        """指数 N 日收益序列（按 as_of 缓存，避免每只股票重复拉取）。

        没有这层缓存时，`_load_one` 会为每只持仓/每只标的各拉一次指数，
        扫描 800 只标的就会重复请求 800 次，是实际的性能瓶颈。
        """
        if as_of in self._market_ret_cache:
            return self._market_ret_cache[as_of]
        index = self.cfg.get("strategy.entry.market_index", "000852.SH")
        window = int(self.cfg.get("strategy.entry.market_ret_window", 60))
        start = (pd.Timestamp(as_of) - pd.Timedelta(days=int(window * 2.5) + 400)
                 ).strftime("%Y%m%d")
        s = self.data.index_daily(index, start, as_of)
        if s is None or len(s) <= window:
            LOG.warning(f"指数 {index} 数据不足，无法计算择时")
            self._market_ret_cache[as_of] = None
            return None
        out = market_return_series(s, window)
        self._market_ret_cache[as_of] = out
        return out

    def load_codes(self, codes: list[str], as_of: str,
                   min_bars: int = 160) -> dict[str, pd.DataFrame]:
        """加载并计算多只股票的指标数据。"""
        start = (pd.Timestamp(as_of) - pd.Timedelta(days=HISTORY_DAYS * 4)
                 ).strftime("%Y%m%d")
        cal = [d for d in self.calendar if start <= d <= as_of]
        mret = self.market_ret(as_of)
        out: dict[str, pd.DataFrame] = {}
        for i, code in enumerate(codes, 1):
            try:
                df = self.data.daily(code, start, as_of, adj="qfq")
                if df is None or len(df) < min_bars:
                    continue
                d = compute_indicators(df, self.cfg.strategy_cfg,
                                       calendar=cal, market_ret=mret)
                d["code"] = code
                out[code] = d
            except Exception as e:
                LOG.debug(f"{code} 数据处理失败: {e}")
            if i % 100 == 0:
                LOG.info(f"  载入 {i}/{len(codes)}，成功 {len(out)}")
        return out

    # ------------------------------------------------------------ 盘前
    def task_prepare(self, as_of: str | None = None) -> dict:
        """盘前：更新标的池 + 市场择时 + 今日计划。"""
        trade_date = as_of or self.trade_date()
        if not trade_date:
            raise RuntimeError("无法确定最近交易日，请检查交易日历接口")

        uni = self.cfg.section("universe")
        codes = self.data.build_universe(
            trade_date,
            cap_min=float(uni.get("market_cap_min", 30)),
            cap_max=float(uni.get("market_cap_max", 80)),
            exclude_st=bool(uni.get("exclude_st", True)),
            exclude_bj=bool(uni.get("exclude_bj", True)),
            exclude_kcb=bool(uni.get("exclude_kcb", False)),
        )
        codes = codes[:UNIVERSE_MAX]

        passed, mret = self.strategy.market_ok(self.market_ret(trade_date), trade_date)
        plan = {
            "trade_date": trade_date,
            "universe_size": len(codes),
            "market_ret_pct": round(mret, 2) if mret is not None else None,
            "market_threshold_pct": self.strategy.market_max,
            "market_passed": passed,
            "action": "允许开仓" if passed else "空仓等待（择时未通过）",
            "mode": self.cfg.mode,
        }
        self.save_state("universe.json", {"date": trade_date, "codes": codes})
        self.save_state("plan.json", plan)
        self._log_run("prepare", plan)
        return plan

    # ------------------------------------------------------------ 扫描
    def task_scan(self, as_of: str | None = None,
                  limit: int | None = None) -> pd.DataFrame:
        """扫描最新交易日的入场信号（只读）。

        limit : 只扫描前 N 只（调试/快速验证用，不影响 prepare 记录的标的池）
        """
        trade_date = as_of or self.trade_date()
        uni = self.load_state("universe.json", {})
        codes = uni.get("codes", [])
        if not codes:
            raise RuntimeError("标的池为空，请先执行 prepare")
        if limit:
            codes = codes[:limit]

        data = self.load_codes(codes, trade_date)
        rows = []
        for code, d in data.items():
            sig = self.strategy.screen(d)
            if sig:
                rows.append(sig)
        signals = pd.DataFrame(rows)
        if len(signals):
            signals = signals.sort_values(["pos120", "code"]).reset_index(drop=True)

        out_path = self.output_dir / f"signals_{trade_date}.csv"
        if len(signals):
            signals.to_csv(out_path, index=False, encoding="utf-8-sig")
        self.save_state("signals.json", signals.to_dict("records") if len(signals) else [])
        self._log_run("scan", {"trade_date": trade_date, "n_signals": int(len(signals))})
        return signals

    # ------------------------------------------------------------ 建仓
    def task_execute(self, as_of: str | None = None, dry_run: bool = False,
                     limit: int | None = None) -> dict:
        """
        按「上一交易日收盘信号」，在 `as_of`（默认最近交易日）开盘建仓。

        与回测口径完全一致：
            T 日收盘出信号 -> T+1 开盘成交（今日开盘价）
        因此本任务应在当日收盘数据可用后（例如 15:30 之后）运行。
        """
        trade_date = as_of or self.trade_date()
        if not trade_date:
            raise RuntimeError("无法确定最近交易日")
        signal_date = self._previous_trade_date(trade_date)
        if signal_date is None:
            return {"trade_date": trade_date, "skipped": True,
                    "reason": "找不到上一交易日"}

        passed, mret = self.strategy.market_ok(self.market_ret(signal_date), signal_date)
        if not passed:
            out = {"trade_date": trade_date, "signal_date": signal_date,
                   "skipped": True, "market_ret_pct":
                       round(mret, 2) if mret is not None else None,
                   "reason": "择时未通过，空仓等待"}
            self._log_run("execute", out)
            return out

        uni = self.load_state("universe.json", {})
        codes = uni.get("codes", [])
        if not codes:
            raise RuntimeError("标的池为空，请先执行 prepare")
        if limit:
            codes = codes[:limit]

        data = self.load_codes(codes, trade_date)
        candidates = []
        for code, d in data.items():
            if len(d) < 2:
                continue
            if bool(d.iloc[-2].get("signal", False)):
                candidates.append(code)
        candidates.sort()

        if not candidates:
            out = {"trade_date": trade_date, "signal_date": signal_date,
                   "n_signals": 0, "n_filled": 0}
            self._log_run("execute", out)
            return out

        if dry_run:
            out = {"trade_date": trade_date, "signal_date": signal_date,
                   "dry_run": True, "n_signals": len(candidates),
                   "codes": candidates}
            self._log_run("execute", out)
            return out

        broker = self._broker()
        free = self.strategy.max_positions - len(broker.positions)
        if free <= 0:
            out = {"trade_date": trade_date, "n_signals": len(candidates),
                   "n_filled": 0, "reason": "已满仓"}
            self._log_run("execute", out)
            return out

        marks = self._marks(broker, trade_date)
        equity = broker.equity(marks)

        filled = []
        for code in candidates[:free]:
            d = data.get(code)
            if d is None or len(d) < 2:
                continue
            fill_bar = d.iloc[-1]          # 今日（成交日）K 线
            sig_bar = d.iloc[-2]
            shares = self.strategy.position_size(
                equity, broker.cash, len(broker.positions), float(fill_bar["open"]))
            if shares <= 0:
                continue
            stop = self.strategy.initial_stop(float(fill_bar["open"]), fill_bar)
            trade = broker.buy(
                code, fill_bar, str(pd.Timestamp(trade_date).date()), shares,
                stop_price=stop, reason="signal",
                signal={"signal_date": str(pd.Timestamp(signal_date).date()),
                        "signal_close": float(sig_bar["close"]),
                        "pos120": round(float(sig_bar["pos120"]), 3),
                        "lower_streak": int(sig_bar["lower_streak"])},
            )
            if trade:
                filled.append({"code": code, "shares": trade.shares,
                               "price": trade.price, "amount": round(trade.amount, 2)})

        out = {"trade_date": trade_date, "signal_date": signal_date,
               "n_signals": len(candidates), "n_filled": len(filled), "fills": filled}
        self._log_run("execute", out)
        return out

    # ------------------------------------------------------------ 持仓监控
    def task_monitor(self, as_of: str | None = None, dry_run: bool = False) -> dict:
        """检查持仓出场条件并在模拟盘卖出。"""
        trade_date = as_of or self.trade_date()
        broker = self._broker()
        if not broker.positions:
            out = {"trade_date": trade_date, "n_positions": 0, "n_sold": 0}
            self._log_run("monitor", out)
            return out

        rows, sold = [], []
        cal = [d for d in self.calendar if d <= trade_date]
        for code, pos in list(broker.positions.items()):
            df = self._load_one(code, trade_date)
            if df is None or len(df) == 0:
                rows.append({"code": code, "status": "无数据"})
                continue
            bar = df.iloc[-1]
            # 已持有交易日数（用交易日历计算，避免自然日误差）
            after = [d for d in cal if d > pos.entry_date.replace("-", "")]
            held = len(after)
            reason = self.strategy.exit_reason(bar, {
                "entry_price": pos.entry_price,
                "shares": pos.shares,
                "stop_price": pos.stop_price,
            }, held)
            pnl = (float(bar["close"]) / pos.entry_price - 1) * 100
            rows.append({
                "code": code,
                "entry_date": pos.entry_date,
                "entry_price": round(pos.entry_price, 3),
                "close": round(float(bar["close"]), 3),
                "pnl_pct": round(pnl, 2),
                "held_days": held,
                "stop_price": round(pos.stop_price, 3),
                "action": "sell" if reason else "hold",
                "reason": reason or "持有中",
            })
            if reason and not dry_run:
                t = broker.sell(code, bar, str(pd.Timestamp(trade_date).date()), reason=reason)
                if t:
                    sold.append({"code": code, "price": t.price,
                                 "shares": t.shares, "reason": reason})
        monitor = pd.DataFrame(rows)
        if len(monitor):
            monitor.to_csv(self.output_dir / f"monitor_{trade_date}.csv",
                           index=False, encoding="utf-8-sig")
        out = {"trade_date": trade_date, "n_positions": len(monitor),
               "n_sold": len(sold), "sold": sold,
               "dry_run": dry_run}
        self._log_run("monitor", out)
        return out

    # ------------------------------------------------------------ 结算
    def task_settle(self, as_of: str | None = None) -> dict:
        """收盘结算：按收盘价市值、记录权益、生成日报。"""
        trade_date = as_of or self.trade_date()
        broker = self._broker()
        marks = self._marks(broker, trade_date)
        snap = broker.mark_to_market(str(pd.Timestamp(trade_date).date()), marks)
        broker.save()

        report = {
            "date": snap["date"],
            "mode": self.cfg.mode,
            "equity": snap["equity"],
            "cash": snap["cash"],
            # _marks 返回的是「每股价格」，市值需按持仓股数加权
            "market_value": round(broker.market_value(marks), 2),
            "n_positions": snap["n_positions"],
            "total_return_pct": round(
                (snap["equity"] / broker.initial_capital - 1) * 100, 2),
            "positions": broker.summary(marks)["positions"],
        }
        self.save_state("report.json", report)
        self._log_run("settle", report)

        eq_path = self.output_dir / "equity.csv"
        hist = pd.DataFrame(broker.equity_history)
        if len(hist):
            hist.to_csv(eq_path, index=False, encoding="utf-8-sig")
        return report

    # ------------------------------------------------------------ 数据更新
    def task_update(self, as_of: str | None = None, limit: int | None = None) -> dict:
        """更新标的池内全部股票的 K 线缓存。"""
        trade_date = as_of or self.trade_date()
        uni = self.load_state("universe.json", {})
        codes = uni.get("codes", [])
        if limit:
            codes = codes[:limit]
        start = (pd.Timestamp(trade_date) - pd.Timedelta(days=HISTORY_DAYS * 4)
                 ).strftime("%Y%m%d")
        ok = 0
        for i, code in enumerate(codes, 1):
            try:
                df = self.data.daily(code, start, trade_date, adj="qfq")
                if df is not None and len(df):
                    ok += 1
            except Exception as e:
                LOG.debug(f"{code} 更新失败: {e}")
            if i % 100 == 0:
                LOG.info(f"  更新 {i}/{len(codes)}，成功 {ok}")
        out = {"trade_date": trade_date, "total": len(codes), "updated": ok}
        self._log_run("update", out)
        return out

    # ------------------------------------------------------------ 内部
    def _broker(self) -> PaperBroker:
        pf = self.cfg.section("strategy.portfolio")
        return PaperBroker(
            initial_capital=float(pf.get("initial_capital", 1_000_000)),
            cost=CostModel.from_config(self.cfg.section("strategy")),
            state_dir=self.state_dir,
            limit_up_pct=float(self.cfg.get("constraints.no_buy_limit_up", 0.095)) * 100,
            limit_down_pct=float(self.cfg.get("constraints.no_sell_limit_down", -0.095)) * 100,
        )

    def _load_one(self, code: str, as_of: str) -> pd.DataFrame | None:
        start = (pd.Timestamp(as_of) - pd.Timedelta(days=HISTORY_DAYS * 4)).strftime("%Y%m%d")
        df = self.data.daily(code, start, as_of, adj="qfq")
        if df is None or len(df) < 20:
            return None
        return compute_indicators(df, self.cfg.strategy_cfg,
                                  calendar=self._calendar_slice(start, as_of),
                                  market_ret=self.market_ret(as_of))

    def _calendar_slice(self, start: str, as_of: str) -> list[str]:
        """交易日历子区间（带缓存，避免每次重算列表推导）。"""
        key = (start, as_of)
        if key not in self._calendar_cache:
            self._calendar_cache[key] = [d for d in self.calendar
                                         if start <= d <= as_of]
        return self._calendar_cache[key]

    def _marks(self, broker: PaperBroker, as_of: str) -> dict[str, float]:
        marks: dict[str, float] = {}
        for code in broker.positions:
            df = self._load_one(code, as_of)
            if df is not None and len(df):
                marks[code] = float(df.iloc[-1]["close"])
        return marks

    def _next_trade_date(self, date: str) -> str | None:
        later = [d for d in self.calendar if d > date]
        return later[0] if later else None
