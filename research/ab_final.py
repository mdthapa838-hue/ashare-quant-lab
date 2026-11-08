# -*- coding: utf-8 -*-
"""research/ab_final.py — 在「关闭择时」这个有样本外信号的基座上控回撤"""
from __future__ import annotations
import copy, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import sweep as S
from sweep import indicator_cache, load_dataset, run_cfg
from tushare_trader.config import load_config

TRAIN = ("2018-01-01", "2022-12-31"); TEST = ("2023-01-01", ""); FULL = ("2018-01-01", "")

def make(base, **kw):
    c = copy.deepcopy(base)
    c["entry"]["use_market_filter"] = False
    for k, v in kw.items():
        sect, key = k.split("__"); c[sect][key] = v
    return c

def main():
    cfg = load_config(); ds = load_dataset(cfg, 150)
    ind = indicator_cache(ds, cfg); base = copy.deepcopy(cfg.strategy_cfg)
    print(f"{'方案':<26}{'期间':<8}{'CAGR%':>8}{'回撤%':>9}{'交易':>6}", flush=True)
    variants = {
        "B0 关择时(5仓/上轨/60)": make(base),
        "B1 10仓": make(base, portfolio__max_positions=10),
        "B2 12仓": make(base, portfolio__max_positions=12),
        "B3 10仓+25%单只": make(base, portfolio__max_positions=10,
                              portfolio__max_position_pct=0.25),
        "B4 10仓+中轨": make(base, portfolio__max_positions=10,
                           exit__take_profit="mid"),
        "B5 10仓+持有40": make(base, portfolio__max_positions=10,
                             exit__max_hold_days=40),
    }
    out = {}
    for name, sc in variants.items():
        out[name] = {}
        for pn, period in (("train", TRAIN), ("test", TEST), ("full", FULL)):
            S._START[0], S._END[0] = period
            m = run_cfg(cfg, ind, ds, sc)
            out[name][pn] = m
            print(f"{name:<26}{pn:<8}{m.get('CAGR%'):>8}{m.get('最大回撤%'):>9}"
                  f"{m.get('交易笔数'):>6}", flush=True)
        print(flush=True)
    p = ROOT / "research" / "results" / "ab_final.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[done] {p}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
