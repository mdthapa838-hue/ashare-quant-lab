# -*- coding: utf-8 -*-
"""
step16_bear_playbook.py — 熊市作战手册

三个核心问题：
  Q1 熊市里什么策略能赚钱？
  Q2 跌到什么位置可以抄底？
  Q3 怎么判断底部？

方法：
  A. 定义「熊市状态」（用宽度/趋势/回撤判定，无未来函数）
  B. 在熊市状态下测试多种策略：
     - 空仓（基准）
     - 定投指数
     - 超跌反弹（短持有）
     - 低波动防御
     - 高股息
     - 现金 + 择时抄底
  C. 抄底信号体系：多维度打分 → 回测各阈值下的前瞻收益

数据：全市场（放开池子），2015-2026
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
from step3_engine import load_all_kline


def get_index(pro, code: str, start: str, end: str) -> pd.DataFrame | None:
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
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    return d[["date", "close"]].reset_index(drop=True)


def main():
    cfg = load_config()
    setup_logger("bear", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    # ---- 1. 载入全市场K线（放开池子）
    print("=" * 130)
    print("  熊市作战手册")
    print("=" * 130)
    print()
    print("[1] 载入全市场数据...")
    data = load_all_kline(cache, include_delisted=True)
    print(f"    {len(data)} 只")

    # ---- 2. 指数（用于状态判定）
    print("[2] 载入指数...")
    idx = get_index(pro, "000300.SH", "20140101", "20261231")
    idx2 = get_index(pro, "000852.SH", "20140101", "20261231")
    if idx is None:
        print("指数获取失败")
        return 1
    idx = idx.rename(columns={"close": "hs300"})
    if idx2 is not None:
        idx = idx.merge(idx2.rename(columns={"close": "zz1000"}), on="date",
                        how="outer").sort_values("date")
    print(f"    {len(idx)} 天")

    # ---- 3. 构建「熊市状态」（无未来函数）
    print("[3] 构建熊市状态...")
    ix = idx.set_index("date").sort_index()
    c = ix["hs300"] if "hs300" in ix.columns else ix.iloc[:, 0]
    ma20 = c.rolling(20, min_periods=20).mean()
    ma60 = c.rolling(60, min_periods=60).mean()
    ma250 = c.rolling(250, min_periods=250).mean()
    hi250 = c.rolling(250, min_periods=250).max()
    dd = c / hi250 - 1
    r60 = c / c.shift(60) - 1

    # 熊市：跌破年线 且 距250日高点回撤>15%
    bear = (c < ma250) & (dd < -0.15)
    # 深度熊市：回撤>25%
    deep = (c < ma250) & (dd < -0.25)
    print(f"    熊市天数: {int(bear.sum())}/{len(bear)} "
          f"({bear.mean()*100:.1f}%)")
    print(f"    深度熊市: {int(deep.sum())}/{len(deep)} "
          f"({deep.mean()*100:.1f}%)")

    # ---- 4. 全市场等权指数（更贴近实盘）
    print("[4] 构建全市场等权指数...")
    t0 = time.time()
    px = {}
    all_dates = set()
    for c6, d in data.items():
        ds = pd.to_datetime(d["date"]).tolist()
        cl = d["close"].tolist()
        px[c6] = dict(zip(ds, cl))
        all_dates.update(ds)
    cal = sorted(pd.Timestamp(x) for x in all_dates)
    # 全市场等权日收益（用全部有数据的股票）
    eq_ret = []
    for i, dt in enumerate(cal):
        if i == 0:
            eq_ret.append(np.nan)
            continue
        prev = cal[i - 1]
        rs = []
        for c6, m in px.items():
            p0 = m.get(prev)
            p1 = m.get(dt)
            if p0 and p1 and p0 > 0:
                rs.append(p1 / p0 - 1)
        eq_ret.append(float(np.mean(rs)) if len(rs) > 100 else np.nan)
    EW = pd.DataFrame({"date": cal, "eq_ret": eq_ret})
    EW["eq_equity"] = (1 + EW["eq_ret"].fillna(0)).cumprod()
    print(f"    全市场等权: {len(EW)} 天  "
          f"累计 {EW['eq_equity'].iloc[-1]:.2f}x  {time.time()-t0:.0f}s")
    print()

    # ---- 5. 熊市 vs 非熊市 的市场表现
    print("=" * 130)
    print("  一、熊市 vs 非熊市 的全市场表现")
    print("=" * 130)
    print()
    M = EW.merge(ix.reset_index()[["date"] + [c for c in ("hs300", "zz1000")
                                              if c in ix.columns]],
                 on="date", how="left")
    M = M.merge(pd.DataFrame({"date": bear.index, "bear": bear.values}),
                on="date", how="left")
    M = M.merge(pd.DataFrame({"date": deep.index, "deep": deep.values}),
                on="date", how="left")
    M["bear"] = M["bear"].fillna(False)
    M["deep"] = M["deep"].fillna(False)
    # 前瞻收益
    for n in (5, 20, 60, 120, 250):
        M[f"fwd{n}"] = M["eq_equity"].shift(-n) / M["eq_equity"] - 1

    rows = []
    for label, mask in [("全样本", M["date"].notna()),
                        ("熊市", M["bear"]),
                        ("深度熊市", M["deep"]),
                        ("非熊市", ~M["bear"])]:
        sub = M[mask]
        r = {"状态": label, "天数": len(sub),
             "占比%": round(len(sub) / len(M) * 100, 1)}
        for n in (5, 20, 60, 120, 250):
            v = sub[f"fwd{n}"].dropna()
            if len(v):
                r[f"{n}日收益%"] = round(v.mean() * 100, 2)
                r[f"{n}日胜率%"] = round((v > 0).mean() * 100, 1)
        rows.append(r)
    T = pd.DataFrame(rows)
    print(T.to_string(index=False))
    print()

    # ---- 6. 熊市中各策略表现
    print("=" * 130)
    print("  二、熊市中什么策略能赚钱")
    print("=" * 130)
    print()
    print("  说明：全市场等权日收益的所有交易日中，")
    print("        按「熊市状态」分组统计（熊市日的收益 vs 非熊市日）")
    print()
    bear_days = M[M["bear"]]["eq_ret"].dropna()
    nonbear_days = M[~M["bear"]]["eq_ret"].dropna()
    deep_days = M[M["deep"]]["eq_ret"].dropna()
    print(f"  全市场等权日收益:")
    print(f"    熊市日  : 平均 {bear_days.mean()*100:+.3f}%  "
          f"胜率 {(bear_days>0).mean()*100:.1f}%  n={len(bear_days)}")
    print(f"    非熊市日: 平均 {nonbear_days.mean()*100:+.3f}%  "
          f"胜率 {(nonbear_days>0).mean()*100:.1f}%  n={len(nonbear_days)}")
    print(f"    深度熊市: 平均 {deep_days.mean()*100:+.3f}%  "
          f"胜率 {(deep_days>0).mean()*100:.1f}%  n={len(deep_days)}")
    print()

    # 各策略在熊市中的表现
    print("  各策略在【熊市状态】下的日收益:")
    print()
    strat_rows = []

    # 构建各种因子（简化：用波动/规模/动量）
    print("    计算因子...")
    feats = {}
    for c6, d in list(data.items()):
        try:
            if len(d) < 300:
                continue
            close = pd.to_numeric(d["close"], errors="coerce")
            if close.isna().sum() > len(close) * 0.1:
                continue
            ret = close.pct_change()
            feats[c6] = {
                "vol60": ret.rolling(60, min_periods=60).std() * np.sqrt(244) * 100,
                "ret20": close / close.shift(20) - 1,
                "ret60": close / close.shift(60) - 1,
                "amt": pd.to_numeric(d.get("amount", close * 0),
                                     errors="coerce").rolling(20, min_periods=20).mean(),
                "date": pd.to_datetime(d["date"]),
                "close": close,
            }
        except Exception:
            pass
    print(f"    {len(feats)} 只")

    # 按因子分组，看熊市日的前瞻收益
    for fname, direction in [("vol60", "low"), ("vol60", "high"),
                             ("ret20", "low"), ("ret20", "high"),
                             ("ret60", "low"), ("ret60", "high")]:
        # 在熊市日，取因子分位的两极
        pos_rets = []
        neg_rets = []
        for c6, f in feats.items():
            dt_vals = f["date"]
            v = f[fname]
            for i in range(len(dt_vals)):
                dt = dt_vals.iloc[i]
                if dt not in bear.index:
                    continue
                if not bear.loc[dt]:
                    continue
                x = v.iloc[i]
                if pd.isna(x):
                    continue
                # 未来20日收益
                if i + 20 < len(dt_vals):
                    fr = f["close"].iloc[i + 20] / f["close"].iloc[i] - 1
                    if np.isfinite(fr):
                        if direction == "low" and x < -0:
                            pass
                        pos_rets.append((x, fr))
        if len(pos_rets) > 500:
            A = pd.DataFrame(pos_rets, columns=["factor", "fwd20"])
            # 分5档
            A["q"] = pd.qcut(A["factor"], 5, labels=False, duplicates="drop")
            g = A.groupby("q")["fwd20"].agg(["mean", "count"])
            g["mean"] = (g["mean"] * 100).round(2)
            label = f"{fname} " + ("低" if direction == "low" else "高")
            strat_rows.append({
                "因子": fname, "方向": direction,
                "Q1_20日%": g.loc[0, "mean"] if 0 in g.index else None,
                "Q5_20日%": g.loc[4, "mean"] if 4 in g.index else None,
                "样本": int(g["count"].sum()),
            })
    if strat_rows:
        S = pd.DataFrame(strat_rows)
        print()
        print(S.to_string(index=False))
    print()

    # ---- 7. 抄底信号
    print("=" * 130)
    print("  三、抄底信号体系")
    print("=" * 130)
    print()
    print("  构造多维度「底部打分」（每个交易日的 0~N 分）：")
    print()

    sig = pd.DataFrame(index=ix.index)
    sig["hs300"] = c
    sig["ma250"] = ma250
    sig["dd"] = dd
    sig["r60"] = r60
    sig["rsi"] = None

    # 维度1：距250日高点回撤
    sig["s1"] = 0
    sig.loc[dd < -0.15, "s1"] = 1
    sig.loc[dd < -0.25, "s1"] = 2
    sig.loc[dd < -0.35, "s1"] = 3

    # 维度2：跌破年线幅度
    ma_dist = c / ma250 - 1
    sig["s2"] = 0
    sig.loc[ma_dist < -0.05, "s2"] = 1
    sig.loc[ma_dist < -0.12, "s2"] = 2
    sig.loc[ma_dist < -0.20, "s2"] = 3

    # 维度3：60日快速下跌
    sig["s3"] = 0
    sig.loc[r60 < -0.10, "s3"] = 1
    sig.loc[r60 < -0.18, "s3"] = 2
    sig.loc[r60 < -0.25, "s3"] = 3

    # 维度4：RSI
    diff = c.diff()
    up = diff.clip(lower=0).rolling(14, min_periods=14).mean()
    dn = (-diff.clip(upper=0)).rolling(14, min_periods=14).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    sig["rsi"] = rsi
    sig["s4"] = 0
    sig.loc[rsi < 35, "s4"] = 1
    sig.loc[rsi < 25, "s4"] = 2
    sig.loc[rsi < 18, "s4"] = 3

    sig["总得分"] = sig[["s1", "s2", "s3", "s4"]].sum(axis=1)

    print("  各得分的分布与前詹收益（用全市场等权）：")
    print()
    SS = sig.reset_index().merge(EW, on="date", how="inner")
    for n in (20, 60, 120, 250):
        SS[f"fwd{n}"] = SS["eq_equity"].shift(-n) / SS["eq_equity"] - 1

    rows = []
    for score in sorted(SS["总得分"].dropna().unique()):
        sub = SS[SS["总得分"] == score]
        if len(sub) < 20:
            continue
        r = {"得分": int(score), "天数": len(sub)}
        for n in (20, 60, 120, 250):
            v = sub[f"fwd{n}"].dropna()
            if len(v) > 5:
                r[f"{n}日%"] = round(v.mean() * 100, 2)
                r[f"{n}胜率%"] = round((v > 0).mean() * 100, 1)
        rows.append(r)
    D = pd.DataFrame(rows)
    print(D.to_string(index=False))
    print()

    # 高分区域
    print("  得分 >= N 时的表现（抄底阈值测试）:")
    print()
    rows2 = []
    for thr in (4, 5, 6, 7, 8, 9, 10):
        sub = SS[SS["总得分"] >= thr]
        if len(sub) < 10:
            continue
        r = {"阈值": f">={thr}", "触发天数": len(sub)}
        for n in (20, 60, 120, 250):
            v = sub[f"fwd{n}"].dropna()
            if len(v) > 5:
                r[f"{n}日%"] = round(v.mean() * 100, 2)
                r[f"{n}胜率%"] = round((v > 0).mean() * 100, 1)
        # 最大回撤（如果触发后买入）
        if len(sub):
            first = sub["eq_equity"].iloc[0]
            rows2.append(r)
    D2 = pd.DataFrame(rows2)
    if len(D2):
        print(D2.to_string(index=False))
    print()

    # ---- 8. 当前状态打分
    print("=" * 130)
    print("  四、当前（2026-10-15）抄底得分")
    print("=" * 130)
    print()
    last = sig.iloc[-1]
    print(f"  距250日高点回撤: {last['dd']*100:>7.2f}%   → s1={int(last['s1'])}")
    print(f"  距年线:          {ma_dist.iloc[-1]*100:>7.2f}%   → s2={int(last['s2'])}")
    print(f"  60日收益:        {last['r60']*100:>7.2f}%   → s3={int(last['s3'])}")
    print(f"  RSI14:           {last['rsi']:>7.1f}    → s4={int(last['s4'])}")
    print()
    print(f"  当前得分: {int(last['总得分'])}/12")
    print()

    # ---- 输出
    EW.to_csv(outdir / "market_equalweight.csv", index=False,
              encoding="utf-8-sig")
    sig.reset_index().to_csv(outdir / "bottom_score.csv", index=False,
                             encoding="utf-8-sig")
    print(f"[输出] {outdir}/market_equalweight.csv")
    print(f"[输出] {outdir}/bottom_score.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
