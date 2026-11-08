# -*- coding: utf-8 -*-
"""research/ab_split.py — 固定标的池 + 样本内外切分，严格对比

之前几次对比混用了不同的标的池（prepare 会重新生成 universe.json），
数字不可比。本脚本一次加载数据，固定标的池，同时跑 train/test，
所有方案在同一批股票、同一份数据上比较。
"""
from __future__ import annotations
import copy, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import sweep as S
from sweep import indicator_cache, load_dataset, run_cfg
from tushare_trader.config import load_config

TRAIN = ("2018-01-01", "2022-12-31")
TEST = ("2023-01-01", "")
FULL = ("2018-01-01", "")


def make(base, **kw):
    c = copy.deepcopy(base)
    for k, v in kw.items():
        sect, key = k.split("__")
        c[sect][key] = v
    return c


def main():
    cfg = load_config()
    ds = load_dataset(cfg, 150)
    print(f"[data] {len(ds['raw'])} 只（固定标的池）", flush=True)
    ind = indicator_cache(ds, cfg)
    base = copy.deepcopy(cfg.strategy_cfg)

    old = make(base, entry__use_market_filter=True, exit__take_profit="upper",
               exit__max_hold_days=60, portfolio__max_positions=5)
    variants = {
        "A_基线(当前config)": old,
        "B_仅关择时": make(base, entry__use_market_filter=False,
                          exit__take_profit="upper", exit__max_hold_days=60,
                          portfolio__max_positions=5),
        "C_关择时+控回撤": make(base, entry__use_market_filter=False,
                            exit__take_profit="mid", exit__max_hold_days=20,
                            portfolio__max_positions=8),
        "D_关择时+更严止损": make(base, entry__use_market_filter=False,
                             exit__take_profit="upper", exit__max_hold_days=20,
                             exit__stop_loss_pct=-0.08,
                             portfolio__max_positions=6),
    }

    out = {}
    print(f"{'方案':<20}{'期间':<10}{'CAGR%':>8}{'回撤%':>9}{'交易':>6}{'Sharpe':>8}", flush=True)
    for name, sc in variants.items():
        out[name] = {}
        for pname, period in (("train", TRAIN), ("test", TEST), ("full", FULL)):
            S._START[0], S._END[0] = period
            m = run_cfg(cfg, ind, ds, sc)
            out[name][pname] = m
            print(f"{name:<20}{pname:<10}{m.get('CAGR%'):>8}{m.get('最大回撤%'):>9}"
                  f"{m.get('交易笔数'):>6}{str(m.get('Sharpe')):>8}", flush=True)
        print(flush=True)

    p = ROOT / "research" / "results" / "ab_split.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    print(f"[done] {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
