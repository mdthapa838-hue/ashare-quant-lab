# -*- coding: utf-8 -*-
"""
data.py — 数据加载、缓存、指标计算

职责：
  1. 从本地缓存或 akshare 获取个股日线
  2. 计算全部技术指标
  3. 获取市场指数（用于择时）
  4. 提供统一的 DataBundle 结构

缓存策略：
  - 原始日线缓存到 {cache_dir}/kline/{code}.csv
  - 指标不缓存（计算快，避免缓存脏数据）
"""
from __future__ import annotations

import os
import time
import glob
import numpy as np
import pandas as pd

try:
    import akshare as ak
    HAS_AK = True
except ImportError:
    HAS_AK = False

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib import indicators as ind


# ---------------------------------------------------------------------------
# 代码工具
# ---------------------------------------------------------------------------
def normalize_code(code) -> str:
    """统一为 6 位数字字符串。"""
    return str(code).strip().zfill(6)


def to_sina_symbol(code: str) -> str:
    """转换为新浪行情接口的 symbol 格式。"""
    c = normalize_code(code)
    if c.startswith(("60", "68")):
        return "sh" + c
    if c.startswith(("00", "30", "20")):
        return "sz" + c
    if c.startswith(("8", "4")):
        return "bj" + c
    return "sz" + c


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def load_kline(code: str, cache_dir: str, allow_fetch: bool = True,
               adjust: str = "qfq") -> pd.DataFrame | None:
    """
    加载单只股票的日线数据。

    Returns
    -------
    DataFrame with columns: date, open, high, low, close, volume, amount
    索引为 RangeIndex（已排序），date 为 datetime64。
    失败返回 None。
    """
    code = normalize_code(code)

    # 兼容多种缓存命名：kline_cache/{code}.csv、kline/{code}.csv、kline/{code}_qfq.csv
    cache_path = None
    for sub in ("kline_cache", "kline"):
        for suf in ("", "_qfq", "_hfq"):
            p = os.path.join(cache_dir, sub, f"{code}{suf}.csv")
            if os.path.exists(p) and os.path.getsize(p) > 500:
                cache_path = p
                break
        if cache_path:
            break
    if cache_path is None:
        target_dir = os.path.join(cache_dir, "kline_cache")
        os.makedirs(target_dir, exist_ok=True)
        cache_path = os.path.join(target_dir, f"{code}.csv")

    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 500:
        try:
            df = pd.read_csv(cache_path)
            df = _clean_kline(df)
            if df is not None and len(df) >= 60:
                return df
        except Exception:
            pass

    if not allow_fetch or not HAS_AK:
        return None

    for attempt in range(3):
        try:
            df = ak.stock_zh_a_daily(symbol=to_sina_symbol(code), adjust=adjust)
            if df is None or len(df) < 60:
                return None
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            df.to_csv(cache_path, index=False, encoding="utf-8-sig")
            return _clean_kline(df)
        except Exception:
            time.sleep(0.8 + attempt * 1.2)
    return None


def _clean_kline(df: pd.DataFrame) -> pd.DataFrame | None:
    """标准化 K 线 DataFrame。"""
    if df is None or len(df) == 0:
        return None
    if "date" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            return None
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "amount" not in df.columns:
        df["amount"] = df["close"] * df["volume"]
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    if df["close"].isna().sum() > len(df) * 0.05:
        return None
    return df


def load_index(index_code: str, cache_dir: str, allow_fetch: bool = True) -> pd.Series | None:
    """
    加载指数日线收盘价。

    Returns Series with DatetimeIndex, name='close'.
    """
    cache_path = os.path.join(cache_dir, "index", f"{index_code}.csv")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 200:
        try:
            df = pd.read_csv(cache_path)
            col = "日期" if "日期" in df.columns else "date"
            price_col = "收盘" if "收盘" in df.columns else ("收盘价" if "收盘价" in df.columns else "close")
            df[col] = pd.to_datetime(df[col], errors="coerce")
            df = df.dropna(subset=[col]).sort_values(col)
            return pd.Series(pd.to_numeric(df[price_col], errors="coerce").values,
                             index=df[col].values, name="close")
        except Exception:
            pass

    if not allow_fetch or not HAS_AK:
        return None

    try:
        # 中证2000 用中证指数公司接口
        if index_code == "932000":
            df = ak.stock_zh_index_hist_csindex(symbol="932000",
                                                start_date="20130101",
                                                end_date=time.strftime("%Y%m%d"))
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            df.to_csv(cache_path, index=False, encoding="utf-8-sig")
            df["日期"] = pd.to_datetime(df["日期"])
            df = df.sort_values("日期")
            return pd.Series(pd.to_numeric(df["收盘"], errors="coerce").values,
                             index=df["日期"].values, name="close")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame, cfg: dict, market_ret: pd.Series | None = None,
                       calendar: list | None = None) -> pd.DataFrame:
    """
    为单只股票计算全部技术指标。

    Parameters
    ----------
    df : DataFrame
        原始 K 线（load_kline 输出）。
    cfg : dict
        策略配置（entry / universe 等节）。
    market_ret : Series, optional
        市场收益（按日期索引），用于择时过滤。
    calendar : list, optional
        交易日历。用于月线/周线严格判定「周期是否结束」，
        避免数据截断时把部分周期误当完整周期（未来函数）。

    Returns
    -------
    DataFrame（与输入等长，附加指标列）
    """
    entry = cfg.get("entry", {})
    uni = cfg.get("universe", {})
    out = df.copy()
    _calendar = calendar

    close, high, low, open_ = out["close"], out["high"], out["low"], out["open"]

    # 布林带
    bb = ind.bollinger(close, period=entry.get("bb_period", 20),
                       num_std=entry.get("bb_std", 2.0))
    out["bb_mid"] = bb["mid"]
    out["bb_upper"] = bb["upper"]
    out["bb_lower"] = bb["lower"]
    out["bb_bandwidth"] = bb["bandwidth"]
    out["bb_pct_b"] = bb["pct_b"]

    # 均线
    ma60 = ind.moving_average(close, 60)
    out["ma60"] = ma60
    out["ma60_slope"] = ind.ma_slope(ma60, entry.get("ma60_slope_window", 20))

    # 相对位置
    out["pos120"] = ind.position(close, high, low, 120)

    # 连续触轨
    at_lower = (close <= out["bb_lower"]).fillna(False)
    out["at_lower"] = at_lower.astype(int)
    out["lower_streak"] = ind.streak(at_lower)

    # KDJ（备用，默认不启用）
    kk = ind.kdj(high, low, close)
    out["K"] = kk["K"]
    out["D"] = kk["D"]
    out["J"] = kk["J"]

    # ATR
    out["atr14"] = ind.atr(high, low, close, 14)

    # 开盘涨跌幅（用于涨跌停判断）
    out["prev_close"] = close.shift(1)
    out["open_chg_pct"] = (open_ / out["prev_close"] - 1.0) * 100

    # 月线趋势（严格无未来函数：用交易日历判定周期是否结束）
    if entry.get("use_monthly_filter", True):
        need = entry.get("monthly_ma", 6)
        if len(out) > need * 21:
            tmp = out.set_index("date")["close"]
            m = ind.resample_ma(tmp, "ME", need, entry.get("monthly_slope_window", 1),
                                calendar=_calendar)
            out["monthly_ma"] = m["ma"].values
            out["monthly_slope"] = m["slope"].values
            out["monthly_trend_up"] = m["trend_up"].fillna(False).values.astype(bool)
        else:
            out["monthly_ma"] = np.nan
            out["monthly_slope"] = np.nan
            out["monthly_trend_up"] = False
    else:
        out["monthly_trend_up"] = True

    # 周线趋势（备用）
    if entry.get("use_weekly_filter", False):
        tmp = out.set_index("date")["close"]
        w = ind.resample_ma(tmp, "W", 20, 4, calendar=_calendar)
        out["weekly_trend_up"] = w["trend_up"].fillna(False).values.astype(bool)
    else:
        out["weekly_trend_up"] = True

    # 市场择时
    if entry.get("use_market_filter", True) and market_ret is not None:
        mr = market_ret.reindex(out["date"].values, method="ffill")
        out["market_ret"] = mr.values
    else:
        out["market_ret"] = -999.0   # 不启用时置为满足条件

    # 流动性
    out["amount_ma20"] = out["amount"].rolling(20, min_periods=20).mean()

    return out


# ---------------------------------------------------------------------------
# 数据包
# ---------------------------------------------------------------------------
class DataBundle:
    """
    加载并缓存全部标的与指数的指标数据。

    Usage
    -----
    bundle = DataBundle(cfg, cache_dir)
    bundle.load_all(codes)         # 或 bundle.discover_universe()
    df = bundle.get("600630")
    market = bundle.market_ret
    """

    def __init__(self, cfg: dict, cache_dir: str, allow_fetch: bool = True):
        self.cfg = cfg
        self.cache_dir = cache_dir
        self.allow_fetch = allow_fetch
        self.data: dict[str, pd.DataFrame] = {}
        self.market_ret: pd.Series | None = None
        self.index_close: pd.Series | None = None
        self.calendar: list = []
        self._load_market()
        self._load_calendar()

    def _load_calendar(self):
        """
        构建交易日历。

        用于月线/周线严格判定周期是否结束。合并指数日历与全部标的的
        日期并集，确保任何被截断的数据都不会把「部分周期」误判为「完整周期」。
        """
        dates = set()
        if self.index_close is not None:
            dates.update(pd.DatetimeIndex(self.index_close.index).values)
        # 指数未加载时，先收集标的日期（延迟到 load_all 后更新）
        self.calendar = sorted(dates)

    def extend_calendar(self, dates) -> None:
        """把新的日期并入日历（load_all 时调用）。"""
        cur = set(pd.to_datetime(self.calendar)) if self.calendar else set()
        cur.update(pd.to_datetime(list(dates)))
        self.calendar = sorted(cur)

    def _load_market(self):
        entry = self.cfg.get("entry", {})
        if not entry.get("use_market_filter", True):
            return
        code = entry.get("market_index", "932000")
        s = load_index(code, self.cache_dir, self.allow_fetch)
        if s is None:
            print(f"[warn] 市场指数 {code} 加载失败，择时过滤将跳过")
            return
        self.index_close = s
        win = entry.get("market_ret_window", 60)
        self.market_ret = (s / s.shift(win) - 1.0) * 100

    def load_one(self, code: str) -> bool:
        code = normalize_code(code)
        if code in self.data:
            return True
        raw = load_kline(code, self.cache_dir, self.allow_fetch)
        if raw is None:
            return False
        self.data[code] = compute_indicators(raw, self.cfg, self.market_ret,
                                            calendar=self.calendar or None)
        return True

    def load_all(self, codes, verbose: bool = True) -> int:
        # 先建立完整交易日历（含全部标的日期），保证月线判定不受单只数据截断影响
        all_dates = set()
        for c in codes:
            raw = load_kline(normalize_code(c), self.cache_dir, self.allow_fetch)
            if raw is not None:
                all_dates.update(pd.DatetimeIndex(raw["date"]).values)
        if all_dates:
            self.extend_calendar(all_dates)

        ok = 0
        for i, c in enumerate(codes):
            if self.load_one(c):
                ok += 1
            if verbose and (i + 1) % 100 == 0:
                print(f"  加载 {i+1}/{len(codes)}  成功 {ok}", flush=True)
        return ok

    def discover_universe(self, spot_df: pd.DataFrame) -> list[str]:
        """
        从全市场快照中筛选标的池。

        Parameters
        ----------
        spot_df : DataFrame
            需含列：code, name, mktcap(万元), amount(元)
        """
        uni = self.cfg.get("universe", {})
        df = spot_df.copy()
        df["code"] = df["code"].map(normalize_code)
        df["cap_yi"] = pd.to_numeric(df["mktcap"], errors="coerce") / 1e4
        df["amt_wan"] = pd.to_numeric(df["amount"], errors="coerce") / 1e4
        df["name"] = df["name"].astype(str)

        m = pd.Series(True, index=df.index)
        m &= df["cap_yi"].between(uni.get("market_cap_min", 30),
                                  uni.get("market_cap_max", 80), inclusive="left")
        if uni.get("exclude_st", True):
            m &= ~df["name"].str.contains("ST|退", na=False)
        if uni.get("exclude_bj", True):
            m &= ~df["code"].str.startswith(("8", "4", "9"))
        m &= df["amt_wan"] >= uni.get("min_avg_amount", 2000)
        return df.loc[m, "code"].tolist()

    def get(self, code: str) -> pd.DataFrame | None:
        return self.data.get(normalize_code(code))

    def codes(self) -> list[str]:
        return list(self.data.keys())

    def __len__(self):
        return len(self.data)
