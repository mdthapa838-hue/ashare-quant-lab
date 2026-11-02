# -*- coding: utf-8 -*-
"""诊断2：为何 2024-02-05 之后仍不一致。"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import load_config
from core.logger import setup_logger
setup_logger("d", log_dir=None, level="ERROR", console=False)
cfg = load_config()
from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound, _resample_trend

src = TushareSource(cache_dir=cfg.get("datasource.cache_dir"), min_interval=0.2)
st = BollLowerRebound(cfg.to_strategy_cfg(), src)

raw = pd.read_csv("data_cache/kline/603496_qfq.csv")
raw["date"] = pd.to_datetime(raw["date"])
raw = raw.sort_values("date").reset_index(drop=True)
print(f"603496.SH  数据 {raw['date'].min().date()} ~ {raw['date'].max().date()}  {len(raw)}行")

CONDS = ["cond_position", "cond_streak", "cond_ma60_down", "cond_monthly_up", "cond_liquidity"]

for D in ["2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06", "2024-02-07"]:
    dt_ = pd.Timestamp(D)
    trunc = raw[raw["date"] <= dt_].copy()
    if len(trunc) < 130:
        continue
    d_tr = st.compute(trunc)
    d_fu = st.compute(raw.copy())
    r_tr = d_tr.iloc[-1]
    r_fu = d_fu[d_fu["date"] == dt_].iloc[0]

    diffs = [c for c in CONDS if bool(r_tr[c]) != bool(r_fu[c])]
    tag = "一致" if not diffs else f"✗ 不一致: {diffs}"
    print(f"\n{D}  [{tag}]")
    print(f"   截断: close={r_tr['close']:.2f} pos120={r_tr['pos120']:.4f} "
          f"streak={int(r_tr['lower_streak'])} "
          f"ma60slope={r_tr['ma60_slope']:.5f} "
          f"mon_up={bool(r_tr['monthly_trend_up'])} "
          f"mon_slope={r_tr['monthly_slope'] if pd.notna(r_tr['monthly_slope']) else float('nan'):.5f} "
          f"amt20={r_tr['amount_ma20']:,.0f}")
    print(f"   全量: close={r_fu['close']:.2f} pos120={r_fu['pos120']:.4f} "
          f"streak={int(r_fu['lower_streak'])} "
          f"ma60slope={r_fu['ma60_slope']:.5f} "
          f"mon_up={bool(r_fu['monthly_trend_up'])} "
          f"mon_slope={r_fu['monthly_slope'] if pd.notna(r_fu['monthly_slope']) else float('nan'):.5f} "
          f"amt20={r_fu['amount_ma20']:,.0f}")

# 检查是否因为截断导致 >126 根的条件
print()
print("=" * 90)
print(" 检查：截断后长度是否满足月线计算条件 len > monthly_ma*21 = 126")
print("=" * 90)
for D in ["2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06", "2024-02-07"]:
    dt_ = pd.Timestamp(D)
    n = len(raw[raw["date"] <= dt_])
    print(f"  {D}: 截断后 {n} 根  {'✓ >126' if n > 126 else '✗ ≤126 → 月线被跳过'}")

# 检查月线映射
print()
print("=" * 90)
print(" 月线参考值对比")
print("=" * 90)
tmp_f = raw.set_index("date")["close"]
mf = _resample_trend(tmp_f, "ME", 6, 1)
tmp_t = raw[raw["date"] <= "2024-02-07"].set_index("date")["close"]
mt = _resample_trend(tmp_t, "ME", 6, 1)
for D in ["2024-01-31", "2024-02-01", "2024-02-05", "2024-02-07"]:
    d = pd.Timestamp(D)
    a = mf[mf.index == d]
    b = mt[mt.index == d]
    if len(a) and len(b):
        print(f"  {D}  全量: ma={a.iloc[0]['ma']:.4f} pc={a.iloc[0]['period_close']:.2f} "
              f"up={bool(a.iloc[0]['trend_up'])}   |   "
              f"截断: ma={b.iloc[0]['ma']:.4f} pc={b.iloc[0]['period_close']:.2f} "
              f"up={bool(b.iloc[0]['trend_up'])}")
