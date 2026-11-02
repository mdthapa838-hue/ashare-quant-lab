# -*- coding: utf-8 -*-
"""
realtime_ann.py — 实时公告抓取（绕过 Tushare 延迟）

数据源（按可用性排序）：
  ① 上交所 query.sse.com.cn（含当日，盘前即可见）
  ② 深交所 www.szse.cn/api（需处理反爬）
  ③ 巨潮 cninfo.com.cn（全市场，但有延迟）
  ④ Tushare forecast_vip（最后兜底）

输出：当日公告清单（含代码/名称/标题/类型）
"""
from __future__ import annotations

import json
import re
import time
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 业绩类公告的标题关键词
EARN_KEYWORDS = ["业绩预告", "业绩快报", "业绩预增", "业绩预盈",
                 "业绩预亏", "业绩预减", "盈利预测", "业绩预披露"]


def fetch_sse(date_str: str, page_size: int = 100, max_page: int = 8):
    """
    上交所公告（含当日）。

    date_str: YYYY-MM-DD
    """
    out = []
    hdr = {"User-Agent": UA, "Referer": "http://www.sse.com.cn/"}
    for page in range(1, max_page + 1):
        u = "http://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do"
        p = {
            "jsonCallBack": "cb", "isPagination": "true",
            "pageHelp.pageSize": str(page_size),
            "pageHelp.pageNo": str(page), "pageHelp.beginPage": str(page),
            "pageHelp.cacheSize": "1", "pageHelp.endPage": str(page),
            "START_DATE": date_str, "END_DATE": date_str,
            "SECURITY_CODE": "", "BULLETIN_TYPE": "", "TITLE": "",
        }
        try:
            r = requests.get(u, params=p, headers=hdr, timeout=20)
            i = r.text.find("(")
            j = r.text.rfind(")")
            if i < 0 or j < 0:
                break
            obj = json.loads(r.text[i + 1:j])
            data = obj.get("pageHelp", {}).get("data", [])
            if not data or not len(data[0]):
                break
            out.extend(data[0])
            if len(data[0]) < page_size:
                break
        except Exception:
            break
        time.sleep(0.2)
    return out


def fetch_szse(date_str: str, page_size: int = 100, max_page: int = 8):
    """深交所公告（含当日）。"""
    out = []
    hdr = {
        "User-Agent": UA,
        "Referer": "http://www.szse.cn/disclosure/listed/notice/index.html",
        "X-Request-Type": "ajax",
        "Content-Type": "application/json",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    for page in range(1, max_page + 1):
        u = "http://www.szse.cn/api/disc/announcement/annList"
        body = {
            "seDate": [date_str, date_str],
            "channelCode": ["listedNotice_disc"],
            "pageSize": page_size,
            "pageNum": page,
            "random": f"{time.time():.6f}",
        }
        try:
            r = requests.post(u, json=body, headers=hdr, timeout=20)
            if r.status_code != 200:
                break
            obj = r.json()
            data = obj.get("data") or []
            if not data:
                break
            for x in data:
                out.append({
                    "SECURITY_CODE": (x.get("secCode") or [""])[0]
                    if isinstance(x.get("secCode"), list) else x.get("secCode"),
                    "SECURITY_NAME": (x.get("secName") or [""])[0]
                    if isinstance(x.get("secName"), list) else x.get("secName"),
                    "TITLE": x.get("title", ""),
                    "URL": x.get("attachPath", ""),
                    "BULLETIN_TYPE_DESC": x.get("category", ""),
                })
            if len(data) < page_size:
                break
        except Exception:
            break
        time.sleep(0.3)
    return out


def fetch_cninfo(date_str: str, page_size: int = 100, max_page: int = 10):
    """巨潮资讯（全市场）。"""
    out = []
    hdr = {
        "User-Agent": UA,
        "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
        "X-Requested-With": "XMLHttpRequest",
    }
    d = date_str.replace("-", "")
    for page in range(1, max_page + 1):
        u = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        body = {
            "pageNum": str(page), "pageSize": str(page_size),
            "column": "szse", "tabName": "fulltext",
            "plate": "", "stock": "", "searchkey": "",
            "secid": "", "category": "", "trade": "",
            "seDate": f"{date_str}~{date_str}",
            "sortName": "", "sortType": "", "isHLtitle": "true",
        }
        try:
            r = requests.post(u, data=body, headers=hdr, timeout=20)
            obj = r.json()
            anns = obj.get("announcements") or []
            if not anns:
                break
            for a in anns:
                ts = a.get("announcementTime")
                tstr = ""
                if ts:
                    tstr = datetime.fromtimestamp(ts / 1000).strftime(
                        "%Y-%m-%d %H:%M")
                out.append({
                    "SECURITY_CODE": a.get("secCode", ""),
                    "SECURITY_NAME": a.get("secName", ""),
                    "TITLE": re.sub(r"<[^>]+>", "",
                                    a.get("announcementTitle", "")),
                    "URL": ("http://static.cninfo.com.cn/" +
                            a.get("adjunctUrl", "")),
                    "TIME": tstr,
                    "BULLETIN_TYPE_DESC": "",
                })
            if len(anns) < page_size:
                break
        except Exception:
            break
        time.sleep(0.4)
    return out


def is_earnings(title: str) -> bool:
    return any(k in (title or "") for k in EARN_KEYWORDS)


def fetch_all(date_str: str, include_all: bool = False) -> pd.DataFrame:
    """
    抓取指定日期的业绩类公告。

    include_all=True 时返回全部公告（不限业绩类）。
    """
    rows = []
    srcs = []

    def add(items, src):
        n0 = len(rows)
        for x in items:
            t = x.get("TITLE", "")
            if include_all or is_earnings(t):
                rows.append({
                    "数据源": src,
                    "代码": x.get("SECURITY_CODE", ""),
                    "名称": x.get("SECURITY_NAME", ""),
                    "标题": t,
                    "类型": x.get("BULLETIN_TYPE_DESC", ""),
                    "时间": x.get("TIME", ""),
                    "URL": x.get("URL", ""),
                })
        srcs.append((src, len(items), len(rows) - n0))

    try:
        sse = fetch_sse(date_str)
        add(sse, "上交所")
    except Exception:
        sse = []
    try:
        szse = fetch_szse(date_str)
        add(szse, "深交所")
    except Exception:
        szse = []
    try:
        cn = fetch_cninfo(date_str)
        add(cn, "巨潮")
    except Exception:
        cn = []

    print(f"  数据源抓取: 上交所 {len(sse)}  深交所 {len(szse)}  巨潮 {len(cn)}")
    if not rows:
        return pd.DataFrame()
    D = pd.DataFrame(rows).drop_duplicates(subset=["代码", "标题"])
    return D


def main():
    date = None
    all_flag = "--all" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            date = a.split("=")[1]
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    else:
        if "-" not in date and len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"

    print("=" * 120)
    print(f"  实时公告抓取（交易所直连）   {date}   "
          f"运行于 {datetime.now():%H:%M:%S}")
    print("=" * 120)
    print()

    D = fetch_all(date, include_all=all_flag)
    if len(D) == 0:
        print("  未抓到公告")
        return 1
    print()
    print(f"  业绩类公告: {len(D)} 条")
    print()
    if "时间" in D.columns:
        with_t = D[D["时间"].astype(str).str.len() > 0]
        if len(with_t):
            print(f"  含时间戳: {len(with_t)} 条")
            print("  时间分布（小时）:")
            D2 = with_t.copy()
            D2["小时"] = D2["时间"].str[11:13]
            for h, c in D2["小时"].value_counts().sort_index().items():
                print(f"    {h}时: {c} 条")
            print()
    print(f"  {'代码':<10}{'名称':<12}{'时间':<18}{'标题'}")
    print("  " + "-" * 110)
    for _, r in D.head(60).iterrows():
        print(f"  {r['代码']:<10}{str(r['名称'])[:11]:<12}"
              f"{str(r.get('时间','')):<18}{str(r['标题'])[:62]}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
