# -*- coding: utf-8 -*-
"""
monitor_earnings.py — 业绩公告监控（每日运行）

用法：
    python tools/monitor_earnings.py                 # 今日
    python tools/monitor_earnings.py --date=20261016
    python tools/monitor_earnings.py --days=3        # 最近3天

输出：
    ① 当日新增业绩预告/快报
    ② 自动附加：位置/PE/市值/换手
    ③ 分组：高增+低位/中位/已涨
    ④ 每只的公告要点（从 summary 提取）
"""
from __future__ import annotations

import os
import re
import sys
import time
import glob
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def main():
    # 参数
    date = None
    days = 1
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            date = a.split("=")[1]
        if a.startswith("--days="):
            days = int(a.split("=")[1])
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    cfg = load_config()
    setup_logger("me", log_dir=None, level="ERROR", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    end = pd.Timestamp(date)
    start = end - pd.Timedelta(days=days - 1)
    print("=" * 140)
    print(f"  业绩公告监控   {start.date()} ~ {end.date()}")
    print("=" * 140)
    print()

    # ---- 1. 拉取预告
    fc = None
    for attempt in range(3):
        try:
            fc = pro.forecast_vip(start_date=start.strftime("%Y%m%d"),
                                  end_date=end.strftime("%Y%m%d"))
            break
        except Exception:
            time.sleep(1.0)
    if fc is None or len(fc) == 0:
        fc = pd.DataFrame()
    else:
        fc = fc.copy()
        fc["ann_date"] = pd.to_datetime(fc["ann_date"].astype(str),
                                        format="%Y%m%d", errors="coerce")
        for c in ("p_change_min", "p_change_max", "net_profit_min",
                  "net_profit_max", "last_parent_net"):
            if c in fc.columns:
                fc[c] = pd.to_numeric(fc[c], errors="coerce")
        fc["增速"] = fc["p_change_min"].fillna(fc["p_change_max"])
        fc["来源"] = "预告"

    # ---- 2. 拉取快报
    ex = None
    for attempt in range(3):
        try:
            ex = pro.express_vip(start_date=start.strftime("%Y%m%d"),
                                 end_date=end.strftime("%Y%m%d"))
            break
        except Exception:
            time.sleep(1.0)
    if ex is None or len(ex) == 0:
        ex = pd.DataFrame()
    else:
        ex = ex.copy()
        ex["ann_date"] = pd.to_datetime(ex["ann_date"].astype(str),
                                        format="%Y%m%d", errors="coerce")
        for c in ("revenue", "n_income", "diluted_eps", "diluted_roe"):
            if c in ex.columns:
                ex[c] = pd.to_numeric(ex[c], errors="coerce")
        ex["来源"] = "快报"

    print(f"[1] 预告 {len(fc)} 家   快报 {len(ex)} 家")
    print()

    # ---- 3. 实时行情（附加位置）
    print("[2] 附加行情与位置...")
    import requests
    all_codes = []
    if len(fc):
        all_codes += fc["ts_code"].tolist()
    if len(ex):
        all_codes += ex["ts_code"].tolist()
    all_codes = sorted(set(all_codes))

    def to_tx(tc):
        c6, sfx = tc.split(".")
        return ("sh" if sfx == "SH" else "sz") + c6
    tx = [to_tx(c) for c in all_codes]
    RT = {}
    hdr = {"User-Agent": "Mozilla/5.0"}
    for i in range(0, len(tx), 600):
        chunk = tx[i:i + 600]
        try:
            r = requests.get("https://qt.gtimg.cn/q=" + ",".join(chunk),
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
                    RT[p[2]] = {
                        "name": p[1], "price": float(p[3]),
                        "pre": float(p[4]), "chg": float(p[32]),
                        "turnover": float(p[38]) if p[38] else np.nan,
                        "pe": float(p[39]) if p[39] else np.nan,
                        "mv": float(p[45]) if p[45] else np.nan,
                        "pb": float(p[46]) if p[46] else np.nan,
                    }
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.12)
    print(f"    {len(RT)} 只")
    print()

    # ---- 4. 组装
    def build(df, src):
        rows = []
        for _, r in df.iterrows():
            c6 = r["ts_code"].split(".")[0]
            rt = RT.get(c6, {})
            # 位置
            pos = dd250 = None
            for dd in ("kline", "kline_delisted", "kline_full"):
                fp = cache / dd / f"{c6}_qfq.csv"
                if fp.exists():
                    try:
                        x = pd.read_csv(fp, usecols=lambda c: c in
                                        ("date", "high", "low", "close"))
                        if len(x) >= 130:
                            x = x.copy()
                            x["date"] = pd.to_datetime(x["date"])
                            x = x.sort_values("date")
                            last = float(x["close"].iloc[-1])
                            px = rt.get("price", np.nan)
                            ratio = (rt.get("pre", last) / last
                                     if last > 0 else 1.0)
                            seg = x.tail(130)
                            hi = float(seg["high"].max()) * ratio
                            lo = float(seg["low"].min()) * ratio
                            if hi > lo and np.isfinite(px):
                                pos = (px - lo) / (hi - lo) * 100
                            if np.isfinite(px) and hi > 0:
                                dd250 = (px / hi - 1) * 100
                    except Exception:
                        pass
                    break
            rec = {
                "代码": c6, "名称": rt.get("name", "?"),
                "来源": src, "公告日": r["ann_date"].date()
                if pd.notna(r["ann_date"]) else None,
                "类型": r.get("type", ""),
                "增速": r.get("增速", np.nan) if src == "预告" else np.nan,
                "PE": rt.get("pe", np.nan), "PB": rt.get("pb", np.nan),
                "现价": rt.get("price", np.nan),
                "今日涨跌%": rt.get("chg", np.nan),
                "换手%": rt.get("turnover", np.nan),
                "总市值亿": rt.get("mv", np.nan),
                "位置%": round(pos, 1) if pos is not None else np.nan,
                "距130日高%": round(dd250, 1) if dd250 is not None else np.nan,
            }
            if src == "快报":
                rec["营收亿"] = (r.get("revenue", np.nan) / 1e8
                              if pd.notna(r.get("revenue")) else np.nan)
                rec["净利亿"] = (r.get("n_income", np.nan) / 1e8
                              if pd.notna(r.get("n_income")) else np.nan)
                rec["ROE"] = r.get("diluted_roe", np.nan)
            # 公告要点
            s = str(r.get("summary", "") or "")
            rec["要点"] = re.sub(r"\s+", " ", s)[:120]
            rows.append(rec)
        return pd.DataFrame(rows)

    A = pd.DataFrame()
    if len(fc):
        A = build(fc, "预告")
    if len(ex):
        B = build(ex, "快报")
        A = pd.concat([A, B], ignore_index=True) if len(A) else B

    if len(A) == 0:
        print("  当日无业绩公告")
        return 0

    # ---- 5. 输出
    print("=" * 140)
    print("  业绩公告清单（按增速排序）")
    print("=" * 140)
    print()
    A = A.sort_values("增速", ascending=False, na_position="last")
    print(f"  {'代码':<8}{'名称':<11}{'来源':<7}{'类型':<7}{'增速%':>10}"
          f"{'现价':>8}{'今日%':>8}{'位置%':>8}{'距高%':>8}{'PE':>9}{'市值亿':>9}")
    print("  " + "-" * 108)
    for _, r in A.iterrows():
        g = f"{r['增速']:.1f}" if pd.notna(r["增速"]) else "—"
        pos = f"{r['位置%']:.1f}" if pd.notna(r["位置%"]) else "—"
        dd = f"{r['距130日高%']:.1f}" if pd.notna(r["距130日高%"]) else "—"
        pe = f"{r['PE']:.1f}" if pd.notna(r["PE"]) and r["PE"] > 0 else "亏损"
        mv = f"{r['总市值亿']:.0f}" if pd.notna(r["总市值亿"]) else "—"
        px = f"{r['现价']:.2f}" if pd.notna(r["现价"]) else "—"
        chg = f"{r['今日涨跌%']:.2f}" if pd.notna(r["今日涨跌%"]) else "—"
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}{r['来源']:<7}"
              f"{str(r['类型'])[:6]:<7}{g:>10}{px:>8}{chg:>8}{pos:>8}"
              f"{dd:>8}{pe:>9}{mv:>9}")
    print()

    # ---- 6. 分组
    print("=" * 140)
    print("  分组（回测依据：预告后20日胜率 59.9~65.1%）")
    print("=" * 140)
    print()
    T = A.dropna(subset=["位置%"]).copy()
    groups = [
        ("A 高增+低位 ★★★", (T["增速"] >= 50) & (T["位置%"] <= 40),
         "预告后20日胜率 60%，位置低赔率高"),
        ("B 高增+中位 ★★", (T["增速"] >= 50) & (T["位置%"] > 40) &
         (T["位置%"] <= 70), "胜率 60%，但已涨一部分"),
        ("C 高增+已涨 ★", (T["增速"] >= 50) & (T["位置%"] > 70),
         "位置高，追高风险大"),
        ("D 中低增+低位 ★★", (T["增速"].notna()) & (T["增速"] < 50) &
         (T["位置%"] <= 40), "增速一般但位置低"),
    ]
    for lab, mask, note in groups:
        sub = T[mask]
        print(f"  【{lab}】{len(sub)} 只   {note}")
        if len(sub) == 0:
            print()
            continue
        for _, r in sub.head(12).iterrows():
            g = f"{r['增速']:.1f}%" if pd.notna(r["增速"]) else "—"
            print(f"    {r['代码']} {str(r['名称'])[:10]:<11} "
                  f"增速 {g:>10}  位置 {r['位置%']:>5.1f}%  "
                  f"市值 {r['总市值亿']:>7.0f}亿  "
                  f"{str(r['要点'])[:55]}")
        print()

    # 保存
    outdir = cache.parent / "output"
    fn = outdir / f"earnings_monitor_{date}.csv"
    A.to_csv(fn, index=False, encoding="utf-8-sig")
    print(f"[输出] {fn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
