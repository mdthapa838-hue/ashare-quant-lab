# -*- coding: utf-8 -*-
"""
ann_timeline.py — 公告披露时间分析（回答「什么时候能看到」）

核查内容：
  ① 业绩类公告的实际披露时间分布（用巨潮时间戳）
  ② 前一天盘后披露的公告，次日何时反应
  ③ 最能提前把握的时点在哪

结论用于设定监控时间。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch_cninfo_full(date_str: str, max_page: int = 40):
    """抓取巨潮指定日期的全部公告（含时间）。"""
    out = []
    hdr = {
        "User-Agent": UA,
        "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
        "X-Requested-With": "XMLHttpRequest",
    }
    for page in range(1, max_page + 1):
        u = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        body = {
            "pageNum": str(page), "pageSize": "100",
            "column": "szse", "tabName": "fulltext",
            "plate": "", "stock": "", "searchkey": "",
            "secid": "", "category": "", "trade": "",
            "seDate": f"{date_str}~{date_str}",
            "sortName": "", "sortType": "", "isHLtitle": "true",
        }
        try:
            r = requests.post(u, data=body, headers=hdr, timeout=25)
            obj = r.json()
            anns = obj.get("announcements") or []
            if not anns:
                break
            for a in anns:
                ts = a.get("announcementTime")
                tstr = ""
                if ts:
                    tstr = datetime.fromtimestamp(ts / 1000).strftime("%H:%M")
                import re
                out.append({
                    "代码": a.get("secCode", ""),
                    "名称": a.get("secName", ""),
                    "标题": re.sub(r"<[^>]+>", "",
                                   a.get("announcementTitle", "")),
                    "时间": tstr,
                })
            print(f"    page {page}: +{len(anns)}  累计 {len(out)}", flush=True)
            if len(anns) < 100:
                break
        except Exception as e:
            print(f"    page {page} 失败: {str(e)[:50]}")
            break
        time.sleep(0.35)
    return out


def main():
    date = datetime.now().strftime("%Y-%m-%d")
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            d = a.split("=")[1]
            date = f"{d[:4]}-{d[4:6]}-{d[6:]}" if "-" not in d else d

    print("=" * 120)
    print(f"  公告披露时间分析   {date}")
    print("=" * 120)
    print()

    D = fetch_cninfo_full(date)
    if not D:
        print("  未抓到公告")
        return 1
    A = pd.DataFrame(D)
    print()
    print(f"  总计: {len(A)} 条公告")
    print()

    # 时间分布
    A2 = A[A["时间"].astype(str).str.len() == 5].copy()
    print("=" * 120)
    print("  一、披露时间分布（小时）")
    print("=" * 120)
    print()
    if len(A2):
        A2["小时"] = A2["时间"].str[:2]
        cnt = A2["小时"].value_counts().sort_index()
        total = cnt.sum()
        print(f"  {'时段':<8}{'条数':>8}{'占比':>10}{'累计':>10}  分布")
        print("  " + "-" * 72)
        cum = 0
        for h, c in cnt.items():
            cum += c
            bar = "█" * max(1, int(c / max(cnt.max(), 1) * 40))
            print(f"  {h}:00{'':<3}{c:>8}{c/total*100:>9.1f}%"
                  f"{cum/total*100:>9.1f}%  {bar}")
        print()

    # 业绩类公告
    print("=" * 120)
    print("  二、业绩类公告的披露时间")
    print("=" * 120)
    print()
    KW = ["业绩预告", "业绩快报", "业绩预增", "业绩预盈", "业绩预亏",
          "业绩预减", "盈利预测", "业绩预披露", "季度报告", "半年度报告"]
    E = A[A["标题"].str.contains("|".join(KW), na=False)]
    print(f"  业绩类公告: {len(E)} 条")
    if len(E):
        E2 = E[E["时间"].astype(str).str.len() == 5].copy()
        if len(E2):
            E2["小时"] = E2["时间"].str[:2]
            print()
            print("  时间分布:")
            for h, c in E2["小时"].value_counts().sort_index().items():
                print(f"    {h}时: {c} 条")
            print()
            print("  最早5条:")
            for _, r in E2.sort_values("时间").head(5).iterrows():
                print(f"    {r['时间']}  {r['代码']} {str(r['名称'])[:8]}  "
                      f"{str(r['标题'])[:50]}")
            print()
            print("  最晚5条:")
            for _, r in E2.sort_values("时间", ascending=False).head(5).iterrows():
                print(f"    {r['时间']}  {r['代码']} {str(r['名称'])[:8]}  "
                      f"{str(r['标题'])[:50]}")
            print()

    # 结论
    print("=" * 120)
    print("  三、结论：监控时点应该怎么设")
    print("=" * 120)
    print()
    if len(A2):
        early = int((A2["小时"] < "09").sum())
        morning = int(((A2["小时"] >= "09") & (A2["小时"] < "12")).sum())
        noon = int(((A2["小时"] >= "12") & (A2["小时"] < "14")).sum())
        after = int(((A2["小时"] >= "14") & (A2["小时"] < "17")).sum())
        evening = int((A2["小时"] >= "17").sum())
        tot = len(A2)
        print(f"  披露时间分布:")
        print(f"    09:00 前（隔夜+盘前）: {early:>6} 条  ({early/tot*100:>5.1f}%)")
        print(f"    09:00-12:00（上午）  : {morning:>6} 条  ({morning/tot*100:>5.1f}%)")
        print(f"    12:00-14:00（午间）  : {noon:>6} 条  ({noon/tot*100:>5.1f}%)")
        print(f"    14:00-17:00（下午）  : {after:>6} 条  ({after/tot*100:>5.1f}%)")
        print(f"    17:00 后（盘后）     : {evening:>6} 条  ({evening/tot*100:>5.1f}%)")
        print()
        print("  → 如果「17:00 后」占比最高，说明公告集中在盘后，")
        print("    那么「盘后监控」是主力，「盘前监控」用于确认隔夜公告。")
        print()

    # 保存
    outdir = HERE / "output"
    outdir.mkdir(exist_ok=True)
    A.to_csv(outdir / f"ann_timeline_{date.replace('-','')}.csv",
             index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/ann_timeline_{date.replace('-','')}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
