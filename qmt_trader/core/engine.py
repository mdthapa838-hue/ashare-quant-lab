# -*- coding: utf-8 -*-
"""
core/engine.py — 交易引擎（策略 + 数据 + 交易的编排层）

职责：
  - 串起数据源、策略、交易接口
  - 提供高层动作：prepare / execute_entry / monitor_exit / settle / update_data
  - 状态持久化（持仓台账、订单记录）
  - 安全门禁（模式检查、风控约束）
"""
from __future__ import annotations

import os
import json
import time
from pathlib import Path
from datetime import datetime

import pandas as pd

from core.logger import get_logger
from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound
from core.qmt_trader import QMTTrader, detect_userdata
from core.purchasing_power import resolve_purchasing_power, check_sufficient

LOG = get_logger("engine")


class TradingEngine:
    """
    交易引擎。

    状态文件（state/）：
      positions.json  持仓台账（策略视角）
      orders.json     订单记录
      signals.json    最近一次信号
      runs.jsonl      运行日志
    """

    def __init__(self, cfg, source: TushareSource | None = None,
                 trader: QMTTrader | None = None):
        self.cfg = cfg
        self.src = source
        self.trader = trader

        self.strategy_cfg = cfg.to_strategy_cfg()
        self.strategy = BollLowerRebound(self.strategy_cfg, self.src) if source else None

        # 状态目录
        base = Path(cfg.get("datasource.cache_dir", "./data_cache")).parent
        self.state_dir = base / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.positions = self._load_json("positions.json", {})
        self.orders = self._load_json("orders.json", [])
        self._last_connect_attempt = 0.0
        self._connect_failed = False

    # ------------------------------------------------------------ 状态
    def _load_json(self, name: str, default):
        p = self.state_dir / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return default

    def _save_json(self, name: str, data) -> None:
        p = self.state_dir / name
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def _log_run(self, task: str, result: dict) -> None:
        rec = {"time": datetime.now().isoformat(), "task": task, "result": result}
        with open(self.state_dir / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    # ------------------------------------------------------------ 连接
    def ensure_source(self) -> TushareSource:
        if self.src is None:
            ds = self.cfg.section("datasource")
            ts_cfg = ds.get("tushare", {})
            self.src = TushareSource(
                sources=ts_cfg.get("credential_sources"),
                cache_dir=ds.get("cache_dir"),
                min_interval=ts_cfg.get("min_interval", 0.35),
                retry=ts_cfg.get("retry", 3),
            )
            self.strategy = BollLowerRebound(self.strategy_cfg, self.src)
        return self.src

    def ensure_trader(self) -> QMTTrader | None:
        if not self.cfg.qmt_enabled:
            return None
        if self.trader is not None and self.trader.is_connected():
            return self.trader
        # 连接失败后 60 秒内不再重试（避免同一任务内反复连接刷屏）
        now = time.time()
        if now - self._last_connect_attempt < 60 and self._connect_failed:
            return None
        self._last_connect_attempt = now

        q = self.cfg.section("qmt")
        path = q.get("userdata_path") or detect_userdata(q.get("auto_detect_paths"))
        if not path:
            LOG.warning("未找到 userdata_mini 目录，交易功能不可用")
            self._connect_failed = True
            return None
        self.trader = QMTTrader(
            userdata_path=path,
            session_id=q.get("session_id", 20261014),
            account_id=q.get("account_id", ""),
            mode=self.cfg.mode,
            connect_timeout=q.get("connect_timeout", 30),
        )
        ok = self.trader.connect()
        self._connect_failed = not ok
        if not ok:
            LOG.warning("miniQMT 连接失败（可能是终端未启动/未登录）。"
                        "本轮任务跳过交易操作。")
        return self.trader if ok else None

    # ------------------------------------------------------------ 任务：盘前准备
    def task_prepare(self) -> dict:
        """盘前：更新标的池、检查择时、输出今日计划。"""
        LOG.info("=" * 60)
        LOG.info("【盘前准备】")
        self.ensure_source()

        today = datetime.now().strftime("%Y%m%d")
        # 用最近交易日
        cal = self.src.trade_calendar(
            (pd.Timestamp(today) - pd.Timedelta(days=15)).strftime("%Y%m%d"), today)
        trade_date = cal[-1] if cal else today

        uni = self.cfg.section("universe")
        codes = self.src.build_universe(
            trade_date,
            cap_min=uni.get("market_cap_min", 30),
            cap_max=uni.get("market_cap_max", 80),
            exclude_st=uni.get("exclude_st", True),
            exclude_bj=uni.get("exclude_bj", True),
            exclude_kcb=uni.get("exclude_kcb", False),
        )

        passed, mret = self.strategy.market_ok(trade_date)
        plan = {
            "trade_date": trade_date,
            "universe_size": len(codes),
            "market_ret": round(mret, 2) if mret is not None else None,
            "market_threshold": self.strategy.market_max,
            "market_passed": passed,
            "action": "可扫描信号" if passed else "空仓等待（择时未通过）",
        }
        self._save_json("universe.json", {"date": trade_date, "codes": codes})
        self._save_json("plan.json", plan)
        self._log_run("prepare", plan)

        LOG.info(f"  交易日      : {trade_date}")
        LOG.info(f"  标的池      : {len(codes)} 只")
        LOG.info(f"  市场N日收益 : {plan['market_ret']}%  (门槛 {plan['market_threshold']}%)")
        LOG.info(f"  择时        : {'通过' if passed else '未通过'}")
        LOG.info(f"  今日动作    : {plan['action']}")
        return plan

    # ------------------------------------------------------------ 任务：执行建仓
    def task_execute_entry(self, dry_run: bool = False) -> dict:
        """开盘后：扫描信号并下单。"""
        LOG.info("=" * 60)
        LOG.info("【执行建仓】" + ("（演练模式）" if dry_run else ""))
        self.ensure_source()

        plan = self._load_json("plan.json", {})
        if not plan.get("market_passed", False):
            LOG.info("  择时未通过，跳过建仓")
            out = {"skipped": True, "reason": "择时未通过"}
            self._log_run("execute_entry", out)
            return out

        uni = self._load_json("universe.json", {})
        codes = uni.get("codes", [])
        if not codes:
            LOG.warning("  标的池为空，请先运行 prepare")
            return {"skipped": True, "reason": "标的池为空"}

        sig = self.strategy.scan(codes)
        self._save_json("signals.json", sig.to_dict("records") if len(sig) else [])
        LOG.info(f"  信号数: {len(sig)}")

        if len(sig) == 0 or dry_run:
            out = {"signals": len(sig), "ordered": 0, "dry_run": dry_run}
            self._log_run("execute_entry", out)
            return out

        # 下单
        trader = self.ensure_trader()
        if trader is None:
            LOG.warning("  交易接口不可用，仅保存信号")
            return {"signals": len(sig), "ordered": 0, "reason": "无交易接口"}

        pf = self.cfg.section("strategy.portfolio")
        risk = self.cfg.section("strategy.risk")
        asset = trader.query_asset()

        # ---- 购买力解析（兼容余额理财自动申赎）
        pp_mode = self.cfg.get("strategy.portfolio.purchasing_power_mode", "auto")
        min_order = pf.get("min_order_amount", 10000)
        # 若用户设置了初始资金，用它作为兜底口径
        pp = resolve_purchasing_power(asset, mode=pp_mode, min_order=min_order)
        LOG.info(f"  购买力: {pp.available:,.0f} 元（口径 {pp.source}）")
        if pp.in_wealth > 0:
            LOG.info(f"  理财占用: {pp.in_wealth:,.0f} 元"
                     + ("（依赖自动赎回）" if pp.auto_redeem else ""))
        if not pp.ok:
            # 兜底：用配置的初始资金（便于演练与小资金测试）
            fallback = float(pf.get("initial_capital", 0) or 0)
            if fallback > 0 and pp_mode != "cash":
                LOG.warning(f"  账户购买力不足，回退到配置资金 {fallback:,.0f} 元")
                pp.available = fallback * 0.98
                pp.total = fallback
                pp.source = "config.initial_capital"
                pp.ok = True
            else:
                LOG.warning(f"  购买力不足，跳过建仓: {pp.message}")
                return {"signals": len(sig), "ordered": 0, "reason": pp.message}

        total = pp.total or pf.get("initial_capital", 1e6)
        cash = pp.available
        max_pos = pf.get("max_positions", 5)
        max_pct = pf.get("max_position_pct", 0.25)

        cur_pos = trader.query_positions()
        n_held = len(cur_pos)
        free = max_pos - n_held
        if free <= 0:
            LOG.info(f"  已满仓（{n_held}/{max_pos}），不再建仓")
            return {"signals": len(sig), "ordered": 0, "reason": "已满仓"}

        ordered = []
        for _, s in sig.head(free).iterrows():
            code = s["code"]
            price = float(s["close"])
            if price <= 0:
                continue
            q = self.cfg.section("qmt.order")
            vol, amt = self.strategy.calc_position_size(total, cash, max_pos, max_pct, price)
            if vol <= 0:
                LOG.warning(f"  {code} 仓位计算为 0，跳过")
                continue
            if amt > cash:
                LOG.warning(f"  {code} 需要 {amt:,.0f} 元 > 剩余 {cash:,.0f} 元，跳过")
                continue
            limit_price = round(price * (1 + q.get("price_offset_pct", 0.003)), 2)
            oid = trader.buy(code, vol, limit_price, q.get("price_type", "FIX_PRICE"))
            if oid:
                self.positions[code] = {
                    "entry_date": datetime.now().strftime("%Y-%m-%d"),
                    "entry_price": limit_price,
                    "shares": vol,
                    "order_id": oid,
                    "signal": s.to_dict(),
                }
                self._save_json("positions.json", self.positions)
                ordered.append({"code": code, "vol": vol, "price": limit_price, "order_id": oid})
                cash -= amt

        out = {"signals": len(sig), "ordered": len(ordered), "orders": ordered}
        self._log_run("execute_entry", out)
        LOG.info(f"  已下单 {len(ordered)} 笔")
        return out

    # ------------------------------------------------------------ 任务：持仓监控
    def task_monitor_exit(self, dry_run: bool = False) -> dict:
        """盘中/尾盘：检查持仓是否触发离场。"""
        LOG.info("=" * 60)
        LOG.info("【持仓监控】" + ("（演练模式）" if dry_run else ""))
        self.ensure_source()

        trader = self.ensure_trader()
        rows = []

        # 优先用 QMT 实际持仓
        if trader is not None and trader.is_connected():
            pos = trader.query_positions()
            if len(pos):
                for _, p in pos.iterrows():
                    code = p["证券代码"]
                    entry = self.positions.get(code, {}).get("entry_price") or p["成本价"]
                    edate = self.positions.get(code, {}).get("entry_date") or \
                        datetime.now().strftime("%Y-%m-%d")
                    r = self.strategy.check_exit(code, float(entry), edate)
                    r["来源"] = "QMT持仓"
                    rows.append(r)
        # 回退到本地台账
        if not rows:
            for code, info in self.positions.items():
                r = self.strategy.check_exit(code, info["entry_price"], info["entry_date"])
                r["来源"] = "本地台账"
                rows.append(r)

        df = pd.DataFrame(rows)
        sells = []
        if len(df):
            LOG.info("\n" + df[["code", "现价", "成本", "盈亏%", "持有天数",
                                "距止盈%", "距止损%", "action", "reason"]].to_string(index=False))
            for _, r in df[df["action"] == "sell"].iterrows():
                code = r["code"]
                if dry_run:
                    LOG.info(f"  [演练] 应卖出 {code}: {r['reason']}")
                    continue
                if trader is None or not trader.is_connected():
                    LOG.warning(f"  {code} 需卖出但交易接口不可用")
                    continue
                pos = trader.query_positions()
                vol = 0
                if len(pos):
                    m = pos[pos["证券代码"] == code]
                    if len(m):
                        vol = int(m.iloc[0]["可用数量"])
                if vol <= 0:
                    LOG.warning(f"  {code} 可用数量为 0，跳过")
                    continue
                q = self.cfg.section("qmt.order")
                limit_price = round(float(r["现价"]) * (1 - q.get("price_offset_pct", 0.003)), 2)
                oid = trader.sell(code, vol, limit_price, q.get("price_type", "FIX_PRICE"))
                if oid:
                    sells.append({"code": code, "vol": vol, "price": limit_price,
                                  "reason": r["reason"], "order_id": oid})
                    self.positions.pop(code, None)
                    self._save_json("positions.json", self.positions)

        out = {"monitored": len(df), "sold": len(sells), "details": sells}
        self._log_run("monitor_exit", out)
        return out

    # ------------------------------------------------------------ 任务：收盘结算
    def task_settle(self) -> dict:
        """收盘后：记录成交、更新台账、生成日报。"""
        LOG.info("=" * 60)
        LOG.info("【收盘结算】")
        trader = self.ensure_trader()
        asset = trader.query_asset() if trader else {}
        pos = trader.query_positions() if trader else pd.DataFrame()
        trades = trader.query_trades() if trader else pd.DataFrame()
        orders = trader.query_orders() if trader else pd.DataFrame()

        # 清理台账中已无持仓的记录
        if len(pos):
            held = set(pos["证券代码"].tolist())
            for c in list(self.positions.keys()):
                if c not in held and self.positions[c].get("order_id"):
                    LOG.info(f"  {c} 已不在持仓，从台账移除")
                    self.positions.pop(c, None)
            self._save_json("positions.json", self.positions)

        report = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "asset": asset,
            "positions": len(pos),
            "trades_today": len(trades),
            "orders_today": len(orders),
        }
        self._save_json("report.json", report)
        if len(trades):
            trades.to_csv(self.state_dir / f"trades_{report['date']}.csv",
                          index=False, encoding="utf-8-sig")
        self._log_run("settle", report)

        LOG.info(f"  总资产   : {asset.get('总资产', 0):,.0f}")
        LOG.info(f"  可用资金 : {asset.get('可用资金', 0):,.0f}")
        LOG.info(f"  持仓数   : {len(pos)}")
        LOG.info(f"  今日成交 : {len(trades)}")
        return report

    # ------------------------------------------------------------ 任务：数据更新
    def task_update_data(self, codes: list[str] | None = None) -> dict:
        """更新本地K线缓存。"""
        LOG.info("=" * 60)
        LOG.info("【数据更新】")
        self.ensure_source()
        if codes is None:
            uni = self._load_json("universe.json", {})
            codes = uni.get("codes", [])
        ok = 0
        for i, c in enumerate(codes):
            try:
                df = self.src.daily(c, "20150101",
                                    datetime.now().strftime("%Y%m%d"), adj="qfq")
                if df is not None:
                    ok += 1
            except Exception:
                pass
            if (i + 1) % 50 == 0:
                LOG.info(f"  更新 {i+1}/{len(codes)}，成功 {ok}")
        out = {"total": len(codes), "updated": ok}
        self._log_run("update_data", out)
        return out

    # ------------------------------------------------------------ 状态摘要
    def status(self) -> dict:
        plan = self._load_json("plan.json", {})
        report = self._load_json("report.json", {})
        sig = self._load_json("signals.json", [])
        return {
            "mode": self.cfg.mode,
            "plan": plan,
            "last_report": report,
            "signals": len(sig) if isinstance(sig, list) else 0,
            "positions_tracked": len(self.positions),
            "qmt_connected": bool(self.trader and self.trader.is_connected()),
        }
