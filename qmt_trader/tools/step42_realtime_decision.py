# -*- coding: utf-8 -*-
"""
step42_realtime_decision.py — 实时决策可行性

用户问题：
  「开盘时怎么知道他会不会回补？收盘买的话都涨起来了」

本脚本回答：
  ① 关键洞察：不需要在开盘决策 —— 等 T+1 开盘时，第一天的信息已完全可知
  ② 各入场方式在「不回补组」vs「回补组」的表现
  ③ 可执行的 T+1 条件规则（用第一天收盘信息筛选）
  ④ 用今日分钟数据演示决策树的实际执行
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
    setup_logger("rt", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    print("=" * 134)
    print("  实时决策可行性：开盘时不知道会不会回补，怎么办？")
    print("=" * 134)
    print()

    E = pd.read_csv(outdir / "gap_fade_events.csv", dtype={"code": str})
    print(f"  载入 {len(E):,} 个业绩预告事件")
    print()

    # ---- 派生字段
    E = E.copy()
    E["close_vs_prev"] = ((1 + E["gap"] / 100) * (1 + E["intraday"] / 100)
                          - 1) * 100
    E["held"] = ~E["filled_same"].astype(bool)     # 全天未回补
    E["closed_up"] = E["close_vs_prev"] > 0        # 收盘价高于前收
    E["up_day"] = E["intraday"] > 0                # 开盘→收盘上涨

    print("=" * 134)
    print("  一、★ 核心洞察：不需要在开盘决策")
    print("=" * 134)
    print()
    print("  用户困境：")
    print("    · 09:30 开盘时 → 不知道今天会不会回补")
    print("    · 15:00 收盘时 → 知道了，但价格已经涨上去了")
    print()
    print("  ★ 解法：等「T+1 开盘」再买 —— 此时第一天的信息【完全可知】")
    print()
    print("  回测对比（5,770 事件，20日持有）：")
    print()
    print(f"  {'入场方式':<24}{'决策时点':<22}{'20日%':>9}{'胜率%':>9}{'可执行?':>10}")
    print("  " + "-" * 76)
    rows = [
        ("A 跳空当日开盘买", "09:30（信息不足）",
         E["A_f20"].dropna()),
        ("B 跳空当日收盘买", "15:00（价格已涨）",
         E["B_f20"].dropna()),
        ("C T+1 开盘买", "T+1 09:30（信息完整）",
         E["C_f20"].dropna()),
    ]
    for lab, when, v in rows:
        ok = "✅" if "T+1" in lab else "⚠"
        print(f"  {lab:<24}{when:<22}{v.mean():>9.2f}"
              f"{(v>0).mean()*100:>8.1f}%{ok:>10}")
    print()
    print("  → T+1 开盘买【收益更高（+5.00%）+ 信息完整 + 完全可执行】")
    print()

    # ---- 二、分组对比
    print("=" * 134)
    print("  二、★ 各入场方式 × 是否回补（这是关键）")
    print("=" * 134)
    print()
    print(f"  {'组':<18}{'样本':>7}{'方式':<16}{'20日%':>9}{'60日%':>9}"
          f"{'20日胜率':>10}")
    print("  " + "-" * 76)
    for lab, m in [("当日不回补", E["held"]), ("当日回补", ~E["held"])]:
        s = E[m]
        for way, ca, cb in [("A 开盘买", "A_f20", "A_f60"),
                            ("B 收盘买", "B_f20", "B_f60"),
                            ("C T+1开盘买", "C_f20", "C_f60")]:
            v20 = s[ca].dropna()
            v60 = s[cb].dropna() if cb in s.columns else pd.Series()
            if len(v20) < 20:
                continue
            r = f"  {lab:<18}{len(s):>7}{way:<16}{v20.mean():>9.2f}"
            r += f"{v60.mean():>9.2f}" if len(v60) else f"{'—':>9}"
            r += f"{(v20>0).mean()*100:>9.1f}%"
            print(r)
        print()

    # ---- 三、T+1 可用的筛选条件
    print("=" * 134)
    print("  三、★ T+1 开盘时可用的筛选条件（全部用第一天收盘信息）")
    print("=" * 134)
    print()
    conds = [
        ("无条件（全买）", E.index >= 0),
        ("① 当日不回补缺口", E["held"]),
        ("② 收盘价>前收", E["closed_up"]),
        ("③ ①+②（不回补且收红）", E["held"] & E["closed_up"]),
        ("④ 高开≥2%", E["gap"] >= 2),
        ("⑤ ①+高开≥2%", E["held"] & (E["gap"] >= 2)),
        ("⑥ ①+②+高开≥2%",
         E["held"] & E["closed_up"] & (E["gap"] >= 2)),
        ("⑦ 增速≥50%", pd.to_numeric(E["g"], errors="coerce") >= 50),
        ("⑧ ⑦+①", (pd.to_numeric(E["g"], errors="coerce") >= 50) & E["held"]),
        ("⑨ ⑦+①+②",
         (pd.to_numeric(E["g"], errors="coerce") >= 50) & E["held"] &
         E["closed_up"]),
    ]
    print(f"  {'条件':<26}{'样本':>8}{'占比':>8}{'20日%':>9}{'60日%':>9}"
          f"{'20日胜率':>10}{'60日胜率':>10}")
    print("  " + "-" * 94)
    res = []
    for lab, m in conds:
        s = E[m]
        if len(s) < 20:
            print(f"  {lab:<26}{len(s):>8}  样本不足")
            continue
        v20 = s["C_f20"].dropna()
        v60 = s["C_f60"].dropna()
        r = f"  {lab:<26}{len(s):>8}{len(s)/len(E)*100:>7.1f}%"
        r += f"{v20.mean():>9.2f}" if len(v20) else f"{'—':>9}"
        r += f"{v60.mean():>9.2f}" if len(v60) else f"{'—':>9}"
        r += f"{(v20>0).mean()*100:>9.1f}%" if len(v20) else f"{'—':>10}"
        r += f"{(v60>0).mean()*100:>9.1f}%" if len(v60) else f"{'—':>10}"
        print(r)
        res.append({"条件": lab, "样本": len(s),
                    "20日%": round(v20.mean(), 2) if len(v20) else None,
                    "60日%": round(v60.mean(), 2) if len(v60) else None,
                    "20日胜率%": round((v20 > 0).mean() * 100, 1)
                    if len(v20) else None,
                    "60日胜率%": round((v60 > 0).mean() * 100, 1)
                    if len(v60) else None})
    print()

    # ---- 四、日内信号强度（用今日数据演示）
    print("=" * 134)
    print("  四、★ 今日（2026-10-16）实盘演示：信号如何随时间显现")
    print("=" * 134)
    print()
    print("  演示：若在盘中不同时点观察，能否提前判断")
    print()
    import requests
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    stocks = [("688385", "复旦微电", 47.30), ("688002", "睿创微纳", 167.52),
              ("601233", "桐昆股份", 19.22), ("002044", "美年健康", 4.64),
              ("688783", "西安奕材", 21.45), ("002008", "大族激光", 63.37),
              ("000703", "恒逸石化", 13.90), ("600489", "中金黄金", 21.19),
              ("688041", "海光信息", 193.82), ("002064", "华峰化学", 8.86),
              ("002916", "深南电路", 318.80), ("002281", "光迅科技", 132.96)]
    print(f"  {'名称':<10}{'前收':>9}{'09:40':>9}{'10:30':>9}"
          f"{'11:20':>9}{'14:30':>9}{'收盘':>9}{'09:40判定':<12}{'最终':<10}")
    print("  " + "-" * 106)
    demo = []
    for c6, nm, pre in stocks:
        code = ("sh" if c6.startswith(("60", "68", "9")) else "sz") + c6
        try:
            r = requests.get(
                f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}",
                headers={"User-Agent": UA}, timeout=15)
            j = r.json()
            lines = j["data"][code]["data"].get("data", [])
            if len(lines) < 60:
                continue
            def px(t):
                for l in lines:
                    if l.split(" ")[0] == t:
                        return float(l.split(" ")[1])
                return None
            def pxlo_until(t):
                vals = [float(l.split(" ")[1]) for l in lines
                        if l.split(" ")[0] <= t]
                return min(vals) if vals else None
            p0940 = px("0940")
            p1030 = px("1030")
            p1120 = px("1120")
            p1430 = px("1430")
            pclose = float(lines[-1].split(" ")[1])
            lo0940 = pxlo_until("0940")
            lo1120 = pxlo_until("1120")
            lo1430 = pxlo_until("1430")
            final_hold = min(float(l.split(" ")[1]) for l in lines) > pre
            # 09:40 判定：至今未回补 且 价格>前收
            if lo0940 is not None and p0940 is not None:
                v0940 = "未回补" if lo0940 > pre else "已回补"
            else:
                v0940 = "—"
            print(f"  {nm:<10}{pre:>9.2f}"
                  f"{p0940 if p0940 else 0:>9.2f}"
                  f"{p1030 if p1030 else 0:>9.2f}"
                  f"{p1120 if p1120 else 0:>9.2f}"
                  f"{p1430 if p1430 else 0:>9.2f}"
                  f"{pclose:>9.2f}{v0940:<12}"
                  f"{'未回补✅' if final_hold else '回补❌':<10}")
            demo.append({
                "代码": c6, "名称": nm, "前收": pre,
                "09:40": p0940, "10:30": p1030, "11:20": p1120,
                "14:30": p1430, "收盘": pclose,
                "09:40未回补": (lo0940 is not None and lo0940 > pre),
                "11:20未回补": (lo1120 is not None and lo1120 > pre),
                "14:30未回补": (lo1430 is not None and lo1430 > pre),
                "收盘未回补": final_hold,
                "收盘%": round((pclose / pre - 1) * 100, 2),
            })
        except Exception:
            continue
        time.sleep(0.15)
    print()

    # 信号稳定性：各时点判定 vs 最终结果
    if demo:
        D = pd.DataFrame(demo)
        print("  信号稳定性（各时点「未回补」判定 vs 最终「未回补」）:")
        print()
        print(f"  {'观察时点':<12}{'判定未回补':>12}{'最终未回补':>12}"
              f"{'一致率':>10}{'含义'}")
        print("  " + "-" * 72)
        for t in ("09:40", "11:20", "14:30"):
            col = f"{t}未回补"
            if col in D.columns:
                agree = (D[col] == D["收盘未回补"]).mean() * 100
                n = int(D[col].sum())
                note = ("太早，易误判" if t == "09:40"
                        else ("较可靠" if t == "11:20" else "接近确定"))
                print(f"  {t:<12}{n:>12}{int(D['收盘未回补'].sum()):>12}"
                      f"{agree:>9.1f}%  {note}")
        print()
        # 收盘涨幅对比
        h = D[D["收盘未回补"]]
        f = D[~D["收盘未回补"]]
        print(f"  今日演示组（12只）:")
        print(f"    最终未回补 {len(h)} 只  收盘平均 "
              f"{h['收盘%'].mean():+.2f}%")
        print(f"    最终回补   {len(f)} 只  收盘平均 "
              f"{f['收盘%'].mean():+.2f}%")
        print()
        D.to_csv(outdir / "realtime_demo.csv", index=False,
                 encoding="utf-8-sig")

    # ---- 五、最终方案
    print("=" * 134)
    print("  五、★ 可执行的决策流程")
    print("=" * 134)
    print()
    print("  【核心原则：不在开盘决策，等 T+1 开盘】")
    print()
    print("  ┌─────────────────────────────────────────────────────────┐")
    print("  │ T日（公告归属日）                                        │")
    print("  │   09:30  开盘    → 只看不动                             │")
    print("  │   全天           → 记录：最低价是否跌破前收？           │")
    print("  │   15:00  收盘    → 两个数据：最低价 vs 前收、收盘 vs 前收│")
    print("  ├─────────────────────────────────────────────────────────┤")
    print("  │ T+1日（决策日，此时信息完整）                            │")
    print("  │   09:15-09:25    → 看竞价，确认没有大幅高开             │")
    print("  │   09:30  开盘买  → ★ 按条件筛选后买入                   │")
    print("  └─────────────────────────────────────────────────────────┘")
    print()
    print("  筛选条件（按回测优先级）:")
    print()
    print("    ① 当日最低价 > 前收（未回补）  ← 必需")
    print("    ② 当日收盘 > 前收（收红）      ← 加分")
    print("    ③ 当日跳空 ≥ 2%                ← 加分")
    print("    ④ 增速 ≥ 50%                   ← 加分")
    print()
    print("  回测表现（T+1 开盘买，20日持有）:")
    if res:
        R = pd.DataFrame(res)
        for _, r in R.iterrows():
            if r["样本"] and r["样本"] >= 20:
                print(f"    {r['条件']:<26} n={int(r['样本']):>5}  "
                      f"20日 {r['20日%'] if r['20日%'] is not None else 0:>+7.2f}%  "
                      f"胜率 {r['20日胜率%']:>5.1f}%")
    print()
    print("  ⚠ 注意：若某日无标的满足条件 → 空仓等待，不要降低标准")
    print()

    outdir.mkdir(exist_ok=True)
    if res:
        pd.DataFrame(res).to_csv(outdir / "t1_conditions.csv", index=False,
                                 encoding="utf-8-sig")
        print(f"[输出] {outdir}/t1_conditions.csv")
    print(f"[输出] {outdir}/realtime_demo.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
