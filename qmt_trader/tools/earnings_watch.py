# -*- coding: utf-8 -*-
"""
earnings_watch.py — 业绩公告全自动监控 v2

v2 改进（基于 step39 位置效应回测）：
  ① 位置分三档：20日 / 60日 / 120日（原来只有单一130日）
  ② 分组按「持有期」区分：
     · A1 中期优选：高增 + 60/120日位置 60~100%（趋势确认，60日+11~17%）
     · A2 短期观察：高增 + 20日位置 <20%（低位，20日+4.7%）
     · B  中位、C 已涨、D 中增、E 低增
  ③ 措辞预警：扫描公告原文的负面/正面词
  ④ 输出位置矩阵（三周期是否共振）

回测依据（6,408 个业绩公告事件）：
  · 位置 vs 60日：Spearman +0.1745  p<0.0001
  · 位置 vs 120日：Spearman +0.2063  p<0.0001
  · D10（位置≥84.7%）60日 +16.64% / 胜率 64.2%
  · D1（位置≤6.9%）60日 −4.13% / 胜率 35.3%

用法：
    python tools/earnings_watch.py                    # 当日
    python tools/earnings_watch.py --date=20261016
    python tools/earnings_watch.py --days=3
    python tools/earnings_watch.py --quiet

退出码：
    0 = 无信号
    2 = 有 A1/A2 信号
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger

# ---- 回测依据的阈值
MIN_GROWTH = 50.0        # 增速下限 %
OUTDIR_NAME = "earnings_watch"

# 措辞预警词表
NEG_WORDS = ["递延", "滞后", "顺延", "下滑", "压力", "短缺", "消化",
             "调整", "下降", "缩减", "退单", "取消", "亏损扩大",
             "不及预期", "放缓", "疲软", "承压"]
POS_WORDS = ["环比提升", "环比增长", "创历史新高", "满产", "满稼动",
             "供不应求", "订单充足", "订单充裕", "量价齐升", "毛利率提升",
             "同比和环比", "持续增长", "显著提升", "大幅增长"]


def scan_text(txt: str) -> tuple:
    """扫描公告措辞，返回 (负面词列表, 正面词列表)。"""
    if not txt:
        return [], []
    neg = [w for w in NEG_WORDS if w in txt]
    pos = [w for w in POS_WORDS if w in txt]
    return neg, pos


def fetch_price(codes: list) -> dict:
    """腾讯批量行情。"""
    import requests
    out = {}
    hdr = {"User-Agent": "Mozilla/5.0"}

    def to_tx(tc):
        c6, sfx = tc.split(".") if "." in tc else (tc, "SZ")
        return ("sh" if sfx.upper() == "SH" else "sz") + c6
    tx = [to_tx(c) for c in codes]
    for i in range(0, len(tx), 600):
        chunk = tx[i:i + 600]
        try:
            r = requests.get("https://qt.gtimg.cn/q=" + ",".join(chunk),
                             headers=hdr, timeout=15)
            r.encoding = "gbk"
            for line in r.text.strip().split("\n"):
                m = re.match(r'v_(\w+)="([^"]*)"', line.strip())
                if not m:
                    continue
                p = m.group(2).split("~")
                if len(p) < 50:
                    continue
                try:
                    out[p[2]] = {
                        "name": p[1], "price": float(p[3]),
                        "pre": float(p[4]), "chg": float(p[32]),
                        "turnover": float(p[38]) if p[38] else np.nan,
                        "pe": float(p[39]) if p[39] else np.nan,
                        "mv": float(p[45]) if p[45] else np.nan,
                        "pb": float(p[46]) if p[46] else np.nan,
                    }
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.12)
    return out


def calc_positions(c6: str, cache: Path, rt: dict,
                   pro=None, ts_code: str = None) -> dict:
    """
    返回多周期位置：20日/60日/120日 + 距高。

    优先用本地缓存；缺失时用 Tushare 补（大市值股不在本地小盘池里）。
    """
    out = {"p20": None, "p60": None, "p120": None, "dd120": None}

    def compute(x, px, pre):
        """x 需含 high/low/close，按时间升序。"""
        if len(x) < 20:
            return out
        last = float(x["close"].iloc[-1])
        if not np.isfinite(px) or last <= 0:
            return out
        ratio = pre / last if np.isfinite(pre) else 1.0
        hi_arr = x["high"].values * ratio
        lo_arr = x["low"].values * ratio
        for n, key in [(20, "p20"), (60, "p60"), (120, "p120")]:
            if len(x) >= n:
                h = np.nanmax(hi_arr[-n:])
                l = np.nanmin(lo_arr[-n:])
                if h > l:
                    out[key] = round((px - l) / (h - l) * 100, 1)
        n120 = min(len(x), 120)
        h120 = np.nanmax(hi_arr[-n120:])
        if h120 > 0:
            out["dd120"] = round((px / h120 - 1) * 100, 1)
        return out

    px = rt.get("price", np.nan)
    pre = rt.get("pre", np.nan)

    # 1) 本地缓存
    for dd in ("kline", "kline_delisted", "kline_full"):
        fp = cache / dd / f"{c6}_qfq.csv"
        if not fp.exists():
            continue
        try:
            x = pd.read_csv(fp, usecols=lambda c: c in
                            ("date", "high", "low", "close"))
            if len(x) < 20:
                break
            x = x.copy()
            x["date"] = pd.to_datetime(x["date"])
            x = x.sort_values("date")
            # 本地是前复权，用比例换算到现价
            last_close = float(x["close"].iloc[-1])
            if last_close <= 0:
                break
            ratio = pre / last_close if np.isfinite(pre) else 1.0
            for col in ("high", "low", "close"):
                x[col] = x[col] * ratio
            if len(x) >= 20:
                return compute(x, px, px)  # 已换算，pre 用 px 避免二次缩放
        except Exception:
            pass
        break

    # 2) Tushare 补数（大市值股）
    if pro is not None and ts_code:
        try:
            start = (datetime.now() -
                     timedelta(days=280)).strftime("%Y%m%d")
            end = datetime.now().strftime("%Y%m%d")
            d = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
            if d is not None and len(d) >= 20:
                d = d.copy()
                d["date"] = pd.to_datetime(d["trade_date"], format="%Y%m%d")
                d = d.sort_values("date")
                for c in ("high", "low", "close"):
                    d[c] = pd.to_numeric(d[c], errors="coerce")
                return compute(d, px, pre)
        except Exception:
            pass
    return out


LEDGER_NAME = "forecast_ledger.csv"


def _period_window(ymd: str) -> str:
    """按公告日推断其所属报告期的起始日。"""
    try:
        d = datetime.strptime(ymd, "%Y%m%d")
    except ValueError:
        d = datetime.now()
    y, m = d.year, d.month
    if m <= 2:
        return "%d0101" % (y - 1)
    if m <= 4:
        return "%d0101" % y
    if m <= 8:
        return "%d0401" % y
    if m <= 10:
        return "%d0701" % y
    return "%d1001" % y


def load_ledger(outdir):
    """读取本地预告台账。台账按 (ts_code, end_date, ann_date) 去重，保留每次公告。"""
    p = outdir / LEDGER_NAME
    if not p.exists():
        return pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "type", "g"])
    try:
        L = pd.read_csv(p, dtype={"ts_code": str, "ann_date": str, "end_date": str})
        for c in ("ann_date", "end_date"):
            if c in L.columns:
                L[c] = L[c].astype(str).str[:8]
        L["g"] = pd.to_numeric(L["g"], errors="coerce")
        return L.dropna(subset=["g"])
    except Exception:
        return pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "type", "g"])


def save_ledger(outdir, L):
    p = outdir / LEDGER_NAME
    L = L.drop_duplicates(subset=["ts_code", "end_date", "ann_date"], keep="last")
    L = L.sort_values(["ts_code", "end_date", "ann_date"])
    L.to_csv(p, index=False, encoding="utf-8-sig")


def detect_revisions(L, ymd, days=3):
    """
    从台账识别修正事件。

    返回 {(ts_code, ann_date_str): {...}}

    为什么用台账而非接口：
        Tushare forecast_vip 对同一 (股票, 报告期) 只保留最新一条，
        用 period= 或区间拉取都拿不到首次公告的基线，
        导致 shift(1) 后 prev_g 全为 NaN，修正识别恒为空（已实测）。
        本地台账累积每次公告，才能正确建基线。
    """
    out = {}
    if L is None or len(L) == 0:
        return out
    cur = datetime.strptime(ymd, "%Y%m%d")
    win = {(cur - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)}

    L = L.sort_values(["ts_code", "end_date", "ann_date"]).copy()
    L["prev_g"] = L.groupby(["ts_code", "end_date"])["g"].shift(1)
    rev = L[L["prev_g"].notna()]
    for r in rev.itertuples():
        if str(r.ann_date) not in win:
            continue
        d = r.g - r.prev_g
        if d > 1:
            tag = "向上修正"
        elif d < -1:
            tag = "向下修正"
        else:
            tag = "基本不变"
        out[(r.ts_code, str(r.ann_date))] = {
            "dirn": tag, "prev_g": float(r.prev_g),
            "delta": float(d), "cur_g": float(r.g),
        }
    return out


def update_ledger(pro, outdir, ymd, days=3):
    """从接口拉本期窗口的公告，追加进台账。"""
    L = load_ledger(outdir)
    pstart = _period_window(ymd)
    df = None
    for _ in range(3):
        try:
            df = pro.forecast_vip(start_date=pstart, end_date=ymd)
            break
        except Exception:
            time.sleep(1.2)
    if df is None or len(df) == 0:
        return L
    df = df.copy()
    df["ann_date"] = df["ann_date"].astype(str).str[:8]
    df["end_date"] = df["end_date"].astype(str).str[:8]
    df["g"] = pd.to_numeric(df["p_change_min"], errors="coerce").fillna(
        pd.to_numeric(df["p_change_max"], errors="coerce"))
    df = df.dropna(subset=["g"])
    keep = [c for c in ["ts_code", "ann_date", "end_date", "type", "g"] if c in df.columns]
    add = df[keep]
    if len(L):
        L = pd.concat([L, add], ignore_index=True)
    else:
        L = add
    L = L.drop_duplicates(subset=["ts_code", "end_date", "ann_date"], keep="last")
    save_ledger(outdir, L)
    return L


def main():
    date = None
    days = 1
    quiet = False
    for a in sys.argv[1:]:
        if a.startswith("--date="):
            date = a.split("=")[1]
        if a.startswith("--days="):
            days = int(a.split("=")[1])
        if a == "--quiet":
            quiet = True
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    # ---- 护栏：禁止用 --date 回补历史 ----
    # 原因：calc_positions() 的价格取 fetch_price() 的实时价，
    # 若 --date 指向历史日，会出现「窗口是历史、价格是现在」的时点错配，
    # 导致 p20/p60/p120 全部算错（实测偏差中位 9.9pp、最大 91.6pp）。
    # 历史回补请用独立重建脚本（见 回补污染事故说明.md）。
    _today = datetime.now().strftime("%Y%m%d")
    if date != _today:
        msg = (
            "[拒绝] --date=%s 属于历史回补。\n"
            "   calc_positions() 使用实时价计算位置分位，回补历史日会产生\n"
            "   「窗口是历史、价格是现在」的时点错配，实测偏差中位 9.9pp、最大 91.6pp。\n"
            "   历史回补请使用独立重建脚本，详见 tools/回补污染事故说明.md\n"
            "   如需强制运行（仅用于验证代码路径），加 --allow-historical"
        ) % date
        if "--allow-historical" not in sys.argv:
            print(msg)
            return 1
        print("[警告] 已强制放行历史回补，结果可能含前视污染")

    cfg = load_config()
    setup_logger("ew", log_dir=None, level="ERROR", console=False)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache.parent / OUTDIR_NAME
    outdir.mkdir(parents=True, exist_ok=True)
    arch = outdir / "history"
    arch.mkdir(exist_ok=True)

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    end = pd.Timestamp(date)
    start = end - pd.Timedelta(days=days - 1)

    def pull(api, **kw):
        for _ in range(3):
            try:
                return getattr(pro, api)(**kw)
            except Exception:
                time.sleep(1.0)
        return None

    fc = pull("forecast_vip", start_date=start.strftime("%Y%m%d"),
              end_date=end.strftime("%Y%m%d"))
    ex = pull("express_vip", start_date=start.strftime("%Y%m%d"),
              end_date=end.strftime("%Y%m%d"))
    fc = fc if fc is not None else pd.DataFrame()
    ex = ex if ex is not None else pd.DataFrame()

    rows = []
    if len(fc):
        fc = fc.copy()
        fc["ann_date"] = pd.to_datetime(fc["ann_date"].astype(str),
                                        format="%Y%m%d", errors="coerce")
        for c in ("p_change_min", "p_change_max", "net_profit_min",
                  "net_profit_max", "last_parent_net"):
            if c in fc.columns:
                fc[c] = pd.to_numeric(fc[c], errors="coerce")
        fc["增速"] = fc["p_change_min"].fillna(fc["p_change_max"])
        for _, r in fc.iterrows():
            reason = str(r.get("change_reason", "") or "")
            summ = str(r.get("summary", "") or "")
            neg, pos = scan_text(reason + " " + summ)
            rows.append({
                "ts_code": r["ts_code"], "来源": "预告",
                "类型": str(r.get("type", "") or ""),
                "增速": r.get("增速"),
                "净利下限万": r.get("net_profit_min"),
                "去年同期万": r.get("last_parent_net"),
                "公告日": r["ann_date"],
                "要点": re.sub(r"\s+", " ", summ)[:200],
                "负面词": "/".join(neg), "正面词": "/".join(pos),
                "负面数": len(neg), "正面数": len(pos),
                "全文": re.sub(r"\s+", " ", reason)[:600],
            })
    if len(ex):
        ex = ex.copy()
        ex["ann_date"] = pd.to_datetime(ex["ann_date"].astype(str),
                                        format="%Y%m%d", errors="coerce")
        for c in ("revenue", "n_income", "diluted_eps", "diluted_roe"):
            if c in ex.columns:
                ex[c] = pd.to_numeric(ex[c], errors="coerce")
        for _, r in ex.iterrows():
            perf = str(r.get("perf_summary", "") or "")
            neg, pos = scan_text(perf)
            rows.append({
                "ts_code": r["ts_code"], "来源": "快报", "类型": "快报",
                "增速": np.nan,
                "净利下限万": (r.get("n_income") / 1e4
                            if pd.notna(r.get("n_income")) else None),
                "去年同期万": None,
                "公告日": r["ann_date"],
                "要点": re.sub(r"\s+", " ", perf)[:200],
                "负面词": "/".join(neg), "正面词": "/".join(pos),
                "负面数": len(neg), "正面数": len(pos),
                "全文": perf[:600],
            })

    # ---- 东财补充源（取并集，补齐 Tushare 覆盖盲区）
    n_tx_raw = len(rows)
    try:
        from em_forecast import fetch_em_forecast
        win = [start + pd.Timedelta(days=i)
               for i in range((end - start).days + 1)]
        em_df = fetch_em_forecast([d.strftime("%Y%m%d") for d in win],
                                  verbose=not quiet)
        if len(em_df):
            have = set()
            for r in rows:
                try:
                    have.add((r["ts_code"], pd.Timestamp(r["公告日"]).strftime("%Y%m%d")))
                except Exception:
                    pass
            added = 0
            for _, r in em_df.iterrows():
                key = (r["ts_code"], pd.Timestamp(r["公告日"]).strftime("%Y%m%d"))
                if key in have:
                    continue
                neg, pos = scan_text(str(r.get("全文") or ""))
                rows.append({
                    "ts_code": r["ts_code"], "来源": r["来源"],
                    "类型": r["类型"], "增速": r["增速"],
                    "净利下限万": r["净利下限万"],
                    "去年同期万": r["去年同期万"],
                    "公告日": r["公告日"],
                    "要点": r["要点"],
                    "负面词": "/".join(neg), "正面词": "/".join(pos),
                    "负面数": len(neg), "正面数": len(pos),
                    "全文": r["全文"],
                })
                have.add(key)
                added += 1
            if not quiet and added:
                print(f"  [东财补充] 新增 {added} 只 "
                      f"(Tushare {n_tx_raw} → 合计 {len(rows)})")
    except Exception as _e:
        if not quiet:
            print(f"  [东财补充] 跳过：{type(_e).__name__}: {_e}")

    if not rows:
        if not quiet:
            print("  当日无新增业绩公告")
        return 0

    A = pd.DataFrame(rows)
    px = fetch_price(A["ts_code"].tolist())

    recs = []
    n_tx = 0
    for _, r in A.iterrows():
        c6 = r["ts_code"].split(".")[0]
        rt = px.get(c6, {})
        pos = calc_positions(c6, cache, rt, pro=pro, ts_code=r["ts_code"])
        if pos["p20"] is not None:
            n_tx += 1
        rec = {
            "代码": c6, "名称": rt.get("name", "?"), "ts_code": r["ts_code"],
            "来源": r["来源"], "类型": r["类型"],
            "增速%": r["增速"], "公告日": r["公告日"],
            "现价": rt.get("price"), "今日%": rt.get("chg"),
            "换手%": rt.get("turnover"),
            "PE": rt.get("pe"), "PB": rt.get("pb"),
            "总市值亿": rt.get("mv"),
            "位置20日%": pos["p20"], "位置60日%": pos["p60"],
            "位置120日%": pos["p120"], "距120日高%": pos["dd120"],
            "负面词": r["负面词"], "正面词": r["正面词"],
            "负面数": r["负面数"], "正面数": r["正面数"],
            "要点": r["要点"], "全文": r["全文"],
        }
        recs.append(rec)
    D = pd.DataFrame(recs)
    if not quiet:
        print(f'  [位置] 成功获取 {n_tx}/{len(D)} 只  '
              f'({len(D)-n_tx} 只用 Tushare 补数)')
        print()

    # ---- 修正识别（向下修正是已验证的负向信号）
    rev_map = {}
    try:
        # 窗口基准用 date（脚本参数），不能用 now()：
        # 用 --date=历史日 回补时，若按 now() 开窗会拿不到该日的修正记录
        _L = update_ledger(pro, outdir, date, days=max(days, 3))
        rev_map = detect_revisions(_L, date, days=max(days, 3))
        if rev_map and not quiet:
            print(f"  [修正] 识别到 {len(rev_map)} 条修正公告 "
                  f"(上{sum(1 for v in rev_map.values() if v['dirn']=='向上修正')} "
                  f"下{sum(1 for v in rev_map.values() if v['dirn']=='向下修正')})")
    except Exception as _e:
        if not quiet:
            print(f"  [修正] 跳过：{type(_e).__name__}")

    def rev_of(row):
        code = row.get("ts_code", "")
        try:
            ad = pd.Timestamp(row.get("公告日")).strftime("%Y%m%d")
        except Exception:
            return None
        return rev_map.get((code, ad))

    # ---- 新分组（按持有期）
    def grp(r):
        g = r["增速%"]
        p60 = r["位置60日%"]
        p120 = r["位置120日%"]
        p20 = r["位置20日%"]
        neg = r["负面数"]
        # 修正事件优先级最高：向下修正是已验证的负向信号，不参与位置分组
        rv = rev_of(r)
        if rv is not None:
            if rv["dirn"] == "向下修正":
                return "X 向下修正（回避）"
            if rv["dirn"] == "向上修正":
                return "Y 向上修正（不显著）"
        if pd.isna(g):
            return "快报（需核实）"
        if g < 20:
            return "E 低增/负增长"
        # 措辞有瑕疵 → 降级
        flaw = neg >= 2
        if g >= MIN_GROWTH:
            mid_hi = ((p60 is not None and p60 >= 60) or
                      (p120 is not None and p120 >= 60))
            low = p20 is not None and p20 < 20
            if mid_hi and not flaw:
                return "A1 高增+中期高位（优选）"
            if mid_hi and flaw:
                return "A1' 高增+高位但措辞有瑕疵"
            if low:
                return "A2 高增+低位（短期）"
            return "B 高增+中位"
        if g >= 20:
            return "D 中增"
        return "E 低增"
    D["分组"] = D.apply(grp, axis=1)

    # 自选池
    wl_hits = []
    wl_p = outdir / "watchlist.csv"
    if wl_p.exists():
        try:
            WL = pd.read_csv(wl_p, dtype={"代码": str})
            wl_codes = set(WL["代码"].astype(str).str.zfill(6))
            hit = D[D["代码"].isin(wl_codes)]
            if len(hit):
                wl_hits = hit.to_dict("records")
        except Exception:
            pass

    # ---- 输出
    ORDER = ["X 向下修正（回避）",
             "Y 向上修正（不显著）",
             "A1 高增+中期高位（优选）",
             "A1' 高增+高位但措辞有瑕疵",
             "A2 高增+低位（短期）",
             "B 高增+中位", "D 中增", "E 低增/负增长",
             "快报（需核实）"]
    if not quiet:
        print("=" * 148)
        print(f"  业绩公告监控 v2   {start.date()} ~ {end.date()}   "
              f"{datetime.now():%Y-%m-%d %H:%M:%S}")
        print("=" * 148)
        print()
        for g in ORDER:
            sub = D[D["分组"] == g]
            if len(sub) == 0:
                continue
            note = {
                "A1 高增+中期高位（优选）": "回测：60日 +11~17%、胜率 62~64%",
                "A1' 高增+高位但措辞有瑕疵": "⚠ 公告含负面词，需人工核查",
                "A2 高增+低位（短期）": "回测：20日 +4.7%、胜率 66%（中期转弱）",
                "B 高增+中位": "回测：60日 +5%、胜率 48%",
                "D 中增": "20~50% 档 20日 +6.0%、胜率 65%",
                "E 低增/负增长": "低于随机基准",
                "快报（需核实）": "快报无增速字段，需人工核实",
                "X 向下修正（回避）": "已回测：20日超额 -2.40pp、t=-2.77、胜率38%（n=421）",
                "Y 向上修正（不显著）": "已回测：20日超额 +0.94pp、t=0.85（不显著）",
            }.get(g, "")
            print(f"  【{g}】{len(sub)} 只   {note}")
            print(f"    {'代码':<8}{'名称':<11}{'增速%':>9}{'p20':>7}{'p60':>7}"
                  f"{'p120':>7}{'距高%':>8}{'今日%':>8}{'PE':>9}"
                  f"{'市值亿':>9}{'负面':>6}")
            print("    " + "-" * 104)
            for _, r in sub.sort_values("增速%", ascending=False).iterrows():
                gg = f"{r['增速%']:.1f}" if pd.notna(r["增速%"]) else "—"
                def f(v):
                    return f"{v:.1f}" if pd.notna(v) and v is not None else "—"
                pe = (f"{r['PE']:.1f}" if pd.notna(r["PE"]) and r["PE"] > 0
                      else "亏损")
                mv = f"{r['总市值亿']:.0f}" if pd.notna(r["总市值亿"]) else "—"
                print(f"    {r['代码']:<8}{str(r['名称'])[:10]:<11}{gg:>9}"
                      f"{f(r['位置20日%']):>7}{f(r['位置60日%']):>7}"
                      f"{f(r['位置120日%']):>7}{f(r['距120日高%']):>8}"
                      f"{f(r['今日%']):>8}{pe:>9}{mv:>9}"
                      f"{int(r['负面数']):>6}")
            # 措辞提示
            flaw_rows = sub[sub["负面数"] >= 2]
            if len(flaw_rows):
                print("    [!] 措辞预警:")
                for _, r in flaw_rows.iterrows():
                    print(f"      {r['代码']} {r['名称']}: 负面词 [{r['负面词']}]")
            print()
        if wl_hits:
            print("=" * 148)
            print(f"  ★ 自选池命中（{len(wl_hits)} 只）")
            print("=" * 148)
            for h in wl_hits:
                print(f"    {h['代码']} {h['名称']}  {h['来源']}  "
                      f"增速 {h['增速%']}  分组 {h['分组']}")
            print()

    # ---- 归档
    stamp = pd.Timestamp(date).strftime("%Y%m%d")
    D.drop(columns=["全文"]).to_csv(arch / f"earnings_{stamp}.csv",
                                     index=False, encoding="utf-8-sig")
    # 全文单独存
    try:
        D[["代码", "名称", "全文"]].to_csv(
            arch / f"text_{stamp}.csv", index=False, encoding="utf-8-sig")
    except Exception:
        pass

    log_p = outdir / "log.csv"
    A1 = int((D["分组"] == "A1 高增+中期高位（优选）").sum())
    A2 = int((D["分组"] == "A2 高增+低位（短期）").sum())
    row = {
        "运行时间": datetime.now().isoformat(timespec="seconds"),
        "日期": stamp, "公告数": len(D),
        "A1": A1, "A2": A2,
        "A1瑕疵": int((D["分组"] == "A1' 高增+高位但措辞有瑕疵").sum()),
        "B": int((D["分组"] == "B 高增+中位").sum()),
        "自选命中": len(wl_hits),
    }
    if log_p.exists():
        L = pd.read_csv(log_p)
        L = pd.concat([L, pd.DataFrame([row])], ignore_index=True)
    else:
        L = pd.DataFrame([row])
    L.to_csv(log_p, index=False, encoding="utf-8-sig")

    # ---- Markdown
    md = outdir / f"report_{stamp}.md"
    with open(md, "w", encoding="utf-8") as f:
        f.write(f"# 业绩公告监控 {stamp}\n\n")
        f.write(f"运行时间：{datetime.now():%Y-%m-%d %H:%M:%S}　|　"
                f"公告 {len(D)} 家　|　A1 {A1}　A2 {A2}\n\n")
        for g in ORDER:
            sub = D[D["分组"] == g]
            if len(sub) == 0:
                continue
            f.write(f"## {g}（{len(sub)}）\n\n")
            f.write("| 代码 | 名称 | 增速% | p20 | p60 | p120 | 距高% | "
                    "今日% | PE | 市值亿 | 负面词 |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|---|\n")
            for _, r in sub.sort_values("增速%", ascending=False).iterrows():
                def fv(v, d=1):
                    return f"{v:.{d}f}" if pd.notna(v) and v is not None else "—"
                f.write(f"| {r['代码']} | {r['名称']} | "
                        f"{fv(r['增速%'])} | {fv(r['位置20日%'])} | "
                        f"{fv(r['位置60日%'])} | {fv(r['位置120日%'])} | "
                        f"{fv(r['距120日高%'])} | {fv(r['今日%'],2)} | "
                        f"{fv(r['PE'])} | {fv(r['总市值亿'],0)} | "
                        f"{r['负面词'] or '—'} |\n")
            f.write("\n")
        f.write(f"\n---\n数据：`history/earnings_{stamp}.csv`\n")

    if not quiet:
        print(f"[归档] history/earnings_{stamp}.csv")
        print(f"[报告] report_{stamp}.md")
        print()
        print("=" * 148)
        print(f"  ★ 结论：A1 {A1} 只（优选）  A2 {A2} 只（短期）  "
              f"自选命中 {len(wl_hits)} 只")
        print("=" * 148)

    return 2 if (A1 or A2) else 0


if __name__ == "__main__":
    sys.exit(main())
