# -*- coding: utf-8 -*-
"""
run_backtest.py — 回测主程序

用法：
    python run_backtest.py                      # 使用默认配置
    python run_backtest.py --config xxx.yaml     # 指定配置
    python run_backtest.py --no-market-filter    # 关闭择时（对比用）
    python run_backtest.py --compare             # 跑多组对比

输出：
    output/<timestamp>/
        equity.csv      资金曲线
        trades.csv      交易明细
        metrics.json    绩效指标
        report.xlsx     汇总报告
"""
from __future__ import annotations

import os
import sys
import json
import time
import argparse
import glob

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from lib.data import DataBundle, load_kline, normalize_code
from lib.signals import collect_signals
from lib.backtest import Backtester, performance

try:
    import yaml
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
def load_config(path: str) -> dict:
    if yaml is None:
        raise RuntimeError("需要 PyYAML：pip install pyyaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_cache_dir() -> str:
    """定位已有 K 线缓存目录。"""
    here = os.path.dirname(os.path.abspath(__file__))
    bases = [
        os.path.join(here, "..", "research", "smallcap_20261014", "out"),
        os.path.join(here, "..", "..", "research", "smallcap_20261014", "out"),
        os.path.join(here, "..", "..", "..", "research", "smallcap_20261014", "out"),
    ]
    for b in bases:
        b = os.path.abspath(b)
        for sub in ("kline_cache", "kline"):
            kd = os.path.join(b, sub)
            if os.path.isdir(kd) and len(glob.glob(os.path.join(kd, "*.csv"))) > 100:
                return b
    # 兜底：在 strategy 目录下建缓存
    local = os.path.abspath(os.path.join(here, "cache"))
    os.makedirs(os.path.join(local, "kline_cache"), exist_ok=True)
    return local


def kline_dir(cache_dir: str) -> str:
    """返回实际存放 K 线的子目录（兼容 kline / kline_cache 两种命名）。"""
    for sub in ("kline_cache", "kline"):
        d = os.path.join(cache_dir, sub)
        if os.path.isdir(d) and len(glob.glob(os.path.join(d, "*.csv"))) > 0:
            return d
    d = os.path.join(cache_dir, "kline_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _code_from_file(path: str) -> str | None:
    """从文件名提取6位代码，兼容 {code}.csv / {code}_qfq.csv 等命名。"""
    stem = os.path.basename(path)[:-4]
    for suf in ("_qfq", "_hfq", "_bfq"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
            break
    if stem.isdigit() and len(stem) == 6:
        return stem.zfill(6)
    return None


def load_universe_from_cache(cache_dir: str) -> list[str]:
    """从已有缓存加载标的池。"""
    d = kline_dir(cache_dir)
    out = []
    for f in glob.glob(os.path.join(d, "*.csv")):
        c = _code_from_file(f)
        if c:
            out.append(c)
    return sorted(set(out))


def load_spot(cache_dir: str) -> pd.DataFrame | None:
    """加载全市场快照（用于动态筛池）。"""
    for name in ("spot_mktcap.csv", "spot_all_sina.csv", "spot_close.csv"):
        p = os.path.join(cache_dir, name)
        if os.path.exists(p):
            try:
                df = pd.read_csv(p)
                if "symbol" in df.columns:
                    df["code"] = df["symbol"].str[2:].str.zfill(6)
                elif "代码" in df.columns:
                    df["code"] = df["代码"].astype(str).str.zfill(6)
                else:
                    continue
                if "mktcap" not in df.columns:
                    continue
                return df
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
def run_single(cfg: dict, cache_dir: str, codes: list[str],
               label: str = "strategy", verbose: bool = True):
    """跑一次完整回测。"""
    t0 = time.time()
    bundle = DataBundle(cfg, cache_dir, allow_fetch=True)
    ok = bundle.load_all(codes, verbose=verbose)
    if verbose:
        print(f"[data] 加载 {ok}/{len(codes)} 只  用时 {time.time()-t0:.0f}s")

    sig = collect_signals(bundle, cfg, verbose=verbose)
    if verbose:
        print(f"[signal] 信号 {len(sig)} 个  用时 {time.time()-t0:.0f}s")
        if len(sig):
            print(f"         区间 {sig['signal_date'].min().date()} ~ {sig['signal_date'].max().date()}")

    bt = Backtester(bundle, sig, cfg)
    if verbose:
        print(f"[backtest] 交易日 {len(bt.calendar)}  "
              f"{bt.calendar[0].date()} ~ {bt.calendar[-1].date()}")
    eq, tr = bt.run(verbose=verbose)
    m = performance(eq, tr, cfg, label)
    if verbose:
        print(f"[done] 用时 {time.time()-t0:.0f}s")
    return bundle, sig, eq, tr, m


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="布林带下轨反弹策略回测")
    ap.add_argument("--config", default=os.path.join(HERE, "config", "boll_lower_rebound.yaml"))
    ap.add_argument("--outdir", default=os.path.join(HERE, "output"))
    ap.add_argument("--no-market-filter", action="store_true", help="关闭择时过滤")
    ap.add_argument("--no-monthly-filter", action="store_true", help="关闭月线过滤")
    ap.add_argument("--compare", action="store_true", help="跑多组配置对比")
    ap.add_argument("--limit", type=int, default=0, help="限制标的数量（调试）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.no_market_filter:
        cfg["entry"]["use_market_filter"] = False
    if args.no_monthly_filter:
        cfg["entry"]["use_monthly_filter"] = False

    cache_dir = find_cache_dir()
    print(f"[env] 缓存目录 {cache_dir}")

    codes = load_universe_from_cache(cache_dir)
    if args.limit:
        codes = codes[:args.limit]
    print(f"[env] 标的池 {len(codes)} 只")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    outdir = os.path.join(args.outdir, stamp)
    os.makedirs(outdir, exist_ok=True)

    if args.compare:
        # 配置对比矩阵
        variants = {
            "完整策略": dict(use_market=True, use_monthly=True, use_weekly=False, use_j=False),
            "无择时": dict(use_market=False, use_monthly=True, use_weekly=False, use_j=False),
            "无月线": dict(use_market=True, use_monthly=False, use_weekly=False, use_j=False),
            "仅基础三条件": dict(use_market=False, use_monthly=False, use_weekly=False, use_j=False),
            "加J值": dict(use_market=True, use_monthly=True, use_weekly=False, use_j=True),
            "加周线": dict(use_market=True, use_monthly=True, use_weekly=True, use_j=False),
        }
        allm, all_eq = [], {}
        for lab, sw in variants.items():
            c = json.loads(json.dumps(cfg))
            c["entry"]["use_market_filter"] = sw["use_market"]
            c["entry"]["use_monthly_filter"] = sw["use_monthly"]
            c["entry"]["use_weekly_filter"] = sw["use_weekly"]
            c["entry"]["use_j_filter"] = sw["use_j"]
            print(f"\n{'='*70}\n▶ {lab}\n{'='*70}")
            _, s, e, t, m = run_single(c, cache_dir, codes, label=lab, verbose=True)
            allm.append(m)
            all_eq[lab] = e
            s.to_csv(os.path.join(outdir, f"signals_{lab}.csv"), index=False, encoding="utf-8-sig")
            if len(t):
                t.to_csv(os.path.join(outdir, f"trades_{lab}.csv"), index=False, encoding="utf-8-sig")
        M = pd.DataFrame(allm)
        M.to_csv(os.path.join(outdir, "compare_metrics.csv"), index=False, encoding="utf-8-sig")
        print("\n" + "=" * 100)
        print("【配置对比结果】")
        print("=" * 100)
        cols = ["策略", "总收益%", "CAGR%", "Sharpe", "最大回撤%", "Calmar",
                "交易笔数", "胜率%", "平均单笔%", "盈亏比", "止损率%"]
        cols = [c for c in cols if c in M.columns]
        print(M[cols].to_string(index=False))

    else:
        bundle, sig, eq, tr, m = run_single(cfg, cache_dir, codes, label="strategy")
        eq.to_csv(os.path.join(outdir, "equity.csv"), index=False, encoding="utf-8-sig")
        if len(sig):
            sig.to_csv(os.path.join(outdir, "signals.csv"), index=False, encoding="utf-8-sig")
        if len(tr):
            tr.to_csv(os.path.join(outdir, "trades.csv"), index=False, encoding="utf-8-sig")
        with open(os.path.join(outdir, "metrics.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2, default=str)

        print("\n" + "=" * 80)
        print("【绩效指标】")
        print("=" * 80)
        for k, v in m.items():
            print(f"  {k}: {v}")

        # 基准
        if bundle.index_close is not None:
            ic = bundle.index_close
            d0, d1 = eq["date"].iloc[0], eq["date"].iloc[-1]
            iw = ic[(ic.index >= d0) & (ic.index <= d1)]
            if len(iw) > 10:
                iret = iw.iloc[-1] / iw.iloc[0] - 1
                yrs = (d1 - d0).days / 365.25
                icagr = (iw.iloc[-1] / iw.iloc[0]) ** (1 / yrs) - 1
                print(f"\n  基准（中证2000）: 总收益 {iret*100:.1f}%  CAGR {icagr*100:.2f}%")
                print(f"  超额: {m['CAGR%'] - icagr*100:+.2f}pct/年")

        # 年度
        e2 = eq.set_index("date")["equity"]
        yr = e2.resample("YE").last()
        yret = yr.pct_change()
        yret.iloc[0] = yr.iloc[0] / e2.iloc[0] - 1
        print("\n  【年度收益】")
        for d, v in yret.items():
            print(f"    {d.year}: {v*100:+.1f}%")

    print(f"\n[输出] {outdir}")
    return outdir


if __name__ == "__main__":
    main()
