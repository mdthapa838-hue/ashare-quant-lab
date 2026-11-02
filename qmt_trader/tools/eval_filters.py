# -*- coding: utf-8 -*-
"""
过滤器价值评估 —— 关闭月线/择时后的对比

背景：
  之前发现「月线」+「择时」两个过滤器把信号从 17233 砍到 533（-97%），
  但 CAGR 从 7.22% 降到 2.84%。需要验证这个反常。

本次对比：
  A. B0 基础三条件（低位 + 连续触轨 + MA60向下）
  B. B0 + 月线
  C. B0 + 择时
  D. B0 + 月线 + 择时（完整策略）

每个配置输出：
  - 信号数 / 交易数 / CAGR / Sharpe / 回撤 / 胜率
  - 分年度表现
  - 收益集中度

并对 B0 做随机入场对照（判断是否有 alpha）。

股池：分层随机样本（700只，板块比例对齐总体）
"""
from __future__ import annotations

import os
import sys
import glob
import time
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.config import load_config
from core.logger import setup_logger
from core.tushare_source import TushareSource
from core.strategy import BollLowerRebound


def to_ts(c6: str) -> str:
    if c6.startswith(("60", "68")):
        return c6 + ".SH"
    if c6.startswith(("8", "4", "9")):
        return c6 + ".BJ"
    return c6 + ".SZ"


# ---------------------------------------------------------------------------
# 简化但严格的事件驱动回测（T收盘信号 → T+1开盘成交）
# ---------------------------------------------------------------------------
class SimpleBacktest:
    def __init__(self, cfg: dict, market_ret: pd.Series | None = None):
        st = cfg.get("strategy", {})
        self.tp = st.get("exit", {}).get("take_profit", "upper")
        self.stop_pct = st.get("exit", {}).get("stop_loss_pct", -0.12)
        self.max_hold = st.get("exit", {}).get("max_hold_days", 60)
        pf = st.get("portfolio", {})
        self.max_pos = pf.get("max_positions", 5)
        self.max_pct = pf.get("max_position_pct", 0.25)
        self.init_cap = pf.get("initial_capital", 1_000_000)
        c = st.get("cost", {})
        self.buy_rate = c.get("commission", 0.00025) + c.get("slippage", 0.00075)
        self.sell_rate = (c.get("commission", 0.00025) + c.get("stamp_tax", 0.0005)
                          + c.get("slippage", 0.00075))
        self.mkt = market_ret

    def run(self, data: dict[str, pd.DataFrame], conds: list[str],
            use_market: bool = False, verbose: bool = False):
        """data: {code: 带指标+条件的DataFrame}"""
        # 交易日历（统一用 pd.Timestamp，避免与 numpy.datetime64 哈希不等）
        cal = set()
        for d in data.values():
            cal.update(pd.to_datetime(d["date"]).tolist())
        cal = sorted(pd.Timestamp(x) for x in cal)

        # 建立索引映射（键统一为 pd.Timestamp）
        idx = {}
        for c, d in data.items():
            idx[c] = {pd.Timestamp(x): i
                      for i, x in enumerate(pd.to_datetime(d["date"]).tolist())}

        # 信号索引：date -> [code]
        sig = {}
        for c, d in data.items():
            need = conds + (["cond_market"] if use_market else [])
            if not all(x in d.columns for x in need):
                continue
            m = d[need].all(axis=1).values
            for k in np.where(m)[0]:
                if k + 1 >= len(d):
                    continue
                ed = pd.Timestamp(d["date"].iloc[k + 1])
                sig.setdefault(ed, []).append(c)

        cash = float(self.init_cap)
        pos = {}
        eq_rows, tr_rows = [], []
        peak = self.init_cap

        for dt in cal:
            # ---- 出场（用昨日已完成K线判定）
            for c in list(pos.keys()):
                p = pos[c]
                d = data[c]
                im = idx[c]
                if dt not in im:
                    continue
                j = im[dt]
                y = j - 1
                if y < 0 or y < p["k0"] + 1:
                    continue
                bar = d.iloc[j]
                ybar = d.iloc[y]
                # 涨停不卖
                oc = bar.get("open_chg_pct")
                if pd.notna(oc) and oc < -9.5:
                    p["days"] += 1
                    continue
                reason = None
                if ybar["low"] <= p["stop"]:
                    reason = "止损"
                else:
                    tgt = ybar["bb_upper"] if self.tp == "upper" else ybar["bb_mid"]
                    if pd.notna(tgt) and ybar["high"] >= tgt:
                        reason = "止盈"
                    elif p["days"] >= self.max_hold:
                        reason = "超时"
                if reason:
                    xp = bar["open"]
                    if not np.isfinite(xp) or xp <= 0:
                        xp = bar["close"]
                    cash += p["shares"] * xp * (1 - self.sell_rate)
                    tr_rows.append({
                        "code": c,
                        "entry_date": p["entry_date"],
                        "exit_date": dt,
                        "entry_price": p["entry_price"],
                        "exit_price": xp,
                        "days": p["days"],
                        "reason": reason,
                        "ret": (p["shares"] * xp * (1 - self.sell_rate)) / p["cost"] - 1,
                        "year": pd.Timestamp(p["entry_date"]).year,
                    })
                    del pos[c]
                    continue
                p["days"] += 1

            # ---- 入场
            cands = sig.get(dt, [])
            if cands:
                free = self.max_pos - len(pos)
                if free > 0:
                    # 当前权益
                    tot = cash
                    for c2, p2 in pos.items():
                        im2 = idx[c2]
                        if dt in im2:
                            tot += data[c2]["close"].iloc[im2[dt]] * p2["shares"]
                        else:
                            tot += p2["entry_price"] * p2["shares"]
                    per = tot / self.max_pos
                    cap = tot * self.max_pct
                    for c in cands[:free * 3]:
                        if len(pos) >= self.max_pos:
                            break
                        if c in pos:
                            continue
                        d = data[c]
                        im = idx[c]
                        if dt not in im:
                            continue
                        j = im[dt]
                        bar = d.iloc[j]
                        oc = bar.get("open_chg_pct")
                        if pd.notna(oc) and oc > 9.5:
                            continue
                        op = bar["open"]
                        if not np.isfinite(op) or op <= 0:
                            continue
                        alloc = min(per, cap, cash * 0.999)
                        if alloc < self.init_cap * 0.01:
                            continue
                        sh = (alloc * (1 - self.buy_rate)) / op
                        if sh <= 0:
                            continue
                        cash -= alloc
                        pos[c] = {
                            "entry_date": dt, "entry_price": op, "shares": sh,
                            "cost": alloc, "days": 0, "k0": j,
                            "stop": op * (1 + self.stop_pct),
                        }

            # ---- 记录权益
            eq = cash
            for c, p in pos.items():
                im = idx[c]
                if dt in im:
                    eq += data[c]["close"].iloc[im[dt]] * p["shares"]
                else:
                    eq += p["entry_price"] * p["shares"]
            peak = max(peak, eq)
            eq_rows.append({"date": dt, "equity": eq, "n": len(pos),
                            "dd": eq / peak - 1})

        return pd.DataFrame(eq_rows), pd.DataFrame(tr_rows)


def metrics(eq, tr, label, years_ref=None):
    if len(eq) < 2:
        return {"配置": label}
    e = eq["equity"].values
    dates = pd.to_datetime(eq["date"])
    yrs = (dates.iloc[-1] - dates.iloc[0]).days / 365.25
    if yrs <= 0:
        return {"配置": label}
    cagr = (e[-1] / e[0]) ** (1 / yrs) - 1
    r = pd.Series(e).pct_change().dropna()
    vol = r.std() * np.sqrt(244) if len(r) > 1 else 0
    sharpe = (r.mean() * 244 - 0.02) / vol if vol > 0 else np.nan
    dd = eq["dd"].min()
    out = {
        "配置": label,
        "总收益%": round((e[-1] / e[0] - 1) * 100, 1),
        "CAGR%": round(cagr * 100, 2),
        "Sharpe": round(sharpe, 2) if np.isfinite(sharpe) else None,
        "最大回撤%": round(dd * 100, 2),
        "Calmar": round(cagr / abs(dd), 2) if dd else None,
        "年数": round(yrs, 1),
    }
    if tr is not None and len(tr):
        rr = tr["ret"]
        w = rr[rr > 0]
        l = rr[rr < 0]
        out.update({
            "交易笔数": len(tr),
            "胜率%": round((rr > 0).mean() * 100, 1),
            "平均单笔%": round(rr.mean() * 100, 2),
            "盈亏比": round(w.mean() / abs(l.mean()), 2) if len(l) and l.mean() != 0 else None,
            "年均交易": round(len(tr) / yrs, 1),
            "平均持有天": round(tr["days"].mean(), 1),
        })
    return out


def main():
    cfg = load_config()
    setup_logger("eval", log_dir=None, level="WARNING", console=False)

    cache = Path(cfg.get("datasource.cache_dir"))
    src = TushareSource(cache_dir=str(cache), min_interval=0.25)

    # ---- 股池：分层样本
    samp = pd.read_csv(cache.parent / "output" / "pool_stratified.csv",
                       dtype={"ts_code": str})
    codes = sorted(set(samp["ts_code"].str[:6].tolist()))
    print(f"股池: {len(codes)} 只（分层随机，板块对齐总体）")

    # ---- 择时序列
    idxs = src.index_daily("000852.SH", "20150101", "20261014")
    mkt = (idxs / idxs.shift(60) - 1) * 100 if idxs is not None else None
    src_cfg = cfg.to_strategy_cfg()
    market_max = src_cfg["entry"].get("market_ret_max", -20.0)

    t0 = time.time()
    print("计算指标中...")
    data = {}
    ck = 0
    for c6 in codes:
        p = cache / "kline" / f"{c6}_qfq.csv"
        if not p.exists():
            continue
        try:
            raw = pd.read_csv(p)
            if len(raw) < 150:
                continue
            raw["date"] = pd.to_datetime(raw["date"])
            raw = raw.sort_values("date").reset_index(drop=True)
            # 用「关闭两个过滤器」的配置计算，之后按需取条件
            c = json.loads(json.dumps(src_cfg))
            c["entry"]["use_monthly_filter"] = True   # 先算出来，供对比
            c["entry"]["use_market_filter"] = False
            st = BollLowerRebound(c, src)
            d = st.compute(raw)
            # 市场择时列
            if mkt is not None:
                d = d.merge(mkt.rename("mkt_ret").reset_index().rename(
                    columns={"index": "date"}), on="date", how="left")
                d["cond_market"] = (d["mkt_ret"] < market_max).fillna(False)
            else:
                d["cond_market"] = False
            data[c6] = d
            ck += 1
        except Exception:
            pass
        if ck % 150 == 0 and ck:
            print(f"  {ck} 只  耗时 {time.time()-t0:.0f}s", flush=True)
    print(f"有效: {len(data)} 只  耗时 {time.time()-t0:.0f}s")
    print()

    B0 = ["cond_position", "cond_streak", "cond_ma60_down"]

    configs = [
        ("A. B0基础三条件", B0, False),
        ("B. B0 + 月线", B0 + ["cond_monthly_up"], False),
        ("C. B0 + 择时", B0, True),
        ("D. B0 + 月线 + 择时", B0 + ["cond_monthly_up"], True),
    ]

    results, yearlies, trades_all = [], {}, {}
    for label, conds, use_mkt in configs:
        print(f"▶ {label}")
        bt = SimpleBacktest(cfg.as_dict(), mkt)
        eq, tr = bt.run(data, conds, use_market=use_mkt)
        m = metrics(eq, tr, label)
        results.append(m)
        trades_all[label] = tr
        if len(tr):
            g = tr.groupby("year").agg(
                笔数=("ret", "size"), 平均收益=("ret", "mean"),
                胜率=("ret", lambda x: (x > 0).mean()),
                最大盈=("ret", "max"), 最大亏=("ret", "min"))
            g["平均收益"] = (g["平均收益"] * 100).round(2)
            g["胜率"] = (g["胜率"] * 100).round(1)
            g["最大盈"] = (g["最大盈"] * 100).round(1)
            g["最大亏"] = (g["最大亏"] * 100).round(1)
            yearlies[label] = g
        print(f"   信号条件={len(conds)}  交易={m.get('交易笔数',0)}  "
              f"CAGR={m.get('CAGR%')}%  Sharpe={m.get('Sharpe')}  "
              f"回撤={m.get('最大回撤%')}%")
        print()

    R = pd.DataFrame(results)
    print("=" * 110)
    print("  一、四种配置对比")
    print("=" * 110)
    cols = ["配置", "总收益%", "CAGR%", "Sharpe", "最大回撤%", "Calmar",
            "交易笔数", "胜率%", "平均单笔%", "盈亏比", "年均交易", "平均持有天"]
    cols = [c for c in cols if c in R.columns]
    print(R[cols].to_string(index=False))
    print()

    # ---- 分年度
    for label in ["A. B0基础三条件", "D. B0 + 月线 + 择时"]:
        if label in yearlies:
            print("=" * 110)
            print(f"  二、分年度：{label}")
            print("=" * 110)
            print(yearlies[label].to_string())
            pos_y = (yearlies[label]["平均收益"] > 0).sum()
            print(f"\n  正收益年份: {pos_y}/{len(yearlies[label])}")
            print()

    # ---- 随机对照（针对 A. B0基础）
    print("=" * 110)
    print("  三、随机入场对照（A. B0基础三条件）")
    print("=" * 110)
    rng = np.random.default_rng(7)
    codes_l = list(data.keys())
    rand_sig = {}
    # 每只股票随机取与该股真实信号数相同的日期
    real_bt = SimpleBacktest(cfg.as_dict(), mkt)
    btA = SimpleBacktest(cfg.as_dict(), mkt)
    # 统计真实信号数
    n_real = 0
    for c in codes_l:
        d = data[c]
        m = d[B0].all(axis=1).values
        n_real += int(m.sum())
    print(f"  B0 真实信号数（全历史）: {n_real}")

    # 随机：每只股票随机取 n_real/len 个点
    per = max(3, n_real // max(len(codes_l), 1))
    rand_data = {}
    for c in codes_l:
        d = data[c].copy()
        valid = d[B0].notna().all(axis=1).values
        vi = np.where(valid)[0]
        if len(vi) < 200:
            continue
        pick = rng.choice(vi[2:-2], size=min(per, max(1, len(vi) - 5)), replace=False)
        col = np.zeros(len(d), dtype=bool)
        col[pick] = True
        d["_rand_sig"] = col
        rand_data[c] = d
    # 用随机信号跑（把随机信号伪装成条件全满足）
    for c, d in rand_data.items():
        d["cond_position"] = d["_rand_sig"]
        d["cond_streak"] = d["_rand_sig"]
        d["cond_ma60_down"] = d["_rand_sig"]
    btr = SimpleBacktest(cfg.as_dict(), mkt)
    eqr, trr = btr.run(rand_data, B0, use_market=False)
    mr = metrics(eqr, trr, "随机入场")
    print(f"  随机入场: 交易={mr.get('交易笔数',0)}  CAGR={mr.get('CAGR%')}%  "
          f"Sharpe={mr.get('Sharpe')}  回撤={mr.get('最大回撤%')}%  "
          f"胜率={mr.get('胜率%')}%")
    cmp = pd.DataFrame([results[0], mr])
    print()
    print(cmp[[c for c in ["配置", "CAGR%", "Sharpe", "最大回撤%", "交易笔数", "胜率%"]
               if c in cmp.columns]].to_string(index=False))
    d0 = results[0].get("CAGR%", 0) - (mr.get("CAGR%") or 0)
    print(f"\n  B0 vs 随机: {d0:+.2f}pct/年")
    if d0 > 15:
        print("  √ B0 显著优于随机入场")
    elif d0 > 5:
        print("  △ B0 有一定优势")
    else:
        print("  ⚠ B0 相对随机入场优势不明显")
    print()

    # ---- 收益集中度（A 配置）
    trA = trades_all.get("A. B0基础三条件")
    if trA is not None and len(trA):
        print("=" * 110)
        print("  四、收益集中度（A. B0基础三条件）")
        print("=" * 110)
        r = trA["ret"].sort_values(ascending=False)
        for n_rm in [0, 1, 3, 5, 10, 20]:
            rr = r.iloc[n_rm:]
            if len(rr):
                print(f"    去掉最大{n_rm:3d}笔: 平均 {rr.mean()*100:+6.2f}%  "
                      f"胜率 {(rr>0).mean()*100:5.1f}%  笔数 {len(rr)}")
        byc = trA.groupby("code")["ret"].agg(["size", "sum"])
        top5 = byc.nlargest(5, "sum")
        print(f"\n    涉及标的: {len(byc)} 只")
        print(f"    单只最多交易: {byc['size'].max()} 次")
        print(f"    贡献最大5只合计: {top5['sum'].sum()*100:.1f}%  "
              f"(占全部收益 {top5['sum'].sum()/trA['ret'].sum()*100:.1f}%)")
        print(f"    最大单笔盈利: {r.max()*100:+.1f}%   最大单笔亏损: {r.min()*100:+.1f}%")
        print()

    # ---- 输出
    outdir = cache.parent / "output"
    stamp = time.strftime("%Y%m%d_%H%M")
    R.to_csv(outdir / f"filter_eval_{stamp}.csv", index=False, encoding="utf-8-sig")
    for lab, g in yearlies.items():
        safe = lab.replace(". ", "_").replace(" ", "").replace("+", "plus")
        g.to_csv(outdir / f"yearly_{safe}_{stamp}.csv", encoding="utf-8-sig")
    if trA is not None and len(trA):
        trA.to_csv(outdir / f"trades_B0_{stamp}.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/filter_eval_{stamp}.csv")
    print(f"[输出] {outdir}/trades_B0_{stamp}.csv")

    return 0


if __name__ == "__main__":
    sys.exit(main())
