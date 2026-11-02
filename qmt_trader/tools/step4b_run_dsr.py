# -*- coding: utf-8 -*-
"""
步骤4b：对已有回测结果做 DSR 多重检验校正

读取 dynamic_pool_compare 出的权益曲线，计算：
  - 年化 Sharpe
  - t 统计量 / p 值
  - Deflated Sharpe Ratio（按试验次数打折）
  - Bonferroni / BH-FDR 阈值

试验次数 N 的估计：
  本次研究测试了 19 因子 × 14 方向 = 266 组，加上基础配置变体 ≈ 300 组
"""
from __future__ import annotations

import os
import sys
import glob
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from core.config import load_config
from core.logger import setup_logger
from step4_dsr import evaluate_strategy, bh_fdr, bonferroni, deflated_sharpe_ratio


def main():
    cfg = load_config()
    setup_logger("dsr", log_dir=None, level="WARNING", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / "output"

    print("=" * 120)
    print("  步骤4b：DSR 多重检验校正")
    print("=" * 120)
    print()

    # ---- 收集所有权益曲线
    files = sorted(glob.glob(str(outdir / "dyn_equity_*.csv")))
    if not files:
        print("未找到权益曲线文件")
        return 1

    curves = {}
    for f in files:
        name = os.path.basename(f).replace("dyn_equity_", "").replace(".csv", "")
        tag = name.split("_")[0]
        try:
            d = pd.read_csv(f)
            if "equity" in d.columns and len(d) > 100:
                curves[tag] = d
        except Exception:
            pass

    # 也加入之前的关键结果
    print(f"找到 {len(curves)} 条权益曲线")
    for k in curves:
        print(f"  {k}: {len(curves[k])} 天")
    print()

    # ---- 试验次数
    N_TRIALS = 266   # 19因子 × 14方向
    N_TRIALS_ALL = 300

    print(f"试验次数估计: {N_TRIALS}（19因子×14方向）")
    print()

    # ---- 逐条评估
    rows = []
    for tag, d in curves.items():
        e = d["equity"].values
        r = pd.Series(e).pct_change().dropna().values
        res = evaluate_strategy(r, N_TRIALS, label=tag)
        rows.append(res)
        print(f"  【{tag}】")
        for k, v in res.items():
            if k != "策略":
                print(f"      {k}: {v}")
        print()

    R = pd.DataFrame(rows)
    if len(R):
        print("=" * 120)
        print("  汇总")
        print("=" * 120)
        cols = ["策略", "样本数", "年化Sharpe", "t统计量", "p值",
                "期望最大Sharpe(零假设)", "DSR(概率)", "DSR判定"]
        cols = [c for c in cols if c in R.columns]
        print(R[cols].to_string(index=False))
        print()

        # ---- 多重检验
        pvals = R["p值"].dropna().tolist()
        if pvals:
            print("=" * 120)
            print("  多重检验校正")
            print("=" * 120)
            print()
            b = bonferroni(pvals, 0.05)
            for k, v in b.items():
                print(f"  {k}: {v}")
            print()
            f = bh_fdr(pvals, 0.05)
            for k, v in f.items():
                print(f"  {k}: {v}")
            print()

        # ---- 关键解读
        print("=" * 120)
        print("  解读")
        print("=" * 120)
        print()
        for _, row in R.iterrows():
            sh = row.get("年化Sharpe")
            dsr = row.get("DSR(概率)")
            exp_max = row.get("期望最大Sharpe(零假设)")
            if sh is None:
                continue
            print(f"  {row['策略']}:")
            print(f"    观测 Sharpe          = {sh}")
            print(f"    零假设下期望最大Sharpe = {exp_max}")
            if exp_max is not None and sh is not None:
                if sh > exp_max:
                    print(f"    → 观测值超过多重检验门槛 {sh-exp_max:+.2f}  "
                          f"DSR={dsr}")
                else:
                    print(f"    → 观测值低于门槛 {sh-exp_max:+.2f}  "
                          f"说明结果可由随机搜索解释")
            print()

    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
    if len(R):
        R.to_csv(outdir / f"dsr_result_{stamp}.csv", index=False,
                 encoding="utf-8-sig")
        print(f"[输出] {outdir}/dsr_result_{stamp}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
