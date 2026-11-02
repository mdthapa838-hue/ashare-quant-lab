# -*- coding: utf-8 -*-
"""
core/tushare_source.py — Tushare 数据源封装

Token 获取顺序（不落盘）：
  1. 环境变量 TUSHARE_TOKEN
  2. stock_suite/credentials.py（Windows 凭据管理器）
  3. 直接读 Windows 凭据管理器 Codex/Tushare/Default

职责：
  - 提供日线、复权因子、股票基础信息、指数行情
  - 统一字段名（对齐策略模块的 date/open/high/low/close/volume/amount）
  - 限频、重试、本地缓存
"""
from __future__ import annotations

import os
import sys
import time
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.logger import get_logger

LOG = get_logger("tushare_source")

# stock_suite 凭据模块位置（本机已存在）
SUITE_DIR = r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite"


# ---------------------------------------------------------------------------
# Token 获取
# ---------------------------------------------------------------------------
def _token_from_env() -> str | None:
    v = os.environ.get("TUSHARE_TOKEN", "").strip()
    return v or None


def _token_from_suite() -> str | None:
    """通过 stock_suite/credentials.py 读取（Windows 凭据管理器）。"""
    if not os.path.isdir(SUITE_DIR):
        return None
    try:
        if SUITE_DIR not in sys.path:
            sys.path.insert(0, SUITE_DIR)
        import credentials  # type: ignore
        tok = credentials.get_tushare_token()
        return str(tok).strip() if tok else None
    except Exception as e:
        LOG.debug(f"stock_suite 凭据读取失败: {e}")
        return None


def _token_from_wincred(target: str = "Codex/Tushare/Default") -> str | None:
    """直接读 Windows 凭据管理器（ctypes）。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        dll = ctypes.WinDLL("advapi32.dll", use_last_error=True)
        dll.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        dll.CredReadW.restype = wintypes.BOOL
        dll.CredFree.argtypes = [ctypes.c_void_p]

        pcred = ctypes.POINTER(CREDENTIALW)()
        ok = dll.CredReadW(target, 1, 0, ctypes.byref(pcred))
        if not ok:
            return None
        cred = pcred.contents
        size = cred.CredentialBlobSize
        if size <= 0:
            dll.CredFree(pcred)
            return None
        raw = ctypes.string_at(cred.CredentialBlob, size)
        dll.CredFree(pcred)
        for enc in ("utf-8", "utf-16-le", "gbk"):
            try:
                s = raw.decode(enc).strip("\x00").strip()
                if s and len(s) >= 16:
                    return s
            except Exception:
                continue
    except Exception as e:
        LOG.debug(f"credentials manager 读取失败: {e}")
    return None


def resolve_token(explicit: str | None = None, sources: list[str] | None = None) -> str:
    """
    按优先级解析 Tushare token。

    Parameters
    ----------
    explicit : str, optional
        显式传入的 token（最高优先级）。
    sources : list[str], optional
        token 来源顺序，如 ["env:TUSHARE_TOKEN", "stock_suite.credentials"]。

    Raises
    ------
    RuntimeError
        全部来源均失败。
    """
    if explicit and explicit.strip():
        return explicit.strip()

    sources = sources or ["env:TUSHARE_TOKEN", "stock_suite.credentials",
                          "wincred:Codex/Tushare/Default"]
    tried = []
    for src in sources:
        try:
            if src.startswith("env:"):
                t = _token_from_env()
            elif src == "stock_suite.credentials":
                t = _token_from_suite()
            elif src.startswith("wincred:"):
                t = _token_from_wincred(src.split(":", 1)[1])
            else:
                t = None
            tried.append(f"{src}={'OK' if t else 'MISS'}")
            if t:
                LOG.info(f"Tushare token 已获取（来源 {src}）")
                return t
        except Exception as e:
            tried.append(f"{src}=ERR({type(e).__name__})")
    raise RuntimeError("无法获取 Tushare token。已尝试: " + ", ".join(tried))


# ---------------------------------------------------------------------------
# Tushare 客户端
# ---------------------------------------------------------------------------
class TushareSource:
    """Tushare Pro 数据源。"""

    def __init__(self, token: str | None = None, sources: list[str] | None = None,
                 cache_dir: str | None = None, min_interval: float = 0.35,
                 retry: int = 3):
        try:
            import tushare as ts  # type: ignore
        except ImportError as e:
            raise RuntimeError("需要 tushare：pip install tushare") from e

        self._ts = ts
        self.token = resolve_token(token, sources)
        self.pro = ts.pro_api(self.token)
        self.min_interval = min_interval
        self.retry = retry
        self._last_call = 0.0
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / "kline").mkdir(exist_ok=True)
            (self.cache_dir / "index").mkdir(exist_ok=True)
            (self.cache_dir / "basic").mkdir(exist_ok=True)

    # ------------------------------------------------------------ 限频重试
    def _call(self, fn, **kwargs):
        last_err = None
        for i in range(self.retry):
            gap = time.time() - self._last_call
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            try:
                self._last_call = time.time()
                df = fn(**kwargs)
                return df
            except Exception as e:
                last_err = e
                msg = str(e)
                # 限频错误额外等待
                if "每分钟" in msg or "频率" in msg or "limit" in msg.lower():
                    time.sleep(15 * (i + 1))
                else:
                    time.sleep(1.0 * (i + 1))
        LOG.warning(f"接口调用失败（{self.retry}次）: {last_err}")
        return None

    # ------------------------------------------------------------ 交易日历
    def trade_calendar(self, start: str, end: str, exchange: str = "SSE") -> list[str]:
        p = self.cache_dir / "basic" / f"cal_{exchange}_{start}_{end}.csv" if self.cache_dir else None
        if p and p.exists():
            try:
                df = pd.read_csv(p)
                return df["cal_date"].astype(str).tolist()
            except Exception:
                pass
        df = self._call(self.pro.trade_cal, exchange=exchange,
                        start_date=start, end_date=end)
        if df is None or len(df) == 0:
            return []
        df = df[df["is_open"] == 1].sort_values("cal_date")
        if p:
            df.to_csv(p, index=False, encoding="utf-8-sig")
        return df["cal_date"].astype(str).tolist()

    # ------------------------------------------------------------ 股票列表
    def stock_basic(self, list_status: str = "L") -> pd.DataFrame:
        p = self.cache_dir / "basic" / f"stock_basic_{list_status}.csv" if self.cache_dir else None
        if p and p.exists():
            try:
                age = time.time() - p.stat().st_mtime
                if age < 86400 * 7:      # 一周内有效
                    return pd.read_csv(p, dtype={"ts_code": str, "symbol": str})
            except Exception:
                pass
        df = self._call(self.pro.stock_basic, exchange="", list_status=list_status,
                        fields="ts_code,symbol,name,area,industry,market,list_date,delist_date,is_hs")
        if df is None:
            return pd.DataFrame()
        if p:
            df.to_csv(p, index=False, encoding="utf-8-sig")
        return df

    # ------------------------------------------------------------ 日线
    def daily(self, ts_code: str, start: str, end: str,
              adj: str = "qfq", use_cache: bool = True,
              max_rows: int = 6000) -> pd.DataFrame | None:
        """
        获取日线（含复权），自动分页规避单次行数上限。

        Returns DataFrame with columns:
            date, open, high, low, close, volume, amount
        """
        code6 = ts_code.split(".")[0]
        cache_p = None
        if use_cache and self.cache_dir:
            cache_p = self.cache_dir / "kline" / f"{code6}_{adj}.csv"
            if cache_p.exists():
                try:
                    d = pd.read_csv(cache_p)
                    if len(d) > 50:
                        d["date"] = pd.to_datetime(d["date"])
                        last = d["date"].max()
                        if last >= pd.Timestamp(end) - pd.Timedelta(days=7):
                            return d
                except Exception:
                    pass

        # ---- 分页拉取日线
        frames = []
        cur_end = pd.Timestamp(end)
        start_ts = pd.Timestamp(start)
        guard = 0
        while cur_end >= start_ts and guard < 40:
            guard += 1
            seg_start = max(start_ts, cur_end - pd.Timedelta(days=650))
            dt = self._call(self.pro.daily, ts_code=ts_code,
                            start_date=seg_start.strftime("%Y%m%d"),
                            end_date=cur_end.strftime("%Y%m%d"))
            if dt is None or len(dt) == 0:
                break
            frames.append(dt)
            earliest = pd.to_datetime(dt["trade_date"].min(), format="%Y%m%d")
            if earliest <= start_ts:
                break
            cur_end = earliest - pd.Timedelta(days=1)

        if not frames:
            return None
        dt = pd.concat(frames, ignore_index=True)
        dt = dt.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")

        # ---- 复权
        if adj in ("qfq", "hfq"):
            af_frames = []
            cur_end = pd.Timestamp(end)
            guard = 0
            while cur_end >= start_ts and guard < 40:
                guard += 1
                seg_start = max(start_ts, cur_end - pd.Timedelta(days=650))
                af = self._call(self.pro.adj_factor, ts_code=ts_code,
                                start_date=seg_start.strftime("%Y%m%d"),
                                end_date=cur_end.strftime("%Y%m%d"))
                if af is None or len(af) == 0:
                    break
                af_frames.append(af)
                earliest = pd.to_datetime(af["trade_date"].min(), format="%Y%m%d")
                if earliest <= start_ts:
                    break
                cur_end = earliest - pd.Timedelta(days=1)
            if af_frames:
                afdf = pd.concat(af_frames, ignore_index=True)
                afdf = afdf.drop_duplicates(subset=["trade_date"])
                dt = dt.merge(afdf[["trade_date", "adj_factor"]], on="trade_date", how="left")
                dt = dt.sort_values("trade_date")
                latest = dt["adj_factor"].iloc[-1]
                if pd.notna(latest) and latest > 0:
                    if adj == "qfq":
                        dt["adj_factor"] = dt["adj_factor"] / latest
                    for col in ("open", "high", "low", "close", "pre_close"):
                        if col in dt.columns:
                            dt[col] = dt[col] * dt["adj_factor"]
            else:
                LOG.debug(f"{ts_code} 无复权因子，使用不复权数据")

        df = pd.DataFrame({
            "date": pd.to_datetime(dt["trade_date"], format="%Y%m%d"),
            "open": dt["open"].astype(float),
            "high": dt["high"].astype(float),
            "low": dt["low"].astype(float),
            "close": dt["close"].astype(float),
            "volume": dt["vol"].astype(float) * 100.0,      # 手 → 股
            "amount": dt["amount"].astype(float) * 1000.0,  # 千元 → 元
        }).sort_values("date").reset_index(drop=True)

        if cache_p is not None:
            try:
                df.to_csv(cache_p, index=False, encoding="utf-8-sig")
            except Exception:
                pass
        return df

    # ------------------------------------------------------------ 指数日线
    def index_daily(self, ts_code: str, start: str, end: str,
                    max_rows: int = 6000) -> pd.Series | None:
        """
        指数日线（自动分页，规避单次 429 行上限）。
        """
        code6 = ts_code.split(".")[0]
        cache_p = self.cache_dir / "index" / f"{code6}.csv" if self.cache_dir else None
        if cache_p and cache_p.exists():
            try:
                d = pd.read_csv(cache_p)
                d["date"] = pd.to_datetime(d["date"])
                if d["date"].max() >= pd.Timestamp(end) - pd.Timedelta(days=7):
                    s = d.set_index("date")["close"].astype(float)
                    return s
            except Exception:
                pass

        # 分页拉取：每段最多 400 个交易日（约 1.6 年）
        frames = []
        cur_end = pd.Timestamp(end)
        start_ts = pd.Timestamp(start)
        guard = 0
        while cur_end >= start_ts and guard < 30:
            guard += 1
            seg_start = max(start_ts, cur_end - pd.Timedelta(days=650))
            df = self._call(self.pro.index_daily, ts_code=ts_code,
                            start_date=seg_start.strftime("%Y%m%d"),
                            end_date=cur_end.strftime("%Y%m%d"))
            if df is not None and len(df):
                frames.append(df)
                earliest = pd.to_datetime(df["trade_date"].min(), format="%Y%m%d")
                if earliest <= start_ts:
                    break
                cur_end = earliest - pd.Timedelta(days=1)
            else:
                break

        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
        out = pd.DataFrame({
            "date": pd.to_datetime(df["trade_date"], format="%Y%m%d"),
            "close": df["close"].astype(float),
        }).sort_values("date").reset_index(drop=True)

        if cache_p:
            try:
                out.to_csv(cache_p, index=False, encoding="utf-8-sig")
            except Exception:
                pass
        return out.set_index("date")["close"]

    # ------------------------------------------------------------ 每日指标
    def daily_basic(self, trade_date: str, fields: str = None) -> pd.DataFrame | None:
        """全市场每日指标（总市值/流通市值/换手率/PE/PB）。"""
        f = fields or ("ts_code,trade_date,close,turnover_rate,turnover_rate_f,"
                       "volume_ratio,pe,pe_ttm,pb,total_mv,circ_mv,free_share,total_share")
        df = self._call(self.pro.daily_basic, trade_date=trade_date, fields=f)
        if df is None or len(df) == 0:
            return None
        return df

    # ------------------------------------------------------------ 市值筛选池
    def build_universe(self, trade_date: str, cap_min: float = 30, cap_max: float = 80,
                       exclude_st: bool = True, exclude_bj: bool = True,
                       exclude_kcb: bool = False) -> list[str]:
        """
        按市值区间构建标的池。

        Returns list of ts_code (e.g. ['600630.SH', ...])
        """
        basic = self.stock_basic()
        if len(basic) == 0:
            return []
        db = self.daily_basic(trade_date)
        if db is None:
            LOG.warning(f"{trade_date} 无 daily_basic 数据，请确认是否为交易日")
            return []

        b = basic[["ts_code", "symbol", "name", "market"]].copy()
        m = db[["ts_code", "total_mv", "circ_mv", "close", "pe_ttm", "pb"]].copy()
        m["cap_yi"] = pd.to_numeric(m["total_mv"], errors="coerce") / 1e4
        m["circ_yi"] = pd.to_numeric(m["circ_mv"], errors="coerce") / 1e4
        df = b.merge(m, on="ts_code", how="inner")

        sel = df["cap_yi"].between(cap_min, cap_max, inclusive="left")
        if exclude_st:
            sel &= ~df["name"].astype(str).str.contains("ST|退", na=False)
        if exclude_bj:
            sel &= ~df["ts_code"].str.endswith(".BJ")
        if exclude_kcb:
            sel &= ~df["ts_code"].str.startswith("688")

        codes = df.loc[sel, "ts_code"].tolist()
        LOG.info(f"{trade_date} 市值 {cap_min}-{cap_max}亿 筛出 {len(codes)} 只")
        return codes
