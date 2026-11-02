# -*- coding: utf-8 -*-
"""Announcement acquisition only. Never imports a broker or submits an order.
Historical requests capture current quote snapshots, explicitly not historical PIT.
"""
from __future__ import annotations
import os, re, sys, time, html, importlib.util
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from PySide6.QtCore import QThread, Signal
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
EARN_KW = ["业绩预告", "业绩快报", "业绩预增", "业绩预盈", "业绩预亏", "业绩预减", "盈利预测", "业绩预披露", "业绩披露"]
NEG_WORDS = ["递延", "滞后", "顺延", "下滑", "压力", "短缺", "消化", "调整", "下降", "缩减", "退单", "取消", "亏损扩大", "不及预期", "放缓", "疲软", "承压", "延期"]
POS_WORDS = ["环比提升", "环比增长", "创历史新高", "满产", "满稼动", "供不应求", "订单充足", "订单充裕", "量价齐升", "毛利率提升", "同比和环比", "持续增长", "显著提升", "大幅增长", "扭亏"]

def market_suffix(code):
    """Tushare exchange; BSE 920/4/8 must not be routed to Shanghai/Shenzhen."""
    c6 = str(code).split(".")[0].zfill(6)
    if c6.startswith(("92", "4", "8")):
        return "BJ"
    return "SH" if c6.startswith(("60", "68", "900")) else "SZ"

def tencent_symbol(code):
    c6 = str(code).split(".")[0].zfill(6)
    return market_suffix(c6).lower() + c6

class FetchCancelled(Exception):
    pass

class AcquisitionError(Exception):
    """Safe error message created here, with no request headers or credentials."""
    pass

def get_pro():
    import tushare as ts
    helper = Path(r"C:\Users\Administrator\.codex\tools\tushare_credentials.py")
    spec = importlib.util.spec_from_file_location("earnings_vault", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return ts.pro_api(module.get_token())

class FetchWorker(QThread):
    """后台抓取业绩公告。"""
    progress = Signal(str)
    done = Signal(object)      # DataFrame
    failed = Signal(str)

    def __init__(self, date_str: str, use_tushare: bool = True, cancel_check=None):
        super().__init__()
        datetime.strptime(date_str, "%Y-%m-%d")
        self.date_str = date_str
        self.use_tushare = use_tushare
        self.cancel_check = cancel_check or self.isInterruptionRequested
        self.metadata = {"complete": True, "warnings": [], "historical_pit": False,
                         "query_date": date_str, "quote_basis": "抓取时行情快照，非历史PIT",
                         "source": "cninfo+eastmoney+tencent" + ("+tushare" if use_tushare else ""), "collector_version": "db_batch_v1.3"}
        self._em_cache = {}
        self._financial = None

    def _check(self):
        if self.cancel_check():
            raise FetchCancelled("用户取消，当前日期未完成")

    def _warn(self, message, partial=True):
        if message not in self.metadata["warnings"]:
            self.metadata["warnings"].append(message)
        if partial:
            self.metadata["complete"] = False

    def run(self):
        try:
            self.done.emit(self._fetch())
        except FetchCancelled:
            self.failed.emit("已取消")
        except Exception as exc:
            self.failed.emit(str(exc) if isinstance(exc, AcquisitionError) else type(exc).__name__)

    # ---- 巨潮全市场
    def _cninfo(self):
        import requests
        out, seen, raw_count = [], set(), 0
        hdr = {"User-Agent": UA, "Referer": "https://www.cninfo.com.cn/",
               "X-Requested-With": "XMLHttpRequest"}
        expected = None
        for page in range(1, 5001):
            self._check()
            body = {"pageNum": str(page), "pageSize": "30", "column": "szse",
                    "tabName": "fulltext", "plate": "", "stock": "", "searchkey": "",
                    "secid": "", "category": "", "trade": "",
                    "seDate": f"{self.date_str}~{self.date_str}",
                    "sortName": "", "sortType": "", "isHLtitle": "true"}
            obj = None
            for attempt in range(2):
                self._check()
                try:
                    r = requests.post("https://www.cninfo.com.cn/new/hisAnnouncement/query",
                                      data=body, headers=hdr, timeout=25)
                    r.raise_for_status()
                    obj = r.json()
                    if not isinstance(obj, dict) or "announcements" not in obj:
                        raise ValueError("invalid announcement response")
                    break
                except Exception as exc:
                    if attempt == 1:
                        if not out:
                            raise AcquisitionError(f"巨潮第{page}页失败（{type(exc).__name__}）；不是成功空日") from None
                        self._warn(f"巨潮第{page}页失败（{type(exc).__name__}），仅保存已取得部分")
                        self.metadata["announcement_pages"] = page - 1
                        self.metadata["announcement_raw_count"] = raw_count
                        return out
                    time.sleep(.3)
            anns = obj.get("announcements") or []
            if expected is None:
                expected = obj.get("totalAnnouncement")
                self.metadata["source_reported_total"] = expected
            has_more = obj.get("hasMore")
            if not isinstance(anns, list):
                raise AcquisitionError("巨潮公告列表格式异常")
            if not anns:
                if has_more or (page == 1 and expected and int(expected) > 0):
                    raise AcquisitionError(f"巨潮第{page}页异常空列表；不是成功空日")
                self.metadata["announcement_pages"] = page
                break
            fresh = 0
            raw_count += len(anns)
            for a in anns:
                code = str(a.get("secCode", "")).zfill(6)
                title = html.unescape(re.sub(r"<[^>]+>", "", str(a.get("announcementTitle", ""))))
                uid = str(a.get("announcementId", ""))
                url = "https://static.cninfo.com.cn/" + str(a.get("adjunctUrl", ""))
                key = (code, uid or url, title)
                if key in seen:
                    continue
                fresh += 1
                seen.add(key)
                tstr = ""
                if a.get("announcementTime"):
                    tstr = datetime.fromtimestamp(a["announcementTime"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                out.append({"代码": code, "名称": a.get("secName", ""), "标题": title,
                            "时间": tstr, "URL": url, "公告ID": uid})
            self.metadata["announcement_pages"] = page
            self.progress.emit(f"巨潮 {self.date_str} 第{page}页（累计{len(out)}条）")
            if not fresh and has_more:
                self._warn("巨潮分页重复，停止并标记部分数据")
                break
            if has_more is False or (has_more is None and len(anns) < 30):
                break
            time.sleep(.12)
        else:
            self._warn("巨潮超过分页安全上限，标记部分数据")
        if expected is not None and raw_count < int(expected):
            self._warn(f"巨潮返回{raw_count}条，少于报告总数{expected}条")
        self.metadata["announcement_raw_count"] = raw_count
        self.metadata["announcement_unique_count"] = len(out)
        return out

    @staticmethod
    def _title_key(title):
        title = html.unescape(re.sub(r"<[^>]+>", "", str(title)))
        title = re.split(r"[:：]", title, maxsplit=1)[-1]
        return re.sub(r"[\s，,。．·（）()【】\[\]_-]", "", title)

    def _em_event(self, code, title):
        """Exact title and document day only. Never substitute a later event."""
        import requests
        if code not in self._em_cache:
            self._check()
            try:
                r = requests.get("https://np-anotice-stock.eastmoney.com/api/security/ann",
                                 params={"sr": "-1", "page_size": "100", "page_index": "1",
                                         "ann_type": "A", "client_source": "web", "stock_list": code},
                                 headers={"User-Agent": UA}, timeout=20)
                r.raise_for_status()
                self._em_cache[code] = r.json().get("data", {}).get("list", []) or []
            except Exception as exc:
                self._warn(f"{code} 东财列表缺失（{type(exc).__name__}）")
                self._em_cache[code] = []
        matches = [x for x in self._em_cache[code]
                   if self._title_key(x.get("title", "")) == self._title_key(title)
                   and str(x.get("notice_date", ""))[:10] == self.date_str]
        if len(matches) != 1:
            self._warn(f"{code} 本公告精确时间/正文未匹配，保留巨潮原文链接", partial=False)
            return None, {"摘要": "", "原因": ""}
        event = matches[0]
        stamp = None
        try:
            stamp = datetime.strptime(str(event.get("display_time", ""))[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            pass
        result = {"摘要": "", "原因": ""}
        if not event.get("art_code"):
            return stamp, result
        self._check()
        try:
            r = requests.get("https://np-cnotice-stock.eastmoney.com/api/content/ann",
                             params={"art_code": event["art_code"], "client_source": "web", "page_index": "1"},
                             headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
            data = r.json().get("data") or {}
            body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", data.get("notice_content") or data.get("content") or ""))
            result["摘要"] = body[:4000]
            m = re.search(r"(?:变动原因|业绩变动原因|原因说明)[:：]?(.{0,1200})", body)
            if m:
                result["原因"] = m.group(1)
        except Exception as exc:
            self._warn(f"{code} 东财正文缺失（{type(exc).__name__}）")
        return stamp, result

    # ---- tushare 财务
    def _tushare(self, pro=None):
        pro = pro or get_pro()
        d = None
        for attempt in range(2):
            self._check()
            try:
                d = pro.forecast_vip(ann_date=self.date_str.replace("-", ""))
                break
            except Exception as exc:
                if attempt == 1:
                    raise AcquisitionError(f"财务接口失败（{type(exc).__name__}）") from None
                time.sleep(.5)
        out = {}
        if d is not None and len(d):
            for _, r in d.iterrows():
                c6 = str(r["ts_code"]).split(".")[0]
                rec = {"增速": pd.to_numeric(r.get("p_change_min"), errors="coerce"),
                       "增速上限": pd.to_numeric(r.get("p_change_max"), errors="coerce"),
                       "类型": str(r.get("type", "") or ""), "数据类型": "forecast", "财务来源": "tushare.forecast_vip", "报告期": str(r.get("end_date", "")),
                       "财务公告日": str(r.get("ann_date", "")),
                       "原因": re.sub(r"\s+", " ", str(r.get("change_reason", "") or ""))[:2000],
                       "摘要": re.sub(r"\s+", " ", str(r.get("summary", "") or ""))[:2000]}
                if pd.isna(rec["增速"]):
                    rec["增速"] = rec["增速上限"]
                out.setdefault(c6, []).append(rec)
        self.metadata["forecast_records"] = sum(map(len, out.values()))
        self._check()
        try:
            # yoy_net_profit is an amount for the previous year, NOT a growth rate.
            # Official schema: https://tushare.pro/document/2?doc_id=46
            express = pro.express_vip(ann_date=self.date_str.replace("-", ""),
                fields="ts_code,ann_date,end_date,n_income,yoy_dedu_np,perf_summary,is_audit")
            if express is not None and len(express):
                for _, r in express.iterrows():
                    c6 = str(r["ts_code"]).split(".")[0]
                    growth = pd.to_numeric(r.get("yoy_dedu_np"), errors="coerce")
                    out.setdefault(c6, []).append({
                        "增速": growth, "增速上限": growth, "类型": "快报", "数据类型": "express",
                        "财务来源": "tushare.express_vip:yoy_dedu_np", "报告期": str(r.get("end_date", "")),
                        "财务公告日": str(r.get("ann_date", "")), "原因": "",
                        "摘要": re.sub(r"\s+", " ", str(r.get("perf_summary", "") or ""))[:2000],
                        "净利润元": pd.to_numeric(r.get("n_income"), errors="coerce")})
            self.metadata["express_records"] = len(express) if express is not None else 0
        except FetchCancelled:
            raise
        except Exception as exc:
            self._warn(f"快报财务接口缺失（{type(exc).__name__}）")
        self.metadata["financial_records"] = sum(map(len, out.values()))
        return out

    def _finance_for(self, records, title):
        wanted = "express" if "快报" in title else "forecast"
        records = [r for r in records if r.get("数据类型", "forecast") == wanted]
        if not records:
            return {}
        year = re.search(r"(20\d{2})", title)
        suffix = None
        if any(x in title for x in ("前三季", "第三季", "1-9", "三季")):
            suffix = "0930"
        elif any(x in title for x in ("半年度", "半年", "中期", "1-6")):
            suffix = "0630"
        elif "一季" in title:
            suffix = "0331"
        elif "年度" in title or "年报" in title:
            suffix = "1231"
        if year and suffix:
            hits = [r for r in records if r.get("报告期") == year[1] + suffix]
            return hits[0] if len(hits) == 1 else {}
        return records[0] if len(records) == 1 else {}

    # ---- 实时行情
    def _px(self, codes):
        import requests
        out = {}
        hdr = {"User-Agent": UA}

        def to_tx(c6):
            return tencent_symbol(c6)
        tx = [to_tx(c) for c in codes if c and len(c) == 6]
        for i in range(0, len(tx), 600):
            self._check()
            try:
                r = requests.get("https://qt.gtimg.cn/q=" +
                                 ",".join(tx[i:i + 600]),
                                 headers=hdr, timeout=15)
                r.raise_for_status()
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
                            "quote_time": datetime.strptime(p[30], "%Y%m%d%H%M%S").isoformat(sep=" ") if len(p[30]) == 14 else "未知",
                            "pre": float(p[4]), "chg": float(p[32]),
                            "turnover": float(p[38]) if p[38] else np.nan,
                            "pe": float(p[39]) if p[39] else np.nan,
                            "mv": float(p[45]) if p[45] else np.nan,
                            "pb": float(p[46]) if p[46] else np.nan,
                        }
                    except Exception:
                        continue
            except Exception as exc:
                self._warn(f"行情批次缺失（{type(exc).__name__}）")
            time.sleep(0.1)
        missing = set(codes) - set(out)
        if missing:
            self._warn(f"{len(missing)}只股票缺行情")
        return out

    # ---- 跳空/回补（公告反应日）
    def _gap_of(self, c6, ann_date, rt, cache, pro=None, ts_code=None):
        """返回 (跳空%, 是否回补)。ann_date 即反应日。"""
        if not ann_date:
            return None, False
        try:
            self._check()
            d = None
            if pro is not None and ts_code:
                st = (pd.Timestamp(ann_date) -
                      pd.Timedelta(days=15)).strftime("%Y%m%d")
                en = (pd.Timestamp(ann_date) +
                      pd.Timedelta(days=5)).strftime("%Y%m%d")
                d = pro.daily(ts_code=ts_code, start_date=st, end_date=en)
            if d is None or len(d) < 2:
                return None, False
            d = d.copy()
            d["date"] = pd.to_datetime(d["trade_date"], format="%Y%m%d")
            d = d.sort_values("date").reset_index(drop=True)
            for c in ("open", "high", "low", "close"):
                d[c] = pd.to_numeric(d[c], errors="coerce")
            ad = pd.Timestamp(ann_date)
            idx = d.index[d["date"] == ad]
            if not len(idx):
                # 找第一个 >= 的交易日
                nxt = d.index[d["date"] >= ad]
                if not len(nxt):
                    return None, False
                i = int(nxt[0])
            else:
                i = int(idx[0])
            if i < 1:
                return None, False
            pre = float(d["close"].iloc[i - 1])
            o = float(d["open"].iloc[i])
            lo = float(d["low"].iloc[i])
            if pre <= 0:
                return None, False
            gap = (o / pre - 1) * 100
            return round(gap, 2), bool(lo <= pre)
        except Exception:
            return None, False

    # ---- 位置（本地缓存 → Tushare 兜底）
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
                    # 本地前复权先归一到昨收；compute的pre也必须是昨收，避免将当前价抵消
                    last = float(x["close"].values[-1])
                    if last > 0 and np.isfinite(pre):
                        r = pre / last
                        for col in ("high", "low", "close"):
                            x[col] = x[col].astype(float) * r
                    if np.isfinite(px):
                        o["位置基准日"] = str(x["date"].iloc[-1])
                        return compute(x, px, pre)
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
                        o["位置基准日"] = str(d["trade_date"].iloc[-1])
                        return compute(d, px, pre)
            except Exception:
                pass
        return o

    def _fetch(self) -> pd.DataFrame:
        try:
            from core.config import load_config
            cache = Path(load_config().get("datasource.cache_dir"))
        except Exception:
            cache = HERE / "data_cache"
        self.metadata["fetch_started_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self._reaction_day = self.date_str
        self.progress.emit(f"抓取 {self.date_str} 巨潮公告...")
        cn = self._cninfo()
        if not cn:
            self.metadata["earnings_events"] = 0
            return pd.DataFrame()
        D = pd.DataFrame(cn)
        E = D[D["标题"].str.contains("|".join(EARN_KW), na=False)].copy()
        E = E.drop_duplicates(subset=["代码", "标题", "URL"])
        self.metadata["earnings_events"] = len(E)
        if E.empty:
            return pd.DataFrame()
        self.progress.emit(f"业绩公告 {len(E)}条，{E['代码'].nunique()}只股票")
        ts_data, pro = {}, None
        if self.use_tushare:
            self._check()
            self.progress.emit("查询财务数据...")
            try:
                pro = get_pro()
                ts_data = self._tushare(pro)
            except FetchCancelled:
                raise
            except Exception as exc:
                self._warn(str(exc) if isinstance(exc, AcquisitionError) else f"财务连接失败（{type(exc).__name__}）")
        codes = sorted(set(E["代码"].astype(str)))
        self.progress.emit("查询抓取时行情（非历史行情）...")
        px = self._px(codes)
        positions = {}
        rows = []
        matched_finance = 0
        for i, (_, t0) in enumerate(E.iterrows(), 1):
            self._check()
            c6 = str(t0["代码"])
            rt = px.get(c6, {})
            if c6 not in positions:
                positions[c6] = self._pos(c6, rt, cache, pro=pro,
                    ts_code=c6 + "." + market_suffix(c6))
            ps = positions[c6]
            ex, txt = self._em_event(c6, t0["标题"])
            tsr = self._finance_for(ts_data.get(c6, []), t0["标题"])
            matched_finance += bool(tsr)
            g = tsr.get("增速")
            g2 = tsr.get("增速上限")
            typ = tsr.get("类型", "")
            reason = tsr.get("原因", "") or txt.get("原因", "")
            summ = tsr.get("摘要", "") or txt.get("摘要", "")
            blob = (reason or "") + " " + (summ or "")
            neg = [w for w in NEG_WORDS if w in blob]
            pos = [w for w in POS_WORDS if w in blob]

            p20, p60, p120 = ps["p20"], ps["p60"], ps["p120"]
            # 分组（v3：加入「跳空/回补」硬门槛）
            # 反应日 = 查询日期（与回测假设一致：ann_date 即股价反应日）
            _reaction = getattr(self, "_reaction_day", None) or self.date_str
            gap, filled = self._gap_of(
                c6, _reaction, rt, cache, pro,
                c6 + "." + market_suffix(c6))
            hi_mid = ((p60 is not None and p60 >= 60) or
                      (p120 is not None and p120 >= 60))
            if pd.isna(g) or g is None:
                grp = "快报/待核实"
            elif float(g) < 20:
                grp = "E 低增"
            elif float(g) < 50:
                grp = "D 中增" if hi_mid else "D 中增(低位)"
            elif hi_mid and gap is not None and gap >= 2 and not filled:
                grp = ("A1' 高位+措辞瑕疵" if len(neg) >= 2
                       else "A1 优选（高位+跳空+未回补）★★★")
            elif hi_mid and gap is not None and gap >= 2:
                grp = "A1 次选（高位+跳空，但回补）"
            elif hi_mid:
                grp = "A1 次选（高位，跳空弱）"
            elif p20 is not None and p20 < 20:
                grp = "A2 观察（低位，不推荐）"
            else:
                grp = "B 高增+中位"

            rows.append({
                "代码": c6,
                "名称": rt.get("name", t0["名称"]),
                "公告ID": t0.get("公告ID", ""),
                "查询日期": self.date_str,
                "获取时间": datetime.now().astimezone().isoformat(timespec="seconds"),
                "行情时间": rt.get("quote_time", "未知"),
                "行情口径": "抓取时快照（非历史PIT）",
                "位置基准日": ps.get("位置基准日", "未知"),
                "跳空口径": "查询日起首个日线交易日；不是已验证的公告反应日",
                "报告期": tsr.get("报告期", ""),
                "财务公告日": tsr.get("财务公告日", ""),
                "财务来源": tsr.get("财务来源", ""),
                "同比原始值%": tsr.get("增速"),
                "跳空%": gap,
                "回补": ("是" if filled else "否") if gap is not None else "—",
                "增速%": (round(float(g), 1)
                        if g is not None and pd.notna(g) else None),
                "增速上限%": (round(float(g2), 1)
                          if g2 is not None and pd.notna(g2) else None),
                "类型": typ,
                "分组": grp,
                "p20": p20, "p60": p60, "p120": p120,
                "距高%": ps["dd120"],
                "现价": rt.get("price"), "今日%": rt.get("chg"),
                "换手%": rt.get("turnover"),
                "PE": rt.get("pe"), "PB": rt.get("pb"),
                "市值亿": rt.get("mv"),
                "发布时间": (ex.strftime("%Y-%m-%d %H:%M:%S")
                          if ex else str(t0.get("时间", ""))),
                "发布日": (ex.strftime("%Y-%m-%d") if ex else str(t0.get("时间", ""))[:10]),
                "公告文档日": self.date_str,
                "发布时间精度": "秒（东财匹配）" if ex else "日期（巨潮，可能为00:00占位）",
                "负面词": "/".join(neg),
                "正面词": "/".join(pos),
                "负面数": len(neg),
                "标题": t0["标题"],
                "原因": (reason or "")[:3000],
                "摘要": (summ or "")[:3000],
                "链接": t0.get("URL", ""),
            })
            self.progress.emit(f"处理 {self.date_str} {i}/{len(E)}  {c6}")
            time.sleep(0.15)

        self.metadata["financial_matched_events"] = matched_finance
        self.metadata["quote_matched_stocks"] = len(px)
        self.metadata["fetch_finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        return pd.DataFrame(rows)


