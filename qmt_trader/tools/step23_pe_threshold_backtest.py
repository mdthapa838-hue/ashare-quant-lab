# -*- coding: utf-8 -*-
"""
step23_pe_threshold_backtest.py — PE 阈值买入回测

用户要求：回测「PE < 11.6 买入」之后的胜率

严谨设计：
  1. 数据：沪深300 PE(TTM) 2014-06-12 至今（Tushare index_dailybasic）
     基准：沪深300 价格指数 + 说明（未含股息，另注明）
  2. 事件定义：PE 从 >= 阈值 首次跌破阈值 = 一个买入事件
     （避免每日重复计数造成样本虚高）
  3. 结果统计：从该事件日起的 20/60/120/250/500/750 日前瞻收益、
     期间最大浮亏、回本时间
  4. 三个层次验证：
     A. 事件级（去重叠）—— 最严格
     B. 逐日级 —— 样本多但重叠
     C. 逐笔实盘案例 —— 具体到日期，可人工核查
  5. 多阈值对比：PE 9/10/11/11.6/12/13/14/15
  6. PE 分位阈值对比：20%/30%/40% 分位
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
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def fetch(pro, api, code, start, end, **kw):
    """分页拉取。"""
    frames = []
    cur = pd.Timestamp(end)
    s0 = pd.Timestamp(start)
    for _ in range(60):
        seg = max(s0, cur - pd.Timedelta(days=500))
        got = None
        for attempt in range(3):
            try:
                got = getattr(pro, api)(ts_code=code,
                                        start_date=seg.strftime("%Y%m%d"),
                                        end_date=cur.strftime("%Y%m%d"), **kw)
                break
            except Exception as e:
                time.sleep(0.8 * (attempt + 1))
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
    return d.reset_index(drop=True)


def main():
    cfg = load_config()
    setup_logger("pe", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"
    outdir.mkdir(parents=True, exist_ok=True)

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 130)
    print("  PE 阈值买入回测：PE < 11.6 买入之后的胜率")
    print("=" * 130)
    print()

    # ---- 数据
    print("[1] 获取数据...")
    hs = fetch(pro, "index_daily", "000300.SH", "20050101", "20261231",
               fields="trade_date,close")
    db = fetch(pro, "index_dailybasic", "000300.SH", "20050101", "20261231",
               fields="trade_date,pe_ttm,pb")
    if hs is None or db is None:
        print("数据获取失败")
        return 1
    hs["close"] = pd.to_numeric(hs["close"], errors="coerce")
    for c in ("pe_ttm", "pb"):
        if c in db.columns:
            db[c] = pd.to_numeric(db[c], errors="coerce")
    hs = hs.sort_values("date")
    db = db.sort_values("date")
    print(f"    沪深300: {len(hs)} 天 ({hs['date'].min().date()} ~ {hs['date'].max().date()})")
    print(f"    PE数据 : {len(db)} 天 ({db['date'].min().date()} ~ {db['date'].max().date()})")

    M = hs.merge(db[["date", "pe_ttm", "pb"]], on="date", how="inner")
    M = M.dropna(subset=["pe_ttm"]).sort_values("date").reset_index(drop=True)
    print(f"    合并后: {len(M)} 天 ({M['date'].min().date()} ~ {M['date'].max().date()})")
    print(f"    当前 PE: {M['pe_ttm'].iloc[-1]:.2f}   当前 PB: {M['pb'].iloc[-1]:.2f}")
    print()

    # 计算历史 PE 分位（用全样本，仅用于展示参考）
    pe = M["pe_ttm"]
    print(f"    PE 统计: min {pe.min():.2f}  P10 {pe.quantile(.10):.2f}  "
          f"P20 {pe.quantile(.20):.2f}  P30 {pe.quantile(.30):.2f}  "
          f"中位 {pe.median():.2f}  P80 {pe.quantile(.80):.2f}  max {pe.max():.2f}")
    print()

    close = M["close"].values
    pev = M["pe_ttm"].values
    dates = M["date"].values
    n = len(M)

    # ---- 前瞻工具
    def fwd(i, hz):
        j = i + hz
        if j >= n:
            return np.nan
        return close[j] / close[i] - 1

    def fwd_min(i, hz):
        j = min(i + hz, n - 1)
        if j <= i:
            return np.nan
        return close[i:j + 1].min() / close[i] - 1

    def recovery_days(i, max_days=1500):
        """从买入日回到买入价所需交易日；未回本返回 None。"""
        j = min(i + max_days, n - 1)
        for k in range(i + 1, j + 1):
            if close[k] >= close[i]:
                return k - i
        return None

    # =========================================================
    # A. 事件级回测（去重叠）—— 最严格
    # =========================================================
    print("=" * 130)
    print("  A. 事件级回测（PE 首次跌破阈值，去重叠）")
    print("=" * 130)
    print()
    print("  定义：PE 从 >= 阈值 变为 < 阈值 的那一天 = 买入日")
    print("        （同一次跌破只算一次，避免每日重复计数）")
    print()

    def events(threshold):
        ev = []
        for i in range(1, n):
            if pd.isna(pev[i]) or pd.isna(pev[i - 1]):
                continue
            if pev[i] < threshold and pev[i - 1] >= threshold:
                ev.append(i)
        return ev

    THRESHOLDS = [9.0, 10.0, 11.0, 11.6, 12.0, 13.0, 14.0, 15.0]
    rows_all = []
    for thr in THRESHOLDS:
        ev = events(thr)
        if len(ev) == 0:
            continue
        r = {"阈值": thr, "事件数": len(ev)}
        for hz, lab in [(20, "20日"), (60, "60日"), (120, "120日"),
                        (250, "1年"), (500, "2年"), (750, "3年")]:
            vs = [fwd(i, hz) for i in ev]
            vs = [v for v in vs if np.isfinite(v)]
            if vs:
                r[f"{lab}平均"] = round(np.mean(vs) * 100, 1)
                r[f"{lab}中位"] = round(np.median(vs) * 100, 1)
                r[f"{lab}胜率"] = round(np.mean(np.array(vs) > 0) * 100, 0)
                r[f"{lab}最差"] = round(np.min(vs) * 100, 1)
        # 浮亏
        for hz, lab in [(250, "1年"), (750, "3年")]:
            vs = [fwd_min(i, hz) for i in ev]
            vs = [v for v in vs if np.isfinite(v)]
            if vs:
                r[f"{lab}内最深浮亏"] = round(np.min(vs) * 100, 1)
                r[f"{lab}内浮亏中位"] = round(np.median(vs) * 100, 1)
        # 回本
        rds = [recovery_days(i) for i in ev]
        rds2 = [x for x in rds if x is not None]
        r["未回本数"] = len(rds) - len(rds2)
        if rds2:
            r["回本天数中位"] = int(np.median(rds2))
            r["回本最长天数"] = int(np.max(rds2))
        rows_all.append(r)

    EV = pd.DataFrame(rows_all)
    if len(EV):
        print("  【各阈值汇总】")
        cols = ["阈值", "事件数", "1年平均", "1年中位", "1年胜率", "1年最差",
                "2年平均", "2年胜率", "3年中位", "3年胜率",
                "1年内浮亏中位", "1年内最深浮亏"]
        cols = [c for c in cols if c in EV.columns]
        print(EV[cols].to_string(index=False))
        print()

    # 重点：PE < 11.6
    print("=" * 130)
    print("  【重点】PE < 11.6 买入的完整结果")
    print("=" * 130)
    print()
    ev116 = events(11.6)
    print(f"  历史上 PE 首次跌破 11.6 的次数: {len(ev116)}")
    print()
    if ev116:
        print(f"  {'买入日':<13}{'PE':>7}{'指数':>9}{'20日':>9}{'60日':>9}"
              f"{'120日':>9}{'1年':>9}{'2年':>9}{'3年':>9}{'1年内浮亏':>11}")
        print("  " + "-" * 108)
        for i in ev116:
            vals = [fwd(i, h) for h in (20, 60, 120, 250, 500, 750)]
            md = fwd_min(i, 250)
            def fmt(v):
                return f"{v*100:>8.1f}%" if np.isfinite(v) else f"{'—':>9}"
            print(f"  {str(pd.Timestamp(dates[i]).date()):<13}"
                  f"{pev[i]:>7.2f}{close[i]:>9.0f}"
                  f"{fmt(vals[0])}{fmt(vals[1])}{fmt(vals[2])}"
                  f"{fmt(vals[3])}{fmt(vals[4])}{fmt(vals[5])}"
                  f"{md*100 if np.isfinite(md) else float('nan'):>10.1f}%")
        print()
        # 统计
        print("  统计:")
        for hz, lab in [(20, "20日"), (60, "60日"), (120, "120日"),
                        (250, "1年"), (500, "2年"), (750, "3年")]:
            vs = [fwd(i, hz) for i in ev116]
            vs = [v for v in vs if np.isfinite(v)]
            if vs:
                print(f"    {lab:<6} 样本{len(vs):>3}  平均 {np.mean(vs)*100:>+7.1f}%  "
                      f"中位 {np.median(vs)*100:>+7.1f}%  "
                      f"胜率 {np.mean(np.array(vs)>0)*100:>5.0f}%  "
                      f"最差 {np.min(vs)*100:>+7.1f}%  "
                      f"最好 {np.max(vs)*100:>+7.1f}%")
        print()
        vs = [fwd_min(i, 250) for i in ev116]
        vs = [v for v in vs if np.isfinite(v)]
        if vs:
            print(f"    买入后1年内最大浮亏: 中位 {np.median(vs)*100:+.1f}%  "
                  f"最深 {np.min(vs)*100:+.1f}%")
        rds = [recovery_days(i) for i in ev116]
        rds2 = [x for x in rds if x is not None]
        print(f"    回本: {len(rds2)}/{len(rds)} 已回本"
              + (f"，中位 {int(np.median(rds2))} 交易日"
                 f"（{np.median(rds2)/244:.1f} 年），最长 {int(np.max(rds2))} 交易日"
                 if rds2 else ""))
        print()

    # =========================================================
    # B. 逐日级（样本多，但有重叠）
    # =========================================================
    print("=" * 130)
    print("  B. 逐日级回测（PE < 阈值 的所有交易日，含重叠）")
    print("=" * 130)
    print()
    rows2 = []
    for thr in THRESHOLDS:
        idxs = np.where(pev < thr)[0]
        if len(idxs) < 20:
            continue
        r = {"阈值": thr, "天数": len(idxs),
             "占比%": round(len(idxs) / n * 100, 1)}
        for hz, lab in [(250, "1年"), (750, "3年")]:
            vs = [fwd(i, hz) for i in idxs]
            vs = [v for v in vs if np.isfinite(v)]
            if vs:
                r[f"{lab}平均"] = round(np.mean(vs) * 100, 1)
                r[f"{lab}中位"] = round(np.median(vs) * 100, 1)
                r[f"{lab}胜率"] = round(np.mean(np.array(vs) > 0) * 100, 0)
                r[f"{lab}最差"] = round(np.min(vs) * 100, 1)
        rows2.append(r)
    D2 = pd.DataFrame(rows2)
    if len(D2):
        print(D2.to_string(index=False))
        print()

    # =========================================================
    # C. 分档对比（PE 区间）
    # =========================================================
    print("=" * 130)
    print("  C. PE 区间分档对比")
    print("=" * 130)
    print()
    BANDS = [(0, 9.5), (9.5, 10.5), (10.5, 11.6), (11.6, 12.5),
             (12.5, 13.5), (13.5, 15.0), (15.0, 100)]
    rows3 = []
    print(f"  {'PE区间':<14}{'天数':>7}{'占比':>8}"
          f"{'20日':>9}{'60日':>9}{'1年':>9}{'1年胜率':>9}"
          f"{'2年':>9}{'3年':>9}{'1年内浮亏中位':>14}")
    print("  " + "-" * 104)
    for lo, hi in BANDS:
        idxs = np.where((pev >= lo) & (pev < hi))[0]
        if len(idxs) < 10:
            continue
        def avg(hz):
            vs = [fwd(i, hz) for i in idxs]
            vs = [v for v in vs if np.isfinite(v)]
            return np.mean(vs) * 100 if vs else np.nan
        def win(hz):
            vs = [fwd(i, hz) for i in idxs]
            vs = [v for v in vs if np.isfinite(v)]
            return np.mean(np.array(vs) > 0) * 100 if vs else np.nan
        mds = [fwd_min(i, 250) for i in idxs]
        mds = [v for v in mds if np.isfinite(v)]
        lab = f"{lo:.1f}~{hi:.1f}" if hi < 100 else f">{lo:.1f}"
        print(f"  {lab:<14}{len(idxs):>7}{len(idxs)/n*100:>7.1f}%"
              f"{avg(20):>8.1f}%{avg(60):>8.1f}%{avg(250):>8.1f}%"
              f"{win(250):>8.0f}%{avg(500):>8.1f}%{avg(750):>8.1f}%"
              f"{np.median(mds)*100 if mds else float('nan'):>13.1f}%")
        rows3.append({
            "PE区间": lab, "天数": len(idxs),
            "20日%": round(avg(20), 1), "60日%": round(avg(60), 1),
            "1年%": round(avg(250), 1), "1年胜率%": round(win(250), 0),
            "2年%": round(avg(500), 1), "3年%": round(avg(750), 1),
            "1年内浮亏中位%": round(np.median(mds) * 100, 1) if mds else None,
        })
    print()
    D3 = pd.DataFrame(rows3)
    if len(D3):
        D3.to_csv(outdir / "pe_band_compare.csv", index=False, encoding="utf-8-sig")

    # =========================================================
    # D. PE 分位阈值（滚动5年）
    # =========================================================
    print("=" * 130)
    print("  D. PE 滚动分位阈值（更贴近实盘：用过去5年数据判断贵贱）")
    print("=" * 130)
    print()
    M["pe_pct"] = M["pe_ttm"].rolling(1250, min_periods=500).apply(
        lambda s: (s <= s.iloc[-1]).mean(), raw=False)
    pctv = M["pe_pct"].values
    rows4 = []
    for thr in [0.10, 0.20, 0.30, 0.40, 0.50]:
        idxs = np.where(pctv < thr)[0]
        if len(idxs) < 20:
            continue
        r = {"分位阈值": f"<{int(thr*100)}%", "天数": len(idxs)}
        for hz, lab in [(250, "1年"), (500, "2年"), (750, "3年")]:
            vs = [fwd(i, hz) for i in idxs]
            vs = [v for v in vs if np.isfinite(v)]
            if vs:
                r[f"{lab}平均"] = round(np.mean(vs) * 100, 1)
                r[f"{lab}中位"] = round(np.median(vs) * 100, 1)
                r[f"{lab}胜率"] = round(np.mean(np.array(vs) > 0) * 100, 0)
        mds = [fwd_min(i, 250) for i in idxs]
        mds = [v for v in mds if np.isfinite(v)]
        if mds:
            r["1年内浮亏中位"] = round(np.median(mds) * 100, 1)
        rows4.append(r)
    D4 = pd.DataFrame(rows4)
    if len(D4):
        print(D4.to_string(index=False))
        print()

    # =========================================================
    # E. 结论
    # =========================================================
    print("=" * 130)
    print("  E. 结论")
    print("=" * 130)
    print()
    cur_pe = pev[-1]
    print(f"  当前 PE: {cur_pe:.2f}")
    # 找当前 PE 落在哪个区间
    for lo, hi in BANDS:
        if lo <= cur_pe < hi:
            sub = D3[D3["PE区间"].str.startswith(f"{lo:.1f}~")]
            if len(sub):
                r = sub.iloc[0]
                print(f"  当前落在区间: {r['PE区间']}")
                print(f"    历史天数: {r['天数']}  占比 {r['天数']/n*100:.1f}%")
                print(f"    1年收益: 平均 {r['1年%']}%  胜率 {r['1年胜率%']:.0f}%")
                print(f"    历史同期1年内最大浮亏中位: {r['1年内浮亏中位%']}%")
            break
    print()

    # 保存
    M.to_csv(outdir / "pe_backtest_data.csv", index=False, encoding="utf-8-sig")
    if len(EV):
        EV.to_csv(outdir / "pe_event_backtest.csv", index=False, encoding="utf-8-sig")
    if len(D2):
        D2.to_csv(outdir / "pe_daily_backtest.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/pe_event_backtest.csv")
    print(f"[输出] {outdir}/pe_band_compare.csv")
    print(f"[输出] {outdir}/pe_backtest_data.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
