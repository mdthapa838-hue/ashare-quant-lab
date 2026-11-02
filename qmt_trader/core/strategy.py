# -*- coding: utf-8 -*-
"""
core/strategy.py — 策略引擎（接入 Tushare 数据）

与 research 版策略逻辑完全一致，但数据源改为 Tushare：
  - 指标计算复用经过验证的算法
  - 标的池由 Tushare daily_basic 动态构建
  - 支持实盘信号扫描与持仓管理

复用 strategy/lib/indicators.py 的算法（已验证）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from core.logger import get_logger
from core.tushare_source import TushareSource

LOG = get_logger("strategy")

# 复用已验证的指标库
_RESEARCH_LIB = Path(r"F:\deepseek\股市\股市2\strategy")
if str(_RESEARCH_LIB) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_LIB))

try:
    from lib import indicators as ind  # type: ignore
    HAS_IND = True
except ImportError as e:
    HAS_IND = False
    LOG.warning(f"未能加载指标库（将使用内置实现）: {e}")


# ---------------------------------------------------------------------------
# 内置指标（回退实现，与 research 版一致）
# ---------------------------------------------------------------------------
def _bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    if HAS_IND:
        return ind.bollinger(close, period, num_std)
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower,
                         "bandwidth": (upper - lower) / mid.replace(0, np.nan),
                         "pct_b": (close - lower) / (upper - lower).replace(0, np.nan)})


def _streak(cond: pd.Series) -> pd.Series:
    if HAS_IND:
        return ind.streak(cond)
    c = cond.fillna(False).astype(bool)
    return c.groupby((c != c.shift()).cumsum()).cumsum().astype(int)


def _resample_trend(close: pd.Series, freq: str, ma_window: int, slope_window: int,
                    calendar=None) -> pd.DataFrame:
    """
    周线/月线趋势（**严格无未来函数**）。

    未结束的周期其「周期末收盘」尚不存在，只能用上一个已完成周期。
    周期是否结束由**交易日历**判定，不能用数据末行判定
    （否则数据截断时会把部分月误当完整月）。
    """
    if HAS_IND:
        return ind.resample_ma(close, freq, ma_window, slope_window, calendar=calendar)

    PERIOD = "M" if str(freq).upper().startswith("M") else "W"
    idx = pd.DatetimeIndex(close.index)
    pk = idx.to_period(PERIOD)
    period_close = close.groupby(pk).last()

    if calendar is not None and len(calendar):
        cal = pd.DatetimeIndex(pd.to_datetime(list(calendar))).unique().sort_values()
        cpk = cal.to_period(PERIOD)
        last_day = pd.Series(cal.values, index=cpk).groupby(level=0).max()
    else:
        tmp = pd.DataFrame({"d": idx, "p": pk})
        last_day = tmp.groupby("p")["d"].max()

    pld = pd.Series(last_day.reindex(pk).values, index=idx)
    at_end = pd.Series(pd.to_datetime(idx.values) >= pd.to_datetime(pld.values),
                       index=idx)

    ref = pd.Series(pk, index=idx).where(
        at_end.values, pd.Series((pk - 1).values, index=idx).values)
    pma = period_close.rolling(ma_window, min_periods=ma_window).mean()
    psl = pma / pma.shift(slope_window) - 1.0

    ok = ref.isin(period_close.index)
    ma = pd.Series(np.nan, index=idx)
    sl = pd.Series(np.nan, index=idx)
    pc = pd.Series(np.nan, index=idx)
    ma.loc[ok.values] = pma.reindex(ref[ok.values].values).values
    sl.loc[ok.values] = psl.reindex(ref[ok.values].values).values
    pc.loc[ok.values] = period_close.reindex(ref[ok.values].values).values

    up = ((pc > ma) | (sl > 0)).fillna(False).astype(bool)
    return pd.DataFrame({"ma": ma.values, "slope": sl.values,
                         "trend_up": up.values, "period_close": pc.values},
                        index=close.index)


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------
class BollLowerRebound:
    """
    布林带下轨反弹策略。

    Parameters
    ----------
    cfg : dict
        策略配置（strategy 节）
    source : TushareSource
        数据源
    """

    def __init__(self, cfg: dict, source: TushareSource):
        self.cfg = cfg
        self.src = source
        e = cfg.get("entry", {})
        self.bb_period = e.get("bb_period", 20)
        self.bb_std = e.get("bb_std", 2.0)
        self.pos_max = e.get("pos120_max", 0.30)
        self.streak_min = e.get("streak_min", 2)
        self.ma60_win = e.get("ma60_slope_window", 20)
        self.ma60_max = e.get("ma60_slope_max", -0.01)
        self.use_monthly = e.get("use_monthly_filter", True)
        self.monthly_ma = e.get("monthly_ma", 6)
        self.use_market = e.get("use_market_filter", True)
        self.market_index = e.get("market_index", "000852.SH")
        self.market_win = e.get("market_ret_window", 60)
        self.market_max = e.get("market_ret_max", -20.0)
        self.use_j = e.get("use_j_filter", False)
        self.j_max = e.get("j_max", 13)
        self.use_weekly = e.get("use_weekly_filter", False)

        ex = cfg.get("exit", {})
        self.take_profit = ex.get("take_profit", "upper")
        self.stop_pct = ex.get("stop_loss_pct", -0.12)
        self.max_hold = ex.get("max_hold_days", 60)

        uni = cfg.get("universe", {})
        self.cap_min = uni.get("market_cap_min", 30)
        self.cap_max = uni.get("market_cap_max", 80)
        self.min_bars = uni.get("min_bars", 400)

        self._market_series: pd.Series | None = None
        self._kline_cache: dict[str, pd.DataFrame] = {}
        self._cal: list | None = None

    # ------------------------------------------------------------ 市场择时
    def market_ret(self, as_of: str | None = None) -> float | None:
        """当前市场 N 日收益（%）。"""
        end = as_of or datetime.now().strftime("%Y%m%d")
        start = (pd.Timestamp(end) - pd.Timedelta(days=int(self.market_win * 2.2))).strftime("%Y%m%d")
        s = self.src.index_daily(self.market_index, start, end)
        if s is None or len(s) < self.market_win:
            return None
        self._market_series = s
        return float((s.iloc[-1] / s.iloc[-1 - self.market_win] - 1) * 100)

    def market_ok(self, as_of: str | None = None) -> tuple[bool, float | None]:
        r = self.market_ret(as_of)
        if r is None:
            return (not self.use_market), None
        return (r < self.market_max) if self.use_market else True, r

    # ------------------------------------------------------------ K线
    def get_kline(self, ts_code: str, as_of: str | None = None,
                  days: int = 900) -> pd.DataFrame | None:
        key = f"{ts_code}_{as_of or 'latest'}"
        if key in self._kline_cache:
            return self._kline_cache[key]
        end = as_of or datetime.now().strftime("%Y%m%d")
        start = (pd.Timestamp(end) - pd.Timedelta(days=int(days * 1.5))).strftime("%Y%m%d")
        df = self.src.daily(ts_code, start, end, adj="qfq")
        if df is None or len(df) < 130:
            return None
        self._kline_cache[key] = df
        return df

    # ------------------------------------------------------------ 交易日历
    def _calendar(self) -> list:
        """
        返回交易日历（用于判定周期是否结束）。

        无未来函数的关键：周期是否结束由「该周期在真实交易日历中的
        最后一个交易日」决定，与当前数据是否被截断无关。
        """
        if self._cal is not None:
            return self._cal
        try:
            # 覆盖足够长的历史，确保历史回溯时日历完整
            cal = self.src.trade_calendar("20050101", "20301231")
            self._cal = cal if cal else []
        except Exception as e:
            LOG.warning(f"交易日历获取失败，回退到数据末行判定: {e}")
            self._cal = []
        return self._cal

    # ------------------------------------------------------------ 指标
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算全部指标 + 条件。"""
        out = df.copy()
        close, high, low = out["close"], out["high"], out["low"]

        bb = _bollinger(close, self.bb_period, self.bb_std)
        out["bb_mid"] = bb["mid"]
        out["bb_upper"] = bb["upper"]
        out["bb_lower"] = bb["lower"]

        ma60 = close.rolling(60, min_periods=60).mean()
        out["ma60"] = ma60
        out["ma60_slope"] = ma60 / ma60.shift(self.ma60_win) - 1

        llv = low.rolling(120, min_periods=120).min()
        hhv = high.rolling(120, min_periods=120).max()
        out["pos120"] = (close - llv) / (hhv - llv).replace(0, np.nan)

        at_lower = (close <= out["bb_lower"]).fillna(False)
        out["lower_streak"] = _streak(at_lower)

        # KDJ
        ln = low.rolling(9, min_periods=9).min()
        hn = high.rolling(9, min_periods=9).max()
        rsv = ((close - ln) / (hn - ln).replace(0, np.nan) * 100).fillna(50)
        k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
        d = k.ewm(alpha=1 / 3, adjust=False).mean()
        out["J"] = 3 * k - 2 * d

        out["prev_close"] = close.shift(1)
        out["open_chg_pct"] = (out["open"] / out["prev_close"] - 1) * 100
        out["amount_ma20"] = out["amount"].rolling(20, min_periods=20).mean()

        # 月线趋势（严格无未来函数；用交易日历判定周期是否结束）
        if self.use_monthly:
            tmp = out.set_index("date")["close"]
            if len(tmp) > self.monthly_ma * 21:
                m = _resample_trend(tmp, "ME", self.monthly_ma, 1,
                                    calendar=self._calendar())
                out["monthly_trend_up"] = m["trend_up"].fillna(False).values.astype(bool)
                out["monthly_slope"] = m["slope"].values
            else:
                out["monthly_trend_up"] = False
                out["monthly_slope"] = np.nan
        else:
            out["monthly_trend_up"] = True
            out["monthly_slope"] = np.nan

        # 周线（备用）
        if self.use_weekly:
            tmp = out.set_index("date")["close"]
            w = _resample_trend(tmp, "W", 20, 4, calendar=self._calendar())
            out["weekly_trend_up"] = w["trend_up"].fillna(False).values.astype(bool)
        else:
            out["weekly_trend_up"] = True

        # 条件
        out["cond_position"] = (out["pos120"] < self.pos_max).fillna(False)
        out["cond_streak"] = (out["lower_streak"] >= self.streak_min).fillna(False)
        out["cond_ma60_down"] = (out["ma60_slope"] < self.ma60_max).fillna(False)
        out["cond_monthly_up"] = out["monthly_trend_up"] if self.use_monthly else True
        out["cond_j_low"] = ((out["J"] < self.j_max) if self.use_j else True)
        out["cond_liquidity"] = (out["amount_ma20"] >= 2000e4).fillna(False)

        cond_cols = ["cond_position", "cond_streak", "cond_ma60_down",
                     "cond_monthly_up", "cond_j_low", "cond_liquidity"]
        out["n_ok"] = out[cond_cols].sum(axis=1)
        out["n_tot"] = len(cond_cols)
        out["signal"] = out[cond_cols].all(axis=1)
        return out

    # ------------------------------------------------------------ 扫描
    def scan(self, codes: list[str], as_of: str | None = None,
             market_passed: bool | None = None) -> pd.DataFrame:
        """
        扫描信号。

        Returns DataFrame，列含 code / close / bb_* / pos120 / streak / 条件详情
        """
        if market_passed is None:
            market_passed, _ = self.market_ok(as_of)

        rows = []
        total = len(codes)
        t0 = time.time()
        for i, code in enumerate(codes):
            try:
                df = self.get_kline(code, as_of)
                if df is None or len(df) < 130:
                    continue
                d = self.compute(df)
                if len(d) == 0:
                    continue
                r = d.iloc[-1]
                cond_cols = ["cond_position", "cond_streak", "cond_ma60_down",
                             "cond_monthly_up", "cond_j_low", "cond_liquidity"]
                n_ok = int(r[cond_cols].sum())
                rec = {
                    "code": code,
                    "date": r["date"].strftime("%Y-%m-%d"),
                    "close": round(float(r["close"]), 2),
                    "bb_lower": round(float(r["bb_lower"]), 2) if pd.notna(r["bb_lower"]) else None,
                    "bb_mid": round(float(r["bb_mid"]), 2) if pd.notna(r["bb_mid"]) else None,
                    "bb_upper": round(float(r["bb_upper"]), 2) if pd.notna(r["bb_upper"]) else None,
                    "pos120": round(float(r["pos120"]), 3) if pd.notna(r["pos120"]) else None,
                    "streak": int(r["lower_streak"]),
                    "ma60_slope%": round(float(r["ma60_slope"]) * 100, 2)
                    if pd.notna(r["ma60_slope"]) else None,
                    "monthly_up": bool(r.get("monthly_trend_up", False)),
                    "J": round(float(r["J"]), 1) if pd.notna(r.get("J")) else None,
                    "n_ok": n_ok, "n_tot": len(cond_cols),
                    "conditions": f"{n_ok}/{len(cond_cols)}",
                    "signal": bool(r["signal"]),
                }
                for c in cond_cols:
                    rec[c] = bool(r[c])
                if rec["signal"]:
                    rows.append(rec)
            except Exception as e:
                LOG.debug(f"{code} 扫描失败: {e}")
            if (i + 1) % 100 == 0:
                LOG.info(f"扫描进度 {i+1}/{total}，耗时 {time.time()-t0:.0f}s")

        out = pd.DataFrame(rows)
        if len(out):
            out = out.sort_values("code").reset_index(drop=True)
        LOG.info(f"扫描完成：{len(codes)} 只，命中信号 {len(out)} 个，耗时 {time.time()-t0:.0f}s")
        return out

    # ------------------------------------------------------------ 持仓监控
    def check_exit(self, ts_code: str, entry_price: float, entry_date: str,
                   as_of: str | None = None) -> dict:
        """
        检查单只持仓是否触发离场。

        Returns
        -------
        dict: {'action': 'hold'|'sell', 'reason': str, 'price': float, ...}
        """
        df = self.get_kline(ts_code, as_of)
        if df is None:
            return {"action": "unknown", "reason": "无数据"}
        d = self.compute(df)
        r = d.iloc[-1]
        cur = float(r["close"])
        tgt = float(r["bb_upper"]) if self.take_profit == "upper" else float(r["bb_mid"])
        stop = entry_price * (1 + self.stop_pct)
        ed = pd.Timestamp(entry_date)
        held = int((d["date"] > ed).sum())

        out = {
            "code": ts_code, "现价": round(cur, 2),
            "成本": round(entry_price, 2),
            "盈亏%": round((cur / entry_price - 1) * 100, 2),
            "持有天数": held, "剩余天数": self.max_hold - held,
            "止盈价": round(tgt, 2), "距止盈%": round((tgt / cur - 1) * 100, 2),
            "止损价": round(stop, 2), "距止损%": round((stop / cur - 1) * 100, 2),
            "action": "hold", "reason": "持有中",
        }
        if cur >= tgt:
            out.update({"action": "sell", "reason": "触及止盈"})
        elif cur <= stop:
            out.update({"action": "sell", "reason": "触及止损"})
        elif held >= self.max_hold:
            out.update({"action": "sell", "reason": "达到时限"})
        return out

    # ------------------------------------------------------------ 仓位计算
    def calc_position_size(self, total_asset: float, available_cash: float,
                           max_positions: int, max_pct: float,
                           price: float) -> tuple[int, float]:
        """
        计算下单量。

        Returns (股数, 金额)
        """
        per_slot = total_asset / max(1, max_positions)
        cap = total_asset * max_pct
        amount = min(per_slot, cap, available_cash * 0.98)
        if amount <= 0 or price <= 0:
            return 0, 0.0
        shares = int(amount / price / 100) * 100
        return shares, shares * price
