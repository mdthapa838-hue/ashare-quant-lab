# -*- coding: utf-8 -*-
"""
step14_market_state.py — 当前市场状态全景

数据维度：
  1. 主要指数（沪深300/中证500/1000/2000/创业板/科创50）
     - 当前点位、距高点回撤、近N日收益
  2. 技术状态：均线位置、布林带、RSI
  3. 估值：PE/PB 分位
  4. 市场宽度：上涨家数、创新高/新低
  5. 资金：成交额、两融余额
  6. 熊市判定：按多维度打分
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger

INDEXES = {
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "932000.CSI": "中证2000",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
}


def get_index_daily(pro, code: str, start: str, end: str) -> pd.DataFrame | None:
    """
    分页拉取指数日线。

    注意：Tushare 单次返回上限约 650 行，需按更短区间分页
    （用 500 天避免边界问题）。
    """
    frames = []
    cur = pd.Timestamp(end)
    s0 = pd.Timestamp(start)
    for _ in range(40):
        seg = max(s0, cur - pd.Timedelta(days=500))
        got = None
        for attempt in range(3):
            try:
                got = pro.index_daily(ts_code=code,
                                      start_date=seg.strftime("%Y%m%d"),
                                      end_date=cur.strftime("%Y%m%d"))
                break
            except Exception:
                time.sleep(1.0 * (attempt + 1))
        if got is None or len(got) == 0:
            break
        frames.append(got)
        earliest = pd.to_datetime(got["trade_date"].min(), format="%Y%m%d")
        if earliest <= s0:
            break
        cur = earliest - pd.Timedelta(days=1)
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d = d.drop_duplicates(subset=["trade_date"]).sort_values("trade_date")
    d["date"] = pd.to_datetime(d["trade_date"], format="%Y%m%d")
    for c in ("open", "high", "low", "close", "vol", "amount"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.reset_index(drop=True)


def tech_state(d: pd.DataFrame) -> dict:
    """技术状态。"""
    c = d["close"]
    out = {}
    for n in (20, 60, 120, 250):
        ma = c.rolling(n, min_periods=n).mean()
        out[f"MA{n}"] = round(float(ma.iloc[-1]), 2)
        out[f"距MA{n}%"] = round(float(c.iloc[-1] / ma.iloc[-1] - 1) * 100, 2)
    # 回撤
    hi_all = c.cummax()
    out["距历史高点%"] = round(float(c.iloc[-1] / hi_all.iloc[-1] - 1) * 100, 2)
    for n, label in ((60, "3月"), (120, "6月"), (250, "1年"), (500, "2年")):
        if len(c) > n:
            hi = c.iloc[-n:].max()
            out[f"距{label}高点%"] = round(float(c.iloc[-1] / hi - 1) * 100, 2)
    # 收益
    for n, lab in ((5, "5日"), (20, "20日"), (60, "60日"), (120, "120日"),
                   (250, "250日")):
        if len(c) > n:
            out[f"{lab}收益%"] = round(
                float(c.iloc[-1] / c.iloc[-1 - n] - 1) * 100, 2)
    # RSI
    diff = c.diff()
    up = diff.clip(lower=0).rolling(14, min_periods=14).mean()
    dn = (-diff.clip(upper=0)).rolling(14, min_periods=14).mean()
    rs = up / dn.replace(0, np.nan)
    out["RSI14"] = round(float(100 - 100 / (1 + rs.iloc[-1])), 1)
    # 布林位置
    mid = c.rolling(20, min_periods=20).mean()
    sd = c.rolling(20, min_periods=20).std(ddof=0)
    out["布林位置%"] = round(
        float((c.iloc[-1] - (mid - 2 * sd).iloc[-1]) /
              ((mid + 2 * sd).iloc[-1] - (mid - 2 * sd).iloc[-1]) * 100), 1)
    # 波动率
    out["年化波动%"] = round(float(c.pct_change().tail(60).std() * np.sqrt(244) * 100), 1)
    return out


def main():
    cfg = load_config()
    setup_logger("mkt", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"
    outdir.mkdir(parents=True, exist_ok=True)

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 120)
    print("  一、当前市场状态全景")
    print("=" * 120)
    print()

    rows = []
    details = {}
    for code, name in INDEXES.items():
        try:
            d = get_index_daily(pro, code, "20100101", "20261231")
            if d is None or len(d) < 300:
                print(f"  {name:<10} 数据不足 ({0 if d is None else len(d)})")
                continue
            t = tech_state(d)
            details[name] = d
            t["指数"] = name
            t["点位"] = round(float(d["close"].iloc[-1]), 2)
            t["最新日期"] = d["date"].iloc[-1].date()
            rows.append(t)
            print(f"  {name:<10} {t['点位']:>9.2f}  "
                  f"距高点 {t.get('距历史高点%'):>7.2f}%  "
                  f"距1年高 {t.get('距1年高点%', 0):>7.2f}%  "
                  f"60日 {t.get('60日收益%', 0):>7.2f}%  "
                  f"250日 {t.get('250日收益%', 0):>7.2f}%  "
                  f"RSI {t.get('RSI14')}")
        except Exception as e:
            print(f"  {name:<10} 失败: {str(e)[:50]}")
    print()

    R = pd.DataFrame(rows)
    if len(R):
        R.to_csv(outdir / "market_state.csv", index=False, encoding="utf-8-sig")

    # ---- 详细技术状态
    print("=" * 120)
    print("  二、主要指数技术状态明细")
    print("=" * 120)
    print()
    for name in ["沪深300", "中证1000", "中证2000", "创业板指"]:
        if name not in details:
            continue
        t = [r for r in rows if r["指数"] == name]
        if not t:
            continue
        t = t[0]
        print(f"【{name}】{t['最新日期']}  点位 {t['点位']}")
        for k in ["距历史高点%", "距1年高点%", "距2年高点%",
                  "MA20", "MA60", "MA120", "MA250",
                  "距MA20%", "距MA60%", "距MA120%", "距MA250%",
                  "5日收益%", "20日收益%", "60日收益%", "120日收益%",
                  "250日收益%", "RSI14", "布林位置%", "年化波动%"]:
            if k in t and pd.notna(t[k]):
                print(f"    {k:<14} {t[k]}")
        print()

    # ---- 市场宽度（用本地K线）
    print("=" * 120)
    print("  三、市场宽度（基于本地K线）")
    print("=" * 120)
    print()
    import glob
    kdir = cache / "kline"
    dates_recent = []
    above20 = above60 = above250 = 0
    total = 0
    rets20 = []
    rets60 = []
    t0 = time.time()
    files = glob.glob(str(kdir / "*.csv"))
    for f in files:
        try:
            d = pd.read_csv(f, usecols=lambda c: c in ("date", "close"))
            if len(d) < 260:
                continue
            d["date"] = pd.to_datetime(d["date"])
            d = d.sort_values("date")
            c = pd.to_numeric(d["close"], errors="coerce")
            if not np.isfinite(c.iloc[-1]):
                continue
            total += 1
            for n, cnt in ((20, "a20"), (60, "a60"), (250, "a250")):
                ma = c.rolling(n, min_periods=n).mean()
                if pd.notna(ma.iloc[-1]) and c.iloc[-1] > ma.iloc[-1]:
                    if n == 20:
                        above20 += 1
                    elif n == 60:
                        above60 += 1
                    else:
                        above250 += 1
            rets20.append(c.iloc[-1] / c.iloc[-21] - 1 if len(c) > 21 else np.nan)
            rets60.append(c.iloc[-1] / c.iloc[-61] - 1 if len(c) > 61 else np.nan)
        except Exception:
            pass
    if total:
        print(f"  有效样本: {total} 只")
        print(f"  站上MA20:  {above20:>5} ({above20/total*100:>5.1f}%)")
        print(f"  站上MA60:  {above60:>5} ({above60/total*100:>5.1f}%)")
        print(f"  站上MA250: {above250:>5} ({above250/total*100:>5.1f}%)")
        r20 = pd.Series(rets20).dropna()
        r60 = pd.Series(rets60).dropna()
        print(f"  近20日中位收益: {r20.median()*100:>6.2f}%   "
              f"正收益占比 {(r20>0).mean()*100:.1f}%")
        print(f"  近60日中位收益: {r60.median()*100:>6.2f}%   "
              f"正收益占比 {(r60>0).mean()*100:.1f}%")
    print(f"  （耗时 {time.time()-t0:.0f}s）")
    print()

    # ---- 熊市判定
    print("=" * 120)
    print("  四、熊市判定打分")
    print("=" * 120)
    print()
    score = 0
    reasons = []

    def add(cond, pts, desc):
        nonlocal score
        if cond:
            score += pts
            reasons.append(f"  ✓ [{pts:>2}分] {desc}")
        else:
            reasons.append(f"  ✗ [ 0分] {desc}")

    if "中证2000" in details:
        d = details["中证2000"]
        c = d["close"]
        dd = float(c.iloc[-1] / c.cummax().iloc[-1] - 1) * 100
        add(dd < -20, 20, f"中证2000 距历史高点 {dd:.1f}%（<-20%）")
        r250 = float(c.iloc[-1] / c.iloc[-251] - 1) * 100 if len(c) > 251 else 0
        add(r250 < -20, 15, f"中证2000 近250日 {r250:.1f}%（<-20%）")
        r60 = float(c.iloc[-1] / c.iloc[-61] - 1) * 100 if len(c) > 61 else 0
        add(r60 < -10, 10, f"中证2000 近60日 {r60:.1f}%（<-10%）")
        ma250 = c.rolling(250, min_periods=250).mean().iloc[-1]
        add(c.iloc[-1] < ma250, 10, f"中证2000 跌破年线")
    if total:
        add(above250 / total < 0.3, 15,
            f"站上年线比例 {above250/total*100:.1f}%（<30%）")
        add(above60 / total < 0.3, 10,
            f"站上季线比例 {above60/total*100:.1f}%（<30%）")

    print("\n".join(reasons))
    print()
    print(f"  熊市得分: {score}/80")
    if score >= 60:
        verdict = "深度熊市"
    elif score >= 40:
        verdict = "确认熊市"
    elif score >= 20:
        verdict = "熊市初期/调整"
    else:
        verdict = "非熊市"
    print(f"  判定: 【{verdict}】")
    print()

    # ---- 保存
    pd.DataFrame({"项目": ["得分", "判定"], "值": [score, verdict]}).to_csv(
        outdir / "bear_score.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/market_state.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
