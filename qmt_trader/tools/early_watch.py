# -*- coding: utf-8 -*-
"""
early_watch.py — 早盘实时监控（解决「8点半太晚」的问题）

核心发现（已验证）：
  · 公告日 == 股价反应日（同一天）
  · 复旦微电 10-16 披露 + 当天 +14.61%
  · 说明公告在「开盘前或盘中」就已经披露

所以正确的监控节奏：
  ① 08:00-09:15  盘前：抓「当日已披露」的公告 → 开盘前决策
  ② 09:30-11:30  盘中：公告持续披露，定时扫描 → 及时跟进
  ③ 13:00-15:00  下午：继续扫描
  ④ 15:30-18:30  盘后：全天归档 + 次日预备

本脚本专为「盘前/盘中」设计：
  · 多源抓取（上交所/深交所/巨潮）
  · 只输出「当场才出现」的新公告（去重）
  · 附带实时行情（涨跌幅、是否已启动）
  · 支持 --loop 模式（每N秒循环）

用法：
    python tools/early_watch.py                    # 抓一次
    python tools/early_watch.py --loop             # 循环（默认300秒）
    python tools/early_watch.py --loop --interval=120
    python tools/early_watch.py --date=20261016
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import glob
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

EARN_KEYWORDS = ["业绩预告", "业绩快报", "业绩预增", "业绩预盈",
                 "业绩预亏", "业绩预减", "盈利预测", "业绩预披露"]


# ---------------------------------------------------------------- 数据源
def fetch_sse(date_str: str) -> list:
    """上交所（含当日）。"""
    out = []
    hdr = {"User-Agent": UA, "Referer": "http://www.sse.com.cn/"}
    for page in range(1, 6):
        u = "http://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do"
        p = {"jsonCallBack": "cb", "isPagination": "true",
             "pageHelp.pageSize": "100", "pageHelp.pageNo": str(page),
             "pageHelp.beginPage": str(page), "pageHelp.cacheSize": "1",
             "pageHelp.endPage": str(page),
             "START_DATE": date_str, "END_DATE": date_str,
             "SECURITY_CODE": "", "BULLETIN_TYPE": "", "TITLE": ""}
        try:
            r = requests.get(u, params=p, headers=hdr, timeout=20)
            i, j = r.text.find("("), r.text.rfind(")")
            if i < 0:
                break
            obj = json.loads(r.text[i + 1:j])
            data = obj.get("pageHelp", {}).get("data", [])
            if not data or not len(data[0]):
                break
            for x in data[0]:
                out.append({
                    "源": "SSE", "代码": x.get("SECURITY_CODE", ""),
                    "名称": x.get("SECURITY_NAME", ""),
                    "标题": x.get("TITLE", ""),
                    "时间": "",
                    "URL": "http://www.sse.com.cn" + str(x.get("URL", "")),
                })
            if len(data[0]) < 100:
                break
        except Exception:
            break
        time.sleep(0.2)
    return out


def fetch_szse(date_str: str) -> list:
    """深交所。"""
    out = []
    hdr = {"User-Agent": UA,
           "Referer": "http://www.szse.cn/disclosure/listed/notice/index.html",
           "X-Request-Type": "ajax",
           "Content-Type": "application/json"}
    for page in range(1, 6):
        u = "http://www.szse.cn/api/disc/announcement/annList"
        body = {"seDate": [date_str, date_str],
                "channelCode": ["listedNotice_disc"],
                "pageSize": 100, "pageNum": page,
                "random": f"{time.time():.6f}"}
        try:
            r = requests.post(u, json=body, headers=hdr, timeout=20)
            if r.status_code != 200:
                break
            obj = r.json()
            data = obj.get("data") or []
            if not data:
                break
            for x in data:
                sc = x.get("secCode")
                sn = x.get("secName")
                out.append({
                    "源": "SZSE",
                    "代码": sc[0] if isinstance(sc, list) and sc else (sc or ""),
                    "名称": sn[0] if isinstance(sn, list) and sn else (sn or ""),
                    "标题": x.get("title", ""), "时间": "",
                    "URL": str(x.get("attachPath", "")),
                })
            if len(data) < 100:
                break
        except Exception:
            break
        time.sleep(0.3)
    return out


def fetch_cninfo(date_str: str, max_page: int = 12) -> list:
    """巨潮（含时间戳，但偏盘后）。"""
    out = []
    hdr = {"User-Agent": UA,
           "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
           "X-Requested-With": "XMLHttpRequest"}
    for page in range(1, max_page + 1):
        u = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        body = {"pageNum": str(page), "pageSize": "100",
                "column": "szse", "tabName": "fulltext",
                "plate": "", "stock": "", "searchkey": "",
                "secid": "", "category": "", "trade": "",
                "seDate": f"{date_str}~{date_str}",
                "sortName": "", "sortType": "", "isHLtitle": "true"}
        try:
            r = requests.post(u, data=body, headers=hdr, timeout=25)
            obj = r.json()
            anns = obj.get("announcements") or []
            if not anns:
                break
            for a in anns:
                ts_ = a.get("announcementTime")
                tstr = ""
                if ts_:
                    tstr = datetime.fromtimestamp(ts_ / 1000).strftime("%H:%M")
                out.append({
                    "源": "CNINFO",
                    "代码": a.get("secCode", ""),
                    "名称": a.get("secName", ""),
                    "标题": re.sub(r"<[^>]+>", "",
                                   a.get("announcementTitle", "")),
                    "时间": tstr,
                    "URL": "http://static.cninfo.com.cn/" +
                           str(a.get("adjunctUrl", "")),
                })
            if len(anns) < 100:
                break
        except Exception:
            break
        time.sleep(0.35)
    return out


# ---------------------------------------------------------------- 行情
def fetch_px(codes: list) -> dict:
    out = {}
    hdr = {"User-Agent": UA}

    def to_tx(c6):
        if c6.startswith(("60", "68", "9")):
            return "sh" + c6
        return "sz" + c6
    tx = [to_tx(c) for c in codes if c and len(c) == 6]
    for i in range(0, len(tx), 600):
        try:
            r = requests.get("https://qt.gtimg.cn/q=" +
                             ",".join(tx[i:i + 600]),
                             headers=hdr, timeout=15)
            r.encoding = "gbk"
            for line in r.text.strip().split("\n"):
                m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
                if not m:
                    continue
                p = m.group(2).split("~")
                if len(p) < 50:
                    continue
                try:
                    out[p[2]] = {
                        "name": p[1], "price": float(p[3]),
                        "pre": float(p[4]), "chg": float(p[32]),
                        "high": float(p[33]), "low": float(p[34]),
                        "turnover": float(p[38]) if p[38] else np.nan,
                        "pe": float(p[39]) if p[39] else np.nan,
                        "mv": float(p[45]) if p[45] else np.nan,
                    }
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.12)
    return out


def main():
    date = None
    loop = "--loop" in sys.argv
    interval = 300
    show_all = "--all" in sys.argv
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            d = a.split("=")[1]
            date = f"{d[:4]}-{d[4:6]}-{d[6:]}" if "-" not in d else d
        if a.startswith("--interval="):
            interval = int(a.split("=")[1])
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    cfg = load_config()
    setup_logger("early", log_dir=None, level="ERROR", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "earnings_watch"
    outdir.mkdir(parents=True, exist_ok=True)
    seen_p = outdir / "seen_today.json"

    def one_pass():
        now = datetime.now()
        print("=" * 130)
        print(f"  早盘实时监控   {now:%Y-%m-%d %H:%M:%S}   目标日 {date}")
        print("=" * 130)
        print()

        # 抓取
        sse = fetch_sse(date)
        szse = fetch_szse(date)
        cn = fetch_cninfo(date)
        allann = sse + szse + cn
        print(f"  抓取: 上交所 {len(sse)}  深交所 {len(szse)}  巨潮 {len(cn)}  "
              f"合计 {len(allann)}")
        if not allann:
            print("  无公告")
            return 0
        D = pd.DataFrame(allann).drop_duplicates(subset=["代码", "标题"])

        # 筛选
        if not show_all:
            E = D[D["标题"].str.contains("|".join(EARN_KEYWORDS), na=False)]
        else:
            E = D
        print(f"  业绩类: {len(E)} 条")
        print()

        # 去重（记录已见过的）
        seen = set()
        if seen_p.exists():
            try:
                seen = set(json.loads(seen_p.read_text(encoding="utf-8")))
            except Exception:
                pass
        new_mask = ~E.apply(lambda r: f"{r['代码']}|{r['标题']}" in seen, axis=1)
        NEW = E[new_mask]
        print(f"  ★ 本次新增: {len(NEW)} 条   （历史已见 {len(E)-len(NEW)}）")
        print()

        if len(NEW) == 0:
            print("  无新增业绩公告")
            return 0

        # 行情
        codes = NEW["代码"].dropna().astype(str).tolist()
        px = fetch_px(codes)

        print(f"  {'代码':<8}{'名称':<11}{'现价':>9}{'今日%':>9}{'换手%':>8}"
              f"{'源':<8}{'时间':<7}{'标题'}")
        print("  " + "-" * 118)
        for _, r in NEW.iterrows():
            rt = px.get(str(r["代码"]), {})
            pv = f"{rt['price']:.2f}" if "price" in rt else "—"
            cg = f"{rt['chg']:.2f}" if "chg" in rt else "—"
            tn = f"{rt['turnover']:.2f}" if rt.get("turnover") is not None \
                and np.isfinite(rt.get("turnover", np.nan)) else "—"
            print(f"  {str(r['代码']):<8}{str(r['名称'])[:10]:<11}{pv:>9}"
                  f"{cg:>9}{tn:>8}{r['源']:<8}{str(r['时间']):<7}"
                  f"{str(r['标题'])[:58]}")
        print()

        # 更新 seen
        for _, r in E.iterrows():
            seen.add(f"{r['代码']}|{r['标题']}")
        try:
            seen_p.write_text(json.dumps(sorted(seen), ensure_ascii=False),
                              encoding="utf-8")
        except Exception:
            pass

        # 归档
        fn = outdir / f"early_{date.replace('-','')}.csv"
        NEW["抓取时间"] = now.strftime("%H:%M:%S")
        if fn.exists():
            old = pd.read_csv(fn, dtype=str)
            NEW2 = pd.concat([old, NEW.astype(str)], ignore_index=True)
            NEW2 = NEW2.drop_duplicates(subset=["代码", "标题"], keep="last")
        else:
            NEW2 = NEW.astype(str)
        NEW2.to_csv(fn, index=False, encoding="utf-8-sig")
        print(f"  [归档] {fn}")
        return 0

    if loop:
        print(f"  循环模式（每 {interval} 秒）—— Ctrl+C 停止")
        print()
        while True:
            try:
                one_pass()
                print(f"  ... 等待 {interval} 秒 ...")
                print()
                time.sleep(interval)
            except KeyboardInterrupt:
                print("\n  已停止")
                break
        return 0
    return one_pass()


if __name__ == "__main__":
    sys.exit(main())
