# -*- coding: utf-8 -*-
"""
after_close_exact.py — 精确找出「今天收盘后」新发布的业绩公告

方法：
  ① 巨潮抓今日公告（归属日=今日）
  ② 对每条业绩类公告，用东财查「精确发布时间」
  ③ 筛选：精确时间 >= 今日 15:00
  → 这才是真正"今天收盘后"发布的
"""
from __future__ import annotations

import sys
import re
import time
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from today_after_close import fetch_cninfo_all, fetch_px, EARN_KW

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def em_exact(code: str) -> list:
    """东财：返回该股全部公告的精确时间。"""
    out = []
    try:
        u = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        p = {"sr": "-1", "page_size": "40", "page_index": "1",
             "ann_type": "A", "client_source": "web",
             "stock_list": code, "f_node": "0", "s_node": "0"}
        r = requests.get(u, params=p, headers={
            "User-Agent": UA, "Referer": "https://data.eastmoney.com/"},
            timeout=20)
        obj = r.json()
        for x in (obj.get("data", {}).get("list", []) or []):
            t = None
            v = str(x.get("display_time", "") or "")
            if len(v) >= 19:
                try:
                    t = datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
            if t is None:
                ei = x.get("eiTime")
                if isinstance(ei, (int, float)) and ei > 1e9:
                    t = datetime.fromtimestamp(ei / 1000)
            out.append({
                "标题": str(x.get("title", "")),
                "归属日": str(x.get("notice_date", ""))[:10],
                "精确时间": t,
            })
    except Exception:
        pass
    return out


def main():
    date_str = datetime.now().strftime("%Y-%m-%d")
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            d = a.split("=")[1]
            date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}" if "-" not in d else d

    print("=" * 132)
    print(f"  精确核实：{date_str} 收盘后新发布的业绩公告")
    print("=" * 132)
    print()

    # 1. 巨潮抓今日
    print("[1] 抓取巨潮今日公告...")
    A = fetch_cninfo_all(date_str)
    if not A:
        print("  未抓到")
        return 1
    D = pd.DataFrame(A)
    print(f"    共 {len(D)} 条")
    print()

    # 2. 业绩类
    E = D[D["标题"].str.contains("|".join(EARN_KW), na=False)].copy()
    # 去重（按代码）
    codes = sorted(set(E["代码"].astype(str)))
    print(f"[2] 业绩类: {len(E)} 条，涉及 {len(codes)} 只")
    print()

    # 3. 逐个查精确时间
    print("[3] 用东方财富查精确发布时间...")
    rows = []
    for i, c6 in enumerate(codes, 1):
        anns = em_exact(c6)
        # 找业绩类且归属日=今日 或 发布时间=今日
        hit = None
        for a in anns:
            if any(k in a["标题"] for k in EARN_KW):
                t = a["精确时间"]
                if t and t.strftime("%Y-%m-%d") == date_str:
                    hit = a
                    break
                if a["归属日"] == date_str and hit is None:
                    hit = a
        if hit is None and anns:
            for a in anns:
                if any(k in a["标题"] for k in EARN_KW):
                    hit = a
                    break
        name = ""
        sub = E[E["代码"].astype(str) == c6]
        if len(sub):
            name = sub.iloc[0]["名称"]
        rows.append({
            "代码": c6, "名称": name,
            "标题": (hit or {}).get("标题", ""),
            "归属日": (hit or {}).get("归属日", ""),
            "精确时间": (hit or {}).get("精确时间"),
        })
        if i % 5 == 0:
            print(f"    {i}/{len(codes)}", flush=True)
        time.sleep(0.25)
    print()
    R = pd.DataFrame(rows)

    # 4. 筛选"今天收盘后"
    R["发布日"] = R["精确时间"].apply(
        lambda t: t.strftime("%Y-%m-%d") if t else "")
    today_after = R[(R["发布日"] == date_str) &
                    (R["精确时间"].apply(
                        lambda t: t.hour * 60 + t.minute >= 15 * 60
                        if t else False))]
    prev_day = R[(R["发布日"] != date_str) & R["发布日"].ne("")]

    print("=" * 132)
    print("  一、★ 今日（10-16）收盘后新发布")
    print("=" * 132)
    print()
    if len(today_after):
        print(f"  {len(today_after)} 只")
        print()
        print(f"  {'代码':<8}{'名称':<12}{'精确时间':<21}{'标题'}")
        print("  " + "-" * 110)
        for _, r in today_after.sort_values("精确时间").iterrows():
            print(f"  {r['代码']:<8}{str(r['名称'])[:11]:<12}"
                  f"{r['精确时间'].strftime('%Y-%m-%d %H:%M:%S'):<21}"
                  f"{str(r['标题'])[:56]}")
    else:
        print("  无")
    print()

    print("=" * 132)
    print("  二、之前已发布（归属日为今日，但实际前一天发布）")
    print("=" * 132)
    print()
    print(f"  {len(prev_day)} 只")
    print()
    print(f"  {'代码':<8}{'名称':<12}{'实际发布时间':<21}{'标题'}")
    print("  " + "-" * 110)
    for _, r in prev_day.sort_values("精确时间").iterrows():
        ts = r["精确时间"].strftime("%Y-%m-%d %H:%M:%S") \
            if r["精确时间"] else "—"
        print(f"  {r['代码']:<8}{str(r['名称'])[:11]:<12}{ts:<21}"
              f"{str(r['标题'])[:56]}")
    print()

    # 5. 全量时间分布
    print("=" * 132)
    print("  三、全量发布时间分布")
    print("=" * 132)
    print()
    ok = R[R["精确时间"].notna()].copy()
    if len(ok):
        ok["日"] = ok["精确时间"].apply(lambda t: t.strftime("%Y-%m-%d"))
        ok["小时"] = ok["精确时间"].apply(lambda t: t.hour)
        print("  按发布日:")
        for k, v in ok["日"].value_counts().sort_index().items():
            print(f"    {k}: {v} 只")
        print()
        print("  按小时:")
        for k, v in ok["小时"].value_counts().sort_index().items():
            print(f"    {k}时: {v} 只")
    print()

    outdir = HERE / "earnings_watch"
    outdir.mkdir(exist_ok=True)
    R.to_csv(outdir / f"exact_{date_str.replace('-','')}.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/exact_{date_str.replace('-','')}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
