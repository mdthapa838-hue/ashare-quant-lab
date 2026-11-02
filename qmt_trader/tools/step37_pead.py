# -*- coding: utf-8 -*-
"""
step37_pead.py — 「业绩公告后买入」回测（PEAD 检验）

锐科激光的模式：业绩快报公告（10-14）后股价上涨。
用户问：能否找类似的业绩驱动标的？

这需要检验「财报后漂移效应」（PEAD, Post-Earnings Announcement Drift）：
  在业绩预告/快报公告后买入，是否能获得超额收益？

严谨设计：
  · 用 Tushare forecast（业绩预告）+ express（业绩快报）
  · 按 ann_date 生效（PIT 正确）
  · 在公告后第 1 个交易日开盘买入
  · 前瞻 20/60/120/250 日
  · 按业绩增速分层
  · 与全市场随机对比
"""
from __future__ import annotations

import os
import sys
import glob
import time
from pathlib import Path

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
    setup_logger("pead", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 128)
    print("  「业绩公告后买入」回测（PEAD 检验）")
    print("=" * 128)
    print()

    # ---- 1. 拉取历史业绩预告（分批，避免超时）
    print("[1] 拉取历史业绩预告...")
    t0 = time.time()
    frames = []
    # 按报告期拉取
    periods = ["20250331", "20250630", "20250930", "20251231",
               "20260331", "20260630", "20260930"]
    for period in periods:
        got = None
        for attempt in range(3):
            try:
                got = pro.forecast_vip(period=period)
                break
            except Exception:
                time.sleep(1.5)
        if got is not None and len(got):
            frames.append(got)
            print(f"    {period}: {len(got):,} 条", flush=True)
        time.sleep(0.4)
    if not frames:
        print("    预告获取失败，尝试 forecast")
        try:
            got = pro.forecast(period="20260930")
            if got is not None and len(got):
                frames.append(got)
        except Exception:
            pass
    FC = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    FC = FC.drop_duplicates(subset=["ts_code", "ann_date", "end_date"]) \
        if len(FC) else FC
    print(f"    合计 {len(FC):,} 条  ({time.time()-t0:.0f}s)")

    # 快报
    t0 = time.time()
    eframes = []
    for period in periods:
        got = None
        for attempt in range(2):
            try:
                got = pro.express_vip(period=period)
                break
            except Exception:
                time.sleep(1.0)
        if got is not None and len(got):
            eframes.append(got)
            print(f"    快报 {period}: {len(got):,} 条", flush=True)
        time.sleep(0.3)
    EX = pd.concat(eframes, ignore_index=True) if eframes else pd.DataFrame()
    print(f"    快报合计 {len(EX):,} 条  ({time.time()-t0:.0f}s)")
    print()

    # ---- 2. 组装事件
    events = []
    if len(FC):
        f = FC.copy()
        f["ann_date"] = pd.to_datetime(f["ann_date"].astype(str),
                                       format="%Y%m%d", errors="coerce")
        for c in ("p_change_min", "p_change_max"):
            if c in f.columns:
                f[c] = pd.to_numeric(f[c], errors="coerce")
        f = f.dropna(subset=["ann_date"])
        f["g"] = f["p_change_min"].fillna(f["p_change_max"])
        f = f.dropna(subset=["g"])
        f["src"] = "预告"
        events.append(f[["ts_code", "ann_date", "g", "src"]])
    if len(EX):
        e = EX.copy()
        e["ann_date"] = pd.to_datetime(e["ann_date"].astype(str),
                                       format="%Y%m%d", errors="coerce")
        if "yoy_net_profit" in e.columns:
            e["g"] = pd.to_numeric(e["yoy_net_profit"], errors="coerce")
            e = e.dropna(subset=["ann_date", "g"])
            e["src"] = "快报"
            events.append(e[["ts_code", "ann_date", "g", "src"]])
    if not events:
        print("无业绩事件")
        return 1
    EV = pd.concat(events, ignore_index=True)
    EV = EV.drop_duplicates(subset=["ts_code", "ann_date"])
    print(f"[2] 业绩事件: {len(EV):,}  （{EV['ann_date'].min().date()} ~ "
          f"{EV['ann_date'].max().date()}）")
    print()

    # ---- 3. 载入K线（只需要事件涉及的股票）
    print("[3] 载入K线...")
    t0 = time.time()
    codes = sorted(set(c[:6] for c in EV["ts_code"].unique()))
    data = {}
    for c6 in codes:
        for d in ("kline", "kline_delisted", "kline_full"):
            fp = cache / d / f"{c6}_qfq.csv"
            if fp.exists():
                try:
                    x = pd.read_csv(fp, usecols=lambda c: c in
                                    ("date", "open", "high", "low", "close"))
                    if len(x) >= 60:
                        x = x.copy()
                        x["date"] = pd.to_datetime(x["date"])
                        x = x.sort_values("date").reset_index(drop=True)
                        for c in ("open", "high", "low", "close"):
                            x[c] = pd.to_numeric(x[c], errors="coerce")
                        data[c6] = x
                except Exception:
                    pass
                break
    print(f"    {len(data):,}/{len(codes):,} 只  ({time.time()-t0:.0f}s)")
    print()

    # ---- 4. 匹配
    print("[4] 计算前瞻收益（公告后次日开盘买入）...")
    t0 = time.time()
    rows = []
    for c6, d in data.items():
        ds = pd.to_datetime(d["date"]).values
        cl = d["close"].values
        op = d["open"].values
        lo = d["low"].values
        n = len(d)
        sub = EV[EV["ts_code"].str[:6] == c6]
        for _, r in sub.iterrows():
            ad = np.datetime64(r["ann_date"])
            # 找到公告日之后的第一个交易日
            idx = np.searchsorted(ds, ad, side="right")
            if idx >= n - 2 or idx < 1:
                continue
            p0 = op[idx]
            if not (np.isfinite(p0) and p0 > 0):
                continue
            rec = {"date": pd.Timestamp(ds[idx]), "code": c6,
                   "g": r["g"], "src": r["src"],
                   "ann": r["ann_date"]}
            for hz in (20, 60, 120, 250):
                j = idx + hz
                rec[f"f{hz}"] = (cl[j] / p0 - 1) * 100 if j < n else np.nan
            j2 = min(idx + 60, n - 1)
            rec["mdd60"] = (np.nanmin(lo[idx:j2 + 1]) / p0 - 1) * 100
            rows.append(rec)
    print(f"    匹配: {len(rows):,}  ({time.time()-t0:.0f}s)")
    print()
    if not rows:
        print("无匹配")
        return 1
    E = pd.DataFrame(rows)

    # ---- 5. 分层
    print("=" * 128)
    print("  一、按业绩增速分层（PEAD 检验）")
    print("=" * 128)
    print()
    print(f"  {'增速档':<14}{'样本':>8}{'20日%':>9}{'60日%':>9}{'120日%':>9}"
          f"{'250日%':>9}{'60日胜率':>10}{'250日胜率':>11}{'60日浮亏':>10}")
    print("  " + "-" * 100)
    bands = [
        ("<0%", E["g"] < 0),
        ("0~20%", (E["g"] >= 0) & (E["g"] < 20)),
        ("20~50%", (E["g"] >= 20) & (E["g"] < 50)),
        ("50~100%", (E["g"] >= 50) & (E["g"] < 100)),
        ("100~300%", (E["g"] >= 100) & (E["g"] < 300)),
        ("300%以上", E["g"] >= 300),
    ]
    band_rows = []
    for lab, mask in bands:
        s = E[mask]
        if len(s) < 30:
            continue
        line = f"  {lab:<14}{len(s):>8}"
        r = {"增速档": lab, "样本": len(s)}
        for hz in (20, 60, 120, 250):
            v = s[f"f{hz}"].dropna()
            line += f"{v.mean():>9.2f}"
            r[f"{hz}日%"] = round(v.mean(), 2)
        for hz in (60, 250):
            v = s[f"f{hz}"].dropna()
            line += f"{(v>0).mean()*100:>9.1f}%"
            r[f"{hz}日胜率%"] = round((v > 0).mean() * 100, 1)
        md = s["mdd60"].dropna()
        line += f"{md.mean():>10.2f}"
        r["60日浮亏%"] = round(md.mean(), 2)
        band_rows.append(r)
        print(line)
    print()

    # ---- 6. 十分位
    print("=" * 128)
    print("  二、十分位检验（增速 vs 收益的单调性）")
    print("=" * 128)
    print()
    E2 = E.dropna(subset=["f60"]).copy()
    try:
        E2["q"] = pd.qcut(E2["g"], 10, labels=False, duplicates="drop")
        print(f"  {'十分位':<8}{'增速范围':<26}{'样本':>8}{'60日%':>10}"
              f"{'60日胜率':>11}{'250日%':>10}{'250日胜率':>11}")
        print("  " + "-" * 84)
        for q, g in E2.groupby("q"):
            lo, hi = g["g"].min(), g["g"].max()
            v60 = g["f60"].dropna()
            v250 = g["f250"].dropna()
            print(f"  D{int(q)+1:<7}{f'{lo:.0f}%~{hi:.0f}%':<26}{len(g):>8}"
                  f"{v60.mean():>10.2f}{(v60>0).mean()*100:>10.1f}%"
                  f"{v250.mean():>10.2f}{(v250>0).mean()*100:>10.1f}%")
    except Exception as e:
        print(f"    分层失败: {str(e)[:50]}")
    print()

    # 相关系数
    from scipy import stats
    EE = E.dropna(subset=["f250"])
    if len(EE) > 100:
        r, p = stats.spearmanr(EE["g"], EE["f250"])
        print(f"  Spearman 相关（增速 vs 250日收益）: {r:+.4f}   p值 {p:.4f}")
        EE60 = E.dropna(subset=["f60"])
        r2, p2 = stats.spearmanr(EE60["g"], EE60["f60"])
        print(f"  Spearman 相关（增速 vs 60日收益）:  {r2:+.4f}   p值 {p2:.4f}")
    print()

    # ---- 7. 与随机对比
    print("=" * 128)
    print("  三、与全市场随机对比")
    print("=" * 128)
    print()
    bp = outdir / "breakout_baseline.csv"
    if bp.exists():
        B = pd.read_csv(bp)
        print(f"  {'指标':<14}{'业绩公告后':>12}{'全市场随机':>12}{'超额':>10}")
        print("  " + "-" * 50)
        for hz in (20, 60, 120, 250):
            v1 = E[f"f{hz}"].dropna()
            v2 = B[f"f{hz}"].dropna()
            print(f"  {hz}日收益%{'':<5}{v1.mean():>12.2f}{v2.mean():>12.2f}"
                  f"{v1.mean()-v2.mean():>+10.2f}")
        for hz in (60, 250):
            v1 = E[f"f{hz}"].dropna()
            v2 = B[f"f{hz}"].dropna()
            print(f"  {hz}日胜率%{'':<5}{(v1>0).mean()*100:>12.1f}"
                  f"{(v2>0).mean()*100:>12.1f}"
                  f"{((v1>0).mean()-(v2>0).mean())*100:>+10.1f}")
        v1 = E["mdd60"].dropna()
        v2 = B["mdd60"].dropna()
        print(f"  {'60日浮亏%':<12}{v1.mean():>12.2f}{v2.mean():>12.2f}"
              f"{v1.mean()-v2.mean():>+10.2f}")
    print()

    # ---- 8. 预告 vs 快报
    print("=" * 128)
    print("  四、预告 vs 快报（锐科用的是快报）")
    print("=" * 128)
    print()
    for src in E["src"].unique():
        s = E[E["src"] == src]
        if len(s) < 30:
            continue
        print(f"  【{src}】{len(s):,} 条")
        for hz in (20, 60, 120, 250):
            v = s[f"f{hz}"].dropna()
            if len(v):
                print(f"    {hz:>3}日: 平均 {v.mean():>+7.2f}%   "
                      f"胜率 {(v>0).mean()*100:>5.1f}%")
        md = s["mdd60"].dropna()
        if len(md):
            print(f"    60日浮亏: {md.mean():>+7.2f}%")
        print()

    # 保存
    E.to_csv(outdir / "pead_events.csv", index=False, encoding="utf-8-sig")
    if band_rows:
        pd.DataFrame(band_rows).to_csv(outdir / "pead_bands.csv", index=False,
                                       encoding="utf-8-sig")
    print(f"[输出] {outdir}/pead_events.csv  ({len(E):,})")
    print(f"[输出] {outdir}/pead_bands.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
