# -*- coding: utf-8 -*-
"""
step20_doubt_test.py — 检验「熊市会一直跌」的质疑

用户的质疑非常关键：
  「如果不是历史底部，跌了以后怎么能判断它未来会涨呢？
    现在如果是熊市的话，很容易一直跌对不对？」

这不是需要安慰的问题，是可以用数据检验的命题。本脚本做三件事：

A. 「买了之后还要跌多少、套多久」的完整分布
   - 在 -18%（当前档位）买入后，后续最大浮亏分布
   - 回本所需时间的分布
   - 「几年都回不来」的概率

B. 最关键的问题：长期收益到底从哪来？
   - 分解为：盈利增长（EPS） + 估值变化（PE）
   - 如果靠估值，那均值回归是赌；如果靠盈利，那不依赖底部

C. 用最坏的样本测试：专门挑「大熊市」起点买入
   - 2007、2008、2015、2018 的顶点买入会怎样
   - 结果可能比想象的好，也可能更差 —— 用数据说话

D. 检验"一直跌"到底有多常见
   - 连续下跌年数的分布
   - 最长的连续下跌

数据：沪深300 全收益 + 上证指数长历史
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


def get_index(pro, code, start, end, api="index_daily"):
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
                                        end_date=cur.strftime("%Y%m%d"))
                break
            except Exception:
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
    for c in ("open", "high", "low", "close"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.reset_index(drop=True)


def main():
    cfg = load_config()
    setup_logger("doubt", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    # ---- 数据
    sh = get_index(pro, "000001.SH", "19950101", "20261231")
    hs = get_index(pro, "000300.SH", "20050101", "20261231")
    print("=" * 120)
    print("  检验「熊市会一直跌」")
    print("=" * 120)
    print()
    print(f"上证指数: {len(sh)} 天 ({sh['date'].min().date()} ~ {sh['date'].max().date()})")
    print(f"沪深300 : {len(hs)} 天 ({hs['date'].min().date()} ~ {hs['date'].max().date()})")
    print()

    # =========================================================
    # A. 买了之后还要跌多少、套多久
    # =========================================================
    print("=" * 120)
    print("  A. 「在 -18% 档位买入后」会发生什么（完整分布）")
    print("=" * 120)
    print()

    d = sh.copy()
    c = d["close"]
    hi = c.cummax()
    # 用「距历史高点回撤」定义（长期熊市）
    dd = (c / hi - 1) * 100
    # 距250日高点
    hi250 = c.rolling(250, min_periods=250).max()
    dd250 = (c / hi250 - 1) * 100

    d["dd"] = dd.values
    d["dd250"] = dd250.values
    d["close"] = c.values

    results = []
    dates = d["date"].values
    closes = d["close"].values
    dd250v = d["dd250"].values
    n = len(d)

    # 在 dd250 首次跌破 -18% 时买入
    entry_points = []
    for i in range(1, n):
        if pd.isna(dd250v[i]) or pd.isna(dd250v[i - 1]):
            continue
        if dd250v[i] <= -18 and dd250v[i - 1] > -18:
            entry_points.append(i)
    print(f"  「首次跌破 -18%」的买入点: {len(entry_points)} 个")
    print()

    # 对每个买入点，计算后续
    rows = []
    for i in entry_points:
        p0 = closes[i]
        # 后续最大浮亏（1年内 / 3年内 / 全部）
        for horizon, label in [(250, "1年"), (750, "3年"), (1250, "5年")]:
            j = min(i + horizon, n - 1)
            seg = closes[i:j + 1]
            if len(seg) < 20:
                continue
            mdd = seg.min() / p0 - 1
            rows.append({
                "买入日": pd.Timestamp(dates[i]).date(),
                "horizon": label,
                "区间最大浮亏%": round(mdd * 100, 1),
            })

        # 回本时间：只在「买入后曾跌破买入价」的情况下计算
        # （若买入后立即上涨，不属于「被套」样本）
        back_idx = None
        min_after = closes[i + 1:min(i + 1250, n)].min() if i + 1 < n else np.nan
        was_underwater = np.isfinite(min_after) and min_after < p0
        if was_underwater:
            for k in range(i + 1, n):
                if closes[k] >= p0:
                    back_idx = k - i
                    break
        rows.append({
            "买入日": pd.Timestamp(dates[i]).date(),
            "horizon": "回本",
            "区间最大浮亏%": round((min_after / p0 - 1) * 100, 1)
            if np.isfinite(min_after) else None,
            "回本天数": back_idx,
            "曾浮亏": bool(was_underwater),
        })
    R = pd.DataFrame(rows)
    if len(R):
        print("  【后续最大浮亏分布】")
        for label in ["1年", "3年", "5年"]:
            sub = R[R["horizon"] == label]
            if len(sub) == 0:
                continue
            v = sub["区间最大浮亏%"].dropna()
            print(f"    {label}: 平均 {v.mean():>7.1f}%  中位 {v.median():>7.1f}%  "
                  f"最深 {v.min():>7.1f}%  最浅 {v.max():>7.1f}%")
        print()
        print("  【套牢与回本（只看买入后确实浮亏的样本）】")
        bk = R[R["horizon"] == "回本"].copy()
        if len(bk):
            uw = bk[bk["曾浮亏"] == True]
            print(f"    买入后曾浮亏: {len(uw)}/{len(bk)} "
                  f"({len(uw)/len(bk)*100:.0f}%)")
            if len(uw):
                v = uw["区间最大浮亏%"].dropna()
                print(f"    浮亏幅度: 中位 {v.median():.1f}%  最深 {v.min():.1f}%")
                bb = uw["回本天数"].dropna()
                n_never = uw["回本天数"].isna().sum()
                print(f"    已回本: {len(bb)}/{len(uw)} ({len(bb)/len(uw)*100:.0f}%)")
                if len(bb):
                    print(f"    回本天数: 中位 {bb.median():.0f} 天 "
                          f"({bb.median()/244:.1f} 年)   "
                          f"最长 {bb.max():.0f} 天 ({bb.max()/244:.1f} 年)")
                    print(f"      1年内回本: {(bb<=250).mean()*100:.0f}%")
                    print(f"      2年内回本: {(bb<=500).mean()*100:.0f}%")
                    print(f"      3年内回本: {(bb<=750).mean()*100:.0f}%")
                    print(f"      5年内回本: {(bb<=1250).mean()*100:.0f}%")
                if n_never:
                    print(f"    ⚠ 至今未回本: {n_never} 个")
                print()
                print("    【分时代】")
                uw2 = uw.copy()
                uw2["yr"] = pd.to_datetime(uw2["买入日"]).dt.year
                for lo, hi, lab in [(1995, 2005, "1995-2005"),
                                    (2006, 2015, "2006-2015"),
                                    (2016, 2026, "2016-2026")]:
                    s2 = uw2[(uw2["yr"] >= lo) & (uw2["yr"] <= hi)]
                    if len(s2) == 0:
                        continue
                    b2 = s2["回本天数"].dropna()
                    v2 = s2["区间最大浮亏%"].dropna()
                    if len(b2):
                        print(f"      {lab}: n={len(s2)}  "
                              f"浮亏中位 {v2.median():>6.1f}%  "
                              f"回本中位 {b2.median():>5.0f}天 "
                              f"({b2.median()/244:.1f}年)  "
                              f"最长 {b2.max():>5.0f}天  "
                              f"1年回本率 {(b2<=250).mean()*100:>3.0f}%")
        print()

    # =========================================================
    # B. 长期收益从哪来：盈利 vs 估值
    # =========================================================
    print("=" * 120)
    print("  B. 最关键的问题：长期收益从哪来？（盈利 vs 估值）")
    print("=" * 120)
    print()
    print("  逻辑：")
    print("    指数 = EPS × PE")
    print("    收益来源 = 盈利增长（EPS↑） + 估值变化（PE↑）")
    print("    若靠估值 → 赌均值回归；若靠盈利 → 不依赖底部")
    print()

    # 用中证全指或沪深300 的 PE 数据
    try:
        db = pro.index_dailybasic(ts_code="000300.SH",
                                  start_date="20140101",
                                  end_date="20261231",
                                  fields="trade_date,pe_ttm,pb,total_mv")
        if db is not None and len(db) > 100:
            db["date"] = pd.to_datetime(db["trade_date"], format="%Y%m%d")
            db = db.sort_values("date")
            db["pe_ttm"] = pd.to_numeric(db["pe_ttm"], errors="coerce")
            print(f"  沪深300 PE数据: {len(db)} 天 "
                  f"({db['date'].min().date()} ~ {db['date'].max().date()})")
            print()
            # 找几个时点
            for label, dt in [("2014-01", "2014-01-31"), ("2018-01", "2018-01-31"),
                              ("2019-01", "2019-01-31"), ("2021-02", "2021-02-26"),
                              ("2024-02", "2024-02-29"), ("现在", None)]:
                if dt:
                    sub = db[db["date"] <= pd.Timestamp(dt)]
                else:
                    sub = db
                if len(sub) == 0:
                    continue
                r = sub.iloc[-1]
                px = None
                h = hs[hs["date"] <= r["date"]]
                if len(h):
                    px = float(h["close"].iloc[-1])
                print(f"    {label:<8} PE {r['pe_ttm']:>6.2f}   "
                      f"PB {float(r.get('pb', np.nan)):>5.2f}   "
                      f"沪深300 {px if px else 'N/A'}")
            print()
            # PE 分位
            pe = db["pe_ttm"].dropna()
            cur_pe = float(pe.iloc[-1])
            pct = (pe <= cur_pe).mean() * 100
            print(f"    当前 PE: {cur_pe:.2f}   历史分位: {pct:.1f}%")
            print(f"    PE 范围: {pe.min():.2f} ~ {pe.max():.2f}   中位 {pe.median():.2f}")
            print()

            # 估值贡献分解
            print("  【收益分解示例】")
            print(f"    {'区间':<20}{'指数变化':>10}{'PE变化':>10}"
                  f"{'隐含EPS变化':>12}{'结论'}")
            print("  " + "-" * 76)
            pairs = [("2014-01-31", "2019-01-31"),
                     ("2019-01-31", "2021-02-26"),
                     ("2021-02-26", "2024-02-29"),
                     ("2024-02-29", None)]
            for a, b in pairs:
                sa = db[db["date"] <= pd.Timestamp(a)]
                sb = db if b is None else db[db["date"] <= pd.Timestamp(b)]
                if len(sa) == 0 or len(sb) == 0:
                    continue
                ha = hs[hs["date"] <= pd.Timestamp(a)]
                hb = hs if b is None else hs[hs["date"] <= pd.Timestamp(b)]
                if len(ha) == 0 or len(hb) == 0:
                    continue
                pxa = float(ha["close"].iloc[-1])
                pxb = float(hb["close"].iloc[-1])
                pea = float(sa["pe_ttm"].iloc[-1])
                peb = float(sb["pe_ttm"].iloc[-1])
                idx_chg = pxb / pxa - 1
                pe_chg = peb / pea - 1
                eps_chg = (1 + idx_chg) / (1 + pe_chg) - 1
                tag = ("盈利驱动" if abs(eps_chg) > abs(pe_chg)
                       else "估值驱动")
                bl = b if b else "现在"
                print(f"    {a}~{bl:<10}{idx_chg*100:>9.1f}%{pe_chg*100:>9.1f}%"
                      f"{eps_chg*100:>11.1f}%  {tag}")
            print()
    except Exception as e:
        print(f"  PE 数据获取失败: {str(e)[:80]}")
        print()

    # =========================================================
    # C. 最坏情形：在历史顶点买入
    # =========================================================
    print("=" * 120)
    print("  C. 最坏情形测试：在历史顶点买入会怎样")
    print("=" * 120)
    print()
    peaks = [
        ("2007-10-16", "2007大顶"),
        ("2009-08-04", "2009反弹顶"),
        ("2015-06-12", "2015杠杆牛顶"),
        ("2018-01-24", "2018年初"),
        ("2021-02-18", "2021核心资产顶"),
    ]
    print(f"  {'买入时点':<16}{'买入价':>10}{'1年后':>10}{'3年后':>10}"
          f"{'5年后':>10}{'最惨时':>10}{'至今':>10}")
    print("  " + "-" * 82)
    for dt_s, label in peaks:
        dt = pd.Timestamp(dt_s)
        sub = sh[sh["date"] <= dt]
        if len(sub) == 0:
            continue
        p0 = float(sub["close"].iloc[-1])
        base_i = sub.index[-1]
        # 用位置索引
        pos = sh.index.get_loc(base_i)
        outs = []
        worst = None
        for yrs, hz in [(1, 244), (3, 732), (5, 1220)]:
            j = min(pos + hz, len(sh) - 1)
            outs.append(sh["close"].iloc[j] / p0 - 1)
        seg = sh["close"].iloc[pos:]
        worst = seg.min() / p0 - 1
        now = sh["close"].iloc[-1] / p0 - 1
        print(f"  {label:<16}{p0:>10.0f}{outs[0]*100:>9.1f}%{outs[1]*100:>9.1f}%"
              f"{outs[2]*100:>9.1f}%{worst*100:>9.1f}%{now*100:>9.1f}%")
    print()
    print("  注：未含股息。上证指数股息率约 2-3%/年，持有 5 年累计约 +10~15%")
    print()

    # =========================================================
    # D. 「一直跌」到底多常见
    # =========================================================
    print("=" * 120)
    print("  D. 「一直跌」有多常见？")
    print("=" * 120)
    print()
    # 年度收益
    d2 = sh.copy()
    d2["year"] = d2["date"].dt.year
    yr = d2.groupby("year")["close"].agg(["first", "last"])
    yr["ret"] = yr["last"] / yr["first"] - 1
    # 去掉不完整年份
    full_years = yr[(yr.index >= 1996) & (yr.index <= 2025)]
    print(f"  上证指数年度收益（1996-2025，{len(full_years)} 年）:")
    neg = (full_years["ret"] < 0).sum()
    print(f"    下跌年份: {neg}/{len(full_years)} ({neg/len(full_years)*100:.0f}%)")
    print(f"    上涨年份: {len(full_years)-neg}/{len(full_years)} "
          f"({(len(full_years)-neg)/len(full_years)*100:.0f}%)")
    print()
    # 连续下跌
    runs = []
    cur_run = 0
    for v in full_years["ret"].values:
        if v < 0:
            cur_run += 1
        else:
            if cur_run > 0:
                runs.append(cur_run)
            cur_run = 0
    if cur_run > 0:
        runs.append(cur_run)
    if runs:
        print(f"    连续下跌年数: 最大 {max(runs)} 年   平均 {np.mean(runs):.1f} 年")
        from collections import Counter
        c2 = Counter(runs)
        for k in sorted(c2):
            print(f"      连续{k}年下跌: {c2[k]} 次")
    print()
    # 年度列表
    print("  年度收益明细:")
    line = "    "
    for y, r in full_years["ret"].items():
        line += f"{y}:{r*100:>5.1f}%  "
        if (y - 1995) % 5 == 0:
            print(line)
            line = "    "
    if line.strip():
        print(line)
    print()

    # =========================================================
    # E. 结论
    # =========================================================
    print("=" * 120)
    print("  E. 结论：质疑成立吗？")
    print("=" * 120)
    print()

    # 输出
    if len(R):
        R.to_csv(outdir / "doubt_test.csv", index=False, encoding="utf-8-sig")
        print(f"[输出] {outdir}/doubt_test.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
