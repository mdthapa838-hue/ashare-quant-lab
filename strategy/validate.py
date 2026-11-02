# -*- coding: utf-8 -*-
"""
validate.py — 策略稳健性验证

检验项目：
  1. 时间分割样本外（前70% / 后30%）
  2. 分年度稳定性
  3. 收益集中度（去掉最大盈利后是否仍成立）
  4. 参数敏感性（止损/持仓数/止盈目标）
  5. 幸存者偏差说明与量化
"""
from __future__ import annotations

import os, sys, json, glob, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib.data import DataBundle
from lib.signals import collect_signals
from lib.backtest import Backtester, performance

try:
    import yaml
except ImportError:
    yaml = None


def load_cfg(path=None):
    path = path or os.path.join(HERE, "config", "boll_lower_rebound.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_cache():
    here = HERE
    for b in [os.path.join(here, "..", "research", "smallcap_20261014", "out"),
              os.path.join(here, "..", "..", "research", "smallcap_20261014", "out")]:
        b = os.path.abspath(b)
        for sub in ("kline_cache", "kline"):
            d = os.path.join(b, sub)
            if os.path.isdir(d) and len(glob.glob(os.path.join(d, "*.csv"))) > 100:
                return b
    raise RuntimeError("未找到 K 线缓存")


def run_once(cfg, cache, codes, label="", verbose=False):
    b = DataBundle(cfg, cache, allow_fetch=False)
    b.load_all(codes, verbose=False)
    s = collect_signals(b, cfg, verbose=False)
    bt = Backtester(b, s, cfg)
    eq, tr = bt.run(verbose=False)
    m = performance(eq, tr, cfg, label)
    return b, s, eq, tr, m


def main():
    cache = find_cache()
    codes = [os.path.basename(f)[:-4].zfill(6)
             for f in glob.glob(os.path.join(cache, "kline_cache", "*.csv"))]
    codes.sort()
    print(f"缓存 {cache}\n标的 {len(codes)}")

    outdir = os.path.join(HERE, "validation", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(outdir, exist_ok=True)

    # =============== 1. 时间分割样本外
    print("\n" + "=" * 90)
    print("【1】时间分割样本外（前70% / 后30%）")
    print("=" * 90)
    # 用无择时版本（信号更多，统计更稳）
    cfg = load_cfg()
    cfg["entry"]["use_market_filter"] = False
    cfg["entry"]["use_monthly_filter"] = True

    b, s, eq, tr, m_all = run_once(cfg, cache, codes, "全区间")
    print(f"\n全区间: 交易 {m_all.get('交易笔数',0)}  CAGR {m_all['CAGR%']}%  "
          f"Sharpe {m_all['Sharpe']}  回撤 {m_all['最大回撤%']}%")

    if len(tr):
        tr2 = tr.copy()
        tr2["entry_dt"] = pd.to_datetime(tr2["entry_date"])
        cut = tr2["entry_dt"].quantile(0.70)
        for lab, sub in [("训练段(前70%)", tr2[tr2["entry_dt"] <= cut]),
                         ("测试段(后30%)", tr2[tr2["entry_dt"] > cut])]:
            if len(sub) == 0:
                continue
            r = sub["net_return"]
            w = r[r > 0]
            l = r[r < 0]
            print(f"  {lab}: 交易{len(sub):4d}  平均{r.mean()*100:+6.2f}%  "
                  f"胜率{(r>0).mean()*100:5.1f}%  盈亏比{(w.mean()/abs(l.mean())) if len(l) else np.nan:.2f}")

    # =============== 2. 分年度
    print("\n" + "=" * 90)
    print("【2】分年度稳定性")
    print("=" * 90)
    if len(tr):
        tt = tr.copy()
        tt["year"] = pd.to_datetime(tt["entry_date"]).dt.year
        g = tt.groupby("year").agg(
            笔数=("net_return", "size"),
            平均收益=("net_return", "mean"),
            胜率=("net_return", lambda x: (x > 0).mean()),
            最大盈=("net_return", "max"),
            最大亏=("net_return", "min"),
        )
        g["平均收益"] = (g["平均收益"] * 100).round(2)
        g["胜率"] = (g["胜率"] * 100).round(1)
        g["最大盈"] = (g["最大盈"] * 100).round(1)
        g["最大亏"] = (g["最大亏"] * 100).round(1)
        print(g.to_string())
        g.to_csv(os.path.join(outdir, "yearly.csv"), encoding="utf-8-sig")
        pos_years = (g["平均收益"] > 0).sum()
        print(f"\n正收益年份: {pos_years}/{len(g)}")

    # =============== 3. 收益集中度
    print("\n" + "=" * 90)
    print("【3】收益集中度检验（去掉最大盈利后是否仍成立）")
    print("=" * 90)
    if len(tr):
        r = tr["net_return"].sort_values(ascending=False)
        for n_rm in [0, 1, 3, 5, 10]:
            rr = r.iloc[n_rm:]
            print(f"  去掉最大{n_rm:2d}笔: 平均 {rr.mean()*100:+6.2f}%  胜率 {(rr>0).mean()*100:5.1f}%  "
                  f"笔数 {len(rr)}")
        # 按标的集中度
        byc = tr.groupby("code")["net_return"].agg(["size", "mean", "sum"])
        print(f"\n  涉及标的 {len(byc)} 只")
        print(f"  单只最大交易次数 {byc['size'].max()}")
        top5 = byc.nlargest(5, "sum")
        print(f"  贡献最大5只合计 {top5['sum'].sum()*100:.1f}% (占总收益 {top5['sum'].sum()/tr['net_return'].sum()*100:.1f}%)")

    # =============== 4. 参数敏感性
    print("\n" + "=" * 90)
    print("【4】参数敏感性")
    print("=" * 90)
    rows = []
    for sp in [-0.08, -0.10, -0.12, -0.15, -0.18]:
        c = load_cfg()
        c["entry"]["use_market_filter"] = False
        c["exit"]["stop_loss_pct"] = sp
        try:
            _, _, e, t, m = run_once(c, cache, codes, f"止损{sp*100:.0f}%")
            rows.append({"参数": f"止损{sp*100:.0f}%", "CAGR%": m["CAGR%"], "Sharpe": m["Sharpe"],
                         "回撤%": m["最大回撤%"], "笔数": m.get("交易笔数", 0),
                         "胜率%": m.get("胜率%", np.nan), "单笔%": m.get("平均单笔%", np.nan)})
        except Exception as ex:
            print("  失败", sp, ex)
    for mp in [3, 5, 8, 12]:
        c = load_cfg()
        c["entry"]["use_market_filter"] = False
        c["portfolio"]["max_positions"] = mp
        c["portfolio"]["max_position_pct"] = min(0.9, 70.0 / (mp * 10))
        try:
            _, _, e, t, m = run_once(c, cache, codes, f"持仓{mp}")
            rows.append({"参数": f"持仓{mp}只", "CAGR%": m["CAGR%"], "Sharpe": m["Sharpe"],
                         "回撤%": m["最大回撤%"], "笔数": m.get("交易笔数", 0),
                         "胜率%": m.get("胜率%", np.nan), "单笔%": m.get("平均单笔%", np.nan)})
        except Exception as ex:
            print("  失败", mp, ex)
    for tp in ["upper", "mid"]:
        c = load_cfg()
        c["entry"]["use_market_filter"] = False
        c["exit"]["take_profit"] = tp
        try:
            _, _, e, t, m = run_once(c, cache, codes, f"止盈{tp}")
            rows.append({"参数": f"止盈{tp}", "CAGR%": m["CAGR%"], "Sharpe": m["Sharpe"],
                         "回撤%": m["最大回撤%"], "笔数": m.get("交易笔数", 0),
                         "胜率%": m.get("胜率%", np.nan), "单笔%": m.get("平均单笔%", np.nan)})
        except Exception as ex:
            print("  失败", tp, ex)
    S = pd.DataFrame(rows)
    print(S.to_string(index=False))
    S.to_csv(os.path.join(outdir, "sensitivity.csv"), index=False, encoding="utf-8-sig")

    # =============== 5. 幸存者偏差
    print("\n" + "=" * 90)
    print("【5】幸存者偏差评估")
    print("=" * 90)
    # 用池内标的的"上市时间"分布评估
    life = []
    for c in codes:
        d = b.get(c)
        if d is not None:
            life.append({"code": c, "first": d["date"].min(), "last": d["date"].max(),
                         "bars": len(d)})
    L = pd.DataFrame(life)
    L["first"] = pd.to_datetime(L["first"]); L["last"] = pd.to_datetime(L["last"])
    cur = L["last"].max()
    print(f"  标的池 {len(L)} 只（均为当前存续标的）")
    print(f"  最晚数据日 {cur.date()}")
    print(f"  数据截止在 {cur.date()} 之前的标的: {(L['last'] < cur - pd.Timedelta(days=20)).sum()} 只")
    print()
    print("  ⚠ 本回测使用「当前存续」标的池回看历史，已退市/长期停牌标的未纳入。")
    print("     本策略专买「低位+下跌趋势」的股票，正是退市风险最高的一类，")
    print("     因此实际收益大概率低于回测值。建议自行折价评估（如打 6-8 折）。")

    print(f"\n[输出] {outdir}")


if __name__ == "__main__":
    main()
