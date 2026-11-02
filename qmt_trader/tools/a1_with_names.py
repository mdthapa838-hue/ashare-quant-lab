# -*- coding: utf-8 -*-
"""a1_with_names.py — 给 A1 清单加上股票名称。"""
import sys
import time
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

OUT = HERE / "output"

import credentials  # type: ignore
import tushare as ts  # type: ignore
pro = ts.pro_api(credentials.get_tushare_token())

D = pd.read_csv(OUT / "a1_fillability.csv", dtype={"code": str})
D["date"] = pd.to_datetime(D["date"])
print(f"A1 记录: {len(D):,}")

# 拿名称
codes = sorted(D["code"].unique())
name_map = {}
try:
    b = pro.stock_basic(exchange="", list_status="L",
                        fields="ts_code,name,industry")
    name_map = dict(zip([str(c)[:6] for c in b["ts_code"]], b["name"]))
    ind_map = dict(zip([str(c)[:6] for c in b["ts_code"]], b["industry"]))
except Exception as e:
    print("名称获取失败:", str(e)[:80])
    ind_map = {}

# 退市股补
missing = [c for c in codes if c not in name_map]
if missing:
    try:
        b2 = pro.stock_basic(exchange="", list_status="D",
                             fields="ts_code,name")
        for c, n in zip([str(x)[:6] for x in b2["ts_code"]], b2["name"]):
            name_map.setdefault(c, n)
    except Exception:
        pass
    try:
        b3 = pro.stock_basic(exchange="", list_status="P",
                             fields="ts_code,name")
        for c, n in zip([str(x)[:6] for x in b3["ts_code"]], b3["name"]):
            name_map.setdefault(c, n)
    except Exception:
        pass

D["名称"] = D["code"].map(lambda c: name_map.get(c, "?"))
D["行业"] = D["code"].map(lambda c: ind_map.get(c, ""))
print(f"名称覆盖: {(D['名称']!='?').sum()}/{len(D)}")
print()

# 整理输出
cols = ["date", "code", "名称", "行业", "g", "gap", "lim",
        "T0涨停", "T1开盘涨停", "T1一字", "T1收涨停", "T1盘中回踩",
        "可成交性", "T1开盘", "T1最高", "T1最低",
        "C_f20", "C_f60", "A_f20"]
cols = [c for c in cols if c in D.columns]
D2 = D[cols].sort_values("date", ascending=False)
D2.to_csv(OUT / "a1_with_names.csv", index=False, encoding="utf-8-sig")

print("=" * 138)
print("  历史 A1 信号完整清单（最近 60 条）")
print("=" * 138)
print()
print(f"  {'日期':<12}{'代码':<8}{'名称':<11}{'增速%':>9}{'跳空%':>8}"
      f"{'可成交':<8}{'T1开盘':>9}{'T1最高':>9}{'20日%':>9}{'60日%':>9}")
print("  " + "-" * 106)
for _, r in D2.head(60).iterrows():
    ok = "✅" if str(r["可成交性"]).startswith("✅") else (
        "⚠" if "极难" in str(r["可成交性"]) else "❌")
    def f(v, d=2):
        return f"{v:.{d}f}" if pd.notna(v) else "—"
    print(f"  {r['date'].strftime('%Y-%m-%d'):<12}{r['code']:<8}"
          f"{str(r['名称'])[:10]:<11}{f(r['g'],1):>9}{f(r['gap']):>8}"
          f"{ok:<8}{f(r['T1开盘']):>9}{f(r['T1最高']):>9}"
          f"{f(r['C_f20']):>9}{f(r['C_f60']):>9}")
print()

# 成功案例（20日 > 20%）
print("=" * 138)
print("  成功案例（T+1买，20日收益 > 20%）")
print("=" * 138)
print()
win = D2[D2["C_f20"] > 20].sort_values("C_f20", ascending=False)
print(f"  共 {len(win)} 个")
print()
for _, r in win.head(25).iterrows():
    g = r["g"]
    gs = f"{g:.1f}" if pd.notna(g) else "—"
    print(f"  {r['date'].strftime('%Y-%m-%d')}  {r['code']} "
          f"{str(r['名称'])[:9]:<10} 增速 {gs:>8}%  "
          f"跳空 {r['gap']:>6.2f}%  20日 +{r['C_f20']:.1f}%")
print()
print(f"[输出] {OUT}/a1_with_names.csv")
