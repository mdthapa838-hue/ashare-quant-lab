# -*- coding: utf-8 -*-
"""
core/purchasing_power.py — 购买力解析

背景：
  券商「余额理财」把闲置资金自动申购货币基金，导致 QMT 的
  `query_stock_asset().cash` 显示 0。下单时券商端自动赎回，
  因此 cash=0 并不代表无法交易。

本模块提供统一的购买力口径：

  mode = "cash"      只用 cash（严格模式，适合无余额理财的账户）
  mode = "auto"      优先 cash；cash 不足时回退到 total_asset（默认）
  mode = "total"     始终用 total_asset

输出结构包含：
  - available       可用于下单的金额（元）
  - cash            股票账户可用资金
  - total           账户总资产
  - in_wealth       理财/货基占用金额
  - source          本次口径来源（cash / total_asset）
  - auto_redeem     是否疑似依赖自动赎回
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from core.logger import get_logger

LOG = get_logger("power")


@dataclass
class PurchasingPower:
    available: float = 0.0
    cash: float = 0.0
    frozen: float = 0.0
    market_value: float = 0.0
    total: float = 0.0
    in_wealth: float = 0.0
    source: str = "cash"
    auto_redeem: bool = False
    ok: bool = False
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_purchasing_power(asset: dict, mode: str = "auto",
                             min_order: float = 10000.0,
                             reserve_ratio: float = 0.02) -> PurchasingPower:
    """
    解析账户购买力。

    Parameters
    ----------
    asset : dict
        QMTTrader.query_asset() 的返回值，含
        总资产/可用资金/冻结资金/持仓市值。
    mode : str
        cash / auto / total，见模块说明。
    min_order : float
        单笔最小下单金额。
    reserve_ratio : float
        预留比例（应对费用、滑点、价格上浮）。

    Returns
    -------
    PurchasingPower
    """
    pp = PurchasingPower()
    if not asset:
        pp.ok = False
        pp.message = "未获取到资产数据"
        return pp

    total = float(asset.get("总资产", 0) or 0)
    cash = float(asset.get("可用资金", 0) or 0)
    frozen = float(asset.get("冻结资金", 0) or 0)
    mv = float(asset.get("持仓市值", 0) or 0)

    pp.total = total
    pp.cash = cash
    pp.frozen = frozen
    pp.market_value = mv
    pp.in_wealth = max(0.0, total - cash - frozen - mv)

    # 口径选择
    if mode == "total":
        base, src = total, "total_asset"
    elif mode == "cash":
        base, src = cash, "cash"
    else:  # auto
        if cash >= min_order:
            base, src = cash, "cash"
        elif total >= min_order:
            base, src = total, "total_asset"
            pp.auto_redeem = True
        else:
            base, src = cash, "cash"

    pp.source = src
    pp.available = max(0.0, base * (1 - reserve_ratio))

    # 判定
    if pp.available < min_order:
        pp.ok = False
        pp.message = (f"可用购买力 {pp.available:,.0f} 元 < "
                      f"最小下单 {min_order:,.0f} 元")
    else:
        pp.ok = True
        if pp.auto_redeem:
            pp.message = (f"使用总资产口径（疑似余额理财自动申赎）；"
                          f"理财占用 {pp.in_wealth:,.0f} 元")
        else:
            pp.message = f"使用可用资金口径，{pp.available:,.0f} 元"

    return pp


def check_sufficient(asset: dict, need_amount: float, mode: str = "auto",
                     min_order: float = 10000.0) -> tuple[bool, str]:
    """
    检查是否有足够购买力下单指定金额。

    Returns (是否足够, 说明)
    """
    pp = resolve_purchasing_power(asset, mode, min_order)
    if not pp.ok:
        return False, pp.message
    if pp.available < need_amount:
        return False, (f"可用 {pp.available:,.0f} 元 < 需要 {need_amount:,.0f} 元"
                       f"（口径 {pp.source}）")
    return True, f"充足（口径 {pp.source}，可用 {pp.available:,.0f} 元）"
