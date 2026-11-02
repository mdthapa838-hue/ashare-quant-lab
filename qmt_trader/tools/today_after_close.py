# -*- coding: utf-8 -*-
"""
today_after_close.py — 今日收盘后发布的业绩公告

抓取多源（巨潮全市场翻页 + 东财精确时间 + 交易所），
筛出「今天 16:00 之后」的业绩类公告，并做分析。

用法：
    python tools/today_after_close.py
    python tools/today_after_close.py --date=20261016
    python tools/today_after_close.py --hours=15   # 15:00 之后
"""
from __future__ import annotations

import sys
import re
import json
import time
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

EARN_KW = ["业绩预告", "业绩快报", "业绩预增", "业绩预盈", "业绩预亏",
           "业绩预减", "盈利预测", "业绩预披露", "第三季度报告",
           "前三季度", "业绩预披露"]


def fetch_cninfo_all(date_str: str, page_size: int = 30,
                     max_page: int = 120) -> list:
    """巨潮全市场公告（翻页，返回全部）。"""
    out = []
    hdr = {"User-Agent": UA,
           "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
           "X-Requested-With": "XMLHttpRequest"}
    seen = set()
    for page in range(1, max_page + 1):
        u = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        body = {"pageNum": str(page), "pageSize": str(page_size),
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
            added = 0
            for a in anns:
                key = (a.get("secCode"), a.get("announcementTitle"))
                if key in seen:
                    continue
                seen.add(key)
                ts_ = a.get("announcementTime")
                tstr = ""
                if ts_:
                    tstr = datetime.fromtimestamp(ts_ / 1000).strftime(
                        "%H:%M:%S")
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
                added += 1
            if page % 10 == 0:
                print(f"      page {page}: 累计 {len(out)}", flush=True)
            if len(anns) < page_size:
                break
        except Exception as e:
            print(f"      page {page} 失败: {str(e)[:40]}")
            break
        time.sleep(0.25)
    return out


def fetch_em_time(code: str, title_kw: str = "") -> datetime:
    """用东财查单只股票的最新公告精确时间。"""
    try:
        u = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        p = {"sr": "-1", "page_size": "20", "page_index": "1",
             "ann_type": "A", "client_source": "web",
             "stock_list": code, "f_node": "0", "s_node": "0"}
        r = requests.get(u, params=p, headers={
            "User-Agent": UA, "Referer": "https://data.eastmoney.com/"},
            timeout=20)
        obj = r.json()
        for x in (obj.get("data", {}).get("list", []) or []):
            t = str(x.get("title", ""))
            if title_kw and title_kw[:8] not in t:
                continue
            v = str(x.get("display_time", ""))
            if v:
                core = v[:19].replace(":", "-", 0)
                # "2026-10-15 17:28:09:599"
                try:
                    return datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
            ei = x.get("eiTime")
            if isinstance(ei, (int, float)) and ei > 1e9:
                return datetime.fromtimestamp(ei / 1000)
            nd = str(x.get("notice_date", ""))[:19]
            try:
                return datetime.strptime(nd, "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
    except Exception:
        pass
    return None


def fetch_px(codes: list) -> dict:
    out = {}
    hdr = {"User-Agent": UA}

    def to_tx(c6):
        return ("sh" if c6.startswith(("60", "68", "9")) else "sz") + c6

    tx = [to_tx(c) for c in codes if c and len(c) == 6]
    for i in range(0, len(tx), 600):
        try:
            r = requests.get("https://qt.gtimg.cn/q=" + ",".join(tx[i:i + 600]),
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
    date_str = None
    hours = 16
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            d = a.split("=")[1]
            date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}" if "-" not in d else d
        if a.startswith("--hours="):
            hours = int(a.split("=")[1])
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    print("=" * 132)
    print(f"  今日收盘后业绩公告   {date_str}   "
          f"运行于 {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 132)
    print()

    # 1. 全市场抓取
    print("[1] 抓取全市场公告（巨潮翻页）...")
    t0 = time.time()
    A = fetch_cninfo_all(date_str)
    print(f"    共 {len(A)} 条  ({time.time()-t0:.0f}s)")
    print()
    if not A:
        print("  未抓到公告")
        return 1
    D = pd.DataFrame(A)
    D = D[D["时间"].astype(str).str.len() >= 5].copy()
    print(f"    含时间戳: {len(D)} 条")

    # 2. 时间过滤
    D["小时"] = D["时间"].str[:2].astype(int)
    AF = D[D["小时"] >= hours].copy()
    print(f"    ≥{hours}:00 发布: {len(AF)} 条")
    print()

    # 3. 业绩类筛选
    E = AF[AF["标题"].str.contains("|".join(EARN_KW), na=False)].copy()
    print(f"[2] 业绩类: {len(E)} 条")
    print()
    if len(E) == 0:
        print("  今日收盘后无业绩类公告")
        # 打印时间分布辅助判断
        print()
        print("  全部公告时间分布:")
        for h, c in D["小时"].value_counts().sort_index().items():
            print(f"    {h}时: {c} 条")
        return 1

    # 4. 精确时间（东财）
    print("[3] 查询精确发布时间...")
    times = []
    for i, (_, r) in enumerate(E.iterrows(), 1):
        t = fetch_em_time(str(r["代码"]), str(r["标题"]))
        times.append(t)
        if i % 10 == 0:
            print(f"    {i}/{len(E)}", flush=True)
        time.sleep(0.2)
    E["精确时间"] = times
    print(f"    完成")
    print()

    # 5. 行情 + 位置
    print("[4] 附加行情与位置...")
    px = fetch_px(E["代码"].astype(str).tolist())
    from core.config import load_config
    from core.logger import setup_logger
    cfg = load_config()
    setup_logger("tac", log_dir=None, level="ERROR", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))

    def get_pos(c6, rt):
        o = {"p20": None, "p60": None, "p120": None}
        for dd in ("kline", "kline_delisted", "kline_full"):
            fp = cache / dd / f"{c6}_qfq.csv"
            if not fp.exists():
                continue
            try:
                x = pd.read_csv(fp, usecols=lambda c: c in
                                ("date", "high", "low", "close"))
                if len(x) < 20:
                    break
                x = x.sort_values("date")
                h = x["high"].values
                l = x["low"].values
                c = x["close"].values
                # 用最新日线归一化到实时价
                last = float(c[-1])
                pre = rt.get("pre", last)
                ratio = pre / last if last > 0 else 1.0
                hi = h * ratio
                lo = l * ratio
                p = rt.get("price", np.nan)
                if not np.isfinite(p):
                    continue
                for n, k in [(20, "p20"), (60, "p60"), (120, "p120")]:
                    if len(x) >= n:
                        hh = np.nanmax(hi[-n:])
                        ll = np.nanmin(lo[-n:])
                        if hh > ll:
                            o[k] = round((p - ll) / (hh - ll) * 100, 1)
            except Exception:
                pass
            break
        return o

    recs = []
    for _, r in E.iterrows():
        c6 = str(r["代码"])
        rt = px.get(c6, {})
        ps = get_pos(c6, rt)
        recs.append({
            "代码": c6, "名称": rt.get("name", r["名称"]),
            "标题": r["标题"],
            "发布时间": r["时间"],
            "精确时间": r["精确时间"],
            "现价": rt.get("price"), "今日%": rt.get("chg"),
            "换手%": rt.get("turnover"),
            "PE": rt.get("pe"), "总市值亿": rt.get("mv"),
            "p20": ps["p20"], "p60": ps["p60"], "p120": ps["p120"],
        })
    R = pd.DataFrame(recs)
    print(f"    完成")
    print()

    # 6. 输出
    print("=" * 132)
    print(f"  今日收盘后业绩公告（≥{hours}:00）")
    print("=" * 132)
    print()
    print(f"  {'代码':<8}{'名称':<11}{'时间':<10}{'今日%':>8}{'p20':>7}"
          f"{'p60':>7}{'p120':>7}{'PE':>9}{'市值亿':>9}  标题")
    print("  " + "-" * 126)
    for _, r in R.iterrows():
        def f(v, d=1):
            return f"{v:.{d}f}" if pd.notna(v) and v is not None else "—"
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}"
              f"{str(r['发布时间'])[:8]:<10}{f(r['今日%'],2):>8}"
              f"{f(r['p20']):>7}{f(r['p60']):>7}{f(r['p120']):>7}"
              f"{f(r['PE']):>9}{f(r['总市值亿'],0):>9}  "
              f"{str(r['标题'])[:44]}")
    print()

    # 7. 分组
    print("=" * 132)
    print("  分组（用 v2 规则）")
    print("=" * 132)
    print()
    hi_mid = R[((R["p60"].fillna(0) >= 60) | (R["p120"].fillna(0) >= 60))]
    low = R[(R["p20"].notna()) & (R["p20"] < 20)]
    print(f"  【中期高位（p60或p120≥60）】{len(hi_mid)} 只")
    for _, r in hi_mid.iterrows():
        print(f"    {r['代码']} {str(r['名称'])[:10]:<11} "
              f"p60={r['p60']} p120={r['p120']} 今日 {r['今日%']}%")
    print()
    print(f"  【低位（p20<20）】{len(low)} 只")
    for _, r in low.iterrows():
        print(f"    {r['代码']} {str(r['名称'])[:10]:<11} "
              f"p20={r['p20']} 今日 {r['今日%']}%")
    print()

    outdir = HERE / "earnings_watch"
    outdir.mkdir(exist_ok=True)
    fn = outdir / f"after_close_{date_str.replace('-','')}.csv"
    R.to_csv(fn, index=False, encoding="utf-8-sig")
    print(f"[输出] {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
