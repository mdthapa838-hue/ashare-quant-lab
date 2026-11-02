# -*- coding: utf-8 -*-
"""
step26_gap_scan.py — 底部跳空扫描 + 信号回测

「底部跳空」定义（可调）：
  跳空 = 今日最低价 > 昨日最高价（向上跳空缺口未回补）
  底部 = 满足以下任一：
      · 收盘价处于 120 日区间的下 X 分位
      · 距 120 日高点回撤 < -Y%
      · 收盘价 < MA60

两件事一起做：
  A. 扫描最新交易日的底部跳空标的（输出可执行清单）
  B. 回测该信号的历史有效性（避免「看起来对」）

数据：Tushare daily（全市场），排除 ST/退市/北交所
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
    # 参数
    target_date = "20261015"
    lookback = 130          # 位置判断回看天数
    gap_min = 0.3           # 最小跳空幅度 %
    pos_pct_max = 0.40      # 收盘价需处于区间下 40% 分位内
    dd_min = -15.0          # 或距高点回撤 < -15%
    backtest = "--no-backtest" not in sys.argv

    for a in sys.argv[1:]:
        if a.startswith("--date="):
            target_date = a.split("=")[1]

    cfg = load_config()
    setup_logger("gap", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"
    outdir.mkdir(parents=True, exist_ok=True)

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    print("=" * 120)
    print(f"  底部跳空扫描  目标日 {target_date}")
    print("=" * 120)
    print()

    # ---- 交易日历，确定前一交易日
    cal = pro.trade_cal(exchange="SSE", start_date="20260601",
                        end_date="20261231")
    opens = sorted(cal[cal["is_open"] == 1]["cal_date"].tolist())
    if target_date not in opens:
        print(f"{target_date} 不是交易日")
        available = [d for d in opens if d <= target_date]
        if not available:
            return 1
        target_date = available[-1]
        print(f"改用最近交易日: {target_date}")
        print()
    idx = opens.index(target_date)
    prev_date = opens[idx - 1]
    print(f"  当前交易日: {target_date}")
    print(f"  前一交易日: {prev_date}")
    print()

    # ---- 股票基础信息
    print("[1] 获取股票基础信息...")
    basic = None
    for st in ("L", "D", "P"):
        for attempt in range(3):
            try:
                b = pro.stock_basic(exchange="", list_status=st,
                                    fields="ts_code,symbol,name,area,industry,"
                                           "market,list_date")
                if b is not None and len(b):
                    b["list_status"] = st
                    basic = b if basic is None else pd.concat([basic, b])
                break
            except Exception:
                time.sleep(1.0)
    if basic is None:
        print("基础信息失败")
        return 1
    basic = basic.drop_duplicates(subset=["ts_code"])
    basic["code6"] = basic["ts_code"].str[:6]
    print(f"    {len(basic)} 只")

    # 排除：ST/退、北交所、上市不足 120 交易日
    bad = basic["name"].str.contains("ST|退", na=False)
    bj = basic["ts_code"].str.endswith(".BJ")
    basic = basic[~bad & ~bj].copy()
    cutoff = pd.Timestamp(target_date) - pd.Timedelta(days=200)
    ld = pd.to_datetime(basic["list_date"], format="%Y%m%d", errors="coerce")
    basic = basic[ld <= cutoff].copy()
    print(f"    过滤后 {len(basic)} 只（排除ST/退/北交所/次新）")
    print()

    # ---- 拉取日线（用本地缓存优先，缺的用 Tushare）
    print("[2] 加载日线...")
    kdir = cache / "kline"
    kdir_d = cache / "kline_delisted"
    codes = basic["code6"].tolist()
    data = {}
    need_api = []
    t0 = time.time()
    for c6 in codes:
        got = None
        for d in (kdir, kdir_d):
            p = d / f"{c6}_qfq.csv"
            if p.exists():
                try:
                    df = pd.read_csv(p, usecols=lambda c: c in
                                     ("date", "open", "high", "low", "close",
                                      "volume", "amount"))
                    if len(df) > 200:
                        df["date"] = pd.to_datetime(df["date"])
                        got = df.sort_values("date")
                    break
                except Exception:
                    pass
        if got is not None:
            # 检查是否已包含目标日
            if got["date"].max() >= pd.Timestamp(target_date):
                data[c6] = got
            else:
                need_api.append(c6)
        else:
            need_api.append(c6)
    print(f"    本地可用 {len(data)} 只   需补 {len(need_api)} 只  "
          f"({time.time()-t0:.0f}s)")

    # 补下载（用 Tushare，只补最近的）
    if need_api:
        print(f"[3] 补下载 {len(need_api)} 只...")
        start = (pd.Timestamp(target_date) -
                 pd.Timedelta(days=260)).strftime("%Y%m%d")
        ok = 0
        for i, c6 in enumerate(need_api):
            # 找 ts_code
            row = basic[basic["code6"] == c6]
            if len(row) == 0:
                continue
            tc = row["ts_code"].iloc[0]
            got = None
            for attempt in range(2):
                try:
                    got = pro.daily(ts_code=tc, start_date=start,
                                    end_date=target_date)
                    break
                except Exception:
                    time.sleep(0.6)
            if got is None or len(got) < 100:
                continue
            got = got.copy()
            got["date"] = pd.to_datetime(got["trade_date"], format="%Y%m%d")
            # 前复权（用 adj_factor）
            af = None
            for attempt in range(2):
                try:
                    af = pro.adj_factor(ts_code=tc, start_date=start,
                                        end_date=target_date)
                    break
                except Exception:
                    time.sleep(0.6)
            if af is not None and len(af):
                af["trade_date"] = af["trade_date"].astype(str)
                got["trade_date"] = got["trade_date"].astype(str)
                got = got.merge(af[["trade_date", "adj_factor"]],
                                on="trade_date", how="left")
                last = got["adj_factor"].iloc[-1]
                if pd.notna(last) and last > 0:
                    got["adj_factor"] = got["adj_factor"] / last
                    for c in ("open", "high", "low", "close"):
                        got[c] = got[c] * got["adj_factor"]
            got = got.sort_values("date").reset_index(drop=True)
            for c in ("open", "high", "low", "close"):
                got[c] = pd.to_numeric(got[c], errors="coerce")
            data[c6] = got[["date", "open", "high", "low", "close"] +
                           ([c for c in ("vol", "amount") if c in got.columns])]
            ok += 1
            if (i + 1) % 200 == 0:
                print(f"    {i+1}/{len(need_api)}  补成功 {ok}", flush=True)
        print(f"    补下载完成: {ok} 只")
    print(f"    合计可用 {len(data)} 只")
    print()

    # ---- 扫描
    print("=" * 120)
    print("  底部跳空扫描结果")
    print("=" * 120)
    print()
    print(f"  条件：")
    print(f"    · 向上跳空：今日最低 > 昨日最高，幅度 >= {gap_min}%")
    print(f"    · 底部位置：收盘处于 {lookback} 日区间下 {int(pos_pct_max*100)}% 分位"
          f"  或  距 {lookback} 日高点 <= {dd_min}%")
    print()

    hits = []
    for c6, d in data.items():
        try:
            d = d.sort_values("date").reset_index(drop=True)
            m = d["date"] == pd.Timestamp(target_date)
            if not m.any():
                continue
            i = int(np.where(m.values)[0][0])
            if i < lookback + 1:
                continue
            t = d.iloc[i]
            y = d.iloc[i - 1]
            if not (np.isfinite(t["low"]) and np.isfinite(y["high"])):
                continue
            if y["high"] <= 0:
                continue
            # 跳空
            if t["low"] <= y["high"]:
                continue
            gap = (t["low"] / y["high"] - 1) * 100
            if gap < gap_min:
                continue
            # 位置
            seg = d.iloc[i - lookback:i + 1]
            hi = seg["high"].max()
            lo = seg["low"].min()
            if not np.isfinite(hi) or not np.isfinite(lo) or hi <= lo:
                continue
            pos = (t["close"] - lo) / (hi - lo)
            dd = (t["close"] / hi - 1) * 100
            if not (pos <= pos_pct_max or dd <= dd_min):
                continue
            # 其他信息
            ma60 = d["close"].iloc[i - 59:i + 1].mean()
            vol20 = None
            if "volume" in d.columns:
                v = pd.to_numeric(d["volume"], errors="coerce")
                if i >= 20:
                    vol20 = v.iloc[i] / v.iloc[i - 19:i + 1].mean() \
                        if v.iloc[i - 19:i + 1].mean() > 0 else None
            info = basic[basic["code6"] == c6]
            nm = info["name"].iloc[0] if len(info) else "?"
            ind = info["industry"].iloc[0] if len(info) else "?"
            hits.append({
                "代码": c6,
                "名称": nm,
                "行业": ind,
                "昨收": round(float(y["close"]), 2),
                "昨高": round(float(y["high"]), 2),
                "今开": round(float(t["open"]), 2),
                "今低": round(float(t["low"]), 2),
                "今收": round(float(t["close"]), 2),
                "跳空%": round(gap, 2),
                "当日涨跌%": round((t["close"] / y["close"] - 1) * 100, 2),
                "位置分位%": round(pos * 100, 1),
                "距高点%": round(dd, 1),
                "距MA60%": round((t["close"] / ma60 - 1) * 100, 1),
                "量比": round(vol20, 2) if vol20 else None,
            })
        except Exception:
            continue

    H = pd.DataFrame(hits)
    if len(H) == 0:
        print("  未找到符合条件的标的")
        return 1

    H = H.sort_values("跳空%", ascending=False)
    print(f"  找到 {len(H)} 只")
    print()
    print(f"  {'代码':<8}{'名称':<10}{'行业':<10}{'昨收':>7}{'今开':>7}"
          f"{'今低':>7}{'今收':>7}{'跳空%':>8}{'涨跌%':>8}"
          f"{'位置%':>7}{'距高%':>8}{'量比':>7}")
    print("  " + "-" * 116)
    for _, r in H.iterrows():
        qb = f"{r['量比']:.2f}" if pd.notna(r["量比"]) else "—"
        print(f"  {r['代码']:<8}{r['名称']:<10}{str(r['行业'])[:9]:<10}"
              f"{r['昨收']:>7.2f}{r['今开']:>7.2f}{r['今低']:>7.2f}"
              f"{r['今收']:>7.2f}{r['跳空%']:>7.2f}%{r['当日涨跌%']:>7.2f}%"
              f"{r['位置分位%']:>6.1f}%{r['距高点%']:>7.1f}%{qb:>7}")
    print()

    H.to_csv(outdir / f"gap_scan_{target_date}.csv", index=False,
             encoding="utf-8-sig")

    # ---- 回测：底部跳空信号是否有效
    if backtest and len(data) > 500:
        print("=" * 120)
        print("  信号回测：底部跳空之后的走势（历史统计）")
        print("=" * 120)
        print()
        print("  说明：在全历史上找所有「底部跳空」事件，统计之后 5/10/20/60 日收益")
        print()
        rows = []
        sample_step = 3   # 采样：每3个交易日检查一次（加速）
        code_list = list(data.keys())
        for ci, c6 in enumerate(code_list):
            try:
                d = data[c6].sort_values("date").reset_index(drop=True)
                if len(d) < lookback + 80:
                    continue
                lows = d["low"].values
                highs = d["high"].values
                closes = d["close"].values
                dates = d["date"].values
                for i in range(lookback, len(d) - 61, sample_step):
                    if not (np.isfinite(lows[i]) and np.isfinite(highs[i - 1])):
                        continue
                    if highs[i - 1] <= 0 or lows[i] <= highs[i - 1]:
                        continue
                    gap = (lows[i] / highs[i - 1] - 1) * 100
                    if gap < gap_min:
                        continue
                    seg_hi = np.nanmax(highs[i - lookback:i + 1])
                    seg_lo = np.nanmin(lows[i - lookback:i + 1])
                    if not np.isfinite(seg_hi) or seg_hi <= seg_lo:
                        continue
                    pos = (closes[i] - seg_lo) / (seg_hi - seg_lo)
                    dd = (closes[i] / seg_hi - 1) * 100
                    if not (pos <= pos_pct_max or dd <= dd_min):
                        continue
                    # 前瞻（从下一日开盘买入，避免用当日收盘）
                    if i + 1 >= len(d):
                        continue
                    p0 = d["open"].iloc[i + 1]
                    if not np.isfinite(p0) or p0 <= 0:
                        continue
                    r = {"date": pd.Timestamp(dates[i]), "code": c6,
                         "gap": gap, "pos": pos, "dd": dd}
                    for hz in (5, 10, 20, 60):
                        j = i + 1 + hz
                        r[f"f{hz}"] = (closes[j] / p0 - 1) * 100 \
                            if j < len(d) else np.nan
                    # 期间最大浮亏
                    j2 = min(i + 1 + 20, len(d) - 1)
                    r["mdd20"] = (np.nanmin(lows[i + 1:j2 + 1]) / p0 - 1) * 100
                    rows.append(r)
            except Exception:
                continue
            if (ci + 1) % 500 == 0:
                print(f"    ... {ci+1}/{len(code_list)}  事件 {len(rows)}",
                      flush=True)

        if rows:
            G = pd.DataFrame(rows)
            print()
            print(f"  历史事件总数: {len(G):,}")
            print(f"  时间范围: {G['date'].min().date()} ~ {G['date'].max().date()}")
            print()
            print(f"  {'持有期':<8}{'样本':>9}{'平均%':>9}{'中位%':>9}"
                  f"{'胜率':>8}{'最好%':>9}{'最差%':>9}")
            print("  " + "-" * 62)
            for hz in (5, 10, 20, 60):
                v = G[f"f{hz}"].dropna()
                if len(v):
                    print(f"  {hz}日{'':<5}{len(v):>9}{v.mean():>9.2f}"
                          f"{v.median():>9.2f}{(v>0).mean()*100:>7.1f}%"
                          f"{v.max():>9.1f}{v.min():>9.1f}")
            md = G["mdd20"].dropna()
            if len(md):
                print(f"  {'20日内最大浮亏':<8}{'':>9}{md.mean():>9.2f}"
                      f"{md.median():>9.2f}{'':>8}{md.min():>9.1f}")
            print()

            # 分档：跳空幅度
            print("  按跳空幅度分档（20日收益）:")
            G2 = G.dropna(subset=["f20"]).copy()
            if len(G2) > 100:
                G2["gq"] = pd.qcut(G2["gap"], min(4, G2["gap"].nunique()),
                                   labels=False, duplicates="drop")
                gg = G2.groupby("gq").agg(
                    跳空最小值=("gap", "min"), 跳空最大值=("gap", "max"),
                    样本=("f20", "size"),
                    平均=("f20", "mean"), 中位=("f20", "median"),
                    胜率=("f20", lambda s: (s > 0).mean() * 100)).round(2)
                print(gg.to_string())
            print()

            # 分档：位置
            print("  按位置分位分档（20日收益）:")
            if len(G2) > 100:
                G2["pq"] = pd.qcut(G2["pos"], min(4, G2["pos"].nunique()),
                                   labels=False, duplicates="drop")
                gp = G2.groupby("pq").agg(
                    位置最小=("pos", "min"), 位置最大=("pos", "max"),
                    样本=("f20", "size"),
                    平均=("f20", "mean"), 中位=("f20", "median"),
                    胜率=("f20", lambda s: (s > 0).mean() * 100)).round(3)
                print(gp.to_string())
            print()

            # 与基准对比（同期全样本平均）
            print("  对照：同期全市场平均（所有股票、所有交易日）:")
            allr = []
            for c6 in code_list[:800]:
                try:
                    d = data[c6]
                    cl = d["close"].values
                    for i in range(lookback, len(d) - 21, 10):
                        allr.append((cl[i + 20] / cl[i] - 1) * 100)
                except Exception:
                    pass
            if allr:
                a = np.array(allr)
                print(f"    20日: 平均 {a.mean():.2f}%  中位 {np.median(a):.2f}%  "
                      f"胜率 {(a>0).mean()*100:.1f}%  样本 {len(a):,}")
            print()
            G.to_csv(outdir / f"gap_backtest_{target_date}.csv", index=False,
                     encoding="utf-8-sig")
            print(f"[输出] {outdir}/gap_backtest_{target_date}.csv")

    print(f"[输出] {outdir}/gap_scan_{target_date}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
