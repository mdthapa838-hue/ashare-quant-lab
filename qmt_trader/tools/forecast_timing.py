
# -*- coding: utf-8 -*-
"""forecast_timing.py — 业绩预告发布时点预测

背景
----
财报有法定预约披露日（Tushare disclosure_date，准确率 99.85%）。
但业绩预告是【触发式披露】——只有净利润变动超 50%、扭亏、首亏等才强制预告，
没有预约日，API 查不到。

本模块用历史规律做推断：同一股票过去 N 年同一报告期的公告日，
取中位数作为今年的预期日。

已验证精度（Walk-forward 检验，n=511）
--------------------------------------
  中位误差 2.5 天
  ≤5 天  67.1%
  ≤10 天 76.1%
  80% 分位 18 天
  90% 分位 33 天

结论：对 2/3 的股票，预测窗口 ±5 天有效；对 76% 的股票，±10 天有效。
      但尾部很长（90% 分位 33 天），不能当作确定日期使用。

用法
----
    python tools/forecast_timing.py --pool chain
    python tools/forecast_timing.py --codes 605358,002008
    python tools/forecast_timing.py --period 0930 --min-years 3
"""
import sys, os, argparse
sys.stdout.reconfigure(encoding="utf-8")
import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FC = r"F:\deepseek\股市\1\scratch\fc_hist\forecast_all.csv"
RAW = r"F:\deepseek\股市\1\data\raw"

PERIOD_NAME = {"0331": "一季报", "0630": "半年报", "0930": "三季报", "1231": "年报"}


def load_forecast():
    F = pd.read_csv(FC, dtype={"ts_code": str, "ann_date": str, "end_date": str})
    F["ann_date"] = F["ann_date"].astype(str).str[:8]
    F["end_date"] = F["end_date"].astype(str).str[:8]
    return F


def build_prediction(F, period_tail="0930", target_year=2026, min_years=2):
    """
    对每只股票，用 target_year 之前的同报告期公告日，算今年预期日。
    返回 DataFrame: ts_code, n_years, pred_date(YYYYMMDD), min_date, max_date, hist_dates
    """
    q = F[F["end_date"].str[-4:] == period_tail].drop_duplicates(
        subset=["ts_code", "end_date"], keep="first").copy()
    q["year"] = q["end_date"].str[:4].astype(int)

    rows = []
    for code, g in q.groupby("ts_code"):
        hist = g[g["year"] < target_year]
        if len(hist) < min_years:
            continue
        # 把历史 mmdd 平移到 target_year 算 dayofyear
        ds = pd.to_datetime(
            str(target_year) + hist["ann_date"].str[4:],
            format="%Y%m%d", errors="coerce").dropna()
        if len(ds) == 0:
            continue
        doy = ds.dt.dayofyear
        med = int(round(doy.median()))
        # 转回日期
        try:
            base = pd.Timestamp(f"{target_year}-01-01")
            pred = (base + pd.Timedelta(days=med - 1)).strftime("%Y%m%d")
            lo = (base + pd.Timedelta(days=int(doy.min()) - 1)).strftime("%Y%m%d")
            hi = (base + pd.Timedelta(days=int(doy.max()) - 1)).strftime("%Y%m%d")
        except Exception:
            continue
        rows.append({
            "ts_code": code,
            "n_years": len(hist),
            "pred_date": pred,
            "min_date": lo,
            "max_date": hi,
            "hist": ",".join(hist.sort_values("year")["ann_date"].tolist()),
        })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="all", choices=["all", "chain", "file"])
    ap.add_argument("--file", default="")
    ap.add_argument("--codes", default="")
    ap.add_argument("--period", default="0930")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--min-years", type=int, default=2)
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    F = load_forecast()
    P = build_prediction(F, args.period, args.year, args.min_years)
    if len(P) == 0:
        print("无可预测股票（历史年数不足）")
        return 1

    # 已发过本年预告的，排除
    cur_key = f"{args.year}{args.period}"
    done = set(F[F["end_date"] == cur_key]["ts_code"])
    P["已发"] = P["ts_code"].isin(done)

    # 池过滤
    if args.codes:
        want = {c.strip()[:6] for c in args.codes.split(",") if c.strip()}
        P = P[P["ts_code"].str[:6].isin(want)]
    elif args.pool == "chain":
        u = pd.read_csv(os.path.join(ROOT, "output", "ai_chain_full_universe.csv"),
                        dtype={"代码6": str})
        P = P[P["ts_code"].str[:6].isin(set(u["代码6"].str.zfill(6)))]
    elif args.pool == "file" and args.file:
        f = pd.read_csv(args.file, dtype=str)
        col = "代码" if "代码" in f.columns else f.columns[0]
        P = P[P["ts_code"].str[:6].isin(set(f[col].astype(str).str[:6].str.zfill(6)))]

    B = pd.read_csv(os.path.join(RAW, "stock_basic_all.csv"), dtype={"ts_code": str})
    nm = dict(zip(B["ts_code"], B["name"]))
    P["名称"] = P["ts_code"].map(nm)

    pend = P[~P["已发"]].copy()
    print("=" * 104)
    print("  业绩预告时点预测    %d年%s    可预测 %d 只 | 已发 %d | 待发 %d"
          % (args.year, PERIOD_NAME.get(args.period, args.period),
             len(P), P["已发"].sum(), len(pend)))
    print("  精度（walk-forward n=511）：中位误差 2.5 天 | ≤5天 67% | ≤10天 76% | 90%分位 33天")
    print("=" * 104)

    if len(pend):
        pend = pend.sort_values(["pred_date", "ts_code"])
        print("\n按预期日排序（前 %d）：" % args.top)
        print("%-12s%-11s%-7s%-11s%-11s%s" % ("代码", "名称", "年数", "预期日", "历史区间", ""))
        print("-" * 92)
        for r in pend.head(args.top).itertuples():
            print("%-12s%-11s%-7d%-11s%-11s"
                  % (r.ts_code, (r.名称 or "?")[:9], r.n_years, r.pred_date,
                     "%s~%s" % (r.min_date[4:], r.max_date[4:])))

    out = os.path.join(ROOT, "data_cache", "forecast_timing_%d%s.csv" % (args.year, args.period))
    P.to_csv(out, index=False, encoding="utf-8-sig")
    print("\n明细已存: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
