
# -*- coding: utf-8 -*-
"""disclosure_calendar.py — 财报披露日历 + 预告状态

功能
----
给定一个股票池（或全市场），输出：
  1. 每只股票的「下次财报预约披露日」
  2. 是否已披露（actual_date）
  3. 是否已发业绩预告（查本地台账 forecast_ledger.csv）
  4. 按日期汇总的披露压力日历

数据源
------
  Tushare disclosure_date（预约 + 实际披露日）
  本地 earnings_watch/forecast_ledger.csv（预告状态）

用法
----
    python tools/disclosure_calendar.py                    # 全市场未来 30 天
    python tools/disclosure_calendar.py --pool chain       # 只看产业链底库
    python tools/disclosure_calendar.py --pool file --file xxx.csv
    python tools/disclosure_calendar.py --days 15
"""
import sys, os, argparse
sys.stdout.reconfigure(encoding="utf-8")
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = r"F:\deepseek\股市\1\data\raw"
CACHE = os.path.join(ROOT, "data_cache", "disclosure")
os.makedirs(CACHE, exist_ok=True)


def load_disclosure(refresh=False):
    """读取全市场披露日期缓存。"""
    p = os.path.join(CACHE, "disclosure_all.csv")
    if os.path.exists(p) and not refresh:
        return pd.read_csv(p, dtype={"ts_code": str})
    import credentials, tushare as ts, time
    pro = ts.pro_api(credentials.get_tushare_token())
    rows, off = [], 0
    while True:
        df = None
        for _ in range(3):
            try:
                df = pro.disclosure_date(offset=off, limit=6000)
                break
            except Exception:
                time.sleep(1.2)
        if df is None or len(df) == 0:
            break
        rows.append(df)
        if len(df) < 6000:
            break
        off += 6000
        time.sleep(0.25)
    if not rows:
        return pd.DataFrame()
    d = pd.concat(rows, ignore_index=True).drop_duplicates(
        subset=["ts_code", "end_date"], keep="last")
    d.to_csv(p, index=False, encoding="utf-8-sig")
    return d


def latest_period(d, today):
    """找当前正在披露的报告期：预约日 >= 今天-30 且 earliest。"""
    d = d.copy()
    d["pre"] = pd.to_numeric(d["pre_date"], errors="coerce")
    t = int(today)
    cand = d[(d["pre"] >= t - 5) & (d["actual_date"].isna())]
    if len(cand):
        return cand["end_date"].astype(str).mode().iloc[0]
    cand2 = d[d["pre"] >= t - 90]
    if len(cand2):
        return cand2["end_date"].astype(str).max()
    return d["end_date"].astype(str).max()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="all", choices=["all", "chain", "file"])
    ap.add_argument("--file", default="")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--today", default="")
    args = ap.parse_args()

    today = args.today or pd.Timestamp.now().strftime("%Y%m%d")
    t = int(today)

    d = load_disclosure(args.refresh)
    if len(d) == 0:
        print("披露数据为空")
        return 1
    d["pre"] = pd.to_numeric(d["pre_date"], errors="coerce")
    d["act"] = pd.to_numeric(d["actual_date"], errors="coerce")

    # 股票池
    if args.pool == "chain":
        u = pd.read_csv(os.path.join(ROOT, "output", "ai_chain_full_universe.csv"),
                        dtype={"代码6": str})
        pool = set(u["代码6"].str.zfill(6))
        d = d[d["ts_code"].str[:6].isin(pool)]
    elif args.pool == "file" and args.file:
        f = pd.read_csv(args.file, dtype=str)
        col = "代码" if "代码" in f.columns else ("code" if "code" in f.columns else f.columns[0])
        pool = set(f[col].str[:6].str.zfill(6))
        d = d[d["ts_code"].str[:6].isin(pool)]

    # 名称
    B = pd.read_csv(os.path.join(RAW, "stock_basic_all.csv"), dtype={"ts_code": str})
    nm = dict(zip(B["ts_code"], B["name"]))

    # 预告状态
    led_p = os.path.join(ROOT, "earnings_watch", "forecast_ledger.csv")
    fc_set = {}
    if os.path.exists(led_p):
        L = pd.read_csv(led_p, dtype={"ts_code": str})
        L["end_date"] = L["end_date"].astype(str)
        for r in L.itertuples():
            fc_set[(r.ts_code, r.end_date)] = str(r.ann_date)

    period = latest_period(d, today)
    cur = d[d["end_date"].astype(str) == period].copy()
    print("=" * 96)
    print("  财报披露日历    报告期 %s    今天 %s    股票数 %d" % (period, today, len(cur)))
    print("=" * 96)

    done = cur[cur["act"].notna()]
    pend = cur[cur["act"].isna()].copy()
    print("\n已披露 %d 只 | 未披露 %d 只" % (len(done), len(pend)))

    # 日历
    pend["pre_s"] = pend["pre"].astype("Int64").astype(str)
    print("\n【剩余披露压力】")
    print("%-12s%8s%8s%8s" % ("日期", "当日", "累计", "占未披露"))
    g = pend.groupby("pre_s").size().sort_index()
    cum = 0
    for k, v in g.items():
        cum += v
        if k in ("nan", "<NA>"):
            continue
        print("%-12s%8d%8d%7.1f%%" % (k, v, cum, cum / len(pend) * 100))

    # 近期明细
    hi = t + args.days
    soon = pend[(pend["pre"] >= t) & (pend["pre"] <= hi)].copy()
    if len(soon):
        soon["名称"] = soon["ts_code"].map(nm)
        soon["预约日"] = soon["pre"].astype(int).astype(str)
        soon["预告"] = [fc_set.get((r.ts_code, str(r.end_date)), "") for r in soon.itertuples()]
        soon = soon.sort_values(["pre", "ts_code"])
        print("\n【未来 %d 天待披露】%d 只" % (args.days, len(soon)))
        print("%-12s%-11s%-10s%s" % ("代码", "名称", "预约日", "预告"))
        print("-" * 60)
        for r in soon.itertuples():
            f = r.预告[:8] if r.预告 else "—"
            print("%-12s%-11s%-10s%s" % (r.ts_code, (r.名称 or "?")[:9], r.预约日, f))
    else:
        print("\n未来 %d 天无待披露" % args.days)

    out = os.path.join(CACHE, "calendar_%s.csv" % period)
    cur.to_csv(out, index=False, encoding="utf-8-sig")
    print("\n明细已存: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
