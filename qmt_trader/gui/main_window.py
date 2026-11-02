# -*- coding: utf-8 -*-
"""
gui/main_window.py — 主窗口

布局：
  ┌──────────────────────────────────────────────────────────┐
  │  工具栏：体检 | 盘前准备 | 扫描 | 建仓 | 监控 | 账户       │
  ├────────────────────────┬─────────────────────────────────┤
  │  信号列表（买入/卖出）   │   K线图 + 详情                  │
  │  ─────────────────────  │                                 │
  │  账户信息 / 持仓         │                                 │
  ├────────────────────────┴─────────────────────────────────┤
  │  日志 / 状态栏                                            │
  └──────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QFont, QColor, QAction
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTabWidget, QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QTextEdit, QProgressBar, QStatusBar, QToolBar, QMessageBox,
    QGroupBox, QFormLayout, QHeaderView, QComboBox, QCheckBox,
    QAbstractItemView, QFrame, QGridLayout, QSpacerItem, QSizePolicy,
)

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from gui.worker import (Worker, task_doctor, task_prepare, task_scan,
                        task_monitor, task_entry, task_account, task_kline)
from gui.chart import KLineChart

# ---------------------------------------------------------------------------
# 样式
# ---------------------------------------------------------------------------
DARK_QSS = """
QMainWindow, QWidget { background-color: #1e1e1e; color: #ddd; }
QGroupBox {
    border: 1px solid #3a3a3a; border-radius: 5px;
    margin-top: 12px; padding-top: 10px; font-weight: bold; color: #9cdcfe;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QTableWidget {
    background-color: #252526; alternate-background-color: #2a2a2b;
    gridline-color: #3a3a3a; color: #ddd;
    selection-background-color: #094771; selection-color: #fff;
}
QHeaderView::section {
    background-color: #333; color: #ccc;
    padding: 5px; border: none; border-right: 1px solid #444;
    border-bottom: 1px solid #444; font-weight: bold;
}
QPushButton {
    background-color: #0e639c; color: #fff; border: none;
    padding: 7px 14px; border-radius: 4px; font-size: 12px;
}
QPushButton:hover { background-color: #1177bb; }
QPushButton:pressed { background-color: #0a4d7a; }
QPushButton:disabled { background-color: #3a3a3a; color: #777; }
QPushButton#danger { background-color: #a1260d; }
QPushButton#danger:hover { background-color: #c42b1c; }
QPushButton#success { background-color: #16825d; }
QPushButton#success:hover { background-color: #1a9d70; }
QTextEdit {
    background-color: #1a1a1a; color: #ccc;
    border: 1px solid #3a3a3a; font-family: Consolas, monospace; font-size: 12px;
}
QProgressBar {
    border: 1px solid #3a3a3a; border-radius: 3px;
    background-color: #252526; text-align: center; color: #ddd; height: 16px;
}
QProgressBar::chunk { background-color: #0e639c; }
QTabBar::tab {
    background: #2d2d30; color: #aaa; padding: 7px 14px;
    border: 1px solid #3a3a3a; border-bottom: none;
}
QTabBar::tab:selected { background: #1e1e1e; color: #fff; }
QComboBox, QCheckBox { color: #ddd; }
QComboBox {
    background-color: #2d2d30; border: 1px solid #3a3a3a;
    padding: 4px; border-radius: 3px;
}
QToolBar { background: #2d2d30; border: none; padding: 4px; spacing: 6px; }
QStatusBar { background: #007acc; color: #fff; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("布林带下轨反弹策略 · 交易终端")
        self.resize(1600, 950)
        self.setStyleSheet(DARK_QSS)

        self.worker: Worker | None = None
        self.last_scan: dict | None = None
        self.last_account: dict | None = None
        self.current_code: str = ""

        self._build_toolbar()
        self._build_ui()
        self._build_statusbar()

        # 启动后自动体检
        QTimer.singleShot(300, self.on_doctor)

    # ==================================================================== 工具栏
    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setIconSize(QSize(16, 16))
        tb.setMovable(False)
        self.addToolBar(tb)

        def add(name, slot, tip="", danger=False, success=False):
            b = QPushButton(name)
            b.clicked.connect(slot)
            b.setToolTip(tip)
            if danger:
                b.setObjectName("danger")
            if success:
                b.setObjectName("success")
            tb.addWidget(b)
            return b

        add("① 环境体检", self.on_doctor, "检查数据源/交易接口/配置")
        tb.addSeparator()
        self.btn_prepare = add("② 盘前准备", self.on_prepare, "更新标的池、检查择时")
        add("③ 扫描信号", self.on_scan, "扫描买入信号")
        tb.addSeparator()
        self.btn_entry = add("④ 执行建仓", self.on_entry, "按信号下单（演练）", success=True)
        add("⑤ 持仓监控", self.on_monitor, "检查止盈止损")
        add("⑥ 账户查询", self.on_account, "资产/持仓/委托/成交")
        tb.addSeparator()

        # 扫描范围
        tb.addWidget(QLabel(" 扫描范围:"))
        self.cmb_scope = QComboBox()
        self.cmb_scope.addItems(["本地缓存(快)", "全部标的(慢)"])
        self.cmb_scope.setFixedWidth(120)
        tb.addWidget(self.cmb_scope)

        self.chk_ignore_market = QCheckBox("忽略择时")
        self.chk_ignore_market.setToolTip("勾选后仅看形态，不受市场择时限制（观察用）")
        tb.addWidget(self.chk_ignore_market)

        tb.addSeparator()
        self.chk_live = QCheckBox("LIVE")
        self.chk_live.setToolTip("勾选后下单将真实执行！默认演练模式")
        from PySide6.QtGui import QPalette
        self.chk_live.setStyleSheet("QCheckBox { color: #ff6b6b; font-weight: bold; }")
        tb.addWidget(self.chk_live)

    # ==================================================================== 主界面
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # --- 顶部：市场状态条
        root.addWidget(self._build_status_bar_panel())

        # --- 中部：左右分栏
        splitter = QSplitter(Qt.Horizontal)

        # 左：信号 + 账户
        left = QSplitter(Qt.Vertical)
        left.addWidget(self._build_signal_panel())
        left.addWidget(self._build_account_panel())
        left.setSizes([560, 340])
        splitter.addWidget(left)

        # 右：K线 + 详情
        right = QSplitter(Qt.Vertical)
        right.addWidget(self._build_chart_panel())
        right.addWidget(self._build_detail_panel())
        right.setSizes([620, 280])
        splitter.addWidget(right)

        splitter.setSizes([780, 820])
        root.addWidget(splitter, 1)

        # --- 底部：日志
        logbox = QGroupBox("运行日志")
        lv = QVBoxLayout(logbox)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(140)
        lv.addWidget(self.log_view)
        root.addWidget(logbox)

    # ---------------------------------------------------------------- 市场状态
    def _build_status_bar_panel(self):
        box = QGroupBox("市场状态")
        g = QGridLayout(box)
        g.setContentsMargins(10, 4, 10, 4)

        self.lbl_market = QLabel("—")
        self.lbl_market.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        self.lbl_threshold = QLabel("—")
        self.lbl_action = QLabel("—")
        self.lbl_action.setFont(QFont("Microsoft YaHei", 11))
        self.lbl_universe = QLabel("—")
        self.lbl_tradedate = QLabel("—")

        g.addWidget(QLabel("择时状态:"), 0, 0)
        g.addWidget(self.lbl_market, 0, 1)
        g.addWidget(QLabel("门槛:"), 0, 2)
        g.addWidget(self.lbl_threshold, 0, 3)
        g.addWidget(QLabel("今日动作:"), 0, 4)
        g.addWidget(self.lbl_action, 0, 5)
        g.addWidget(QLabel("标的池:"), 0, 6)
        g.addWidget(self.lbl_universe, 0, 7)
        g.addWidget(QLabel("交易日:"), 0, 8)
        g.addWidget(self.lbl_tradedate, 0, 9)
        g.setColumnStretch(10, 1)
        return box

    # ---------------------------------------------------------------- 信号列表
    def _build_signal_panel(self):
        box = QGroupBox("信号列表")
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 4, 6, 6)

        self.tabs = QTabWidget()

        # 买入信号
        self.tbl_signal = self._make_table(
            ["代码", "日期", "现价", "下轨", "中轨", "上轨", "位置",
             "连触", "MA60斜率%", "月线", "条件"])
        self.tabs.addTab(self.tbl_signal, "买入信号 (0)")

        # 接近满足
        self.tbl_near = self._make_table(
            ["代码", "日期", "现价", "下轨", "中轨", "位置",
             "连触", "MA60斜率%", "月线", "条件"])
        self.tabs.addTab(self.tbl_near, "接近满足 (0)")

        # 卖出信号
        self.tbl_sell = self._make_table(
            ["代码", "现价", "成本", "盈亏%", "持有天", "止盈价",
             "距止盈%", "止损价", "距止损%", "动作", "原因"])
        self.tabs.addTab(self.tbl_sell, "卖出信号 (0)")

        for t in (self.tbl_signal, self.tbl_near, self.tbl_sell):
            t.cellClicked.connect(self.on_row_clicked)

        v.addWidget(self.tabs)
        return box

    def _make_table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setAlternatingRowColors(True)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setStretchLastSection(True)
        t.setSortingEnabled(False)
        return t

    # ---------------------------------------------------------------- 账户面板
    def _build_account_panel(self):
        box = QGroupBox("账户 / 持仓")
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 4, 6, 6)

        # 资产摘要
        asset_box = QFrame()
        ab = QHBoxLayout(asset_box)
        ab.setContentsMargins(4, 2, 4, 6)
        self.lbl_asset = []
        for name in ["总资产", "可用资金", "持仓市值", "理财占用", "购买力"]:
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.setContentsMargins(0, 0, 0, 0)
            cap = QLabel(name)
            cap.setStyleSheet("color: #888; font-size: 11px;")
            val = QLabel("—")
            val.setStyleSheet("color: #4ec9b0; font-size: 15px; font-weight: bold;")
            wl.addWidget(cap)
            wl.addWidget(val)
            ab.addWidget(w)
            self.lbl_asset.append(val)
        ab.addStretch()
        v.addWidget(asset_box)

        # 持仓表
        self.tbl_pos = self._make_table(
            ["代码", "持仓量", "可用", "成本价", "市值", "浮动盈亏", "盈亏%"])
        self.tbl_pos.cellClicked.connect(self.on_row_clicked)
        v.addWidget(self.tbl_pos)
        return box

    # ---------------------------------------------------------------- 图表
    def _build_chart_panel(self):
        box = QGroupBox("K线图")
        v = QVBoxLayout(box)
        v.setContentsMargins(4, 4, 4, 4)

        bar = QHBoxLayout()
        self.txt_code = QLabel("未选择标的")
        self.txt_code.setStyleSheet("color: #9cdcfe; font-size: 13px; font-weight: bold;")
        bar.addWidget(self.txt_code)
        bar.addStretch()

        self.chk_signal = QCheckBox("显示信号")
        self.chk_signal.setChecked(True)
        bar.addWidget(self.chk_signal)

        btn = QPushButton("刷新图表")
        btn.setFixedWidth(90)
        btn.clicked.connect(self.on_refresh_chart)
        bar.addWidget(btn)
        v.addLayout(bar)

        self.chart = KLineChart()
        v.addWidget(self.chart, 1)
        return box

    # ---------------------------------------------------------------- 详情
    def _build_detail_panel(self):
        box = QGroupBox("标的详情 / 条件明细")
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 4, 6, 6)
        self.txt_detail = QTextEdit()
        self.txt_detail.setReadOnly(True)
        v.addWidget(self.txt_detail)
        return box

    # ---------------------------------------------------------------- 状态栏
    def _build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(260)
        self.progress.setVisible(False)
        sb.addPermanentWidget(self.progress)
        self.lbl_status = QLabel("就绪")
        sb.addWidget(self.lbl_status)

    # ==================================================================== 工具
    def log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        color = {"ERROR": "#f48771", "WARN": "#dcdcaa",
                 "WARNING": "#dcdcaa", "INFO": "#9cdcfe"}.get(level, "#ccc")
        self.log_view.append(
            f'<span style="color:#666">{ts}</span> '
            f'<span style="color:{color}">[{level}]</span> {msg}')
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum())

    def set_status(self, text: str):
        self.lbl_status.setText(text)

    def _start_worker(self, fn, on_ok, *args, **kwargs):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "提示", "有任务正在运行，请稍候")
            return
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._set_buttons_enabled(False)

        w = Worker(fn, *args, **kwargs)
        w.progress.connect(self._on_progress)
        w.log.connect(self.log)
        w.finished_ok.connect(lambda r: self._on_done(r, on_ok))
        w.failed.connect(self._on_failed)
        w.finished.connect(lambda: self._set_buttons_enabled(True))
        self.worker = w
        w.start()

    def _on_progress(self, pct: int, msg: str):
        self.progress.setValue(pct)
        if msg:
            self.set_status(msg)

    def _on_done(self, result, cb):
        self.progress.setVisible(False)
        self.set_status("就绪")
        try:
            cb(result)
        except Exception as e:
            self.log("ERROR", f"结果处理异常: {e}")

    def _on_failed(self, err: str):
        self.progress.setVisible(False)
        self.set_status("失败")
        self.log("ERROR", err.split("\n")[0])
        QMessageBox.critical(self, "任务失败", err[:800])

    def _set_buttons_enabled(self, on: bool):
        for b in self.findChildren(QPushButton):
            b.setEnabled(on)

    # ==================================================================== 动作
    def on_doctor(self):
        self.log("INFO", "开始环境体检...")
        self._start_worker(task_doctor, self._show_doctor)

    def _show_doctor(self, r: dict):
        self.log("INFO", "─── 环境体检结果 ───")
        for name, ver, ok in r.get("deps", []):
            self.log("INFO", f"  {'✓' if ok else '✗'} {name:<16} {ver}")

        t = r.get("tushare", {})
        if t.get("ok"):
            self.log("INFO", f"  ✓ Tushare token 有效（长度{t.get('token_len')}），"
                             f"交易日 {t.get('cal')} 天")
        else:
            self.log("ERROR", f"  ✗ Tushare: {t.get('err')}")

        q = r.get("qmt", {})
        if q.get("ok"):
            a = q.get("asset", {})
            self.log("INFO", f"  ✓ miniQMT 已连接：{q.get('path')}")
            self.log("INFO", f"    总资产 {a.get('总资产',0):,.0f}  "
                             f"可用 {a.get('可用资金',0):,.0f}  "
                             f"持仓 {q.get('positions')} 只")
        else:
            self.log("WARNING", f"  ○ miniQMT: {q.get('err','未连接')}")

        c = r.get("config", {})
        self.log("INFO", f"  配置: 模式={c.get('mode')}  "
                         f"择时门槛={c.get('market_ret_max')}%  "
                         f"止损={c.get('stop_loss')}  "
                         f"最大持仓={c.get('max_pos')}  "
                         f"购买力口径={c.get('power_mode')}")

        pp = r.get("power", {})
        if pp:
            self.log("INFO", f"  购买力: {pp.get('available',0):,.0f} 元"
                             f"（口径 {pp.get('source')}）")
            self._update_asset_labels(pp.get("total", 0), pp.get("cash", 0),
                                      pp.get("market_value", 0),
                                      pp.get("in_wealth", 0), pp.get("available", 0))

    # ---------------------------------------------------------------- 盘前
    def on_prepare(self):
        self.log("INFO", "开始盘前准备...")
        self._start_worker(task_prepare, self._show_plan)

    def _show_plan(self, p: dict):
        date = p.get("trade_date", "")
        n = p.get("universe_size", 0)
        mret = p.get("market_ret")
        thr = p.get("market_threshold")
        passed = p.get("market_passed", False)

        self.lbl_tradedate.setText(str(date))
        self.lbl_universe.setText(f"{n} 只")
        if mret is not None:
            self.lbl_market.setText(f"{'✓ 通过' if passed else '✗ 未通过'}  ({mret:.2f}%)")
            self.lbl_market.setStyleSheet(
                "color: #4ec9b0; font-weight: bold; font-size: 13px;" if passed
                else "color: #f48771; font-weight: bold; font-size: 13px;")
        self.lbl_threshold.setText(f"{thr}%")
        self.lbl_action.setText(p.get("action", ""))
        self.lbl_action.setStyleSheet(
            "color: #4ec9b0;" if passed else "color: #dcdcaa;")

        self.log("INFO", f"─── 盘前准备完成 ───")
        self.log("INFO", f"  交易日 {date}  标的池 {n} 只")
        if mret is not None:
            self.log("INFO" if passed else "WARN",
                     f"  择时 {'通过' if passed else '未通过'}：市场N日 {mret:.2f}% "
                     f"(门槛 {thr}%)")
            if not passed:
                self.log("WARN", f"  距离门槛还差 {thr - mret:.2f}pct → 策略应空仓")

    # ---------------------------------------------------------------- 扫描
    def on_scan(self):
        cache_only = self.cmb_scope.currentIndex() == 0
        ignore = self.chk_ignore_market.isChecked()
        self.log("INFO", f"开始扫描...（{'本地缓存' if cache_only else '全部标的'}"
                         f"{'，忽略择时' if ignore else ''}）")
        self.tabs.setCurrentIndex(0)
        self._start_worker(task_scan, self._show_scan,
                           use_cache_only=cache_only, ignore_market=ignore)

    def _show_scan(self, r: dict):
        self.last_scan = r
        sigs = r.get("signals", [])
        nears = r.get("near", [])

        # 择时状态
        passed = r.get("market_passed", False)
        mret = r.get("market_ret")
        thr = r.get("market_threshold")
        if mret is not None:
            self.lbl_market.setText(f"{'✓ 通过' if passed else '✗ 未通过'}  ({mret:.2f}%)")
            self.lbl_market.setStyleSheet(
                "color: #4ec9b0; font-weight: bold; font-size: 13px;" if passed
                else "color: #f48771; font-weight: bold; font-size: 13px;")
        self.lbl_threshold.setText(f"{thr}%")

        # 买入信号
        t = self.tbl_signal
        t.setRowCount(len(sigs))
        for i, s in enumerate(sigs):
            vals = [s["code"], s["date"], f"{s['close']:.2f}",
                    f"{s['bb_lower']:.2f}" if s["bb_lower"] else "—",
                    f"{s['bb_mid']:.2f}" if s["bb_mid"] else "—",
                    f"{s['bb_upper']:.2f}" if s["bb_upper"] else "—",
                    f"{s['pos120']:.3f}" if s["pos120"] is not None else "—",
                    str(s["streak"]),
                    f"{s['ma60_slope']:.2f}" if s["ma60_slope"] is not None else "—",
                    "✓" if s["monthly_up"] else "✗",
                    s["conditions"]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 0:
                    it.setForeground(QColor("#4ec9b0"))
                if j == 10:
                    it.setForeground(QColor("#4ec9b0"))
                t.setItem(i, j, it)
        t.resizeColumnsToContents()
        self.tabs.setTabText(0, f"买入信号 ({len(sigs)})")

        # 接近
        t2 = self.tbl_near
        t2.setRowCount(len(nears))
        for i, s in enumerate(nears):
            vals = [s["code"], s["date"], f"{s['close']:.2f}",
                    f"{s['bb_lower']:.2f}" if s["bb_lower"] else "—",
                    f"{s['bb_mid']:.2f}" if s["bb_mid"] else "—",
                    f"{s['pos120']:.3f}" if s["pos120"] is not None else "—",
                    str(s["streak"]),
                    f"{s['ma60_slope']:.2f}" if s["ma60_slope"] is not None else "—",
                    "✓" if s["monthly_up"] else "✗",
                    s["conditions"]]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 9 and s["n_ok"] == 5:
                    it.setForeground(QColor("#dcdcaa"))
                t2.setItem(i, j, it)
        t2.resizeColumnsToContents()
        self.tabs.setTabText(1, f"接近满足 ({len(nears)})")

        # 条件统计
        self.log("INFO", f"─── 扫描完成 ───")
        self.log("INFO", f"  已扫描 {r.get('scanned')} 只  "
                         f"买入信号 {len(sigs)} 个  接近 {len(nears)} 个")
        cs = r.get("cond_stats", {})
        if cs:
            self.log("INFO", "  各条件通过率:")
            name_map = {
                "cond_position": "低位(120日分位<0.30)",
                "cond_streak": "连续触轨≥2天",
                "cond_ma60_down": "MA60斜率<-1%",
                "cond_monthly_up": "月线趋势向上",
                "cond_j_low": "J值<13",
                "cond_liquidity": "流动性达标",
            }
            for k, v in cs.items():
                self.log("INFO", f"    {name_map.get(k,k):<22} "
                                 f"{v['count']:>5} 只  {v['pct']:>5.1f}%")
        if not sigs and not passed and not r.get("ignore_market"):
            self.log("WARN", "  择时未通过 → 无信号为空仓等待，符合策略设计")

    # ---------------------------------------------------------------- 账户
    def on_account(self):
        self.log("INFO", "查询账户...")
        self._start_worker(task_account, self._show_account)

    def _show_account(self, r: dict):
        self.last_account = r
        a = r.get("asset", {})
        pp = r.get("power", {})
        pos = r.get("positions")
        orders = r.get("orders")
        trades = r.get("trades")

        self._update_asset_labels(a.get("总资产", 0), a.get("可用资金", 0),
                                  a.get("持仓市值", 0),
                                  pp.get("in_wealth", 0), pp.get("available", 0))

        # 持仓
        t = self.tbl_pos
        if pos is not None and len(pos):
            t.setRowCount(len(pos))
            for i, (_, p) in enumerate(pos.iterrows()):
                vals = [p["证券代码"], str(p["持仓数量"]), str(p["可用数量"]),
                        f"{p['成本价']:.2f}", f"{p['市值']:,.0f}",
                        f"{p['浮动盈亏']:,.0f}", f"{p['盈亏比例']:.2f}%"]
                for j, v in enumerate(vals):
                    it = QTableWidgetItem(str(v))
                    if j == 6:
                        try:
                            it.setForeground(QColor("#4ec9b0") if float(
                                str(v).replace("%", "")) > 0 else QColor("#f48771"))
                        except Exception:
                            pass
                    t.setItem(i, j, it)
            t.resizeColumnsToContents()
        else:
            t.setRowCount(0)

        # 卖出信号（从持仓生成）
        sell_rows = []
        if pos is not None and len(pos):
            try:
                from core.config import load_config
                cfg = load_config()
                stop = cfg.get("strategy.exit.stop_loss_pct", -0.12)
                maxd = cfg.get("strategy.exit.max_hold_days", 60)
                for _, p in pos.iterrows():
                    cur = p["成本价"] * (1 + p["盈亏比例"] / 100) if p["成本价"] else 0
                    sell_rows.append({
                        "代码": p["证券代码"], "现价": f"{cur:.2f}",
                        "成本": f"{p['成本价']:.2f}",
                        "盈亏%": f"{p['盈亏比例']:.2f}",
                        "持有天": "—",
                        "止盈价": "—", "距止盈%": "—",
                        "止损价": f"{p['成本价']*(1+stop):.2f}",
                        "距止损%": f"{stop*100:.1f}",
                        "动作": "持有", "原因": "—",
                    })
            except Exception:
                pass

        t2 = self.tbl_sell
        t2.setRowCount(len(sell_rows))
        for i, s in enumerate(sell_rows):
            for j, h in enumerate(["代码", "现价", "成本", "盈亏%", "持有天",
                                   "止盈价", "距止盈%", "止损价", "距止损%",
                                   "动作", "原因"]):
                t2.setItem(i, j, QTableWidgetItem(str(s.get(h, ""))))
        t2.resizeColumnsToContents()
        self.tabs.setTabText(2, f"卖出信号 ({len(sell_rows)})")

        self.log("INFO", "─── 账户查询完成 ───")
        self.log("INFO", f"  总资产 {a.get('总资产',0):,.0f}  "
                         f"可用 {a.get('可用资金',0):,.0f}  "
                         f"市值 {a.get('持仓市值',0):,.0f}")
        if pp:
            self.log("INFO", f"  购买力 {pp.get('available',0):,.0f} 元"
                             f"（口径 {pp.get('source')}）；"
                             f"理财占用 {pp.get('in_wealth',0):,.0f} 元")
        self.log("INFO", f"  持仓 {0 if pos is None else len(pos)} 只  "
                         f"委托 {0 if orders is None else len(orders)} 笔  "
                         f"成交 {0 if trades is None else len(trades)} 笔")

    def _update_asset_labels(self, total, cash, mv, wealth, power):
        vals = [f"{total:,.0f}", f"{cash:,.0f}", f"{mv:,.0f}",
                f"{wealth:,.0f}", f"{power:,.0f}"]
        for lbl, v in zip(self.lbl_asset, vals):
            lbl.setText(v)

    # ---------------------------------------------------------------- 建仓
    def on_entry(self):
        dry = not self.chk_live.isChecked()
        if not dry:
            ok = QMessageBox.question(
                self, "确认实盘下单",
                "已勾选 LIVE 模式，建仓将真实下单！\n\n确定继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ok != QMessageBox.Yes:
                self.chk_live.setChecked(False)
                return
        self.log("INFO" if dry else "WARN",
                 f"执行建仓...（{'演练' if dry else '★实盘'}）")
        self._start_worker(task_entry, self._show_entry, dry_run=dry)

    def _show_entry(self, r: dict):
        self.log("INFO", "─── 建仓完成 ───")
        if r.get("skipped"):
            self.log("WARN", f"  跳过：{r.get('reason')}")
            return
        self.log("INFO", f"  信号 {r.get('signals',0)} 个，"
                         f"已下单 {r.get('ordered',0)} 笔")
        for o in r.get("orders", []):
            self.log("INFO", f"    {o['code']} {o['vol']}股 @{o['price']} "
                             f"#{o['order_id']}")

    # ---------------------------------------------------------------- 监控
    def on_monitor(self):
        dry = not self.chk_live.isChecked()
        self.log("INFO", f"持仓监控...（{'演练' if dry else '★实盘'}）")
        self._start_worker(task_monitor, self._show_monitor, dry_run=dry)

    def _show_monitor(self, r: dict):
        n = r.get("monitored", 0)
        sold = r.get("sold", 0)
        self.log("INFO", f"─── 监控完成 ───  监控 {n} 只，卖出 {sold} 笔")
        for d in r.get("details", []):
            self.log("WARN", f"    {d['code']} {d['vol']}股 @{d['price']} "
                             f"({d['reason']})")

    # ---------------------------------------------------------------- 图表
    def on_row_clicked(self, row: int, col: int):
        tbl = self.sender()
        it = tbl.item(row, 0)
        if it is None:
            return
        code = it.text().strip()
        if not code:
            return
        self.current_code = code
        self.txt_code.setText(f"加载 {code} ...")
        self._start_worker(task_kline, self._show_kline, code=code, days=250)

    def _show_kline(self, r: dict):
        code = r["code"]
        d = r["data"]
        if self.chk_signal.isChecked():
            extra = f"  收 {d['close'].iloc[-1]:.2f}  下轨 {d['bb_lower'].iloc[-1]:.2f}  中轨 {d['bb_mid'].iloc[-1]:.2f}  上轨 {d['bb_upper'].iloc[-1]:.2f}"
        else:
            extra = ""
        self.chart.plot(d, code=code, title_extra=extra)
        self.txt_code.setText(f"{code}  {d['date'].iloc[-1].strftime('%Y-%m-%d')}")

        # 详情
        r0 = d.iloc[-1]
        lines = [f"【{code}】{r0['date'].strftime('%Y-%m-%d')}", ""]
        def fmt(v, n=2):
            try:
                return f"{float(v):.{n}f}"
            except Exception:
                return "—"
        lines += [
            f"  收盘价      {fmt(r0['close'])}",
            f"  布林上轨    {fmt(r0.get('bb_upper'))}",
            f"  布林中轨    {fmt(r0.get('bb_mid'))}",
            f"  布林下轨    {fmt(r0.get('bb_lower'))}",
            f"  MA60        {fmt(r0.get('ma60'))}",
            f"  MA60斜率    {fmt(r0.get('ma60_slope',0)*100 if r0.get('ma60_slope') is not None else None)} %",
            f"  120日分位   {fmt(r0.get('pos120'),3)}",
            f"  连续触轨    {int(r0.get('lower_streak',0))} 天",
            f"  月线趋势    {'向上' if r0.get('monthly_trend_up') else '向下'}",
            f"  J值         {fmt(r0.get('J'),1)}",
            "",
            "【条件检查】",
        ]
        cmap = {
            "cond_position": "低位(120日分位<0.30)",
            "cond_streak": "连续触轨≥2天",
            "cond_ma60_down": "MA60斜率<-1%",
            "cond_monthly_up": "月线趋势向上",
            "cond_j_low": "J值<13",
            "cond_liquidity": "流动性达标",
        }
        for k, label in cmap.items():
            if k in d.columns:
                ok = bool(r0[k])
                lines.append(f"  {'✓' if ok else '✗'} {label}")

        # 历史信号
        if "signal" in d.columns:
            sig = d[d["signal"].fillna(False)]
            if len(sig):
                lines += ["", f"【历史信号 {len(sig)} 次】"]
                for _, s in sig.tail(5).iterrows():
                    lines.append(f"  {s['date'].strftime('%Y-%m-%d')}  收 {s['close']:.2f}")

        self.txt_detail.setPlainText("\n".join(lines))

    def on_refresh_chart(self):
        if self.current_code:
            self.on_row_clicked_code(self.current_code)

    def on_row_clicked_code(self, code: str):
        self._start_worker(task_kline, self._show_kline, code=code, days=250)
