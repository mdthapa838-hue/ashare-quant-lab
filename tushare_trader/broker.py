# -*- coding: utf-8 -*-
"""
tushare_trader/broker.py — 模拟盘经纪商（paper broker）

设计目标
--------
在没有券商接口（miniQMT）或不想动真钱时，仍能完整跑通一套交易流程：
    信号 -> 下单 -> 成交 -> 持仓 -> 平仓 -> 结算 -> 绩效

成交模型（保守、可解释）：
  * 信号 T 日收盘后生成，T+1 开盘价成交
  * 买入受涨停约束（开盘涨幅 > limit_up_pct 不成交）
  * 卖出受跌停约束（开盘跌幅 < limit_down_pct 不成交）
  * 买入整手（100 股）；卖出可全平
  * 成本按 CostModel 扣减

持久化
------
状态全部写入 `state/` 目录的 JSON，进程重启后可继续：
    paper_account.json   现金/权益/持仓/成交/订单
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from .costs import CostModel

LOG = logging.getLogger("tushare_trader.broker")


@dataclass
class Position:
    code: str
    entry_date: str
    entry_price: float
    shares: int
    stop_price: float
    signal: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trade:
    code: str
    side: str            # buy / sell
    date: str
    price: float
    shares: int
    amount: float        # 成交金额（未扣费）
    fee: float
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class PaperBroker:
    """
    模拟盘账户。

    Parameters
    ----------
    initial_capital : float
        初始资金。
    cost : CostModel
        交易成本模型。
    state_dir : str | Path, optional
        状态目录；提供时自动持久化。
    limit_up_pct / limit_down_pct : float
        涨停/跌停开盘涨跌幅阈值（百分比）。
    """

    STATE_FILE = "paper_account.json"

    def __init__(self, initial_capital: float = 1_000_000.0,
                 cost: CostModel | None = None,
                 state_dir: str | Path | None = None,
                 limit_up_pct: float = 9.5,
                 limit_down_pct: float = -9.5):
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)
        self.cost = cost or CostModel()
        self.state_dir = Path(state_dir) if state_dir else None
        self.limit_up_pct = float(limit_up_pct)
        self.limit_down_pct = float(limit_down_pct)

        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.orders: list[dict] = []
        self.equity_history: list[dict] = []

        if self.state_dir:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._load()

    # ------------------------------------------------------------ 持久化
    @property
    def _path(self) -> Path | None:
        return self.state_dir / self.STATE_FILE if self.state_dir else None

    def _load(self) -> None:
        p = self._path
        if not p or not p.exists():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            LOG.warning(f"模拟盘状态读取失败，使用初始资金: {e}")
            return
        self.cash = float(raw.get("cash", self.initial_capital))
        self.positions = {
            k: Position(**v) for k, v in (raw.get("positions") or {}).items()
        }
        self.trades = [Trade(**t) for t in raw.get("trades") or []]
        self.orders = list(raw.get("orders") or [])
        self.equity_history = list(raw.get("equity_history") or [])

    def save(self) -> None:
        p = self._path
        if not p:
            return
        data = {
            "cash": self.cash,
            "initial_capital": self.initial_capital,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "trades": [t.to_dict() for t in self.trades],
            "orders": self.orders,
            "equity_history": self.equity_history,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    # ------------------------------------------------------------ 查询
    def market_value(self, marks: dict[str, float]) -> float:
        """按给定价格表计算持仓市值。"""
        total = 0.0
        for code, pos in self.positions.items():
            px = marks.get(code)
            total += (float(px) if px else pos.entry_price) * pos.shares
        return total

    def equity(self, marks: dict[str, float] | None = None) -> float:
        return self.cash + self.market_value(marks or {})

    def position_codes(self) -> list[str]:
        return list(self.positions.keys())

    # ------------------------------------------------------------ 下单
    def buy(self, code: str, bar: pd.Series, date: str,
            shares: int, stop_price: float, reason: str = "signal",
            signal: dict | None = None) -> Trade | None:
        """
        次日开盘买入。

        Parameters
        ----------
        bar : Series
            成交日的 K 线（需含 open / open_chg_pct）。
        shares : int
            期望股数（会向下取整为 100 的整数倍并受现金约束）。
        """
        open_px = float(bar["open"])
        if open_px <= 0:
            return None
        chg = bar.get("open_chg_pct")
        if pd.notna(chg) and float(chg) > self.limit_up_pct:
            self._record_order(code, "buy", date, open_px, shares, "rejected_limit_up")
            return None

        shares = int(shares) // 100 * 100
        afford = self.cost.affordability(open_px, self.cash)
        shares = min(shares, afford)
        if shares <= 0:
            self._record_order(code, "buy", date, open_px, shares, "rejected_no_cash")
            return None

        amount = shares * open_px
        fee = self.cost.buy_cost(amount)
        self.cash -= (amount + fee)
        self.positions[code] = Position(
            code=code, entry_date=date, entry_price=open_px, shares=shares,
            stop_price=float(stop_price), signal=signal or {},
        )
        trade = Trade(code=code, side="buy", date=date, price=open_px,
                      shares=shares, amount=amount, fee=fee, reason=reason)
        self.trades.append(trade)
        self._record_order(code, "buy", date, open_px, shares, "filled")
        self.save()
        return trade

    def sell(self, code: str, bar: pd.Series, date: str,
             reason: str = "exit") -> Trade | None:
        """次日开盘卖出全部持仓。"""
        pos = self.positions.get(code)
        if pos is None:
            return None
        open_px = float(bar["open"])
        if open_px <= 0:
            return None
        chg = bar.get("open_chg_pct")
        if pd.notna(chg) and float(chg) < self.limit_down_pct:
            self._record_order(code, "sell", date, open_px, pos.shares, "rejected_limit_down")
            return None

        amount = pos.shares * open_px
        fee = self.cost.sell_cost(amount)
        self.cash += (amount - fee)
        trade = Trade(code=code, side="sell", date=date, price=open_px,
                      shares=pos.shares, amount=amount, fee=fee, reason=reason)
        self.trades.append(trade)
        self.positions.pop(code, None)
        self._record_order(code, "sell", date, open_px, trade.shares, "filled")
        self.save()
        return trade

    def _record_order(self, code: str, side: str, date: str, price: float,
                      shares: int, status: str) -> None:
        self.orders.append({
            "code": code, "side": side, "date": date, "price": round(float(price), 3),
            "shares": int(shares), "status": status,
        })

    # ------------------------------------------------------------ 结算
    def mark_to_market(self, date: str, marks: dict[str, float]) -> dict:
        """记录当日权益快照。"""
        eq = self.equity(marks)
        rec = {
            "date": date,
            "equity": round(eq, 2),
            "cash": round(self.cash, 2),
            "n_positions": len(self.positions),
        }
        self.equity_history.append(rec)
        return rec

    def summary(self, marks: dict[str, float] | None = None) -> dict:
        marks = marks or {}
        eq = self.equity(marks)
        return {
            "cash": round(self.cash, 2),
            "market_value": round(self.market_value(marks), 2),
            "equity": round(eq, 2),
            "initial_capital": self.initial_capital,
            "total_return_pct": round((eq / self.initial_capital - 1) * 100, 2),
            "n_positions": len(self.positions),
            "n_trades": len(self.trades),
            "positions": [
                {
                    "code": p.code, "entry_date": p.entry_date,
                    "entry_price": round(p.entry_price, 3),
                    "shares": p.shares,
                    "stop_price": round(p.stop_price, 3),
                    "mark": round(float(marks[p.code]), 3) if p.code in marks else None,
                    "pnl_pct": round((float(marks[p.code]) / p.entry_price - 1) * 100, 2)
                    if p.code in marks else None,
                }
                for p in self.positions.values()
            ],
        }
