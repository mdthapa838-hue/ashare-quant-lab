# -*- coding: utf-8 -*-
"""
step9_fundamental.py — 基本面因子库（Tushare）

为下一阶段策略测试准备基本面数据：
  Tushare 接口：
    fina_indicator  财务指标（ROE/毛利率/现金流/负债率）
    income          利润表
    balancesheet    资产负债表
    cashflow        现金流量表
    daily_basic     估值（PE/PB/换手/股息率）
    forecast        业绩预告
    express         业绩快报

关键：**按公告日（ann_date）生效，不能用报告期末（end_date）**
      —— 这是 PIT（Point-In-Time）的核心要求

输出：
  data_cache/fundamental/{ts_code}.csv
  列：end_date, ann_date, roe, grossprofit_margin, debt_to_assets,
      ocf_to_or, netprofit_yoy, or_yoy, eps, bps, ...
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


# 财务指标字段（fina_indicator）
FINA_FIELDS = ("ts_code,ann_date,end_date,eps,dt_eps,total_revenue_ps,"
               "revenue_ps,capital_rese_ps,surplus_rese_ps,undist_profit_ps,"
               "extra_item,profit_dedt,gross_margin,current_ratio,quick_ratio,"
               "cash_ratio,ar_turn,ca_turn,fa_turn,assets_turn,"
               "op_income,valuechange_income,interst_income,daa,ebit,ebitda,"
               "fcff,fcfe,current_exint,noncurrent_exint,interestdebt,"
               "netdebt,tangible_asset,working_capital,networking_capital,"
               "invest_capital,retained_earnings,"
               "diluted2_eps,bps,ocfps,retainedps,cfps,ebit_ps,fcff_ps,fcfe_ps,"
               "netprofit_margin,grossprofit_margin,cogs_of_sales,"
               "expense_of_sales,profit_to_gr,saleexp_to_gr,adminexp_of_gr,"
               "finaexp_of_gr,impai_ttm,gc_of_gr,op_of_gr,ebit_of_gr,"
               "roe,roe_waa,roe_dt,roa,npta,roic,"
               "debt_to_assets,assets_to_eqt,dp_assets_to_eqt,ca_to_assets,"
               "nca_to_assets,tbassets_to_totalassets,"
               "ocf_to_debt,ocf_to_interestdebt,ocf_to_netdebt,"
               "ebit_to_interest,longdeb_to_debt,ocf_to_shortdebt,"
               "debt_to_eqt,eqt_to_debt,eqt_to_interestdebt,eqt_to_netdebt,"
               "ocf_to_liabilities,ocf_to_assets,"
               "or_yoy,op_yoy,ebt_yoy,netprofit_yoy,dt_netprofit_yoy,"
               "ocf_yoy,roe_yoy,bps_yoy,assets_yoy,eqt_yoy,tr_yoy,")

INCOME_FIELDS = ("ts_code,ann_date,end_date,total_revenue,revenue,"
                 "oper_cost,operate_profit,total_profit,n_income,"
                 "n_income_attr_p,basic_eps")
BALANCE_FIELDS = ("ts_code,ann_date,end_date,total_assets,total_cur_assets,"
                  "total_liab,total_cur_liab,total_hldr_eqy_inc_min_int,"
                  "money_cap,accounts_receiv,inventories,goodwill,"
                  "total_share,float_share")
CASHFLOW_FIELDS = ("ts_code,ann_date,end_date,n_cashflow_act,"
                   "n_cashflow_inv_act,n_cash_flows_fnc_act,c_fr_sale_sg,"
                   "free_cashflow")


def fetch_paged(pro, api: str, ts_code: str, start: str, end: str,
                fields: str, max_pages: int = 6) -> pd.DataFrame | None:
    """分页拉取（财报数据按报告期，一页通常够，但保险起见分页）。"""
    frames = []
    cur_end = pd.Timestamp(end)
    start_ts = pd.Timestamp(start)
    for _ in range(max_pages):
        got = None
        for attempt in range(3):
            try:
                fn = getattr(pro, api)
                got = fn(ts_code=ts_code,
                         start_date=cur_end.strftime("%Y%m%d"),
                         end_date=cur_end.strftime("%Y%m%d"),
                         fields=fields)
                break
            except Exception as e:
                msg = str(e)
                if "每分钟" in msg or "频率" in msg:
                    time.sleep(8 * (attempt + 1))
                else:
                    time.sleep(1.2 * (attempt + 1))
        if got is not None and len(got):
            frames.append(got)
        # 按年回溯
        cur_end = cur_end - pd.Timedelta(days=400)
        if cur_end < start_ts:
            break
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["end_date"]) if "end_date" in df.columns else df
    return df.sort_values("end_date")


def main():
    cfg = load_config()
    setup_logger("fund", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache / "fundamental"
    outdir.mkdir(parents=True, exist_ok=True)

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    # 先用少量股票验证接口
    print("=" * 100)
    print("  基本面数据接口验证")
    print("=" * 100)
    print()

    test_codes = ["600630.SH", "000001.SZ", "300750.SZ"]
    for tc in test_codes:
        print(f"[{tc}]")
        for api, fields in [("fina_indicator", FINA_FIELDS),
                            ("income", INCOME_FIELDS),
                            ("balancesheet", BALANCE_FIELDS),
                            ("cashflow", CASHFLOW_FIELDS)]:
            try:
                fn = getattr(pro, api)
                df = fn(ts_code=tc, start_date="20230101", end_date="20261014",
                        fields=fields)
                n = 0 if df is None else len(df)
                cols = len(df.columns) if df is not None and n else 0
                print(f"    {api:<16} {n:>3} 行  {cols:>2} 列")
            except Exception as e:
                print(f"    {api:<16} ✗ {str(e)[:60]}")
        print()

    # 抽样看字段
    print("=" * 100)
    print("  fina_indicator 字段样例")
    print("=" * 100)
    try:
        df = pro.fina_indicator(ts_code="600630.SH",
                                start_date="20250101", end_date="20261014")
        if df is not None and len(df):
            print(f"  行数 {len(df)}  列数 {len(df.columns)}")
            print(f"  列名: {list(df.columns)[:40]}")
            print()
            cols = [c for c in ("end_date", "ann_date", "roe", "roe_dt",
                                "grossprofit_margin", "netprofit_margin",
                                "debt_to_assets", "ocf_to_or", "or_yoy",
                                "netprofit_yoy", "eps", "bps")
                    if c in df.columns]
            print(df[cols].to_string(index=False))
    except Exception as e:
        print(f"  失败: {str(e)[:100]}")
    print()

    # 公告日 vs 报告期 验证
    print("=" * 100)
    print("  PIT 验证：公告日 vs 报告期")
    print("=" * 100)
    print()
    print("  关键：财务数据必须在【公告日】之后才可用")
    print("       用报告期末(end_date)提前交易 = 未来函数")
    print()
    try:
        df = pro.fina_indicator(ts_code="600630.SH",
                                start_date="20240101", end_date="20261014")
        if df is not None and len(df):
            sub = df[["end_date", "ann_date"]].head(8)
            print(sub.to_string(index=False))
            if "ann_date" in df.columns and "end_date" in df.columns:
                d1 = pd.to_datetime(df["end_date"], format="%Y%m%d", errors="coerce")
                d2 = pd.to_datetime(df["ann_date"], format="%Y%m%d", errors="coerce")
                lag = (d2 - d1).dt.days.dropna()
                if len(lag):
                    print()
                    print(f"  公告滞后天数: 最小 {lag.min():.0f}  "
                          f"中位 {lag.median():.0f}  最大 {lag.max():.0f}")
                    print(f"  → 平均 {lag.mean():.0f} 天后才可用")
    except Exception as e:
        print(f"  失败: {str(e)[:80]}")
    print()

    # 小规模下载验证
    print("=" * 100)
    print("  下载 20 只样本测试")
    print("=" * 100)
    print()
    union = pd.read_csv(cache.parent / "output" / "universe_union.csv",
                        dtype={"code6": str})
    # 优先下载已缓存的股票（保证能立即使用）
    have = {os.path.basename(f)[:-len("_qfq.csv")]
            for f in []}
    test_list = union["code6"].head(20).tolist()

    def to_ts(c6):
        if c6.startswith(("60", "68")):
            return c6 + ".SH"
        if c6.startswith(("8", "4", "9")):
            return c6 + ".BJ"
        return c6 + ".SZ"

    ok = fail = 0
    t0 = time.time()
    for i, c6 in enumerate(test_list):
        tc = to_ts(c6)
        p = outdir / f"{c6}.csv"
        if p.exists():
            continue
        try:
            df = pro.fina_indicator(ts_code=tc, start_date="20140101",
                                    end_date="20261231")
            if df is not None and len(df):
                df.to_csv(p, index=False, encoding="utf-8-sig")
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
        time.sleep(0.4)
        if (i + 1) % 5 == 0:
            print(f"  {i+1}/{len(test_list)}  成功{ok} 失败{fail}  "
                  f"{time.time()-t0:.0f}s", flush=True)

    print()
    print(f"测试完成: 成功{ok} 失败{fail}")
    print(f"[输出] {outdir}")
    print()
    print("=" * 100)
    print("  小结")
    print("=" * 100)
    print()
    print("  下一步：把全量 4441 只的 fina_indicator 下载下来（约需 1-2 小时）")
    print("  然后可以测试：ROE、毛利率、现金流、负债率、业绩增速 等因子")
    return 0


if __name__ == "__main__":
    sys.exit(main())
