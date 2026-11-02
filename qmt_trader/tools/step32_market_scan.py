# -*- coding: utf-8 -*-
"""
step32_market_scan.py — 全市场多路线扫描

按 strategy-routing.md 的五条主路线分别筛选，不做统一门槛：
  F 基本面/景气：ROE、毛利、增速、现金流、估值
  T 题材/概念：识别热点方向（用行业+个股涨停聚集度近似）
  S 小盘/供给：市值分位、成交额、换手
  M 情绪/动量：相对强度、量价、位置
  E 事件/修复：深度下跌+催化（增持/回购/订单）

数据源：
  · 腾讯实时行情（全市场，含市值/PE/PB/换手）
  · 本地K线（4199只，算位置/动量/波动）
  · Tushare 财务（PIT，最新期）
"""
from __future__ import annotations

import os
import re
import sys
import time
import glob
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger

LOOKBACK = 130


def fetch_tencent(codes, batch=600):
    """腾讯批量行情，返回 dict。"""
    out = {}
    hdr = {"User-Agent": "Mozilla/5.0"}
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        try:
            r = requests.get(url, headers=hdr, timeout=15)
            r.encoding = "gbk"
            for line in r.text.strip().split("\n"):
                m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
                if not m:
                    continue
                k, v = m.group(1), m.group(2)
                if not v:
                    continue
                p = v.split("~")
                if len(p) < 50:
                    continue
                def f(idx, default=np.nan):
                    try:
                        return float(p[idx]) if p[idx] not in ("", "-") else default
                    except Exception:
                        return default
                out[k] = {
                    "name": p[1], "code": p[2], "price": f(3),
                    "pre_close": f(4), "open": f(5), "vol": f(6),
                    "time": p[30], "chg_pct": f(32), "high": f(33), "low": f(34),
                    "amount": f(37), "turnover": f(38), "pe": f(39),
                    "amplitude": f(43), "circ_mv": f(44), "total_mv": f(45),
                    "pb": f(46), "limit_up": f(47), "limit_down": f(48),
                }
        except Exception:
            pass
        time.sleep(0.12)
    return out


def main():
    cfg = load_config()
    setup_logger("scan", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    now = datetime.now()
    print("=" * 130)
    print(f"  全市场多路线扫描   {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 130)
    print()

    # ---- 代码池
    codes6 = []
    for d in ("kline", "kline_delisted", "kline_full"):
        for f in glob.glob(str(cache / d / "*.csv")):
            stem = os.path.basename(f)[:-4]
            for suf in ("_qfq", "_hfq", "_bfq"):
                if stem.endswith(suf):
                    stem = stem[:-len(suf)]
                    break
            if stem.isdigit() and len(stem) == 6:
                codes6.append(stem)
    codes6 = sorted(set(codes6))

    def to_tx(c6):
        if c6.startswith(("60", "68", "9")):
            return "sh" + c6
        if c6.startswith(("83", "87", "43", "92")):
            return "bj" + c6
        return "sz" + c6
    tx = [to_tx(c) for c in codes6]
    print(f"[1] 代码池 {len(tx)}")

    # ---- 实时行情
    print("[2] 拉取实时行情...")
    t0 = time.time()
    RT = fetch_tencent(tx)
    print(f"    {len(RT):,} 只  ({time.time()-t0:.0f}s)")
    times = [v["time"] for v in RT.values() if v.get("time")]
    dtime = max(set(times), key=times.count) if times else "?"
    print(f"    行情时间 {dtime}")
    print()

    # ---- 行业分类
    ind_map = {}
    ip = cache / "industry" / "code_industry.csv"
    if ip.exists():
        ii = pd.read_csv(ip, dtype={"code6": str})
        ind_map = dict(zip(ii["code6"], ii["l1_name"]))
    print(f"[3] 行业分类 {len(ind_map)} 只")

    # ---- 本地K线指标
    print("[4] 计算技术指标...")
    t0 = time.time()
    rows = []
    for k, rt in RT.items():
        c6 = rt["code"]
        if not c6 or not c6.isdigit():
            continue
        nm = rt["name"]
        if "ST" in nm or "退" in nm or "N " in nm[:2]:
            continue
        if c6.startswith(("83", "87", "43", "92")):
            continue
        p = rt["price"]
        if not (np.isfinite(p) and p > 0):
            continue
        hist = None
        for d in ("kline", "kline_delisted", "kline_full"):
            fp = cache / d / f"{c6}_qfq.csv"
            if fp.exists():
                try:
                    x = pd.read_csv(fp, usecols=lambda c: c in
                                    ("date", "open", "high", "low", "close", "volume"))
                    if len(x) < 120:
                        break
                    x = x.copy()
                    x["date"] = pd.to_datetime(x["date"])
                    hist = x.sort_values("date").reset_index(drop=True)
                except Exception:
                    pass
                break
        if hist is None or len(hist) < 120:
            continue
        # 用实时价/本地末价的比例换算（前复权 → 原始价）
        last_close = float(hist["close"].iloc[-1])
        if last_close <= 0:
            continue
        ratio = rt["pre_close"] / last_close if np.isfinite(rt["pre_close"]) else 1.0
        if not (np.isfinite(ratio) and ratio > 0):
            ratio = 1.0
        seg = hist.tail(LOOKBACK)
        hi = float(seg["high"].max()) * ratio
        lo = float(seg["low"].min()) * ratio
        pos = (p - lo) / (hi - lo) * 100 if hi > lo else np.nan
        dd = (p / hi - 1) * 100 if hi > 0 else np.nan
        ma20 = float(hist["close"].tail(20).mean()) * ratio
        ma60 = float(hist["close"].tail(60).mean()) * ratio
        ma250 = float(hist["close"].tail(250).mean()) * ratio if len(hist) >= 250 else np.nan
        # 动量
        def ret(n):
            if len(hist) > n:
                return (hist["close"].iloc[-1] / hist["close"].iloc[-1 - n] - 1) * 100
            return np.nan
        r20 = ret(20)
        r60 = ret(60)
        r120 = ret(120)
        r250 = ret(250)
        # 波动
        vol60 = float(hist["close"].pct_change().tail(60).std() * np.sqrt(244) * 100)
        # 量能
        v = pd.to_numeric(hist.get("volume"), errors="coerce") if "volume" in hist.columns else None
        vol_ratio = np.nan
        if v is not None and len(v) >= 20:
            avg = v.tail(20).mean()
            if avg > 0:
                vol_ratio = float(v.iloc[-1] / avg)
        # 距年线
        dma250 = (p / ma250 - 1) * 100 if np.isfinite(ma250) and ma250 > 0 else np.nan
        rows.append({
            "代码": c6, "名称": nm, "行业": ind_map.get(c6, "未分类"),
            "现价": p, "涨跌%": rt["chg_pct"], "换手%": rt["turnover"],
            "成交额亿": rt["amount"] / 1e4 if np.isfinite(rt["amount"]) else np.nan,
            "总市值亿": rt["total_mv"] if np.isfinite(rt["total_mv"]) else np.nan,
            "流通市值亿": rt["circ_mv"] if np.isfinite(rt["circ_mv"]) else np.nan,
            "PE": rt["pe"], "PB": rt["pb"],
            "位置%": round(pos, 1) if np.isfinite(pos) else np.nan,
            "距高%": round(dd, 1) if np.isfinite(dd) else np.nan,
            "距MA20%": round((p / ma20 - 1) * 100, 1) if ma20 > 0 else np.nan,
            "距MA60%": round((p / ma60 - 1) * 100, 1) if ma60 > 0 else np.nan,
            "距年线%": round(dma250, 1) if np.isfinite(dma250) else np.nan,
            "20日%": round(r20, 1) if np.isfinite(r20) else np.nan,
            "60日%": round(r60, 1) if np.isfinite(r60) else np.nan,
            "120日%": round(r120, 1) if np.isfinite(r120) else np.nan,
            "250日%": round(r250, 1) if np.isfinite(r250) else np.nan,
            "年化波动%": round(vol60, 1),
            "量比": round(vol_ratio, 2) if np.isfinite(vol_ratio) else np.nan,
        })
    print(f"    {len(rows):,} 只  ({time.time()-t0:.0f}s)")
    print()
    if not rows:
        return 1
    A = pd.DataFrame(rows)
    A.to_csv(outdir / f"market_scan_raw_{now:%Y%m%d_%H%M}.csv", index=False,
             encoding="utf-8-sig")

    # ==========================================================
    # 行业热度（涨停/涨幅聚集度）
    # ==========================================================
    print("=" * 130)
    print("  一、行业热度（按涨停数与平均涨幅）")
    print("=" * 130)
    print()
    A["涨停"] = A["涨跌%"] >= 9.7
    g = A.groupby("行业").agg(
        数量=("代码", "size"),
        涨停数=("涨停", "sum"),
        平均涨幅=("涨跌%", "mean"),
        平均位置=("位置%", "mean"),
        上涨占比=("涨跌%", lambda s: (s > 0).mean() * 100),
    ).round(2)
    g = g[g["数量"] >= 10].sort_values("涨停数", ascending=False)
    print(f"  {'行业':<12}{'数量':>6}{'涨停':>6}{'平均涨幅':>10}"
          f"{'上涨占比':>10}{'平均位置':>10}")
    print("  " + "-" * 58)
    for ind, r in g.head(20).iterrows():
        print(f"  {str(ind)[:11]:<12}{int(r['数量']):>6}{int(r['涨停数']):>6}"
              f"{r['平均涨幅']:>9.2f}%{r['上涨占比']:>9.1f}%{r['平均位置']:>9.1f}%")
    print()
    g.to_csv(outdir / f"industry_heat_{now:%Y%m%d_%H%M}.csv", encoding="utf-8-sig")

    # ==========================================================
    # 多路线筛选
    # ==========================================================
    print("=" * 130)
    print("  二、五条路线候选")
    print("=" * 130)
    print()

    def show(sub, title, note, n=15, cols=None):
        if len(sub) == 0:
            print(f"  【{title}】无候选")
            print()
            return
        print(f"  【{title}】{len(sub)} 只   {note}")
        print()
        cols = cols or ["代码", "名称", "行业", "现价", "涨跌%", "位置%",
                        "距高%", "PE", "总市值亿"]
        cols = [c for c in cols if c in sub.columns]
        hdr = "".join(f"{c:>10}" for c in cols)
        print(f"    {'':<4}{hdr}")
        print("    " + "-" * (4 + 10 * len(cols)))
        for i, (_, r) in enumerate(sub.head(n).iterrows()):
            line = f"    {i+1:<4}"
            for c in cols:
                v = r[c]
                if isinstance(v, (int, float, np.floating)) and np.isfinite(v):
                    line += f"{v:>10.2f}"
                else:
                    line += f"{str(v)[:9]:>10}"
            print(line)
        print()

    # ---- F 基本面/景气
    F = A[(A["PE"] > 0) & (A["PE"] < 30) & (A["PB"] > 0) & (A["PB"] < 4) &
          (A["总市值亿"] > 50) & (A["位置%"] < 40) &
          (A["60日%"] < 0)].copy()
    F = F.sort_values("PE").reset_index(drop=True)
    show(F, "F 基本面/低估值", "PE 0-30 + PB<4 + 市值>50亿 + 位置<40% + 60日下跌")

    # ---- S 小盘/供给
    S = A[(A["总市值亿"] >= 20) & (A["总市值亿"] <= 60) &
          (A["成交额亿"] >= 0.5) & (A["换手%"] >= 1) &
          (A["涨跌%"] > 0)].copy()
    S = S.sort_values("换手%", ascending=False).reset_index(drop=True)
    show(S, "S 小盘活跃", "市值 20-60亿 + 成交额>0.5亿 + 换手>1% + 今日上涨")

    # ---- M 情绪/动量（相对强度）
    M = A[(A["250日%"] > 20) & (A["60日%"] > 0) &
          (A["距高%"] > -10) & (A["涨跌%"] > 0)].copy()
    M = M.sort_values("250日%", ascending=False).reset_index(drop=True)
    show(M, "M 强势动量", "250日 >20% + 60日>0 + 距高点>-10% + 今日上涨")

    # ---- E 事件/超跌修复
    E = A[(A["位置%"] < 15) & (A["距高%"] < -35) &
          (A["涨跌%"] > 2) & (A["量比"] > 1.2)].copy()
    E = E.sort_values("位置%").reset_index(drop=True)
    show(E, "E 超跌修复", "位置<15% + 距高<-35% + 今日涨>2% + 放量")

    # ---- T 题材（热门行业内的活跃股）
    hot = list(g.head(5).index)
    T = A[A["行业"].isin(hot) & (A["涨跌%"] > 3) & (A["换手%"] > 2)].copy()
    T = T.sort_values("涨跌%", ascending=False).reset_index(drop=True)
    show(T, "T 题材（热门行业内）", f"热门行业 {hot} + 涨>3% + 换手>2%")

    # ---- 综合：底部+放量+抗跌（左侧候选）
    L = A[(A["位置%"] < 25) & (A["距高%"] < -30) & (A["60日%"] > -15) &
          (A["涨跌%"] > 1) & (A["总市值亿"] > 30)].copy()
    L = L.sort_values("位置%").reset_index(drop=True)
    show(L, "L 左侧潜伏", "位置<25% + 距高<-30% + 60日>-15%（跌不动了）+ 今日涨")

    # ---- 输出
    A.to_csv(outdir / f"market_scan_{now:%Y%m%d_%H%M}.csv", index=False,
             encoding="utf-8-sig")
    print(f"[输出] {outdir}/market_scan_{now:%Y%m%d_%H%M}.csv")
    print(f"        （{len(A)} 只，含全部指标）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
