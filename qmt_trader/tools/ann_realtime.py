# -*- coding: utf-8 -*-
"""
ann_realtime.py — 公告的真实发布时间（东方财富精确时间戳）

关键发现：
  东财的 display_time / eiTime 字段给出精确到秒的发布时间
  notice_date 只是「公告归属日」（次日）

用法：
    python tools/ann_realtime.py                 # 今日
    python tools/ann_realtime.py --date=20261016
"""
from __future__ import annotations

import sys
import json
import time
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

KW = ["业绩预告", "业绩快报", "业绩预增", "业绩预盈", "业绩预亏",
      "业绩预减", "盈利预测", "业绩预披露"]


def parse_dt(v):
    """解析东财时间（可能是 'YYYY-MM-DD HH:MM:SS:mmm' 或时间戳）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)) and v > 1e9:
        try:
            return datetime.fromtimestamp(v / 1000)
        except Exception:
            return None
    s = str(v)
    # "2026-10-15 17:28:09:599"
    m = s.replace(":", "-", 2)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S-%f",
                "%Y-%m-%d %H:%M:%S:%f", "%Y-%m-%d %H:%M"):
        try:
            core = s
            if s.count(":") == 3:
                core = s.rsplit(":", 1)[0]
            return datetime.strptime(core[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def fetch_eastmoney_ann(code: str, page_size: int = 30) -> list:
    """抓取单只股票的公告（含精确时间）。"""
    out = []
    u = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    p = {"sr": "-1", "page_size": str(page_size), "page_index": "1",
         "ann_type": "A", "client_source": "web",
         "stock_list": code, "f_node": "0", "s_node": "0"}
    try:
        r = requests.get(u, params=p,
                         headers={"User-Agent": UA,
                                  "Referer": "https://data.eastmoney.com/"},
                         timeout=20)
        obj = r.json()
        data = obj.get("data", {}).get("list", [])
        for x in data:
            out.append({
                "代码": code,
                "标题": x.get("title", ""),
                "公告日": str(x.get("notice_date", ""))[:10],
                "发布时间": parse_dt(x.get("display_time")),
                "入库时间": parse_dt(x.get("eiTime")),
            })
    except Exception:
        pass
    return out


def main():
    date_str = None
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            d = a.split("=")[1]
            date_str = f"{d[:4]}-{d[4:6]}-{d[6:]}" if "-" not in d else d
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 132)
    print(f"  公告真实发布时间（东方财富精确时间戳）   {date_str}")
    print("=" * 132)
    print()

    # 当日业绩公告
    ymd = date_str.replace("-", "")
    d = None
    for _ in range(3):
        try:
            d = pro.forecast_vip(ann_date=ymd)
            break
        except Exception:
            time.sleep(1.0)
    if d is None or len(d) == 0:
        print("  当日无预告")
        return 1

    d = d.copy()
    for c in ("p_change_min", "p_change_max"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["增速"] = d["p_change_min"].fillna(d["p_change_max"])

    # 逐只查精确时间
    print("[1] 查询每只的精确发布时间...")
    rows = []
    for i, (_, x) in enumerate(d.iterrows(), 1):
        c6 = x["ts_code"].split(".")[0]
        anns = fetch_eastmoney_ann(c6)
        hit = None
        for a in anns:
            if any(k in a["标题"] for k in KW):
                # 优先匹配公告日=目标日
                if a["公告日"] == date_str or a["发布时间"] is None \
                        or a["发布时间"].strftime("%Y-%m-%d") in (
                            date_str,
                            (pd.Timestamp(date_str) -
                             pd.Timedelta(days=1)).strftime("%Y-%m-%d")):
                    hit = a
                    break
        if hit is None and anns:
            for a in anns:
                if any(k in a["标题"] for k in KW):
                    hit = a
                    break
        rows.append({
            "代码": c6,
            "名称": "",
            "增速%": x["增速"],
            "标题": (hit or {}).get("标题", ""),
            "公告归属日": (hit or {}).get("公告日", ""),
            "实际发布时间": (hit or {}).get("发布时间"),
            "入库时间": (hit or {}).get("入库时间"),
        })
        print(f"    {i}/{len(d)}  {c6}", end="\r", flush=True)
        time.sleep(0.25)
    print()
    print()

    A = pd.DataFrame(rows)
    print("=" * 132)
    print("  一、实际发布时间")
    print("=" * 132)
    print()
    print(f"  {'代码':<8}{'增速%':>9}{'公告归属日':<13}{'实际发布时间':<21}"
          f"{'入库时间':<21}{'相对收盘':<12}")
    print("  " + "-" * 100)
    for _, r in A.iterrows():
        pt = r["实际发布时间"]
        et = r["入库时间"]
        pts = pt.strftime("%Y-%m-%d %H:%M:%S") if pt else "—"
        ets = et.strftime("%Y-%m-%d %H:%M:%S") if et else "—"
        # 相对收盘（15:00）
        rel = ""
        if pt:
            hour = pt.hour + pt.minute / 60
            if hour < 9.25:
                rel = "★开盘前"
            elif hour < 15:
                rel = "盘中"
            else:
                rel = f"收盘后 {hour-15:.1f}h"
        print(f"  {r['代码']:<8}{r['增速%']:>9.1f}"
              f"{str(r['公告归属日']):<13}{pts:<21}{ets:<21}{rel:<12}")
    print()

    # 统计
    print("=" * 132)
    print("  二、统计")
    print("=" * 132)
    print()
    ok = A[A["实际发布时间"].notna()].copy()
    if len(ok):
        ok["小时"] = ok["实际发布时间"].apply(lambda t: t.hour)
        ok["归属日"] = ok["实际发布时间"].apply(
            lambda t: t.strftime("%Y-%m-%d"))
        print(f"  取到时间: {len(ok)}/{len(A)}")
        print()
        print("  按「实际发布日」分布:")
        for k, v in ok["归属日"].value_counts().sort_index().items():
            print(f"    {k}: {v} 只")
        print()
        print("  按「发布小时」分布:")
        for k, v in ok["小时"].value_counts().sort_index().items():
            print(f"    {k}时: {v} 只")
        print()
        # 关键：前一交易日 15:00-24:00 发布的
        prev = (pd.Timestamp(date_str) - pd.Timedelta(days=1)).strftime(
            "%Y-%m-%d")
        pre = ok[(ok["实际发布时间"].dt.strftime("%Y-%m-%d") == prev) &
                 (ok["实际发布时间"].dt.hour >= 15)]
        print(f"  ★ 前一交易日（{prev}）15:00 后发布: {len(pre)} 只")
        if len(pre):
            print(f"    → 这些是「隔夜公告」，次日开盘就会跳空")
            for _, r in pre.iterrows():
                print(f"      {r['代码']}  {r['实际发布时间']}  "
                      f"增速 {r['增速%']:.1f}%")
        print()
        same_before = ok[(ok["实际发布时间"].dt.strftime("%Y-%m-%d")
                          == date_str) &
                         (ok["实际发布时间"].dt.hour < 9)]
        print(f"  ★ 当日（{date_str}）09:00 前发布: {len(same_before)} 只")
        if len(same_before):
            for _, r in same_before.iterrows():
                print(f"      {r['代码']}  {r['实际发布时间']}  "
                      f"增速 {r['增速%']:.1f}%")
    print()

    outdir = HERE / "output"
    outdir.mkdir(exist_ok=True)
    A.to_csv(outdir / f"ann_realtime_{ymd}.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/ann_realtime_{ymd}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
