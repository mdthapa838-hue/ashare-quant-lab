# -*- coding: utf-8 -*-
"""
verify_ann_time.py — 核实公告的实际发布时间

方法：
  ① 开盘跳空分析：若公告是盘前发布，开盘就会跳空
  ② PDF 元数据/HTTP 头
  ③ 多源交叉（上交所/巨潮/东财）
"""
from __future__ import annotations

import sys
import re
import json
import time
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import requests

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def main():
    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 132)
    print("  核实：公告到底是什么时候发布的？")
    print("=" * 132)
    print()

    # ---- 今日15家
    d = pro.forecast_vip(ann_date="20261016")
    d = d.copy()
    for c in ("p_change_min", "p_change_max"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["增速"] = d["p_change_min"].fillna(d["p_change_max"])

    print("=" * 132)
    print("  方法一：开盘跳空分析（决定性证据）")
    print("=" * 132)
    print()
    print("  逻辑：")
    print("    · 若公告「盘前」发布 → 开盘就跳空（开盘涨幅 > 0）")
    print("    · 若公告「盘后」发布 → 开盘平，次日才反应")
    print("    · 若公告「盘中」发布 → 开盘平，盘中拉升")
    print()
    print(f"  {'名称':<11}{'增速%':>9}{'昨收':>9}{'开盘':>9}{'开盘%':>9}"
          f"{'收盘%':>9}{'判定':<16}")
    print("  " + "-" * 90)

    pre_market = 0
    intraday = 0
    post_market = 0
    rows = []
    for _, x in d.iterrows():
        tc = x["ts_code"]
        try:
            q = pro.daily(ts_code=tc, start_date="20261014", end_date="20261016")
            if q is None or len(q) < 2:
                continue
            q = q.sort_values("trade_date")
            prev_close = float(q.iloc[-2]["close"])
            today = q.iloc[-1]
            open_p = float(today["open"])
            close_p = float(today["close"])
            open_chg = (open_p / prev_close - 1) * 100
            close_chg = (close_p / prev_close - 1) * 100
            if open_chg > 2:
                verdict = "★盘前发布"
                pre_market += 1
            elif close_chg - open_chg > 3 or (open_chg < -1 and close_chg > 2):
                verdict = "盘中拉升"
                intraday += 1
            elif abs(close_chg) < 2:
                verdict = "无反应/盘后"
                post_market += 1
            else:
                verdict = "其他"
            rows.append({
                "名称": x.get("ts_code"), "增速": x["增速"],
                "昨收": prev_close, "开盘": open_p,
                "开盘%": round(open_chg, 2), "收盘%": round(close_chg, 2),
                "判定": verdict,
            })
            name = ""
            for a in ("ts_code",):
                pass
            print(f"  {tc:<11}{x['增速']:>9.1f}{prev_close:>9.2f}"
                  f"{open_p:>9.2f}{open_chg:>8.2f}%{close_chg:>8.2f}%"
                  f"  {verdict:<16}")
        except Exception:
            continue
    print()
    print(f"  统计: 盘前发布 {pre_market}   盘中 {intraday}   "
          f"无反应 {post_market}   共 {len(rows)}")
    print()

    # ---- 更精细：用分时判断
    print("=" * 132)
    print("  方法二：分时数据（看开盘第一分钟的量能）")
    print("=" * 132)
    print()
    print("  逻辑：开盘第一分钟若放巨量 → 说明有重大消息在盘前消化")
    print()
    hdr = {"User-Agent": UA}
    print(f"  {'名称':<11}{'开盘':>9}{'09:35':>9}{'10:00':>9}"
          f"{'最高':>9}{'收盘':>9}{'开盘量占比':>12}")
    print("  " + "-" * 78)
    for _, x in d.iterrows():
        tc = x["ts_code"]
        c6, sfx = tc.split(".")
        code = ("sh" if sfx == "SH" else "sz") + c6
        try:
            r = requests.get(
                f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}",
                headers=hdr, timeout=15)
            j = r.json()
            dat = j["data"][code]["data"]
            lines = dat.get("data", [])
            if len(lines) < 30:
                continue
            def px(i):
                return float(lines[min(i, len(lines) - 1)].split(" ")[1])
            def vol(i):
                return float(lines[min(i, len(lines) - 1)].split(" ")[2])
            o = px(0)
            p035 = px(5)
            p100 = px(30)
            pmax = max(float(l.split(" ")[1]) for l in lines)
            pclose = px(len(lines) - 1)
            v_total = vol(len(lines) - 1)
            v_first = vol(0)  # 开盘竞价量
            ratio = v_first / v_total * 100 if v_total > 0 else 0
            print(f"  {c6:<11}{o:>9.2f}{p035:>9.2f}{p100:>9.2f}"
                  f"{pmax:>9.2f}{pclose:>9.2f}{ratio:>11.1f}%")
        except Exception:
            continue
    print()

    # ---- 方法三：交易所时间
    print("=" * 132)
    print("  方法三：上交所公告列表（看是否有时间字段）")
    print("=" * 132)
    print()
    for code in ("688385", "688002"):
        try:
            u = ("http://query.sse.com.cn/security/stock/"
                 "queryCompanyBulletinNew.do")
            p = {"jsonCallBack": "cb", "isPagination": "true",
                 "pageHelp.pageSize": "20", "pageHelp.pageNo": "1",
                 "pageHelp.beginPage": "1", "pageHelp.cacheSize": "1",
                 "pageHelp.endPage": "1",
                 "START_DATE": "2026-10-16", "END_DATE": "2026-10-16",
                 "SECURITY_CODE": code, "BULLETIN_TYPE": "", "TITLE": ""}
            r = requests.get(u, params=p,
                             headers={**hdr, "Referer": "http://www.sse.com.cn/"},
                             timeout=20)
            i, j2 = r.text.find("("), r.text.rfind(")")
            obj = json.loads(r.text[i + 1:j2])
            data = obj.get("pageHelp", {}).get("data", [])
            if data and len(data[0]):
                for x2 in data[0]:
                    print(f"  {code}  {x2.get('SSEDATE')}  "
                          f"{str(x2.get('TITLE'))[:55]}")
                    print(f"      全部字段: {list(x2.keys())}")
            else:
                print(f"  {code}: 无")
        except Exception as e:
            print(f"  {code}: 失败 {str(e)[:50]}")
    print()

    # ---- 方法四：新闻源时间戳
    print("=" * 132)
    print("  方法四：东方财富个股新闻（含时间戳）")
    print("=" * 132)
    print()
    try:
        u = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        p = {"sr": "-1", "page_size": "30", "page_index": "1",
             "ann_type": "A", "client_source": "web",
             "stock_list": "688385", "f_node": "0", "s_node": "0"}
        r = requests.get(u, params=p, headers={
            "User-Agent": UA,
            "Referer": "https://data.eastmoney.com/"}, timeout=20)
        obj = r.json()
        data = obj.get("data", {}).get("list", [])
        print(f"  东方财富返回 {len(data)} 条")
        for x in data[:8]:
            print(f"    {x.get('notice_date')}  {str(x.get('title'))[:60]}")
        if data:
            print()
            print(f"  字段: {list(data[0].keys())}")
    except Exception as e:
        print(f"  失败: {str(e)[:80]}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
