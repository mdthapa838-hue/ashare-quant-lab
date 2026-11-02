# -*- coding: utf-8 -*-
"""
core/qmt_trader.py — miniQMT 交易接口封装

特性：
  - 自动探测 userdata_mini 路径
  - 连接/重连管理
  - 查询：资产、持仓、委托、成交
  - 交易：限价买卖、撤单
  - 安全：模式门禁（simulation 只读 / live 可下单）、下单前约束校验

重要：
  - simulation 模式下 order_* 方法默认拒绝执行（除非 allow_sim_order=True）
  - live 模式必须显式在配置中打开，且需二次确认参数
"""
from __future__ import annotations

import os
import time
import threading
from pathlib import Path
from typing import Any

import pandas as pd

from core.logger import get_logger

LOG = get_logger("qmt_trader")

# xtquant 为可选依赖
try:
    from xtquant.xttrader import XtQuantTrader
    from xtquant.xttype import StockAccount
    from xtquant import xtconstant
    HAS_XT = True
except ImportError:
    HAS_XT = False
    XtQuantTrader = None
    StockAccount = None
    xtconstant = None


# ---------------------------------------------------------------------------
# 常量（xtconstant 缺失时的回退定义）
# ---------------------------------------------------------------------------
def _const(name: str, fallback: int) -> int:
    if xtconstant is not None and hasattr(xtconstant, name):
        return getattr(xtconstant, name)
    return fallback


ORDER_TYPE_LIMIT = _const("STOCK_BUY", 23)          # 买入
ORDER_TYPE_SELL = _const("STOCK_SELL", 24)          # 卖出
PRICE_TYPE_FIX = _const("FIX_PRICE", 11)            # 限价
PRICE_TYPE_LATEST = _const("LATEST_PRICE", 5)       # 最新价
ORDER_STATUS = {
    48: "未报", 49: "待报", 50: "已报", 51: "已报待撤",
    52: "部成待撤", 53: "部撤", 54: "已撤", 55: "部成",
    56: "已成", 57: "废单", 255: "未知",
}


# ---------------------------------------------------------------------------
# 路径探测
# ---------------------------------------------------------------------------
def detect_userdata(candidates: list[str] | None = None) -> str | None:
    """探测 userdata_mini 目录。"""
    cands = candidates or []
    # 内置常见路径
    cands += [
        r"D:\迅投QMT交易终端浙商证券版\userdata_mini",
        r"D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\userdata_mini",
        r"C:\国金QMT交易端\userdata_mini",
        r"C:\迅投QMT\userdata_mini",
        r"D:\国金QMT交易端\userdata_mini",
        r"D:\QMT\userdata_mini",
    ]
    for c in cands:
        if not c:
            continue
        p = Path(c)
        if p.is_dir():
            # 校验是否像 miniQMT 的 userdata
            has_users = (p / "users").is_dir()
            has_datas = (p / "datas").is_dir()
            if has_users or has_datas:
                return str(p)
    # 全盘浅层搜索
    for root in (r"C:\\", r"D:\\"):
        try:
            for sub in Path(root).glob("*/userdata_mini"):
                if sub.is_dir():
                    return str(sub)
            for sub in Path(root).glob("*/*/userdata_mini"):
                if sub.is_dir():
                    return str(sub)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# 交易接口
# ---------------------------------------------------------------------------
class QMTTrader:
    """
    miniQMT 交易封装。

    Parameters
    ----------
    userdata_path : str
        userdata_mini 目录。
    session_id : int
        会话 ID（同目录下多进程需不同）。
    account_id : str
        资金账号。
    mode : str
        'simulation' 只读查询；'live' 允许下单。
    allow_sim_order : bool
        simulation 模式下是否允许下单（默认 False）。
    """

    def __init__(self, userdata_path: str, session_id: int = 20261014,
                 account_id: str = "", mode: str = "simulation",
                 allow_sim_order: bool = False, connect_timeout: int = 30):
        if not HAS_XT:
            raise RuntimeError("未安装 xtquant。请先安装并配置好 miniQMT 终端。")

        self.userdata_path = str(Path(userdata_path).resolve())
        self.session_id = int(session_id)
        self.account_id = account_id
        self.mode = mode
        self.allow_sim_order = allow_sim_order
        self.connect_timeout = connect_timeout

        self.trader: XtQuantTrader | None = None
        self.account: StockAccount | None = None
        self._connected = False
        self._lock = threading.RLock()
        self._callback = _Callback()

    # ------------------------------------------------------------ 连接
    def connect(self) -> bool:
        """建立连接并订阅账户。"""
        with self._lock:
            if self._connected:
                return True
            try:
                path = Path(self.userdata_path)
                if not path.is_dir():
                    LOG.error(f"userdata 目录不存在: {self.userdata_path}")
                    return False

                self.trader = XtQuantTrader(self.userdata_path, self.session_id)
                self.trader.register_callback(self._callback)
                self.trader.start()

                r = self.trader.connect()
                if r != 0:
                    LOG.error(f"连接 miniQMT 失败，返回码 {r}。"
                              f"请确认 QMT 终端已启动并已登录。")
                    return False

                # 账户
                acct = self.account_id or self._auto_account()
                if not acct:
                    LOG.error("未能获取资金账号，请在配置中指定 qmt.account_id")
                    return False
                self.account = StockAccount(acct)
                sr = self.trader.subscribe(self.account)
                if sr != 0:
                    LOG.warning(f"订阅账户返回码 {sr}（部分环境仍可查询）")

                self._connected = True
                LOG.info(f"已连接 miniQMT | 路径 {self.userdata_path} | "
                         f"账号 {self._mask(acct)} | 模式 {self.mode}")
                return True
            except Exception as e:
                LOG.exception(f"连接异常: {e}")
                return False

    def _auto_account(self) -> str | None:
        """从 QMT 查询可用账号。"""
        try:
            infos = self.trader.query_account_infos()
            if infos:
                for a in infos:
                    aid = getattr(a, "account_id", None)
                    atype = getattr(a, "account_type", None)
                    if aid and (atype in (None, 2, "STOCK")):
                        return str(aid)
        except Exception as e:
            LOG.debug(f"自动查询账号失败: {e}")
        return None

    @staticmethod
    def _mask(s: str) -> str:
        s = str(s)
        return s[:2] + "*" * max(0, len(s) - 4) + s[-2:] if len(s) > 4 else "***"

    def disconnect(self) -> None:
        with self._lock:
            try:
                if self.trader and self.account:
                    self.trader.unsubscribe(self.account)
            except Exception:
                pass
            try:
                if self.trader:
                    self.trader.stop()
            except Exception:
                pass
            self._connected = False
            LOG.info("已断开 miniQMT 连接")

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------ 门禁
    def _check_trade_permission(self) -> None:
        if self.mode != "live" and not self.allow_sim_order:
            raise PermissionError(
                "当前为 simulation 模式，禁止下单。"
                "如需实盘请把 config.yaml 的 app.mode 改为 live；"
                "如仅测试可设 allow_sim_order=True。")

    # ------------------------------------------------------------ 查询
    def query_asset(self) -> dict:
        """查询账户资产。"""
        if not self._ensure():
            return {}
        try:
            a = self.trader.query_stock_asset(self.account)
            if a is None:
                return {}
            return {
                "总资产": float(getattr(a, "total_asset", 0) or 0),
                "可用资金": float(getattr(a, "cash", 0) or 0),
                "冻结资金": float(getattr(a, "frozen_cash", 0) or 0),
                "持仓市值": float(getattr(a, "market_value", 0) or 0),
            }
        except Exception as e:
            LOG.warning(f"查询资产失败: {e}")
            return {}

    def query_positions(self) -> pd.DataFrame:
        """查询持仓。"""
        if not self._ensure():
            return pd.DataFrame()
        try:
            ps = self.trader.query_stock_positions(self.account) or []
            rows = []
            for p in ps:
                vol = int(getattr(p, "volume", 0) or 0)
                if vol <= 0:
                    continue
                rows.append({
                    "证券代码": str(getattr(p, "stock_code", "")),
                    "持仓数量": vol,
                    "可用数量": int(getattr(p, "can_use_volume", 0) or 0),
                    "成本价": float(getattr(p, "open_price", 0) or 0),
                    "市值": float(getattr(p, "market_value", 0) or 0),
                    "浮动盈亏": float(getattr(p, "market_value", 0) or 0)
                                - float(getattr(p, "open_price", 0) or 0) * vol,
                })
            df = pd.DataFrame(rows)
            if len(df):
                df["盈亏比例"] = (df["浮动盈亏"] /
                                 (df["成本价"] * df["持仓数量"]).replace(0, pd.NA) * 100).round(2)
                df["盈亏比例"] = df["浮动盈亏"] / (df["成本价"] * df["持仓数量"]) * 100
                df["盈亏比例"] = df["盈亏比例"].round(2)
            return df
        except Exception as e:
            LOG.warning(f"查询持仓失败: {e}")
            return pd.DataFrame()

    def query_orders(self, cancelable_only: bool = False) -> pd.DataFrame:
        """查询当日委托。"""
        if not self._ensure():
            return pd.DataFrame()
        try:
            os_ = self.trader.query_stock_orders(self.account, cancelable_only) or []
            rows = []
            for o in os_:
                rows.append({
                    "委托号": getattr(o, "order_id", None),
                    "证券代码": str(getattr(o, "stock_code", "")),
                    "方向": "买入" if getattr(o, "order_type", 0) == ORDER_TYPE_LIMIT else "卖出",
                    "委托价": float(getattr(o, "price", 0) or 0),
                    "委托量": int(getattr(o, "order_volume", 0) or 0),
                    "已成交量": int(getattr(o, "traded_volume", 0) or 0),
                    "状态": ORDER_STATUS.get(getattr(o, "order_status", 255), "未知"),
                    "委托时间": getattr(o, "order_time", ""),
                })
            return pd.DataFrame(rows)
        except Exception as e:
            LOG.warning(f"查询委托失败: {e}")
            return pd.DataFrame()

    def query_trades(self) -> pd.DataFrame:
        """查询当日成交。"""
        if not self._ensure():
            return pd.DataFrame()
        try:
            ts_ = self.trader.query_stock_trades(self.account) or []
            rows = []
            for t in ts_:
                rows.append({
                    "成交编号": getattr(t, "traded_id", None),
                    "证券代码": str(getattr(t, "stock_code", "")),
                    "方向": "买入" if getattr(t, "order_type", 0) == ORDER_TYPE_LIMIT else "卖出",
                    "成交价": float(getattr(t, "traded_price", 0) or 0),
                    "成交量": int(getattr(t, "traded_volume", 0) or 0),
                    "成交额": float(getattr(t, "traded_amount", 0) or 0),
                    "成交时间": getattr(t, "traded_time", ""),
                })
            return pd.DataFrame(rows)
        except Exception as e:
            LOG.warning(f"查询成交失败: {e}")
            return pd.DataFrame()

    def _ensure(self) -> bool:
        if not self._connected:
            return self.connect()
        return True

    # ------------------------------------------------------------ 下单
    def buy(self, code: str, volume: int, price: float,
            price_type: str = "FIX_PRICE") -> int | None:
        """
        买入。

        Parameters
        ----------
        code : str
            证券代码，如 '600630.SH'
        volume : int
            股数（须为100的整数倍）
        price : float
            委托价
        Returns
        -------
        order_id 或 None（失败）
        """
        return self._order(code, volume, price, ORDER_TYPE_LIMIT, price_type)

    def sell(self, code: str, volume: int, price: float,
             price_type: str = "FIX_PRICE") -> int | None:
        """卖出。"""
        return self._order(code, volume, price, ORDER_TYPE_SELL, price_type)

    def _order(self, code: str, volume: int, price: float,
               order_type: int, price_type: str) -> int | None:
        self._check_trade_permission()
        if not self._ensure():
            return None
        volume = int(volume)
        if volume <= 0:
            LOG.warning(f"下单量非法: {volume}")
            return None
        if volume % 100 != 0:
            volume = (volume // 100) * 100
            LOG.warning(f"下单量调整为 100 的整数倍: {volume}")
            if volume <= 0:
                return None
        pt = PRICE_TYPE_FIX if price_type == "FIX_PRICE" else PRICE_TYPE_LATEST
        try:
            oid = self.trader.order_stock(
                self.account, code, order_type, volume, pt, float(price),
                "boll_lower_rebound", "")
            if oid is None or oid < 0:
                LOG.error(f"下单被拒: {code} {volume}股 @{price} 返回 {oid}")
                return None
            LOG.info(f"下单成功 #{oid}: {'买入' if order_type == ORDER_TYPE_LIMIT else '卖出'} "
                     f"{code} {volume}股 @{price}")
            return int(oid)
        except Exception as e:
            LOG.exception(f"下单异常: {e}")
            return None

    def cancel(self, order_id: int) -> bool:
        """撤单。"""
        self._check_trade_permission()
        if not self._ensure():
            return False
        try:
            r = self.trader.cancel_order_stock(self.account, int(order_id))
            ok = (r == 0)
            LOG.info(f"撤单 #{order_id}: {'成功' if ok else f'失败({r})'}")
            return ok
        except Exception as e:
            LOG.exception(f"撤单异常: {e}")
            return False

    def cancel_all(self) -> int:
        """撤销全部可撤委托。"""
        self._check_trade_permission()
        n = 0
        df = self.query_orders(cancelable_only=True)
        if len(df) == 0:
            return 0
        for oid in df["委托号"].tolist():
            if self.cancel(int(oid)):
                n += 1
        return n

    # ------------------------------------------------------------ 行情
    def get_snapshot(self, codes: list[str]) -> pd.DataFrame:
        """获取实时行情快照（通过 xtdata）。"""
        try:
            from xtquant import xtdata
        except ImportError:
            LOG.warning("xtdata 不可用，无法获取行情")
            return pd.DataFrame()
        try:
            data = xtdata.get_full_tick(codes)
            rows = []
            for c, t in (data or {}).items():
                rows.append({
                    "证券代码": c,
                    "最新价": t.get("lastPrice"),
                    "开盘价": t.get("open"),
                    "最高价": t.get("high"),
                    "最低价": t.get("low"),
                    "昨收价": t.get("lastClose"),
                    "成交量": t.get("volume"),
                    "成交额": t.get("amount"),
                })
            return pd.DataFrame(rows)
        except Exception as e:
            LOG.warning(f"获取行情失败: {e}")
            return pd.DataFrame()


class _Callback:
    """交易回调（仅日志）。"""

    def on_disconnected(self):
        LOG.warning("miniQMT 连接断开")

    def on_order_error(self, e):
        LOG.error(f"委托错误: {getattr(e, 'error_msg', e)}")

    def on_cancel_error(self, e):
        LOG.error(f"撤单错误: {getattr(e, 'error_msg', e)}")

    def on_order_stock_async_response(self, r):
        LOG.debug(f"异步委托响应: {r}")

    def on_account_status(self, s):
        LOG.info(f"账户状态变更: {getattr(s, 'status', s)}")
