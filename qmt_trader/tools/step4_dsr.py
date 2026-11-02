# -*- coding: utf-8 -*-
"""
步骤4：DSR 多重检验校正 + Bonferroni / BH-FDR

背景：
  我测试了 19 个因子 × 14 个方向 = 114 组，然后挑出最好的那个。
  这是典型的数据窥视（data snooping）—— 最高 Sharpe 必须按
  试验次数打折。

方法：
  1. Deflated Sharpe Ratio (Bailey & López de Prado, 2014)
     - 输入：观测 Sharpe、样本长度、偏度、峰度、试验次数 N、试验间方差
     - 输出：打折后的 PSR（真实 Sharpe > 0 的概率）
  2. Bonferroni 校正
  3. Benjamini-Hochberg FDR

参考：
  Bailey, D. H., & López de Prado, M. (2014). The Deflated Sharpe Ratio.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy import stats


# ---------------------------------------------------------------------------
def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """
    在 N 次独立试验中，零假设下期望的最大 Sharpe。

    E[max SR] ≈ sqrt(var_sr) * ((1-γ)*Z^-1(1-1/N) + γ*Z^-1(1-1/(N*e)))
    γ = 欧拉-马斯刻若尼常数
    """
    if n_trials < 2:
        return 0.0
    gamma = 0.5772156649
    z1 = stats.norm.ppf(1 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1 - 1.0 / (n_trials * math.e))
    return math.sqrt(max(var_sr, 1e-12)) * ((1 - gamma) * z1 + gamma * z2)


def deflated_sharpe_ratio(sr: float, n_obs: int, skew: float = 0.0,
                          kurt: float = 3.0, n_trials: int = 1,
                          var_sr: float | None = None,
                          sr_benchmark: float = 0.0) -> dict:
    """
    Deflated Sharpe Ratio。

    Parameters
    ----------
    sr : float
        观测到的 Sharpe（年化或非年化，需与 n_obs 同期口径）
    n_obs : int
        收益率样本数
    skew, kurt : float
        收益率偏度与峰度（kurt 为原始峰度，正态=3）
    n_trials : int
        试验次数（数据窥视的规模）
    var_sr : float, optional
        各次试验 Sharpe 的截面方差；缺省用 0.5
    sr_benchmark : float
        基准 Sharpe（默认0）

    Returns dict with:
        sr, sr_expected_max, sr_deflated, psr, dsr, verdict
    """
    if n_obs < 3:
        return {"error": "样本太少", "n_obs": n_obs}

    if var_sr is None:
        var_sr = 0.5

    # 期望最大 Sharpe（多重检验的门槛）
    sr0 = expected_max_sharpe(n_trials, var_sr) if n_trials > 1 else sr_benchmark

    # 概率式 Sharpe（PSR）的标准误
    # SE(SR) = sqrt((1 - skew*SR + (kurt-1)/4 * SR^2) / (n-1))
    denom = math.sqrt(max(1e-12, (1 - skew * sr + (kurt - 1) / 4.0 * sr * sr)))
    se = denom / math.sqrt(max(1, n_obs - 1))

    # PSR: P(true SR > sr0)
    psr = float(stats.norm.cdf((sr - sr0) / se)) if se > 0 else float("nan")
    # 对 0 的 PSR
    psr0 = float(stats.norm.cdf(sr / se)) if se > 0 else float("nan")

    verdict = "不可信"
    if n_trials > 1:
        if psr >= 0.95:
            verdict = "√ 通过（5%显著性）"
        elif psr >= 0.90:
            verdict = "△ 边缘（10%显著性）"
        else:
            verdict = "✗ 不通过"

    return {
        "sr_observed": round(sr, 4),
        "sr_expected_max": round(sr0, 4),
        "sr_deflated": round(sr - sr0, 4),
        "se": round(se, 5),
        "psr_vs_0": round(psr0, 4),
        "psr_vs_expected_max": round(psr, 4),
        "n_obs": n_obs,
        "n_trials": n_trials,
        "skew": round(skew, 3),
        "kurt": round(kurt, 3),
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
def bonferroni(pvals: Iterable[float], alpha: float = 0.05) -> dict:
    """Bonferroni 校正。"""
    p = np.asarray([x for x in pvals if x is not None and np.isfinite(x)])
    n = len(p)
    if n == 0:
        return {"n": 0}
    thr = alpha / n
    return {
        "方法": "Bonferroni",
        "试验数": n,
        "原alpha": alpha,
        "校正阈值": round(thr, 6),
        "通过数": int((p < thr).sum()),
        "最小p值": round(float(p.min()), 6),
    }


def bh_fdr(pvals: Iterable[float], alpha: float = 0.05) -> dict:
    """Benjamini-Hochberg FDR 控制。"""
    p = np.asarray(sorted(x for x in pvals if x is not None and np.isfinite(x)))
    n = len(p)
    if n == 0:
        return {"n": 0}
    thresholds = alpha * (np.arange(1, n + 1)) / n
    passed = p <= thresholds
    k = int(passed.sum())
    return {
        "方法": "BH-FDR",
        "试验数": n,
        "原alpha": alpha,
        "通过数": k,
        "最大通过p值": round(float(p[k - 1]), 6) if k > 0 else None,
        "最小p值": round(float(p.min()), 6),
    }


def sharpe_ratio(returns: np.ndarray, periods_per_year: int = 244,
                 rf: float = 0.02) -> float:
    """年化 Sharpe。"""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return float("nan")
    mu = r.mean() * periods_per_year
    sd = r.std(ddof=1) * math.sqrt(periods_per_year)
    if sd <= 0:
        return float("nan")
    return (mu - rf) / sd


def tstat_to_pvalue(t: float, df: int, two_sided: bool = True) -> float:
    """t 值转 p 值。"""
    if not np.isfinite(t) or df < 1:
        return float("nan")
    p = 2 * (1 - stats.t.cdf(abs(t), df)) if two_sided else 1 - stats.t.cdf(t, df)
    return float(p)


def evaluate_strategy(returns: np.ndarray, n_trials: int,
                      periods_per_year: int = 244,
                      label: str = "") -> dict:
    """
    对一条策略的日收益序列做完整检验。

    Returns dict with Sharpe, t-stat, p-value, DSR, Bonferroni, FDR
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 30:
        return {"策略": label, "错误": f"样本不足({n})"}

    sr_ann = sharpe_ratio(r, periods_per_year)
    # 非年化 Sharpe（DSR 用）
    sr_raw = sr_ann / math.sqrt(periods_per_year)
    skew = float(stats.skew(r))
    kurt = float(stats.kurtosis(r, fisher=False))

    # t 检验
    t = r.mean() / (r.std(ddof=1) / math.sqrt(n)) if r.std(ddof=1) > 0 else 0
    p = tstat_to_pvalue(t, n - 1)

    dsr = deflated_sharpe_ratio(sr_raw, n, skew, kurt, n_trials)

    return {
        "策略": label,
        "样本数": n,
        "年化Sharpe": round(sr_ann, 3),
        "t统计量": round(t, 3),
        "p值": round(p, 6),
        "偏度": round(skew, 3),
        "峰度": round(kurt, 3),
        "期望最大Sharpe(零假设)": dsr.get("sr_expected_max"),
        "DSR(概率)": dsr.get("psr_vs_expected_max"),
        "DSR判定": dsr.get("verdict"),
        "试验次数": n_trials,
        "Bonferroni阈值": None,
        "是否通过Bonferroni": None,
    }
