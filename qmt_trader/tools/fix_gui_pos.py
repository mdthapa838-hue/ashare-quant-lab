# -*- coding: utf-8 -*-
"""fix_gui_pos.py — 给 GUI 的位置计算加 Tushare 补数兜底。"""
import io
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
p = HERE / "earnings_gui.py"
s = io.open(p, encoding="utf-8").read()

old_start = s.find("    # ---- 位置\n    def _pos(self, c6, rt, cache):")
old_end = s.find("    def _fetch(self) -> pd.DataFrame:")
if old_start < 0 or old_end < 0:
    print("未找到 _pos 段落")
    raise SystemExit(1)

new = '''    # ---- 位置（本地缓存 → Tushare 兜底）
    def _pos(self, c6, rt, cache, pro=None, ts_code=None):
        o = {"p20": None, "p60": None, "p120": None, "dd120": None}

        def compute(x, px, pre):
            h = x["high"].values.astype(float)
            l = x["low"].values.astype(float)
            c = x["close"].values.astype(float)
            last = c[-1]
            if last <= 0 or not np.isfinite(px):
                return o
            ratio = pre / last if (pre and np.isfinite(pre)) else 1.0
            hi = h * ratio
            lo = l * ratio
            for n, k in [(20, "p20"), (60, "p60"), (120, "p120")]:
                if len(x) >= n:
                    hh = np.nanmax(hi[-n:])
                    ll = np.nanmin(lo[-n:])
                    if hh > ll:
                        o[k] = round((px - ll) / (hh - ll) * 100, 1)
            n120 = min(len(x), 120)
            h120 = np.nanmax(hi[-n120:])
            if h120 > 0:
                o["dd120"] = round((px / h120 - 1) * 100, 1)
            return o

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
                if len(x) >= 20:
                    x = x.sort_values("date")
                    # 本地 qfq 需换算到现价；已换算后 pre=px 防二次缩放
                    last = float(x["close"].values[-1])
                    if last > 0 and np.isfinite(pre):
                        r = pre / last
                        for col in ("high", "low", "close"):
                            x[col] = x[col].astype(float) * r
                    if np.isfinite(px):
                        return compute(x, px, px)
            except Exception:
                pass
            break

        # 2) Tushare 兜底（不在本地池的大市值股）
        if pro is not None and ts_code:
            try:
                st = (datetime.now() - timedelta(days=300)).strftime("%Y%m%d")
                en = datetime.now().strftime("%Y%m%d")
                d = pro.daily(ts_code=ts_code, start_date=st, end_date=en)
                if d is not None and len(d) >= 20:
                    d = d.copy()
                    d = d.sort_values("trade_date")
                    for c in ("high", "low", "close"):
                        d[c] = pd.to_numeric(d[c], errors="coerce")
                    if np.isfinite(px):
                        return compute(d, px, pre)
            except Exception:
                pass
        return o

'''
s = s[:old_start] + new + s[old_end:]

# _fetch 里传入 pro / ts_code
s = s.replace(
    "        ts_data = {}\n        if self.use_tushare:\n"
    "            self.progress.emit(\"查询 Tushare 财务数据...\")\n"
    "            try:\n                ts_data = self._tushare()\n"
    "            except Exception:\n                pass",
    "        ts_data = {}\n        pro = None\n"
    "        if self.use_tushare:\n"
    "            self.progress.emit(\"查询 Tushare 财务数据...\")\n"
    "            try:\n"
    "                import credentials  # type: ignore\n"
    "                import tushare as ts  # type: ignore\n"
    "                pro = ts.pro_api(credentials.get_tushare_token())\n"
    "            except Exception:\n                pro = None\n"
    "            try:\n                ts_data = self._tushare()\n"
    "            except Exception:\n                pass")

s = s.replace(
    "            ps = self._pos(c6, rt, cache)",
    "            tsc = None\n"
    "            for s2 in ts_data.keys():\n"
    "                if s2 == c6:\n"
    "                    tsc = c6\n"
    "                    break\n"
    "            ps = self._pos(c6, rt, cache, pro=pro,\n"
    "                           ts_code=(c6 + ('.SH' if c6.startswith(('60','68','9'))\n"
    "                                          else '.SZ')))")

io.open(p, "w", encoding="utf-8").write(s)
print("已修正")
