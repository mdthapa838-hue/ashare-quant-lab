# -*- coding: utf-8 -*-
"""股东持股采集与整理的离线测试（不访问网络）。

网络请求全部用 FakeClient 打桩；整理逻辑用手工构造的小样本覆盖
新进 / 加仓 / 减仓 / 不变 / 清仓 / 中间报告期缺失等情况。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eastmoney_holders import aggregate as agg  # noqa: E402
from eastmoney_holders import api  # noqa: E402
from eastmoney_holders.aggregate import (  # noqa: E402
    build_holder_history,
    build_holder_universe,
    build_period_changes,
    classify_change,
    normalize_detail_row,
    to_number,
)
from eastmoney_holders.crawl import read_csv, write_csv  # noqa: E402
from eastmoney_holders.report import build_report  # noqa: E402


# ---------------------------------------------------------------------------
# 假客户端
# ---------------------------------------------------------------------------
class FakeClient(api.EastmoneyClient):
    """不联网：按 reportName 返回预置数据，记录调用参数。"""

    def __init__(self, pages_by_report=None, page_rows=None, page_size=2000):
        super().__init__(min_interval=0, retry=0, page_size=page_size, sleeper=lambda _s: None)
        self.pages_by_report = pages_by_report or {}
        self.page_rows = page_rows or {}
        self.calls: list[dict] = []

    def fetch_page(self, report_name, columns="ALL", filter_=None, page_number=1,
                   page_size=None, sort_columns=None, sort_types=None):
        self.calls.append({"report": report_name, "filter": filter_,
                           "page": page_number, "columns": columns})
        rows = self.page_rows.get(report_name, [])
        total_pages = self.pages_by_report.get(report_name, 1)
        lo = (page_number - 1) * (page_size or self.page_size)
        hi = lo + (page_size or self.page_size)
        return api.Page(rows=rows[lo:hi], pages=total_pages,
                        count=len(rows), page_number=page_number)


# ---------------------------------------------------------------------------
# 数值归一
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    (None, None), ("", None), ("  ", None), ("-", None), ("--", None),
    ("1,234", 1234.0), (1234, 1234.0), (12.5, 12.5), ("abc", None),
])
def test_to_number(raw, expected):
    assert to_number(raw) == expected


@pytest.mark.parametrize("raw,kind,delta", [
    ("新进", agg.CHANGE_NEW, None),
    ("不变", agg.CHANGE_FLAT, 0.0),
    ("12345", agg.CHANGE_BUY, 12345.0),
    ("-1234", agg.CHANGE_SELL, -1234.0),
    ("0", agg.CHANGE_FLAT, 0.0),
    (None, "", None),
    ("", "", None),
])
def test_classify_change(raw, kind, delta):
    assert classify_change(raw) == (kind, delta)


def test_normalize_detail_row_prefers_free_ratio_and_handles_null_cap():
    row = {
        "END_DATE": "2026-09-30 00:00:00", "SECUCODE": "000001.SZ",
        "SECURITY_CODE": "000001", "SECURITY_NAME_ABBR": "平安银行",
        "HOLDER_NAME": "香港中央结算有限公司", "HOLDER_NEW": "10671586",
        "HOLDER_TYPE": "其它", "HOLDER_RANK": 2, "HOLD_NUM": 1209130304,
        "HOLD_NUM_CHANGE": "472251894", "CHANGE_RATIO": 64.08,
        "HOLD_RATIO": 6.23, "FREE_HOLDNUM_RATIO": 6.2308,
        "HOLDER_MARKET_CAP": None, "SHARES_TYPE": "A股",
    }
    out = normalize_detail_row(row)
    assert out["end_date"] == "2026-09-30"
    assert out["holder_key"] == "10671586"
    assert out["change_kind"] == agg.CHANGE_BUY
    assert out["change_num"] == 472251894.0
    assert out["hold_ratio"] == 6.23
    assert out["market_cap"] is None
    assert out["holder_rank"] == 2.0


# ---------------------------------------------------------------------------
# 小样本：两只股票、三个股东、三个报告期
# ---------------------------------------------------------------------------
def _row(end_date, secucode, holder_key, holder_name, hold_num, change,
         holder_type="其它", rank=1, cap=1000.0):
    return {
        "END_DATE": end_date + " 00:00:00", "SECUCODE": secucode,
        "SECURITY_CODE": secucode.split(".")[0], "SECURITY_NAME_ABBR": secucode,
        "HOLDER_NAME": holder_name, "HOLDER_NEW": holder_key,
        "HOLDER_TYPE": holder_type, "HOLDER_RANK": rank, "HOLD_NUM": hold_num,
        "HOLD_NUM_CHANGE": change, "CHANGE_RATIO": None, "HOLD_RATIO": 1.0,
        "FREE_HOLDNUM_RATIO": 1.0, "HOLDER_MARKET_CAP": cap, "SHARES_TYPE": "A股",
    }


@pytest.fixture
def sample_rows():
    """2026-06-30 -> 2026-09-30 三个股东的行为：
       A 在 000001 加仓、在 000002 新进
       B 在 000001 减仓、在 000002 不变
       C 2026-06-30 持有 000001，2026-09-30 掉出榜单（清仓）
    """
    return [
        # 上期
        _row("2026-06-30", "000001.SZ", "A", "甲基金", 1000, "不变", "证券投资基金", 1, 900.0),
        _row("2026-06-30", "000001.SZ", "B", "乙保险", 500, "不变", "保险公司", 2, 400.0),
        _row("2026-06-30", "000001.SZ", "C", "丙个人", 100, "新进", "个人", 3, 60.0),
        _row("2026-06-30", "000002.SZ", "B", "乙保险", 700, "不变", "保险公司", 1, 700.0),
        # 本期
        _row("2026-09-30", "000001.SZ", "A", "甲基金", 1500, "500", "证券投资基金", 1, 1500.0),
        _row("2026-09-30", "000001.SZ", "B", "乙保险", 300, "-200", "保险公司", 2, 300.0),
        _row("2026-09-30", "000002.SZ", "A", "甲基金", 100, "新进", "证券投资基金", 3, 100.0),
        _row("2026-09-30", "000002.SZ", "B", "乙保险", 700, "不变", "保险公司", 1, 700.0),
    ]


def test_holder_universe_matches_page_semantics(sample_rows):
    uni = {r["holder_key"]: r for r in build_holder_universe(sample_rows)}
    assert set(uni) == {"A", "B"}
    assert uni["A"]["holder_num"] == 2
    assert uni["A"]["new_num"] == 1          # 000002 新进
    assert uni["A"]["up_num"] == 1           # 000001 加仓
    assert uni["A"]["down_num"] == 0
    assert uni["A"]["market_cap"] == 1600.0
    assert uni["B"]["holder_num"] == 2
    assert uni["B"]["down_num"] == 1
    assert uni["B"]["unchanged_num"] == 1
    assert uni["B"]["new_num"] == 0
    assert uni["A"]["first_period"] == "2026-06-30"
    assert uni["A"]["last_period"] == "2026-09-30"
    assert uni["A"]["period_num"] == 2


def test_holder_history_tracks_prev_and_status(sample_rows):
    hist = build_holder_history(sample_rows, holder_keys=["A"])
    assert {r["holder_key"] for r in hist} == {"A"}
    a1 = next(r for r in hist if r["secucode"] == "000001.SZ" and r["end_date"] == "2026-09-30")
    assert a1["prev_hold_num"] == 1000.0
    assert a1["period_change"] == 500.0
    assert a1["period_status"] == agg.CHANGE_BUY
    a2 = next(r for r in hist if r["secucode"] == "000002.SZ" and r["end_date"] == "2026-09-30")
    assert a2["prev_hold_num"] is None
    assert a2["period_change"] == 100.0      # 新进：本期持股数即新增量
    assert a2["period_status"] == agg.CHANGE_NEW


def test_period_changes_reports_exit(sample_rows):
    pc = build_period_changes(sample_rows)
    latest = [r for r in pc if r["end_date"] == "2026-09-30"]
    c = next(r for r in latest if r["holder_key"] == "C")
    assert c["holder_num"] == 0
    assert c["exit_num"] == 1                # 000001 掉出榜单
    assert c["market_cap"] == 0.0
    assert c["market_cap_change"] == -60.0
    a = next(r for r in latest if r["holder_key"] == "A")
    assert a["exit_num"] == 0
    assert a["market_cap_change"] == 700.0   # 900 -> 1600


def test_history_handles_gap_periods():
    """股东中间漏一期又回来：标记「重回」，并按缺口前的持股给出参考基数。"""
    rows = [
        _row("2026-03-31", "000001.SZ", "A", "甲基金", 100, "新进"),
        # 中间这一期 A 掉出榜单（同期的其他股票有数据，故报告期序列是连续的）
        _row("2026-06-30", "000002.SZ", "B", "乙保险", 700, "不变", "保险公司"),
        _row("2026-09-30", "000001.SZ", "A", "甲基金", 300, "新进"),
    ]
    hist = build_holder_history(rows, holder_keys=["A"])
    latest = next(r for r in hist if r["end_date"] == "2026-09-30")
    assert latest["period_status"] == "重回"
    assert latest["prev_hold_num"] is None        # 上期确实不在榜
    assert latest["last_seen_hold_num"] == 100.0  # 缺口前的基数
    assert latest["period_change"] is None        # 非连续期不给环比


def test_history_same_holder_keeps_period_change_when_continuous(sample_rows):
    hist = build_holder_history(sample_rows, holder_keys=["B"])
    b1 = next(r for r in hist if r["secucode"] == "000001.SZ" and r["end_date"] == "2026-09-30")
    assert b1["period_status"] == agg.CHANGE_SELL
    assert b1["period_change"] == -200.0


def test_empty_inputs_return_empty():
    assert build_holder_universe([]) == []
    assert build_holder_history([]) == []
    assert build_period_changes([]) == []


# ---------------------------------------------------------------------------
# 采集与落盘
# ---------------------------------------------------------------------------
def test_fetch_all_walks_every_page():
    rows = [{"i": i} for i in range(7)]
    fake = FakeClient(pages_by_report={"RPT_X": 3}, page_rows={"RPT_X": rows}, page_size=3)
    got = fake.fetch_all("RPT_X")
    assert len(got) == 7
    assert [c["page"] for c in fake.calls] == [1, 2, 3]


def test_fetch_all_stops_when_page_is_empty():
    fake = FakeClient(pages_by_report={"RPT_X": 5}, page_rows={"RPT_X": [{"i": 0}]}, page_size=3)
    got = fake.fetch_all("RPT_X")
    assert len(got) == 1
    assert len(fake.calls) == 2     # 第 2 页空，提前结束


def test_write_and_read_csv_roundtrip(tmp_path):
    rows = [{"a": 1, "b": "中文", "c": None}, {"a": 2, "b": 'x,"y"', "c": 3}]
    p = tmp_path / "sub" / "t.csv"
    assert write_csv(p, rows, ["a", "b", "c"]) == 2
    # BOM 保证 Excel 打开中文不乱码
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")
    back = read_csv(p)
    assert back[0]["b"] == "中文"
    assert back[1]["b"] == 'x,"y"'
    assert back[0]["c"] == ""      # 空值写空串
    assert read_csv(tmp_path / "nope.csv") == []


def test_write_csv_empty_writes_header_only(tmp_path):
    p = tmp_path / "empty.csv"
    assert write_csv(p, [], ["a", "b"]) == 0
    assert p.exists()
    assert read_csv(p) == []
    assert "a,b" in p.read_text(encoding="utf-8-sig")


def test_crawl_period_uses_cache_and_respects_columns(tmp_path, sample_rows):
    from eastmoney_holders import crawl as crawl_mod

    detail_rpt = api.REPORT_NAMES[api.HOLD_TYPE_FREE]["detail"]
    basic_rpt = api.REPORT_NAMES[api.HOLD_TYPE_FREE]["basic_info"]
    fake = FakeClient(pages_by_report={detail_rpt: 2, basic_rpt: 1},
                      page_rows={detail_rpt: sample_rows, basic_rpt: [
                          {"HOLDER_NEW": "A", "HOLDER_NAME": "甲基金", "HOLDER_TYPE": "证券投资基金",
                           "HOLDER_NUM": 2, "HOLDADD_NUM": 1, "HOLDUP_NUM": 1,
                           "HOLDDOWN_NUM": 0, "HOLDUNCHANGED_NUM": 0,
                           "HOLDER_MARKET_CAP": 1600.0, "END_DATE": "2026-09-30"},
                      ]}, page_size=100)
    st = crawl_mod.crawl_period(fake, tmp_path, "2026-09-30", api.HOLD_TYPE_FREE, "2026三季报")
    assert st["detail_rows"] == len(sample_rows)
    assert st["basic_rows"] == 1
    assert (tmp_path / "raw" / "detail_free_2026-09-30.csv").exists()
    # 明细请求必须带上 IS_REPORT 过滤与完整字段
    detail_call = next(c for c in fake.calls if c["report"] == detail_rpt)
    assert "IS_REPORT" in (detail_call["filter"] or "")
    assert "HOLDER_NAME" in detail_call["columns"]

    # 再抓一次应命中缓存，不再发请求
    before = len(fake.calls)
    st2 = crawl_mod.crawl_period(fake, tmp_path, "2026-09-30", api.HOLD_TYPE_FREE, "2026三季报")
    assert st2["cached"] is True
    assert len(fake.calls) == before


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def test_build_report_outputs_csv_and_markdown(tmp_path, sample_rows):
    raw = tmp_path / "raw"
    write_csv(raw / "detail_free_2026-09-30.csv", sample_rows)
    write_csv(raw / "detail_free_2026-06-30.csv",
              [r for r in sample_rows if r["END_DATE"].startswith("2026-06-30")])
    write_csv(raw / "basic_free_2026-09-30.csv", [
        {"HOLDER_NEW": "A", "HOLDER_NAME": "甲基金", "HOLDER_TYPE": "证券投资基金",
         "HOLDER_NUM": 2, "HOLDADD_NUM": 1, "HOLDUP_NUM": 1, "HOLDDOWN_NUM": 0,
         "HOLDUNCHANGED_NUM": 0, "HOLDER_MARKET_CAP": 1600.0, "END_DATE": "2026-09-30"},
    ])
    md = build_report(raw, tmp_path, api.HOLD_TYPE_FREE, top_n=5)
    text = md.read_text(encoding="utf-8")
    assert "东方财富「股东持股」全量整理" in text
    assert "口径核对" in text
    assert "清仓" in text
    for name in ("holders_current_free.csv", "holders_all_periods_free.csv",
                 "holder_stock_history_free.csv"):
        assert (tmp_path / name).exists(), name
    current = read_csv(tmp_path / "holders_current_free.csv")
    assert {r["holder_key"] for r in current} == {"A", "B"}


def test_build_report_errors_without_data(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_report(tmp_path, tmp_path, api.HOLD_TYPE_FREE)


def test_build_report_without_holder_type_column(tmp_path):
    """十大股东口径的明细表没有 HOLDER_TYPE：报告不应出现空的「类型」列。"""
    raw = tmp_path / "raw"
    rows = []
    for r in [
        _row("2026-06-30", "000001.SZ", "A", "甲基金", 1000, "不变"),
        _row("2026-09-30", "000001.SZ", "A", "甲基金", 1500, "500"),
    ]:
        r = dict(r)
        r.pop("HOLDER_TYPE", None)      # 模拟该口径缺字段
        rows.append(r)
    write_csv(raw / "detail_total_2026-09-30.csv",
              [r for r in rows if r["END_DATE"].startswith("2026-09-30")])
    write_csv(raw / "detail_total_2026-06-30.csv",
              [r for r in rows if r["END_DATE"].startswith("2026-06-30")])

    md = build_report(raw, tmp_path, api.HOLD_TYPE_TOTAL, top_n=5)
    text = md.read_text(encoding="utf-8")
    assert "不提供 `HOLDER_TYPE` 字段" in text
    # 榜单表头不应有「类型」列
    assert "| # | 股东名称 | 持股家数 |" in text
    assert "| # | 股东名称 | 类型 | 持股家数 |" not in text


def test_holder_type_column_present_when_data_has_it(tmp_path, sample_rows):
    raw = tmp_path / "raw"
    write_csv(raw / "detail_free_2026-09-30.csv",
              [r for r in sample_rows if r["END_DATE"].startswith("2026-09-30")])
    md = build_report(raw, tmp_path, api.HOLD_TYPE_FREE, top_n=5)
    text = md.read_text(encoding="utf-8")
    assert "| # | 股东名称 | 类型 | 持股家数 |" in text


def test_cli_parser_defaults():
    from eastmoney_holders.cli import build_parser

    args = build_parser().parse_args([])
    assert args.command == "all"
    assert args.type == "both"
    assert args.page_size == 2000

    args = build_parser().parse_args(["report", "--type", "free", "--out", "/tmp/x", "--top", "7"])
    assert args.command == "report"
    assert args.type == "free"
    assert args.top == 7


# ---------------------------------------------------------------------------
# screen：按股东类型筛重仓股
# ---------------------------------------------------------------------------
def _ss_row(end_date, secucode, name, holder_key, hold_num, change, cap,
            ratio=1.0, holder_type="全国社保基金", code=None):
    return {
        "END_DATE": end_date + " 00:00:00", "SECUCODE": secucode,
        "SECURITY_CODE": code or secucode.split(".")[0],
        "SECURITY_NAME_ABBR": secucode, "HOLDER_NAME": name,
        "HOLDER_NEW": holder_key, "HOLDER_TYPE": holder_type,
        "HOLDER_RANK": 1, "HOLD_NUM": hold_num, "HOLD_NUM_CHANGE": change,
        "CHANGE_RATIO": None, "HOLD_RATIO": ratio, "FREE_HOLDNUM_RATIO": ratio,
        "HOLDER_MARKET_CAP": cap, "SHARES_TYPE": "流通A股", "IS_REPORT": "1",
    }


@pytest.fixture
def screen_rows():
    """三个场景：
       A 股：两个组合，一个加仓一个减仓，合计股数下降 → 整体应判「减仓」
       B 股：上期无，本期两只组合新进 → 「新进」
       C 股：单一组合持股不变 → 「不变」
       另有一条非社保记录（QFII）不应被计入
    """
    return [
        # 上期
        _ss_row("2026-06-30", "000001.SZ", "组合甲", "P1", 1000, "不变", 1000.0),
        _ss_row("2026-06-30", "000001.SZ", "组合乙", "P2", 1000, "不变", 1000.0),
        _ss_row("2026-06-30", "000003.SZ", "组合甲", "P1", 500, "不变", 500.0),
        # 本期
        _ss_row("2026-09-30", "000001.SZ", "组合甲", "P1", 1200, "200", 1200.0),   # 加仓
        _ss_row("2026-09-30", "000001.SZ", "组合乙", "P2", 500, "-500", 500.0),    # 减仓
        _ss_row("2026-09-30", "000002.SZ", "组合甲", "P1", 300, "新进", 300.0, 2.5),
        _ss_row("2026-09-30", "000002.SZ", "组合乙", "P2", 200, "新进", 200.0, 1.5),
        _ss_row("2026-09-30", "000003.SZ", "组合甲", "P1", 500, "不变", 500.0),
        # 干扰项：类型不符
        _ss_row("2026-09-30", "000004.SZ", "外资", "Q1", 999, "新进", 999.0,
                holder_type="QFII"),
    ]


def test_screen_status_uses_aggregate_shares(tmp_path, screen_rows):
    """A 股整体 2000→1700 股：即使某组合「加仓」，股票整体应判减仓。"""
    from eastmoney_holders.screen import screen

    raw = tmp_path / "raw"
    write_csv(raw / "detail_free_2026-09-30.csv",
              [r for r in screen_rows if r["END_DATE"].startswith("2026-09-30")])
    write_csv(raw / "detail_free_2026-06-30.csv",
              [r for r in screen_rows if r["END_DATE"].startswith("2026-06-30")])
    csv_path = screen(raw, tmp_path / "out", "全国社保基金", "2026-09-30",
                      prev_end_date="2026-06-30")

    rows = {r["security_code"]: r for r in read_csv(csv_path)}
    assert set(rows) == {"000001", "000002", "000003"}   # QFII 不算进来

    a = rows["000001"]
    assert a["change_status"] == "减仓"
    assert float(a["hold_num_change"]) == -300.0
    assert float(a["hold_num_change_pct"]) == -15.0
    assert a["portfolio_num"] == "2"

    b = rows["000002"]
    assert b["change_status"] == "新进"
    assert b["portfolio_num"] == "2"
    assert float(b["ratio_of_float"]) == 4.0     # 2.5 + 1.5

    c = rows["000003"]
    assert c["change_status"] == "不变"
    assert float(c["hold_num_change"]) == 0.0


def test_screen_sorts_and_writes_markdown(tmp_path, screen_rows):
    from eastmoney_holders.screen import screen

    raw = tmp_path / "raw"
    write_csv(raw / "detail_free_2026-09-30.csv",
              [r for r in screen_rows if r["END_DATE"].startswith("2026-09-30")])
    csv_path = screen(raw, tmp_path / "out", "全国社保基金", "2026-09-30", top_n=2)
    md = csv_path.with_suffix(".md").read_text(encoding="utf-8")
    assert "全国社保基金 重仓股" in md
    assert "口径说明" in md
    assert "只看单一组合的「新进/加仓」会得出相反的结论" in md


def test_screen_rarity_of_holder_type(tmp_path, screen_rows):
    from eastmoney_holders.screen import screen

    raw = tmp_path / "raw"
    write_csv(raw / "detail_free_2026-09-30.csv",
              [r for r in screen_rows if r["END_DATE"].startswith("2026-09-30")])
    with pytest.raises(ValueError):
        screen(raw, tmp_path / "out", "不存在的类型", "2026-09-30")


def test_screen_dedupes_same_portfolio_name_variants(tmp_path):
    """同一组合的简称与全称共用一个编码时，组合数应按编码算 1 个。"""
    from eastmoney_holders.screen import screen

    raw = tmp_path / "raw"
    rows = [
        _ss_row("2026-09-30", "000001.SZ", "社保基金2103组合", "K1", 100, "新进", 100.0),
        _ss_row("2026-09-30", "000001.SZ", "国泰基金管理有限公司-社保基金2103组合",
                "K1", 0.0001, "新进", 1.0),
    ]
    write_csv(raw / "detail_free_2026-09-30.csv", rows)
    csv_path = screen(raw, tmp_path / "out", "全国社保基金", "2026-09-30")
    rec = read_csv(csv_path)[0]
    assert rec["portfolio_num"] == "1"


def test_cli_screen_defaults():
    from eastmoney_holders.cli import build_parser

    args = build_parser().parse_args(["screen"])
    assert args.command == "screen"
    assert args.holder_type == "全国社保基金"
    assert args.sort == "market_cap"
    assert args.period is None
