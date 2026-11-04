# -*- coding: utf-8 -*-
"""
tushare_trader/data.py — Tushare 数据源

职责
----
  * 统一封装 Tushare Pro 接口（日线/复权/指数/每日指标/股票列表/交易日历）
  * 限频、重试、分页（规避单次返回行数上限）
  * 本地 CSV 缓存（按交易日增量判断新鲜度）

字段统一为策略层使用的英文列名：
    date, open, high, low, close, volume(股), amount(元)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd

from .credentials import resolve_token

LOG = logging.getLogger("tushare_trader.data")

# 单次接口建议的最大行数（Tushare 单次上限 429 行附近，留安全余量）
PAGE_DAYS = 600


class TushareData:
    """Tushare Pro 数据客户端（带缓存与限频）。"""

    def __init__(self, token: str | None = None, cache_dir: str | Path | None = None,
                 min_interval: float = 0.35, retry: int = 3, pro=None):
        self._pro = pro           # 允许注入（测试用），None 时按 token 创建
        self._token = token
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.min_interval = float(min_interval)
        self.retry = int(retry)
        self._last_call = 0.0
        if self.cache_dir:
            for sub in ("kline", "index", "basic"):
                (self.cache_dir / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 客户端
    @property
    def pro(self):
        """惰性创建 pro_api：未使用的命令不需要安装 tushare 也不会报错。"""
        if self._pro is None:
            try:
                import tushare as ts
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("需要 tushare：pip install tushare") from e
            tok = resolve_token(self._token)
            ts.set_token(tok)
            self._pro = ts.pro_api(tok)
        return self._pro

    @property
    def token(self) -> str | None:
        return self._token

    # ------------------------------------------------------------ 限频重试
    def _call(self, api_name: str, **kwargs):
        """调用 pro 接口，带限频与重试。失败返回 None（由调用方决定语义）。"""
        last_err: Exception | None = None
        for attempt in range(self.retry):
            gap = time.time() - self._last_call
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            try:
                self._last_call = time.time()
                return getattr(self.pro, api_name)(**kwargs)
            except Exception as e:  # Tushare 用异常传递限频/权限错误
                last_err = e
                msg = str(e)
                if "每分钟" in msg or "频率" in msg or "最多访问" in msg \
                        or "limit" in msg.lower():
                    time.sleep(min(15 * (attempt + 1), 60))
                else:
                    time.sleep(1.0 * (attempt + 1))
        self._last_error = last_err
        return None

    @property
    def last_error(self) -> Exception | None:
        return getattr(self, "_last_error", None)

    # ------------------------------------------------------------ 交易日历
    def trade_calendar(self, start: str, end: str, exchange: str = "SSE") -> list[str]:
        """返回区间内交易日列表（升序，YYYYMMDD）。"""
        cache_p = None
        if self.cache_dir:
            cache_p = self.cache_dir / "basic" / f"cal_{exchange}_{start}_{end}.csv"
            if cache_p.exists():
                try:
                    df = pd.read_csv(cache_p, dtype={"cal_date": str})
                    if len(df):
                        return sorted(df["cal_date"].tolist())
                except Exception:
                    pass

        df = self._call("trade_cal", exchange=exchange,
                        start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return []
        df = df[df["is_open"] == 1].copy()
        df["cal_date"] = df["cal_date"].astype(str)
        df = df.sort_values("cal_date")
        if cache_p is not None:
            try:
                df[["cal_date"]].to_csv(cache_p, index=False)
            except OSError:
                pass
        return df["cal_date"].tolist()

    def latest_trade_date(self, today: str | None = None) -> str | None:
        """最近一个交易日（含当日，若当日休市则回退）。"""
        today = today or pd.Timestamp.now().strftime("%Y%m%d")
        start = (pd.Timestamp(today) - pd.Timedelta(days=30)).strftime("%Y%m%d")
        cal = self.trade_calendar(start, today)
        return cal[-1] if cal else None

    # ------------------------------------------------------------ 股票列表
    def stock_basic(self, list_status: str = "L", max_age_days: int = 7) -> pd.DataFrame:
        """股票基础信息（ts_code/symbol/name/industry/market/list_date...）。"""
        cache_p = self.cache_dir / "basic" / f"stock_basic_{list_status}.csv" \
            if self.cache_dir else None
        if cache_p and cache_p.exists():
            try:
                age = (time.time() - cache_p.stat().st_mtime) / 86400
                if age < max_age_days:
                    return pd.read_csv(cache_p, dtype={"ts_code": str, "symbol": str})
            except Exception:
                pass

        df = self._call("stock_basic", exchange="", list_status=list_status,
                        fields="ts_code,symbol,name,area,industry,market,list_date,delist_date")
        if df is None or len(df) == 0:
            return pd.DataFrame()
        if cache_p is not None:
            try:
                df.to_csv(cache_p, index=False)
            except OSError:
                pass
        return df

    # ------------------------------------------------------------ 个股日线
    def _kline_cache_path(self, code6: str, adj: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / "kline" / f"{code6}_{adj or 'bfq'}.csv"

    def _cache_fresh(self, path: Path, start: str, end: str) -> pd.DataFrame | None:
        """
        读取缓存并**裁剪到请求区间**。

        缓存命中需同时满足：
          1) 末行日期 ≥ 请求终点 - 7 天（数据够新）
          2) 首行日期 ≤ 请求起点 + 45 天（覆盖了请求起点）

        关键：缓存可能包含请求终点之后的数据（例如用近期缓存回放历史某日），
        必须按 end 截断，否则会引入未来数据。
        """
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path)
            if len(df) < 2 or "date" not in df.columns:
                return None
            df["date"] = pd.to_datetime(df["date"])
            if df["date"].max() < pd.Timestamp(end) - pd.Timedelta(days=7):
                return None            # 太旧
            if df["date"].min() > pd.Timestamp(start) + pd.Timedelta(days=45):
                return None            # 不含请求起点，需重新拉取
            # 裁剪到请求区间——防止缓存中的未来数据泄漏进回测
            df = df[(df["date"] >= pd.Timestamp(start)) &
                    (df["date"] <= pd.Timestamp(end))]
            if len(df) < 2:
                return None
            return df.sort_values("date").reset_index(drop=True)
        except Exception:
            return None

    def daily(self, ts_code: str, start: str, end: str, adj: str = "qfq",
              use_cache: bool = True) -> pd.DataFrame | None:
        """
        个股日线（含复权）。

        Returns
        -------
        DataFrame(date, open, high, low, close, volume, amount) 或 None
        """
        code6 = str(ts_code).split(".")[0]
        cache_p = self._kline_cache_path(code6, adj)
        if use_cache and cache_p is not None:
            cached = self._cache_fresh(cache_p, start, end)
            if cached is not None:
                return cached

        raw = self._fetch_paged("daily", ts_code, start, end, ts_code=ts_code)
        if raw is None or len(raw) == 0:
            return None

        if adj in ("qfq", "hfq"):
            af = self._fetch_paged("adj_factor", ts_code, start, end, ts_code=ts_code)
            if af is not None and len(af) and "adj_factor" in af.columns:
                raw = raw.merge(af[["trade_date", "adj_factor"]],
                                on="trade_date", how="left")
                raw = raw.sort_values("trade_date")
                # 填充停牌日的因子缺口，避免复权价出现 NaN
                raw["adj_factor"] = raw["adj_factor"].ffill().bfill()
                latest = raw["adj_factor"].iloc[-1]
                if pd.notna(latest) and latest > 0:
                    if adj == "qfq":
                        raw["adj_factor"] = raw["adj_factor"] / latest
                    for col in ("open", "high", "low", "close", "pre_close"):
                        if col in raw.columns:
                            raw[col] = raw[col] * raw["adj_factor"]

        out = pd.DataFrame({
            "date": pd.to_datetime(raw["trade_date"].astype(str), format="%Y%m%d"),
            "open": raw["open"].astype(float),
            "high": raw["high"].astype(float),
            "low": raw["low"].astype(float),
            "close": raw["close"].astype(float),
            "volume": raw["vol"].astype(float) * 100.0,     # 手 -> 股
            "amount": raw["amount"].astype(float) * 1000.0,  # 千元 -> 元
        }).sort_values("date").reset_index(drop=True)

        if cache_p is not None:
            try:
                out.to_csv(cache_p, index=False)
            except OSError:
                pass
        return out

    # ------------------------------------------------------------ 指数日线
    def index_daily(self, ts_code: str, start: str, end: str,
                    use_cache: bool = True) -> pd.Series | None:
        """
        指数收盘价序列（DatetimeIndex）。

        Returns
        -------
        pd.Series(name="close") 或 None
        """
        code6 = str(ts_code).split(".")[0]
        cache_p = self.cache_dir / "index" / f"{code6}.csv" if self.cache_dir else None
        if use_cache and cache_p is not None:
            cached = self._cache_fresh(cache_p, start, end)
            # 只要缓存覆盖了请求区间就用它（不要求行数，否则短区间会漏到网络路径）
            if cached is not None:
                s = cached.set_index("date")["close"].astype(float).sort_index()
                return s

        raw = self._fetch_paged("index_daily", ts_code, start, end, ts_code=ts_code)
        if raw is None or len(raw) == 0:
            return None
        out = pd.DataFrame({
            "date": pd.to_datetime(raw["trade_date"].astype(str), format="%Y%m%d"),
            "close": raw["close"].astype(float),
        }).sort_values("date").reset_index(drop=True)
        if cache_p is not None:
            try:
                out.to_csv(cache_p, index=False)
            except OSError:
                pass
        return out.set_index("date")["close"]

    # ------------------------------------------------------------ 分页拉取
    def _fetch_paged(self, api_name: str, code_fallback: str,
                     start: str, end: str, ts_code: str,
                     max_pages: int = 40) -> pd.DataFrame | None:
        """按时间窗口分页，规避单次返回行数上限。"""
        frames = []
        cur_end = pd.Timestamp(end)
        start_ts = pd.Timestamp(start)
        for _ in range(max_pages):
            if cur_end < start_ts:
                break
            seg_start = max(start_ts, cur_end - pd.Timedelta(days=PAGE_DAYS))
            df = self._call(api_name, ts_code=ts_code,
                            start_date=seg_start.strftime("%Y%m%d"),
                            end_date=cur_end.strftime("%Y%m%d"))
            if df is None or len(df) == 0:
                break
            frames.append(df)
            earliest = pd.to_datetime(df["trade_date"].astype(str).min(), format="%Y%m%d")
            if earliest <= start_ts:
                break
            next_end = earliest - pd.Timedelta(days=1)
            if next_end >= cur_end:      # 无进展（接口未按区间返回），避免死循环
                break
            cur_end = next_end
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        out["trade_date"] = out["trade_date"].astype(str)
        return out.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")

    # ------------------------------------------------------------ 每日指标
    def daily_basic(self, trade_date: str) -> pd.DataFrame | None:
        """全市场每日指标（市值/换手/PE/PB）。"""
        df = self._call("daily_basic", trade_date=trade_date,
                        fields=("ts_code,trade_date,close,turnover_rate,pe_ttm,pb,"
                                "total_mv,circ_mv,total_share,float_share"))
        if df is None or len(df) == 0:
            return None
        return df

    def daily_basic_with_fallback(self, trade_date: str,
                                  lookback_days: int = 10) -> tuple[pd.DataFrame | None, str | None]:
        """
        获取每日指标；当日数据尚未发布（盘中/非交易日）时回退到最近可用日期。

        Tushare 的 daily_basic 在收盘后才发布，因此盘中调用当日返回 0 行。

        Returns
        -------
        (DataFrame | None, 实际使用的日期)
        """
        candidates = [trade_date]
        cal = self.trade_calendar(
            (pd.Timestamp(trade_date) - pd.Timedelta(days=lookback_days * 3)).strftime("%Y%m%d"),
            trade_date)
        candidates += [d for d in reversed(cal) if d != trade_date]
        for d in candidates[: max(1, lookback_days)]:
            df = self.daily_basic(d)
            if df is not None and len(df):
                return df, d
        return None, None

    # ------------------------------------------------------------ 标的池
    def build_universe(self, trade_date: str, cap_min: float = 30,
                       cap_max: float = 80, exclude_st: bool = True,
                       exclude_bj: bool = True, exclude_kcb: bool = False,
                       min_avg_amount: float | None = None) -> list[str]:
        """
        按总市值区间构建标的池。

        Returns
        -------
        list[str]  ts_code 列表，如 ['600630.SH', ...]
        """
        basic = self.stock_basic()
        if basic is None or len(basic) == 0:
            return []
        db, used_date = self.daily_basic_with_fallback(trade_date)
        if db is None:
            return []
        if used_date and used_date != trade_date:
            LOG.info(f"每日指标当日未发布，使用 {used_date} 的市值数据")

        cols = ["ts_code", "symbol", "name", "market"]
        if "list_date" in basic.columns:
            cols.append("list_date")
        b = basic[cols].copy()
        m = db[["ts_code", "total_mv", "circ_mv", "close"]].copy()
        m["cap_yi"] = pd.to_numeric(m["total_mv"], errors="coerce") / 1e4
        df = b.merge(m, on="ts_code", how="inner")

        sel = df["cap_yi"].between(float(cap_min), float(cap_max), inclusive="left")
        if exclude_st:
            sel &= ~df["name"].astype(str).str.contains("ST|退", na=False)
        if exclude_bj:
            sel &= ~df["ts_code"].str.endswith(".BJ")
        if exclude_kcb:
            sel &= ~df["ts_code"].str.startswith("688")
        # 排除上市不足 1 年的次新股（K 线不足，指标不可算）
        if "list_date" in df.columns:
            ld = pd.to_datetime(df["list_date"].astype(str), format="%Y%m%d", errors="coerce")
            cutoff = pd.Timestamp(trade_date) - pd.Timedelta(days=365)
            sel &= ld.lt(cutoff).fillna(False).values

        return sorted(df.loc[sel, "ts_code"].tolist())
