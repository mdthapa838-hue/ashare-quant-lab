# -*- coding: utf-8 -*-
"""em_forecast.py — 东方财富业绩预告数据源（为 earnings_watch.py 补充覆盖）

背景：
    earnings_watch.py 原本只用 Tushare forecast_vip / express_vip。
    实测 2026 三季报口径下，Tushare 只覆盖 36 只，东财覆盖 192 只
    （严格预增 100 / 含略增 131 / 含扭亏 145），漏报 116 只。

本模块只做一件事：从东财数据中心拉全量业绩预告，输出与 Tushare 同构的 DataFrame，
由 earnings_watch.py 取并集。不改动原有逻辑，可单独回滚。

数据源：
    https://datacenter-web.eastmoney.com/api/data/v1/get
    reportName=RPT_PUBLIC_OP_NEWPREDICT

注意：
    ① 接口不支持 NOTICE_DATE 的区间过滤（会 400），只能按 REPORT_DATE 拉全量，
       再在本地按公告日过滤。
    ② 同一股票会有多条（归母净利润 / 扣非净利润），必须按股票去重取归母。
    ③ 该接口为公开数据中心接口，无鉴权，但需带 Referer。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime

import pandas as pd

BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://data.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}
COLUMNS = (
    "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NOTICE_DATE,REPORT_DATE,"
    "PREDICT_TYPE,ADD_AMP_LOWER,ADD_AMP_UPPER,PREDICT_AMT_LOWER,"
    "PREDICT_AMT_UPPER,PREYEAR_SAME_PERIOD,PREDICT_FINANCE,"
    "PREDICT_CONTENT,CHANGE_REASON_EXPLAIN,IS_LATEST"
)

# 东财预告类型 -> 与 Tushare 对齐的归一化描述
TYPE_MAP = {
    "预增": "预增",
    "略增": "略增",
    "扭亏": "扭亏",
    "续盈": "续盈",
    "预减": "预减",
    "略减": "略减",
    "首亏": "首亏",
    "续亏": "续亏",
    "减亏": "减亏",
    "不确定": "不确定",
}


def _fetch_page(report_date: str, page: int = 1, size: int = 500) -> dict:
    """拉单页。失败重试 3 次。"""
    params = {
        "reportName": "RPT_PUBLIC_OP_NEWPREDICT",
        "columns": COLUMNS,
        "pageSize": str(size),
        "pageNumber": str(page),
        "sortColumns": "NOTICE_DATE",
        "sortTypes": "-1",
        "filter": "(REPORT_DATE='%s')" % report_date,
    }
    url = BASE + "?" + urllib.parse.urlencode(params, safe="(),'=<>%")
    req = urllib.request.Request(url, headers=HEADERS)
    last = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.2)
    raise RuntimeError("eastmoney fetch failed: %s" % last)


def _candidate_report_dates(notice_date: str) -> list:
    """根据公告日推算可能的报告期，按可能性从高到低排列。"""
    try:
        dt = datetime.strptime(notice_date, "%Y%m%d")
    except ValueError:
        dt = datetime.now()
    y = dt.year
    m = dt.month
    out = []
    if 9 <= m <= 10:
        out = ["%d-09-30" % y, "%d-06-30" % y, "%d-12-31" % (y - 1)]
    elif 1 <= m <= 2:
        out = ["%d-12-31" % (y - 1), "%d-09-30" % (y - 1), "%d-12-31" % (y - 2)]
    elif 3 <= m <= 4:
        out = ["%d-12-31" % (y - 1), "%d-03-31" % y, "%d-09-30" % (y - 1)]
    elif 5 <= m <= 8:
        out = ["%d-06-30" % y, "%d-09-30" % y, "%d-03-31" % y,
               "%d-12-31" % (y - 1)]
    else:
        out = ["%d-09-30" % y, "%d-12-31" % y, "%d-06-30" % y]
    return out


def _to_ts_code(code: str, secucode: str) -> str:
    """东财 6 位代码 -> Tushare 风格 ts_code。"""
    sfx = ""
    if secucode and "." in secucode:
        sfx = secucode.split(".")[-1].upper()
    if not sfx:
        c = str(code)
        if c.startswith(("60", "68", "90")):
            sfx = "SH"
        elif c.startswith(("4", "8", "92")):
            sfx = "BJ"
        else:
            sfx = "SZ"
    return "%s.%s" % (code, sfx)


def fetch_em_forecast(notice_dates, verbose: bool = True) -> pd.DataFrame:
    """
    拉东方财富业绩预告，返回与 Tushare 同构的 DataFrame。

    参数
    ----
    notice_dates : 可迭代的公告日，格式 "YYYYMMDD"
    verbose      : 是否打印拉取概况

    返回列（与 earnings_watch.py 的 rows 对齐）::

        ts_code, 来源, 类型, 增速, 净利下限万, 去年同期万, 公告日,
        要点, 负面词, 正面词, 负面数, 正面数, 全文
    """
    targets = {str(d) for d in notice_dates}
    if not targets:
        return pd.DataFrame()

    # 用窗口内最早那天推算候选报告期（同窗口通常属同一报告期）
    probe = sorted(targets)[0]
    raw_rows = []
    used_report_dates = []
    for rd in _candidate_report_dates(probe):
        try:
            j = _fetch_page(rd)
        except Exception:
            continue
        data = ((j.get("result") or {}).get("data")) or []
        if not data:
            continue
        used_report_dates.append(rd)
        raw_rows.extend(data)

    if not raw_rows:
        if verbose:
            print("  [东财] 未取到数据")
        return pd.DataFrame()

    df = pd.DataFrame(raw_rows)
    df["notice_ymd"] = df["NOTICE_DATE"].astype(str).str[:10].str.replace("-", "", regex=False)
    df_all_cnt = len(df)
    df = df[df["notice_ymd"].isin(targets)].copy()
    # 多报告期累积后按 (股票, 公告日) 去重
    df["_rep"] = df["REPORT_DATE"].astype(str)
    df = df.sort_values(["SECURITY_CODE", "notice_ymd", "REPORT_DATE"],
                        ascending=[True, True, False])
    df = df.drop_duplicates(subset=["SECURITY_CODE", "notice_ymd"], keep="first")
    if df.empty:
        # 该窗口内东财没有新增公告，属正常情况，不打印噪声
        return pd.DataFrame()

    # 同一股票多条（归母 / 扣非）——只保留归母净利润
    df["_pref"] = (df["PREDICT_FINANCE"] == "归属于上市公司股东的净利润").astype(int)
    df = df.sort_values(["SECURITY_CODE", "_pref"], ascending=[True, False])
    df = df.drop_duplicates(subset=["SECURITY_CODE"], keep="first")

    rows = []
    for _, r in df.iterrows():
        lo = r.get("ADD_AMP_LOWER")
        hi = r.get("ADD_AMP_UPPER")
        try:
            lo = float(lo) if lo is not None and not pd.isna(lo) else None
        except (TypeError, ValueError):
            lo = None
        try:
            hi = float(hi) if hi is not None and not pd.isna(hi) else None
        except (TypeError, ValueError):
            hi = None
        growth = lo if lo is not None else hi

        npl = r.get("PREDICT_AMT_LOWER")
        try:
            npl = float(npl) / 1e4 if npl else None
        except (TypeError, ValueError):
            npl = None
        pre = r.get("PREYEAR_SAME_PERIOD")
        try:
            pre = float(pre) / 1e4 if pre else None
        except (TypeError, ValueError):
            pre = None

        content = str(r.get("PREDICT_CONTENT") or "")
        reason = str(r.get("CHANGE_REASON_EXPLAIN") or "")
        ptype = TYPE_MAP.get(str(r.get("PREDICT_TYPE") or ""), str(r.get("PREDICT_TYPE") or ""))

        rows.append({
            "ts_code": _to_ts_code(r["SECURITY_CODE"], r.get("SECUCODE", "")),
            "来源": "预告(东财)",
            "类型": ptype,
            "增速": growth,
            "净利下限万": npl,
            "去年同期万": pre,
            "公告日": pd.Timestamp(r["NOTICE_DATE"][:10]),
            "要点": " ".join(content.split())[:200],
            "全文": " ".join((content + " " + reason).split())[:600],
        })

    out = pd.DataFrame(rows)
    if verbose:
        print("  [东财] 报告期 %s | 窗口命中 %d 只"
              % ("/".join(used_report_dates), len(out)))
    return out
