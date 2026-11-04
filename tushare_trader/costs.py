# -*- coding: utf-8 -*-
"""
tushare_trader/costs.py — A 股交易成本模型

回测与模拟盘共用同一套成本口径，避免「回测一套、模拟盘另一套」。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """交易成本：佣金 + 印花税 + 滑点，佣金可设最低值。"""

    commission: float = 0.00025     # 佣金（单边）
    stamp_tax: float = 0.0005       # 印花税（仅卖出）
    slippage: float = 0.00075       # 滑点（单边）
    min_commission: float = 5.0     # 最低佣金（元）

    @classmethod
    def from_config(cls, cfg: dict | None) -> "CostModel":
        c = (cfg or {}).get("cost", cfg or {})
        return cls(
            commission=float(c.get("commission", 0.00025)),
            stamp_tax=float(c.get("stamp_tax", 0.0005)),
            slippage=float(c.get("slippage", 0.00075)),
            min_commission=float(c.get("min_commission", 5.0)),
        )

    # ------------------------------------------------------------------
    @property
    def buy_rate(self) -> float:
        """买入单边成本率（不含最低佣金部分）。"""
        return self.commission + self.slippage

    @property
    def sell_rate(self) -> float:
        """卖出单边成本率（含印花税）。"""
        return self.commission + self.stamp_tax + self.slippage

    @property
    def round_trip(self) -> float:
        return self.buy_rate + self.sell_rate

    # ------------------------------------------------------------------
    def buy_cost(self, amount: float) -> float:
        """买入成交额 `amount` 对应的费用（含滑点折价）。"""
        if amount <= 0:
            return 0.0
        fee = max(amount * self.commission, self.min_commission) + amount * self.slippage
        return fee

    def sell_cost(self, amount: float) -> float:
        """卖出成交额 `amount` 对应的费用（含印花税与滑点）。"""
        if amount <= 0:
            return 0.0
        fee = (max(amount * self.commission, self.min_commission)
               + amount * self.stamp_tax + amount * self.slippage)
        return fee

    def affordability(self, price: float, cash: float, lot: int = 100) -> int:
        """在给定价格与现金下，可买入的整手股数。"""
        if price <= 0 or cash <= 0:
            return 0
        shares = int(cash / (price * (1 + self.buy_rate)) / lot) * lot
        while shares > 0 and price * shares + self.buy_cost(price * shares) > cash:
            shares -= lot
        return max(0, shares)
