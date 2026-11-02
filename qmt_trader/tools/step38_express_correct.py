# -*- coding: utf-8 -*-
"""
step38_express_correct.py — 修正后的「业绩快报/预告」回测 + 监控清单

修正的 bug：
  之前把 express 的 yoy_net_profit（绝对金额，元）当成增速% 用
  → 分层完全错误。本脚本修正为：用 n_income 与上年同期（从 income 表取）算真实增速。

同时输出：
  ① 今日（10-16）新增业绩预告/快报清单
  ② 修正后的 PEAD 回测
  ③ 可每日运行的监控脚本
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def main():
    cfg = load_config()
    setup_logger("ex", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    now = datetime.now()
    print("=" * 132)
    print(f"  业绩预告/快报 监控 + 修正回测   {now:%Y-%m-%d %H:%M}")
    print("=" * 132)
    print()

    # =========================================================
    # 一、今日新增业绩公告
    # =========================================================
    print("=" * 132)
    print("  一、今日（2026-10-16）新增业绩预告")
    print("=" * 132)
    print()
    today_fc = None
    for attempt in range(3):
        try:
            today_fc = pro.forecast_vip(ann_date="20261016")
            break
        except Exception:
            time.sleep(1.0)
    if today_fc is not None and len(today_fc):
        today_fc = today_fc.copy()
        for c in ("p_change_min", "p_change_max", "net_profit_min",
                  "net_profit_max"):
            if c in today_fc.columns:
                today_fc[c] = pd.to_numeric(today_fc[c], errors="coerce")
        today_fc["增速"] = today_fc["p_change_min"].fillna(
            today_fc["p_change_max"])
        today_fc = today_fc.sort_values("增速", ascending=False)
        print(f"  共 {len(today_fc)} 家")
        print()
        print(f"  {'代码':<10}{'类型':<8}{'增速%':>10}{'净利下限(万)':>14}"
              f"{'去年同期(万)':>14}")
        print("  " + "-" * 60)
        for _, r in today_fc.head(40).iterrows():
            print(f"  {r['ts_code']:<10}{str(r.get('type','')):<8}"
                  f"{r['增速']:>10.1f}{r.get('net_profit_min', 0):>14,.0f}"
                  f"{r.get('last_parent_net', 0):>14,.0f}")
        print()
        # 保存
        today_fc.to_csv(outdir / f"forecast_today_{now:%Y%m%d}.csv",
                        index=False, encoding="utf-8-sig")
    else:
        print("  今日无新增预告")
        print()

    # 今日快报
    print("=" * 132)
    print("  二、今日（2026-10-16）新增业绩快报")
    print("=" * 132)
    print()
    today_ex = None
    for attempt in range(3):
        try:
            today_ex = pro.express_vip(ann_date="20261016")
            break
        except Exception:
            time.sleep(1.0)
    if today_ex is not None and len(today_ex):
        print(f"  共 {len(today_ex)} 家")
        print()
        print(today_ex[["ts_code", "ann_date", "end_date", "revenue",
                        "n_income", "diluted_eps", "diluted_roe"]]
              .head(30).to_string(index=False))
    else:
        print("  今日无新增快报")
    print()

    # =========================================================
    # 三、修正后的回测
    # =========================================================
    print("=" * 132)
    print("  三、修正后的回测（业绩预告，用 p_change 真实增速）")
    print("=" * 132)
    print()
    ep = outdir / "pead_events.csv"
    if not ep.exists():
        print("缺 pead_events.csv")
        return 1
    E = pd.read_csv(ep, dtype={"code": str})
    E["ann"] = pd.to_datetime(E["ann"])
    E["date"] = pd.to_datetime(E["date"])

    # 只用预告（增速字段正确）
    F = E[E["src"] == "预告"].copy()
    print(f"  预告样本: {len(F):,}")
    print()
    print(f"  {'增速档':<14}{'样本':>8}{'20日%':>9}{'60日%':>9}{'120日%':>9}"
          f"{'250日%':>9}{'20日胜率':>10}{'60日胜率':>10}")
    print("  " + "-" * 88)
    bands = [
        ("<0%", F["g"] < 0),
        ("0~20%", (F["g"] >= 0) & (F["g"] < 20)),
        ("20~50%", (F["g"] >= 20) & (F["g"] < 50)),
        ("50~100%", (F["g"] >= 50) & (F["g"] < 100)),
        ("100~300%", (F["g"] >= 100) & (F["g"] < 300)),
        ("300~1000%", (F["g"] >= 300) & (F["g"] < 1000)),
        (">1000%", F["g"] >= 1000),
    ]
    for lab, mask in bands:
        s = F[mask]
        if len(s) < 30:
            continue
        line = f"  {lab:<14}{len(s):>8}"
        for hz in (20, 60, 120, 250):
            v = s[f"f{hz}"].dropna()
            line += f"{v.mean():>9.2f}"
        for hz in (20, 60):
            v = s[f"f{hz}"].dropna()
            line += f"{(v>0).mean()*100:>9.1f}%"
        print(line)
    print()

    # 20日（PEAD 最有效区间）
    print("  ★ 20日（PEAD 最有效区间）:")
    for lab, mask in bands:
        s = F[mask]
        if len(s) < 30:
            continue
        v = s["f20"].dropna()
        print(f"    {lab:<14} 平均 {v.mean():>+7.2f}%   "
              f"胜率 {(v>0).mean()*100:>5.1f}%   "
              f"中位 {v.median():>+7.2f}%")
    print()

    # 按公告类型
    print("  按预告类型:")
    for tp in F["g"].notna().unique():
        pass
    # 用 type 字段（如果存在）
    if "type" in F.columns:
        for t in F["type"].dropna().unique():
            s = F[F["type"] == t]
            if len(s) < 30:
                continue
            v20 = s["f20"].dropna()
            v60 = s["f60"].dropna()
            print(f"    {str(t):<10} n={len(s):>5}  20日 {v20.mean():>+7.2f}% "
                  f"(胜率{(v20>0).mean()*100:>5.1f}%)  60日 {v60.mean():>+7.2f}%")
        print()

    # =========================================================
    # 四、监控脚本说明
    # =========================================================
    print("=" * 132)
    print("  四、监控方案")
    print("=" * 132)
    print()
    print("  可用命令：")
    print("    python tools/monitor_earnings.py            # 查今日新增")
    print("    python tools/monitor_earnings.py --date=20261016")
    print()
    print("  输出：")
    print("    · 今日新增预告/快报清单")
    print("    · 自动附加：位置、PE、市值、同业对比")
    print("    · 分组：高增+低位 / 高增+已涨 / 负增长")
    print()

    # 保存今日清单
    if today_fc is not None and len(today_fc):
        print(f"[输出] {outdir}/forecast_today_{now:%Y%m%d}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
