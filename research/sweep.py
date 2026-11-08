# -*- coding: utf-8 -*-
"""
research/sweep.py — 策略参数扫描（离线研究脚本，不属于运行时路径）

用途：一次性加载数据，然后对多组参数跑回测，比较绩效。
避免每次实验都重复拉取网络数据。

用法：
    python research/sweep.py --limit 150
    python research/sweep.py --limit 150 --grid market
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tushare_trader.backtest import Backtester  # noqa: E402
from tushare_trader.config import load_config  # noqa: E402
from tushare_trader.data import TushareData  # noqa: E402
from tushare_trader.runner import TradingRunner  # noqa: E402
from tushare_trader.strategy import compute_indicators, market_return_series  # noqa: E402

START = "2018-01-01"
_START = [START]   # 允许 main 覆盖，用于样本外切分
_END = [""]
WARMUP_DAYS = 500


def load_dataset(cfg, limit: int, end: str | None = None) -> dict:
    """加载并缓存指标数据，供多组参数复用。"""
    runner = TradingRunner(cfg)
    trade_date = end or runner.trade_date()
    uni = runner.load_state("universe.json", {})
    codes = uni.get("codes", [])
    if not codes:
        raise SystemExit("标的池为空，先跑 prepare")
    if limit:
        codes = codes[:limit]

    warmup = pd.Timestamp(START) - pd.Timedelta(days=WARMUP_DAYS)
    index = cfg.get("strategy.entry.market_index", "000852.SH")
    window = int(cfg.get("strategy.entry.market_ret_window", 60))
    print(f"[data] 指数 {index} ...", flush=True)
    idx_close = runner.data.index_daily(index, warmup.strftime("%Y%m%d"), trade_date)
    mret = market_return_series(idx_close, window)
    cal = [d for d in runner.calendar if warmup.strftime("%Y%m%d") <= d <= trade_date]

    print(f"[data] {len(codes)} 只标的 ...", flush=True)
    raw = {}
    for i, code in enumerate(codes, 1):
        try:
            df = runner.data.daily(code, warmup.strftime("%Y%m%d"), trade_date, adj="qfq")
            if df is None or len(df) < 160:
                continue
            raw[code] = df
        except Exception as e:
            print(f"  {code}: {e}", flush=True)
        if i % 50 == 0:
            print(f"  {i}/{len(codes)} 成功 {len(raw)}", flush=True)
    return {"raw": raw, "index_close": idx_close, "mret": mret,
            "calendar": cal, "end_date": trade_date}


def indicator_cache(dataset: dict, cfg: dict) -> dict:
    """计算指标列（不含 signal），用于跨参数复用。

    关键：`signal` 是**所有条件的与**，依赖具体参数（如 market_ret_max）。
    因此这里只保留指标列，`signal` 必须在 run_cfg 里按各自参数重新计算，
    否则所有变体都会复用同一份 signal，参数扫描等于没扫。
    """
    out = {}
    for code, df in dataset["raw"].items():
        c = copy.deepcopy(cfg.strategy_cfg)
        c.setdefault("entry", {})["use_market_filter"] = False
        d = compute_indicators(df, c, calendar=dataset["calendar"], market_ret=None)
        d["code"] = code
        out[code] = d
    return out


def recompute_signal(d: pd.DataFrame, strat_cfg: dict, mret) -> pd.DataFrame:
    """按给定参数重新计算**所有** cond_* 列与 signal。

    关键：cond_position / cond_streak / cond_ma60_down 等列的阈值都来自
    entry 配置，指标缓存里存的是默认阈值算出的结果。若只重算
    cond_market_crash，其余条件仍是旧阈值，参数扫描会得到完全相同的结果
    （这个坑已经踩过一次：pos120_max 从 0.2 扫到 0.5，9 个变体结果一模一样）。
    """
    entry = strat_cfg.get("entry", {})
    uni = strat_cfg.get("universe", {})
    out = d.copy()

    # ---- 市场择时
    if entry.get("use_market_filter", True) and mret is not None:
        mr = mret.reindex(out["date"].values, method="ffill")
        out["market_ret"] = mr.values
        out["cond_market_crash"] = (
            out["market_ret"] < float(entry.get("market_ret_max", -20.0))).fillna(False)
    else:
        out["market_ret"] = -999.0
        out["cond_market_crash"] = True

    # ---- 重新按参数生成各条件列
    out["cond_position"] = (
        out["pos120"] < float(entry.get("pos120_max", 0.30))).fillna(False)
    out["cond_streak"] = (
        out["lower_streak"] >= int(entry.get("streak_min", 2))).fillna(False)
    out["cond_ma60_down"] = (
        out["ma60_slope"] < float(entry.get("ma60_slope_max", -0.01))).fillna(False)
    out["cond_monthly_up"] = out["monthly_trend_up"].astype(bool)
    out["cond_liquidity"] = (
        out["amount_ma20"] >= float(uni.get("min_avg_amount", 2000)) * 1e4).fillna(False)
    if entry.get("use_j_filter", False):
        out["cond_j_low"] = (out["J"] < float(entry.get("j_max", 13))).fillna(False)
    else:
        out["cond_j_low"] = True

    cond_cols = [c for c in out.columns if c.startswith("cond_")]
    out["n_ok"] = out[cond_cols].sum(axis=1).astype(int)
    out["n_tot"] = len(cond_cols)
    out["signal"] = out[cond_cols].all(axis=1).astype(bool)
    return out


def run_cfg(base_cfg: dict, indicators: dict, dataset: dict,
            strat_cfg: dict) -> dict:
    """用给定策略配置跑一次回测，返回指标 dict。"""
    bt_cfg = copy.deepcopy(strat_cfg)
    bt_cfg["backtest"] = {"start_date": _START[0], "end_date": _END[0],
                          "risk_free_rate": 0.02}
    data = {k: recompute_signal(v, strat_cfg, dataset["mret"])
            for k, v in indicators.items()}
    bt = Backtester(data, bt_cfg, index_close=dataset["index_close"])
    res = bt.run(verbose=False)
    m = dict(res.metrics)
    m["_trades"] = int(len(res.trades)) if hasattr(res, "trades") else 0
    return m


def summarize(label: str, m: dict) -> str:
    return (f"{label:<34} CAGR {m.get('CAGR%'):>7}  "
            f"总收益 {m.get('总收益%'):>8}  回撤 {m.get('最大回撤%'):>8}  "
            f"Sharpe {str(m.get('Sharpe')):>6}  交易 {m.get('交易笔数'):>3}  "
            f"超额 {m.get('超额CAGR%'):>7}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150)
    ap.add_argument("--grid", default="market")
    ap.add_argument("--end", default=None)
    ap.add_argument("--start", default=None)
    args = ap.parse_args()

    if args.start:
        _START[0] = args.start
    if args.end:
        _END[0] = args.end
    cfg = load_config()
    t0 = time.time()
    ds = load_dataset(cfg, args.limit, args.end)
    print(f"[data] 完成，用时 {time.time() - t0:.0f}s，{len(ds['raw'])} 只", flush=True)

    base = copy.deepcopy(cfg.strategy_cfg)
    ind = indicator_cache(ds, cfg)
    print(f"[ind] 指标就绪，{len(ind)} 只\n", flush=True)

    variants = []
    if args.grid == "market":
        for th in (-20.0, -15.0, -12.0, -10.0, -8.0, -5.0, 0.0):
            c = copy.deepcopy(base)
            c.setdefault("entry", {})["market_ret_max"] = th
            variants.append((f"market_ret_max={th:>6}", c))
    elif args.grid == "off":
        c = copy.deepcopy(base)
        c.setdefault("entry", {})["use_market_filter"] = False
        variants.append(("market_filter=OFF", c))
    elif args.grid == "exit":
        # 出场维度：止盈方式 / 止损 / 持有上限
        variants.append(("baseline", copy.deepcopy(base)))
        for tp in ("upper", "mid"):
            c = copy.deepcopy(base); c.setdefault("exit", {})["take_profit"] = tp
            variants.append((f"take_profit={tp}", c))
        for sl in (-0.08, -0.10, -0.15):
            c = copy.deepcopy(base); c.setdefault("exit", {})["stop_loss_pct"] = sl
            variants.append((f"stop_loss={sl}", c))
        for mh in (20, 40, 90):
            c = copy.deepcopy(base); c.setdefault("exit", {})["max_hold_days"] = mh
            variants.append((f"max_hold={mh}", c))
    elif args.grid == "entry":
        # 入场维度：相对位置 / 连续破轨 / MA60 斜率
        variants.append(("baseline", copy.deepcopy(base)))
        for v in (0.20, 0.40, 0.50):
            c = copy.deepcopy(base); c.setdefault("entry", {})["pos120_max"] = v
            variants.append((f"pos120_max={v}", c))
        for v in (1, 3, 4):
            c = copy.deepcopy(base); c.setdefault("entry", {})["streak_min"] = v
            variants.append((f"streak_min={v}", c))
        for v in (-0.02, 0.0):
            c = copy.deepcopy(base); c.setdefault("entry", {})["ma60_slope_max"] = v
            variants.append((f"ma60_slope_max={v}", c))
    elif args.grid == "portfolio":
        variants.append(("baseline", copy.deepcopy(base)))
        for v in (3, 8, 10):
            c = copy.deepcopy(base); c.setdefault("portfolio", {})["max_positions"] = v
            variants.append((f"max_positions={v}", c))
        for v in (0.15, 0.35, 0.50):
            c = copy.deepcopy(base); c.setdefault("portfolio", {})["max_position_pct"] = v
            variants.append((f"max_position_pct={v}", c))
    else:
        raise SystemExit(f"未知 grid: {args.grid}")

    rows = []
    for label, c in variants:
        m = run_cfg(cfg, ind, ds, c)
        rows.append((label, m))
        print(summarize(label, m), flush=True)

    outdir = ROOT / "research" / "results"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"sweep_{args.grid}.json").write_text(
        json.dumps({lb: m for lb, m in rows}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print(f"\n[done] 结果写入 {outdir}/sweep_{args.grid}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
