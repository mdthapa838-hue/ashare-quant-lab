# -*- coding: utf-8 -*-
"""
tools/probe_purchasing_power.py — 探测实际可买额度

背景：账户资金在货币基金中，cash 显示 0。需确认：
  1. QMT 是否提供「可买额度」类接口（会自动计入货基）
  2. query_bank_amount 等接口返回什么
  3. 是否存在自动申赎的标志位
  4. 下单前应使用哪个字段判断资金充足

只读，不下单。
"""
from __future__ import annotations

import os
import sys
import inspect
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import pandas as pd
from core.config import load_config
from core.logger import setup_logger
from core.qmt_trader import QMTTrader, detect_userdata

try:
    from xtquant import xtconstant
except ImportError:
    xtconstant = None


def main():
    cfg = load_config()
    setup_logger("probe", log_dir=None, level="WARNING", console=True)

    q = cfg.section("qmt")
    path = q.get("userdata_path") or detect_userdata(q.get("auto_detect_paths"))
    t = QMTTrader(path, session_id=q.get("session_id", 20261014) + 2,
                  account_id=q.get("account_id", ""), mode="simulation")
    if not t.connect():
        print("连接失败")
        return 1

    print("\n" + "=" * 80)
    print("  [A] XtQuantTrader 全部可查询方法（与资金相关）")
    print("=" * 80)
    methods = [m for m in dir(t.trader) if not m.startswith("_")]
    kw = ("cash", "money", "fund", "asset", "balance", "bank", "amount", "buy")
    for m in methods:
        if any(k in m.lower() for k in kw):
            try:
                sig = inspect.signature(getattr(t.trader, m))
                print(f"  {m}{sig}")
            except Exception:
                print(f"  {m}(...)")

    print("\n" + "=" * 80)
    print("  [B] 尝试各查询接口")
    print("=" * 80)

    # 1. query_account_info
    for m in ("query_account_info", "query_account_infos"):
        if hasattr(t.trader, m):
            try:
                r = getattr(t.trader, m)(t.account)
                print(f"\n  ▶ {m} → {type(r).__name__}")
                if r is None:
                    print("     None")
                elif isinstance(r, (list, tuple)):
                    for x in r:
                        d = {f: getattr(x, f, None) for f in dir(x)
                             if not f.startswith("_") and not callable(getattr(x, f, None))}
                        print(f"     {d}")
                else:
                    d = {f: getattr(r, f, None) for f in dir(r)
                         if not f.startswith("_") and not callable(getattr(r, f, None))}
                    print(f"     {d}")
            except Exception as e:
                print(f"\n  ▶ {m} → 异常 {type(e).__name__}: {str(e)[:120]}")

    # 2. query_bank_amount
    if hasattr(t.trader, "query_bank_amount"):
        try:
            r = t.trader.query_bank_amount(t.account)
            print(f"\n  ▶ query_bank_amount → {type(r).__name__}: {r}")
            if r is not None and not isinstance(r, (int, float, str)):
                d = {f: getattr(r, f, None) for f in dir(r)
                     if not f.startswith("_") and not callable(getattr(r, f, None))}
                print(f"     {d}")
        except Exception as e:
            print(f"\n  ▶ query_bank_amount → 异常 {str(e)[:120]}")

    # 3. 资产对象全字段（再确认一次）
    print("\n" + "=" * 80)
    print("  [C] 资产对象全字段 + 可用资金相关推断")
    print("=" * 80)
    a = t.trader.query_stock_asset(t.account)
    if a is not None:
        total = float(getattr(a, "total_asset", 0) or 0)
        cash = float(getattr(a, "cash", 0) or 0)
        frozen = float(getattr(a, "frozen_cash", 0) or 0)
        mv = float(getattr(a, "market_value", 0) or 0)
        cur = float(getattr(a, "current_balance", 0) or 0)
        fetch = float(getattr(a, "fetch_balance", 0) or 0)

        print(f"  total_asset      {total:>14,.2f}   ← 账户总资产（含理财）")
        print(f"  cash             {cash:>14,.2f}   ← 可用资金（股票交易）")
        print(f"  frozen_cash      {frozen:>14,.2f}")
        print(f"  market_value     {mv:>14,.2f}")
        print(f"  current_balance  {cur:>14,.2f}   ← 当前余额")
        print(f"  fetch_balance    {fetch:>14,.2f}   ← 可取余额")
        print()
        print(f"  ┌─ 推断 ─────────────────────────────────────────")
        print(f"  │ 股票账户可用      : {cash:,.2f}")
        print(f"  │ 货基/理财占用     : {total - cash - frozen - mv:,.2f}")
        print(f"  │ 含理财的总购买力  : {total:,.2f}（若支持自动申赎）")
        print(f"  └────────────────────────────────────────────────")

    # 4. 账户状态
    print("\n" + "=" * 80)
    print("  [D] 账户状态（login_status / 是否可交易）")
    print("=" * 80)
    try:
        infos = t.trader.query_account_infos() or []
        for i in infos:
            d = {f: getattr(i, f, None) for f in
                 ("account_id", "account_type", "broker_id", "login_status",
                  "platform_id", "product_name")}
            print(f"  {d}")
            ls = getattr(i, "login_status", None)
            print(f"  login_status={ls}  "
                  f"({'已登录' if ls in (0, '0') else '未登录/未知'})")
    except Exception as e:
        print(f"  异常: {e}")

    # 5. xtconstant 中与资金/申购赎回相关的常量
    print("\n" + "=" * 80)
    print("  [E] xtconstant 资金/申赎相关常量")
    print("=" * 80)
    if xtconstant is not None:
        ks = [k for k in dir(xtconstant)
              if not k.startswith("_") and
              any(w in k.upper() for w in ("FUND", "BANK", "TRANSFER", "CASH"))]
        for k in ks[:40]:
            try:
                print(f"  {k:<42} = {getattr(xtconstant, k)}")
            except Exception:
                pass
    else:
        print("  xtconstant 不可用")

    t.disconnect()
    print("\n探测完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
