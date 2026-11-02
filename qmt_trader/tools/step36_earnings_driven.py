# -*- coding: utf-8 -*-
"""
step36_earnings_driven.py — 找「业绩驱动」的股票

锐科激光的模式（已验证）：
  · 个股特异（同业全跌，它 +18.90%）
  · 业绩快报/预告超预期
  · 股价从底部启动

本脚本找同类：
  ① 找出所有已披露「前三季度业绩快报」的公司
  ② 筛选「超预期」：Q3 单季同比高增 + 加速
  ③ 再筛「位置低」（还没涨）
  ④ 输出候选 + 分类
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def main():
    cfg = load_config()
    setup_logger("ed", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    now = datetime.now()
    print("=" * 132)
    print(f"  找「业绩驱动」标的   {now:%Y-%m-%d %H:%M}")
    print("=" * 132)
    print()
    print("  锐科激光模式：个股特异 + 业绩快报超预期 + 底部启动")
    print()

    # ---- 1. 已披露的业绩预告/快报
    print("[1] 拉取业绩预告（2026-09-01 起）...")
    t0 = time.time()
    frames = []
    cur = pd.Timestamp("20261231")
    s0 = pd.Timestamp("20260901")
    for _ in range(10):
        seg = max(s0, cur - pd.Timedelta(days=40))
        got = None
        for attempt in range(3):
            try:
                got = pro.forecast_vip(start_date=seg.strftime("%Y%m%d"),
                                       end_date=cur.strftime("%Y%m%d"))
                break
            except Exception as e:
                time.sleep(1.0)
        if got is not None and len(got):
            frames.append(got)
        e = pd.to_datetime(seg) - pd.Timedelta(days=1)
        if e <= s0:
            break
        cur = e
    if frames:
        FC = pd.concat(frames, ignore_index=True)
        FC = FC.drop_duplicates(subset=["ts_code", "ann_date", "end_date"])
    else:
        FC = pd.DataFrame()
    print(f"    业绩预告: {len(FC):,} 条  ({time.time()-t0:.0f}s)")

    # 也试 express（快报）
    t0 = time.time()
    try:
        EX = pro.express_vip(start_date="20260901", end_date="20261231")
    except Exception:
        EX = None
    if EX is not None and len(EX):
        print(f"    业绩快报: {len(EX):,} 条")
    else:
        EX = pd.DataFrame()
    print()

    # ---- 2. 合并（预告 + 快报）
    cands = []
    if len(FC):
        f = FC.copy()
        f["ann_date"] = pd.to_datetime(f["ann_date"].astype(str),
                                       format="%Y%m%d", errors="coerce")
        f["end_date"] = pd.to_datetime(f["end_date"].astype(str),
                                       format="%Y%m%d", errors="coerce")
        for c in ("p_change_min", "p_change_max", "net_profit_min",
                  "net_profit_max"):
            if c in f.columns:
                f[c] = pd.to_numeric(f[c], errors="coerce")
        f["source"] = "预告"
        cands.append(f)
    if len(EX):
        e = EX.copy()
        e["ann_date"] = pd.to_datetime(e["ann_date"].astype(str),
                                       format="%Y%m%d", errors="coerce")
        e["end_date"] = pd.to_datetime(e["end_date"].astype(str),
                                       format="%Y%m%d", errors="coerce")
        for c in ("yoy_net_profit", "n_income", "revenue"):
            if c in e.columns:
                e[c] = pd.to_numeric(e[c], errors="coerce")
        e["source"] = "快报"
        cands.append(e)
    if not cands:
        print("无业绩数据")
        return 1
    C = pd.concat(cands, ignore_index=True)
    print(f"[2] 合计: {len(C):,} 条（预告+快报）")
    print()

    # ---- 3. 只看 Q3 (end_date = 20260930)
    Q3 = C[C["end_date"] == pd.Timestamp("20260930")].copy()
    print(f"[3] 前三季度业绩数据: {len(Q3):,} 条")
    print()

    # ---- 4. 实时行情
    print("[4] 拉取实时行情...")
    import requests
    import re

    def to_tx(c6):
        if c6.startswith(("60", "68", "9")):
            return "sh" + c6
        return "sz" + c6
    codes = [to_tx(c[:6]) for c in Q3["ts_code"].unique()]
    RT = {}
    hdr = {"User-Agent": "Mozilla/5.0"}
    for i in range(0, len(codes), 600):
        chunk = codes[i:i + 600]
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
                        "pre_close": float(p[4]), "chg": float(p[32]),
                        "high": float(p[33]), "low": float(p[34]),
                        "turnover": float(p[38]) if p[38] else np.nan,
                        "pe": float(p[39]) if p[39] else np.nan,
                        "total_mv": float(p[45]) if p[45] else np.nan,
                        "pb": float(p[46]) if p[46] else np.nan,
                    }
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.15)
    print(f"    {len(RT):,} 只")
    print()

    # ---- 5. 位置数据（本地K线）
    print("[5] 计算位置...")
    rows = []
    miss = 0
    for _, r in Q3.iterrows():
        c6 = r["ts_code"][:6]
        if c6 not in RT:
            miss += 1
            continue
        rt = RT[c6]
        hist = None
        for dd in ("kline", "kline_delisted", "kline_full"):
            fp = cache / dd / f"{c6}_qfq.csv"
            if fp.exists():
                try:
                    x = pd.read_csv(fp, usecols=lambda c: c in
                                    ("date", "high", "low", "close"))
                    if len(x) >= 130:
                        x = x.copy()
                        x["date"] = pd.to_datetime(x["date"])
                        hist = x.sort_values("date").reset_index(drop=True)
                except Exception:
                    pass
                break
        pos = dd250 = ma20d = None
        if hist is not None and len(hist) >= 130:
            last = float(hist["close"].iloc[-1])
            ratio = rt["pre_close"] / last if last > 0 else 1
            seg = hist.tail(130)
            hi = float(seg["high"].max()) * ratio
            lo = float(seg["low"].min()) * ratio
            if hi > lo:
                pos = (rt["price"] - lo) / (hi - lo) * 100
            dd250 = (rt["price"] / hi - 1) * 100
            ma20 = float(hist["close"].tail(20).mean()) * ratio
            ma20d = (rt["price"] / ma20 - 1) * 100
        else:
            miss += 1
        # 业绩字段
        def gv(col):
            v = r.get(col)
            return v if pd.notna(v) else None
        rows.append({
            "代码": c6, "名称": rt["name"], "公告日": r["ann_date"],
            "来源": r["source"],
            "净利增速下限%": gv("p_change_min") if gv("p_change_min") is not None
            else gv("yoy_net_profit"),
            "净利增速上限%": gv("p_change_max"),
            "现价": rt["price"], "今日涨跌%": rt["chg"],
            "换手%": rt["turnover"], "PE": rt["pe"], "PB": rt["pb"],
            "总市值亿": rt["total_mv"],
            "位置%": round(pos, 1) if pos is not None else None,
            "距130日高%": round(dd250, 1) if dd250 is not None else None,
            "距MA20%": round(ma20d, 1) if ma20d is not None else None,
        })
    print(f"    完成 {len(rows):,} 只（缺数据 {miss}）")
    print()
    if not rows:
        return 1
    A = pd.DataFrame(rows)
    A = A.drop_duplicates(subset=["代码"], keep="first")

    # ---- 6. 筛选
    A["增速"] = A["净利增速下限%"].fillna(A["净利增速上限%"])
    A = A[A["增速"].notna()].copy()

    print("=" * 132)
    print("  一、全部已披露前三季业绩的公司（按增速排序）")
    print("=" * 132)
    print()
    T = A.sort_values("增速", ascending=False)
    print(f"  共 {len(T)} 家  ")
    print()
    print(f"  {'代码':<8}{'名称':<11}{'增速%':>9}{'公告日':<12}{'现价':>8}"
          f"{'今日%':>8}{'位置%':>8}{'距高%':>8}{'市值亿':>9}{'来源':>6}")
    print("  " + "-" * 100)
    for _, r in T.head(40).iterrows():
        ad = r["公告日"].strftime("%Y-%m-%d") if pd.notna(r["公告日"]) else "—"
        pos = f"{r['位置%']:.1f}" if pd.notna(r["位置%"]) else "—"
        dd = f"{r['距130日高%']:.1f}" if pd.notna(r["距130日高%"]) else "—"
        mv = f"{r['总市值亿']:.0f}" if pd.notna(r["总市值亿"]) else "—"
        print(f"  {r['代码']:<8}{str(r['名称'])[:10]:<11}{r['增速']:>9.1f}"
              f"{ad:<12}{r['现价']:>8.2f}{r['今日涨跌%']:>7.2f}%"
              f"{pos:>8}{dd:>8}{mv:>9}{r['来源']:>6}")
    print()

    # ---- 7. 分层
    print("=" * 132)
    print("  二、按「增速 + 位置」分组（找还没涨的）")
    print("=" * 132)
    print()
    T2 = T.dropna(subset=["位置%"]).copy()
    groups = [
        ("A 高增+低位（最优）", (T2["增速"] >= 50) & (T2["位置%"] <= 40)),
        ("B 高增+中位", (T2["增速"] >= 50) & (T2["位置%"] > 40) &
         (T2["位置%"] <= 70)),
        ("C 高增+高位（已涨）", (T2["增速"] >= 50) & (T2["位置%"] > 70)),
        ("D 中增+低位", (T2["增速"] >= 20) & (T2["增速"] < 50) &
         (T2["位置%"] <= 40)),
        ("E 负增长", T2["增速"] < 0),
    ]
    for lab, mask in groups:
        sub = T2[mask]
        print(f"  【{lab}】{len(sub)} 只")
        if len(sub) == 0:
            print()
            continue
        sub = sub.sort_values("增速", ascending=False)
        print(f"    {'代码':<8}{'名称':<11}{'增速%':>9}{'现价':>8}{'今日%':>8}"
              f"{'位置%':>8}{'距高%':>8}{'PE':>9}{'市值亿':>9}")
        print("    " + "-" * 88)
        for _, r in sub.head(15).iterrows():
            pe = f"{r['PE']:.1f}" if pd.notna(r["PE"]) and r["PE"] > 0 else "亏损"
            mv = f"{r['总市值亿']:.0f}" if pd.notna(r["总市值亿"]) else "—"
            print(f"    {r['代码']:<8}{str(r['名称'])[:10]:<11}{r['增速']:>9.1f}"
                  f"{r['现价']:>8.2f}{r['今日涨跌%']:>7.2f}%"
                  f"{r['位置%']:>8.1f}{r['距130日高%']:>8.1f}"
                  f"{pe:>9}{mv:>9}")
        print()

    # ---- 8. 与锐科对比
    print("=" * 132)
    print("  三、锐科激光基准（作为对照）")
    print("=" * 132)
    print()
    print(f"  锐科激光 300747:")
    print(f"    前三季归母 2.570亿  同比 +108.93%")
    print(f"    Q3 单季归母 0.980亿  同比 +96.34%")
    print(f"    公告日: 2026-10-14")
    print(f"    公告后: 10-14 +5.04%、10-16 +6.98%（2日累计 +12.4%）")
    print(f"    同业对比: 大族激光 -9.02%、华工科技 -8.33%（同期）")
    print(f"    → 个股特异，非板块共振")
    print()

    # 保存
    T.to_csv(outdir / f"earnings_driven_{now:%Y%m%d_%H%M}.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/earnings_driven_{now:%Y%m%d_%H%M}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
