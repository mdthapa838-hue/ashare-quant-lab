# -*- coding: utf-8 -*-
"""诊断：截断重算不一致的根本原因。"""
from __future__ import annotations
import os, sys, glob
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import load_config
from core.logger import setup_logger

setup_logger("diag", log_dir=None, level="ERROR", console=False)
cfg = load_config()
scfg = cfg.to_strategy_cfg()

# 直接加载单只股票，对比截断前后的各条件
p = Path(cfg.get("datasource.cache_dir")) / "kline" / "603033_qfq.csv"
raw = pd.read_csv(p)
raw["date"] = pd.to_datetime(raw["date"])
raw = raw.sort_values("date").reset_index(drop=True)

SIG_DATE = pd.Timestamp("2024-02-01")
print(f"诊断标的: 603033.SH   信号日: {SIG_DATE.date()}")
print(f"数据范围: {raw['date'].min().date()} ~ {raw['date'].max().date()}  ({len(raw)}行)")
print()

from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound, _resample_trend

src = TushareSource(cache_dir=cfg.get("datasource.cache_dir"), min_interval=0.2)
st = BollLowerRebound(scfg, src)

cond_cols = ["cond_position", "cond_streak", "cond_ma60_down",
             "cond_monthly_up", "cond_liquidity"]

def show(label, d):
    r = d.iloc[-1]
    print(f"  [{label}] date={r['date'].date()}  close={r['close']:.2f}")
    for c in cond_cols:
        print(f"      {'✓' if bool(r[c]) else '✗'} {c}")
    print(f"      pos120={r['pos120']:.4f}  streak={int(r['lower_streak'])}  "
          f"ma60_slope={r['ma60_slope']:.5f}  monthly_up={bool(r['monthly_trend_up'])}")
    print()

print("=" * 80)
print(" 1) 截断到信号日（模拟当时能看到的数据）")
print("=" * 80)
trunc = raw[raw["date"] <= SIG_DATE].copy()
d1 = st.compute(trunc)
show("截断", d1)
print(f"  截断后月线MA: {d1['monthly_slope'].iloc[-1]:.6f}   "
      f"trend_up={bool(d1['monthly_trend_up'].iloc[-1])}")

print("=" * 80)
print(" 2) 全量数据（回测里实际用的）")
print("=" * 80)
d2 = st.compute(raw.copy())
row = d2[d2["date"] == SIG_DATE]
show("全量", d2[d2["date"] <= SIG_DATE])

print("=" * 80)
print(" 3) 关键：月线趋势是怎么算出来的")
print("=" * 80)
# 复现 _resample_trend
for label, src_df in [("截断", trunc), ("全量", raw)]:
    tmp = src_df.set_index("date")["close"]
    m = _resample_trend(tmp, "ME", st.monthly_ma, 1)
    # 取 2024-01 和 2024-02 附近的月线值
    sub = m[(m.index >= "2023-10-01") & (m.index <= "2024-03-31")]
    print(f"  [{label}] 2023-10 ~ 2024-03 的月线值:")
    shown = sub[~sub.index.duplicated(keep="first")]
    for dt, row_ in shown.iterrows():
        print(f"      {dt.date()}  ma={row_['ma'] if pd.notna(row_['ma']) else float('nan'):.4f}"
              f"  slope={row_['slope'] if pd.notna(row_['slope']) else float('nan'):.6f}"
              f"  up={bool(row_['trend_up'])}")
    print()

print("=" * 80)
print(" 4) 结论分析")
print("=" * 80)
print("""
  若「全量」的 monthly_trend_up=True 而「截断」=False，
  说明月线计算用了【当月尚未发生的月末收盘价】→ 未来函数。

  现有 _resample_trend 的做法：
      key = date.to_period('M')
      pc  = close.groupby(key).last()     ← 取每月最后一个交易日
      ma  = pc.rolling(6).mean()
      out = out.reindex(key)              ← 把月度值映射回每一天

  问题：2024-02-01 属于 2024-02 月，groupby.last() 取的是
       2024-02-29 的收盘价（未来！），却回填给了 2024-02-01。
""")
