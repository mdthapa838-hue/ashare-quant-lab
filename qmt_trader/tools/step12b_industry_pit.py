# -*- coding: utf-8 -*-
"""
step12b_industry_pit.py — PIT 行业归属（含历史进出）

问题：index_member_all 只返回当前成分（out_date 全空），
      无法还原历史时点的行业归属 → 中性化会有前视偏差。

解决：index_member 返回带 in_date/out_date 的完整历史。
      对每个交易日 t，取 in_date <= t < out_date 的行业归属。

输出：
  data_cache/industry/sw_members_pit.csv
  列：l1_code, l1_name, ts_code, in_date, out_date, is_new
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, r"F:\gpt1\REA\workbuddy_deepseek_bridge\stock_suite")

from core.config import load_config
from core.logger import setup_logger


def main():
    cfg = load_config()
    setup_logger("ind2", log_dir=None, level="WARNING", console=True)
    cache = Path(cfg.get("datasource.cache_dir"))
    outdir = cache / "industry"
    outdir.mkdir(parents=True, exist_ok=True)

    import credentials  # type: ignore
    import tushare as ts  # type: ignore
    pro = ts.pro_api(credentials.get_tushare_token())

    l1 = pd.read_csv(outdir / "sw_l1.csv")
    print(f"一级行业: {len(l1)} 个")
    print()

    frames = []
    for i, (_, r) in enumerate(l1.iterrows()):
        code = r["index_code"]
        name = r["industry_name"]
        got = None
        for attempt in range(3):
            try:
                got = pro.index_member(index_code=code)
                break
            except Exception as e:
                msg = str(e)
                if "每分钟" in msg or "频率" in msg:
                    time.sleep(8 * (attempt + 1))
                else:
                    time.sleep(1.5 * (attempt + 1))
        if got is not None and len(got):
            got = got.copy()
            got["l1_code"] = code
            got["l1_name"] = name
            frames.append(got)
            n_out = got["out_date"].notna().sum() if "out_date" in got.columns else 0
            print(f"  {name:<12} {len(got):>5} 条  （已移出 {n_out}）")
        else:
            print(f"  {name:<12} 失败")
        time.sleep(0.35)

    if not frames:
        print("无数据")
        return 1

    M = pd.concat(frames, ignore_index=True)
    for c in ("in_date", "out_date"):
        if c in M.columns:
            M[c] = pd.to_datetime(M[c].astype(str), format="%Y%m%d", errors="coerce")

    print()
    print(f"合计 {len(M)} 条")
    print(f"唯一股票 {M['con_code'].nunique()} 只")
    if "out_date" in M.columns:
        print(f"含 out_date: {M['out_date'].notna().sum()} 条（历史变更记录）")
    print()

    M.to_csv(outdir / "sw_members_pit.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] {outdir}/sw_members_pit.csv")

    # ---- 生成 code -> 行业映射（PIT）
    # 同时给出「当前行业」（out_date 为空）
    cur = M[M["out_date"].isna()].drop_duplicates(subset=["con_code"], keep="first")
    mp = cur[["con_code", "l1_code", "l1_name"]].copy()
    mp["code6"] = mp["con_code"].str[:6]
    mp.to_csv(outdir / "code_industry.csv", index=False, encoding="utf-8-sig")
    print(f"[输出] 当前行业映射: {len(mp)} 只")

    # ---- 对照并集
    un = cache.parent / "output" / "universe_union.csv"
    if un.exists():
        U = pd.read_csv(un, dtype={"code6": str})
        have = set(mp["code6"])
        cov = U["code6"].isin(have).sum()
        print()
        print(f"并集 {len(U)} 只中有行业分类: {cov} ({cov/len(U)*100:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
