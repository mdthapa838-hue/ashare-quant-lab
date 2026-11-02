# -*- coding: utf-8 -*-
"""
earnings_gui.py — 业绩公告监控 GUI

功能：
  ① 自然日选择 + 联网刷新（多线程，逐日保存 SQLite）
  ② 表格展示：代码/名称/增速/位置/PE/市值/分组/措辞预警
  ③ 左侧筛选：分组、增速下限、位置、市值
  ④ 右侧详情：公告原文、变动原因、位置细节
  ⑤ 排序（点列头）、搜索
  ⑥ 导出 CSV/Excel
  ⑦ 双击打开公告原文链接
  ⑧ 离线数据库查询、区间批量获取、取消与历史版本查看

用法：
    python earnings_gui.py
    python earnings_gui.py --database "D:/资料/earnings.sqlite3" --no-auto-refresh
"""
from __future__ import annotations

import os
import argparse
from html import escape
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

from PySide6.QtCore import Qt, QTimer, QDate, QUrl
from PySide6.QtGui import QColor, QBrush, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QDoubleSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QTextEdit,
    QCheckBox, QDateEdit, QMessageBox, QFileDialog, QProgressBar,
    QStatusBar, QAbstractItemView, QDialog)

from earnings_jobs import BatchFetchWorker
from earnings_store import EarningsStore


# ============================================================ 表格模型
COLS = ["代码", "名称", "查询日期", "行情时间", "完整性", "来源",
        "分组", "跳空%", "增速%", "类型", "发布时间",
        "p20", "p60", "p120", "距高%",
        "现价", "今日%", "换手%", "PE", "市值亿",
        "负面词", "标题"]

COL_W = {"代码": 62, "名称": 86, "分组": 190, "跳空%": 60,
         "增速%": 66, "类型": 52,
         "发布时间": 92, "p20": 52, "p60": 52, "p120": 56, "距高%": 60,
         "现价": 60, "今日%": 62, "换手%": 58, "PE": 58, "市值亿": 66,
         "负面词": 130, "查询日期": 100, "行情时间": 152,
         "完整性": 110, "来源": 160, "标题": 400}


# ============================================================ 主窗口
class MainWindow(QMainWindow):
    def __init__(self, store_path=None, auto_load=True):
        super().__init__()
        self.setWindowTitle("业绩公告监控 · A股 · 本地数据库")
        self.resize(1680, 940)
        self.store_path = Path(store_path) if store_path is not None else HERE / "earnings_watch" / "earnings.sqlite3"
        self.store = None
        self.df = pd.DataFrame(columns=COLS)
        self.worker = None
        self._close_pending = False
        self._cancel_requested = False
        self._active_dates = []
        self._batch_summary = None
        self._worker_error = ""
        self._saved_days = 0
        self._history_dialog = None

        self._build_ui()
        self._auto_date()
        self._rebuild_table(self.df)
        try:
            self.store = EarningsStore(self.store_path)
        except Exception as exc:
            self.lbl_status.setText(f"本地数据库不可用：{str(exc)[:300]}")
        if auto_load:
            QTimer.singleShot(0, self.load_today_cache)

    # ---------------- UI
    def _build_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        root = QVBoxLayout(cw)
        root.setContentsMargins(8, 6, 8, 4)
        root.setSpacing(6)

        # ---- 顶部工具条
        bar = QHBoxLayout()
        bar.setSpacing(8)

        bar.addWidget(QLabel("日期:"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setFixedWidth(118)
        bar.addWidget(self.date_edit)

        self.btn_prev = QPushButton("◀ 前一天")
        self.btn_prev.setFixedWidth(84)
        self.btn_prev.clicked.connect(lambda: self._shift_day(-1))
        bar.addWidget(self.btn_prev)

        self.btn_next = QPushButton("后一天 ▶")
        self.btn_next.setFixedWidth(84)
        self.btn_next.clicked.connect(lambda: self._shift_day(1))
        bar.addWidget(self.btn_next)

        self.btn_today = QPushButton("今天")
        self.btn_today.setFixedWidth(56)
        self.btn_today.clicked.connect(self._auto_date)
        bar.addWidget(self.btn_today)

        bar.addSpacing(10)
        self.btn_refresh = QPushButton("联网刷新当日")
        self.btn_refresh.setFixedWidth(112)
        self.btn_refresh.setStyleSheet(
            "QPushButton{background:#1a6fb5;color:white;font-weight:bold;"
            "border-radius:4px;padding:5px}")
        self.btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(self.btn_refresh)

        self.chk_tushare = QCheckBox("含财务数据")
        self.chk_tushare.setChecked(True)
        self.chk_tushare.setToolTip("勾选则查 Tushare（较慢，但有增速/类型）")
        bar.addWidget(self.chk_tushare)

        bar.addStretch()

        self.lbl_info = QLabel("尚未加载")
        self.lbl_info.setStyleSheet("color:#444;font-size:12px")
        bar.addWidget(self.lbl_info)
        root.addLayout(bar)

        # 独立的数据库/批量工具条；查询按钮只读取 SQLite。
        dbbar = QHBoxLayout()
        dbbar.setSpacing(8)
        dbbar.addWidget(QLabel("本地 / 批量区间:"))
        self.start_date_edit = QDateEdit()
        self.end_date_edit = QDateEdit()
        for widget, name in ((self.start_date_edit, "range_start"),
                             (self.end_date_edit, "range_end")):
            widget.setObjectName(name)
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
            widget.setFixedWidth(118)
        dbbar.addWidget(self.start_date_edit)
        dbbar.addWidget(QLabel("至"))
        dbbar.addWidget(self.end_date_edit)
        self.btn_batch = QPushButton("联网批量获取")
        self.btn_batch.setObjectName("batch_fetch")
        self.btn_batch.clicked.connect(self.fetch_range)
        dbbar.addWidget(self.btn_batch)
        self.chk_skip_completed = QCheckBox("跳过已完成")
        self.chk_skip_completed.setChecked(True)
        self.chk_skip_completed.setToolTip("仅跳过此财务选项下完整成功的日期；失败和不完整日期仍可重试。")
        dbbar.addWidget(self.chk_skip_completed)
        self.btn_cancel = QPushButton("取消获取")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_fetch)
        dbbar.addWidget(self.btn_cancel)
        self.btn_query = QPushButton("查本地范围（离线）")
        self.btn_query.setObjectName("query_local")
        self.btn_query.clicked.connect(self.query_local_range)
        dbbar.addWidget(self.btn_query)
        self.btn_all_local = QPushButton("全部本地（离线）")
        self.btn_all_local.setObjectName("all_local")
        self.btn_all_local.clicked.connect(self.query_all_local)
        dbbar.addWidget(self.btn_all_local)
        self.btn_history = QPushButton("历史运行记录")
        self.btn_history.clicked.connect(self.show_history)
        dbbar.addWidget(self.btn_history)
        dbbar.addStretch()
        root.addLayout(dbbar)
        self.lbl_database = QLabel(f"数据库：{self.store_path}　｜　启动只读本地缓存；历史日期查询不等于历史当时可知数据。")
        self.lbl_database.setTextFormat(Qt.PlainText)
        self.lbl_database.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_database.setWordWrap(True)
        self.lbl_database.setStyleSheet("color:#596574;font-size:11px")
        root.addWidget(self.lbl_database)

        # ---- 筛选条
        fb = QHBoxLayout()
        fb.setSpacing(8)

        fb.addWidget(QLabel("搜索:"))
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("代码 / 名称 / 标题 / 摘要")
        self.ed_search.setFixedWidth(190)
        self.ed_search.textChanged.connect(self._apply_filter)
        fb.addWidget(self.ed_search)

        fb.addSpacing(6)
        fb.addWidget(QLabel("增速≥"))
        self.sp_g = QDoubleSpinBox()
        self.sp_g.setRange(-1000, 100000)
        self.sp_g.setValue(-1000)
        self.sp_g.setFixedWidth(84)
        self.sp_g.setDecimals(0)
        self.sp_g.valueChanged.connect(self._apply_filter)
        fb.addWidget(self.sp_g)

        fb.addWidget(QLabel("位置≤"))
        self.sp_pos = QDoubleSpinBox()
        self.sp_pos.setRange(-1, 101)
        self.sp_pos.setValue(101)
        self.sp_pos.setFixedWidth(70)
        self.sp_pos.setDecimals(0)
        self.sp_pos.setToolTip("用 p60 判断（-1 表示不限）")
        self.sp_pos.valueChanged.connect(self._apply_filter)
        fb.addWidget(self.sp_pos)

        fb.addWidget(QLabel("市值(亿)≤"))
        self.sp_mv = QDoubleSpinBox()
        self.sp_mv.setRange(0, 999999)
        self.sp_mv.setValue(0)
        self.sp_mv.setFixedWidth(84)
        self.sp_mv.setDecimals(0)
        self.sp_mv.setSpecialValueText("不限")
        self.sp_mv.valueChanged.connect(self._apply_filter)
        fb.addWidget(self.sp_mv)

        self.chk_flaw = QCheckBox("仅看措辞有瑕疵")
        self.chk_flaw.stateChanged.connect(self._apply_filter)
        fb.addWidget(self.chk_flaw)

        self.btn_clear = QPushButton("清空筛选")
        self.btn_clear.setFixedWidth(84)
        self.btn_clear.clicked.connect(self._clear_filter)
        fb.addWidget(self.btn_clear)

        fb.addStretch()
        self.btn_export = QPushButton("导出 CSV")
        self.btn_export.setFixedWidth(90)
        self.btn_export.clicked.connect(self.export_csv)
        fb.addWidget(self.btn_export)

        self.btn_export_x = QPushButton("导出 Excel")
        self.btn_export_x.setFixedWidth(96)
        self.btn_export_x.clicked.connect(self.export_excel)
        fb.addWidget(self.btn_export_x)
        root.addLayout(fb)

        # ---- 分组快捷按钮
        gb = QGridLayout()
        gb.setSpacing(6)
        gb.addWidget(QLabel("分组:"), 0, 0)
        self.grp_checks = {}
        for g, color in [("A1 优选（高位+跳空+未回补）★★★", "#0a7a0a"),
                         ("A1' 高位+措辞瑕疵", "#c86400"),
                         ("A1 次选（高位+跳空，但回补）", "#3a8a3a"),
                         ("A1 次选（高位，跳空弱）", "#6a9a6a"),
                         ("A2 观察（低位，不推荐）", "#999999"),
                         ("B 高增+中位", "#444"),
                         ("D 中增", "#666"),
                         ("D 中增(低位)", "#888"),
                         ("E 低增", "#aaa"),
                         ("快报/待核实", "#888")]:
            cb = QCheckBox(g)
            cb.setChecked(True)
            cb.setStyleSheet(f"QCheckBox{{color:{color};font-weight:bold}}")
            cb.stateChanged.connect(self._apply_filter)
            self.grp_checks[g] = cb
            position = len(self.grp_checks) - 1
            gb.addWidget(cb, position // 5, position % 5 + 1)
        self.chk_other_groups = QCheckBox("旧版 / 其他分组")
        self.chk_other_groups.setObjectName("legacy_other_groups")
        self.chk_other_groups.setChecked(True)
        self.chk_other_groups.setToolTip("保留旧表的原分组，不映射为当前评级；仅控制不属于上述分组的记录。")
        self.chk_other_groups.stateChanged.connect(self._apply_filter)
        gb.addWidget(self.chk_other_groups, 1, 6)
        gb.setColumnStretch(6, 1)
        root.addLayout(gb)

        # ---- 正文区（表格 + 详情）
        split = QSplitter(Qt.Horizontal)

        self.table = QTableWidget()
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive)
        self.table.itemSelectionChanged.connect(self.on_select)
        self.table.itemDoubleClicked.connect(self.on_double)
        self.table.setStyleSheet(
            "QTableWidget{font-size:12px}"
            "QHeaderView::section{background:#eef2f7;padding:3px;"
            "border:none;border-right:1px solid #d6dde5;font-weight:bold}")
        split.addWidget(self.table)

        # 右侧详情
        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.setSpacing(4)

        self.lbl_detail_head = QLabel("选中一行查看详情")
        self.lbl_detail_head.setStyleSheet(
            "font-size:15px;font-weight:bold;padding:6px;color:#123;"
            "background:#f2f6fa;border-radius:4px")
        self.lbl_detail_head.setTextFormat(Qt.PlainText)
        self.lbl_detail_head.setWordWrap(True)
        rl.addWidget(self.lbl_detail_head)

        self.txt_detail = QTextEdit()
        self.txt_detail.setReadOnly(True)
        self.txt_detail.setStyleSheet("font-size:12px;line-height:150%")
        rl.addWidget(self.txt_detail, 1)

        btn_row = QHBoxLayout()
        self.btn_open = QPushButton("打开公告原文")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self.open_url)
        btn_row.addWidget(self.btn_open)
        self.btn_copy = QPushButton("复制代码")
        self.btn_copy.clicked.connect(self.copy_code)
        btn_row.addWidget(self.btn_copy)
        btn_row.addStretch()
        rl.addLayout(btn_row)

        split.addWidget(rw)
        split.setStretchFactor(0, 72)
        split.setStretchFactor(1, 28)
        split.setSizes([1180, 460])
        root.addWidget(split, 1)

        # ---- 状态栏
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.prog = QProgressBar()
        self.prog.setFixedWidth(260)
        self.prog.setVisible(False)
        self.status.addPermanentWidget(self.prog)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setTextFormat(Qt.PlainText)
        self.status.addWidget(self.lbl_status)

    # ---------------- 逻辑
    def _auto_date(self):
        # 公告可在周末发布，日期不回退到最近交易日。
        today = QDate.currentDate()
        for widget in (self.date_edit, self.start_date_edit, self.end_date_edit):
            widget.setMaximumDate(today)
            widget.setDate(today)

    def _shift_day(self, n):
        self.date_edit.setDate(self.date_edit.date().addDays(n))

    def date_str(self) -> str:
        return self.date_edit.date().toString("yyyy-MM-dd")

    def _range_dates(self):
        start, end = self.start_date_edit.date(), self.end_date_edit.date()
        today = QDate.currentDate()
        if start > end or end > today or start > today:
            self.lbl_status.setText("日期范围无效：起始日期须不晚于结束日期，且都不晚于今天。")
            return None
        return [start.addDays(i).toString("yyyy-MM-dd") for i in range(start.daysTo(end) + 1)]

    def _ensure_store(self):
        if self.store is None:
            self.store = EarningsStore(self.store_path)
        return self.store

    def load_today_cache(self):
        ds = self.date_str()
        self._load_local(ds, ds)

    def query_local_range(self):
        dates = self._range_dates()
        if dates:
            self._load_local(dates[0], dates[-1])

    def query_all_local(self):
        try:
            runs = self._ensure_store().list_runs()
            dates = sorted({str(run.get("query_date", "")) for run in runs
                            if QDate.fromString(str(run.get("query_date", "")), "yyyy-MM-dd").isValid()})
            if not dates:
                self.on_data(pd.DataFrame())
                self.lbl_status.setText("本地数据库暂无运行记录（未联网）。")
                return
            self.start_date_edit.setDate(QDate.fromString(dates[0], "yyyy-MM-dd"))
            self.end_date_edit.setDate(QDate.fromString(dates[-1], "yyyy-MM-dd"))
            self._load_local(dates[0], dates[-1])
        except Exception as exc:
            self.lbl_status.setText(f"本地范围读取失败（未联网）：{str(exc)[:400]}")

    def _load_local(self, start, end):
        try:
            rows = self._ensure_store().query_rows(start, end, latest_only=True,
                                                  include_partial=True)
            self.on_data(pd.DataFrame(rows))
            self.lbl_status.setText(f"离线读取 {start} 至 {end}：{len(rows)} 条公告（未联网）。" +
                                    (" 本地无记录，可手动联网获取；空日/失败请查看历史运行记录。" if not rows else ""))
            return True
        except Exception as exc:
            self.lbl_status.setText(f"本地读取失败（未联网）：{str(exc)[:400]}")
            return False

    def refresh(self):
        ds = self.date_str()
        if self.date_edit.date() > QDate.currentDate():
            self.lbl_status.setText("不能获取未来日期。")
            return
        self.start_date_edit.setDate(self.date_edit.date())
        self.end_date_edit.setDate(self.date_edit.date())
        self._start_fetch([ds], skip_completed=False)

    def fetch_range(self):
        dates = self._range_dates()
        if dates:
            self._start_fetch(dates, skip_completed=self.chk_skip_completed.isChecked())

    def _set_fetch_busy(self, busy):
        for widget in (self.btn_refresh, self.btn_batch, self.date_edit, self.btn_prev,
                       self.btn_next, self.btn_today, self.start_date_edit, self.end_date_edit,
                       self.chk_tushare, self.chk_skip_completed):
            widget.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.prog.setVisible(busy)

    def _start_fetch(self, dates, skip_completed):
        if self.worker is not None:
            self.lbl_status.setText("已有取数任务；请等待完成或取消。")
            return
        try:
            self._ensure_store()
            worker = BatchFetchWorker(dates, use_tushare=self.chk_tushare.isChecked(),
                                      store_path=self.store_path, skip_completed=skip_completed)
        except Exception as exc:
            self.lbl_status.setText(f"无法开始获取：{str(exc)[:400]}")
            return
        self.worker = worker
        self._active_dates = list(dates)
        self._batch_summary = None
        self._worker_error = ""
        self._saved_days = 0
        self._cancel_requested = False
        self._set_fetch_busy(True)
        self.prog.setRange(0, len(dates))
        self.prog.setValue(0)
        self.lbl_status.setText(f"联网获取 {dates[0]} 至 {dates[-1]}，共 {len(dates)} 个自然日；逐日写入数据库。")
        worker.progress.connect(self._on_progress)
        worker.day_done.connect(self._on_day_done)
        worker.summary.connect(self._on_summary)
        worker.failed.connect(self.on_fail)
        worker.finished.connect(self._on_worker_finished)
        try:
            worker.start()
        except Exception as exc:
            self.worker = None
            worker.deleteLater()
            self._set_fetch_busy(False)
            self.lbl_status.setText(f"取数线程未能启动：{str(exc)[:400]}")

    def _on_progress(self, message):
        self.lbl_status.setText(("正在取消，等待当前请求结束。" if self._cancel_requested else "联网获取：") + str(message))

    def _on_day_done(self, day, df, meta):
        # day_done 仅在落库成功后发出，最终展示统一从数据库读取。
        self._saved_days += 1
        self.prog.setValue(self._saved_days)
        completeness = "完整" if meta.get("complete") is True else "不完整"
        self.lbl_status.setText(f"已落库 {day}：{0 if df is None else len(df)} 条，{completeness}；已保存 {self._saved_days} 日。")

    def _on_summary(self, summary):
        self._batch_summary = dict(summary)
        self.prog.setValue(int(summary.get("completed", 0)) + int(summary.get("partial", 0)) +
                           int(summary.get("skipped", 0)) + int(summary.get("failed", 0)))

    def on_fail(self, msg):
        # failed 信号不等于 QThread 已退出；必须等 finished 再释放引用。
        self._worker_error = str(msg)[:1500]
        self.lbl_status.setText(f"获取失败，等待任务退出：{self._worker_error}")

    def _on_worker_finished(self):
        worker = self.worker
        self.worker = None
        self._set_fetch_busy(False)
        if worker is not None:
            worker.deleteLater()
        dates = self._active_dates
        loaded = self._load_local(dates[0], dates[-1]) if dates else False
        summary = self._batch_summary
        if summary is not None:
            fatal = self._worker_error or str(summary.get("fatal_error") or "")
            label = "任务失败" if fatal else "已取消" if summary.get("cancelled") else "批量结束"
            text = (f"{label}：计划 {summary.get('planned', len(dates))} 日，"
                    f"完整 {summary.get('completed', 0)}，不完整 {summary.get('partial', 0)}，"
                    f"跳过 {summary.get('skipped', 0)}，失败 {summary.get('failed', 0)}；"
                    f"本次获取 {summary.get('rows', 0)} 条。")
            if fatal:
                text += f" 任务级错误：{fatal}。"
            text += " 已显示本地范围（含保留的旧版本/不完整记录）。" if loaded else " 本地范围读取失败，请查看历史记录。"
            self.lbl_status.setText(text)
        elif self._worker_error:
            self.lbl_status.setText(f"获取失败：{self._worker_error}；已尝试显示本地已有记录。")
        else:
            self.lbl_status.setText("获取任务已退出，但没有完成摘要；请核对历史运行记录。")
        if self._close_pending:
            QTimer.singleShot(0, self.close)

    def cancel_fetch(self):
        if self.worker is not None:
            self._cancel_requested = True
            self.worker.requestInterruption()
            self.btn_cancel.setEnabled(False)
            self.lbl_status.setText("已请求取消；等待当前网络请求安全结束，已保存记录会保留。")

    def closeEvent(self, event):
        if self.worker is not None:
            self._close_pending = True
            self.cancel_fetch()
            self.lbl_status.setText("正在安全退出：等待当前请求结束后自动关闭，已保存记录会保留。")
            event.ignore()
            return
        event.accept()

    def show_history(self):
        dates = self._range_dates()
        if not dates:
            return
        try:
            runs = self._ensure_store().list_runs(start_date=dates[0], end_date=dates[-1])
        except Exception as exc:
            self.lbl_status.setText(f"历史记录读取失败：{str(exc)[:400]}")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"历史运行记录（离线）· {dates[0]} 至 {dates[-1]}")
        dialog.resize(1300, 580)
        layout = QVBoxLayout(dialog)
        note = QLabel(f"共 {len(runs)} 次；每次获取保留独立版本，失败、空日和不完整记录不会被隐藏。")
        note.setTextFormat(Qt.PlainText)
        layout.addWidget(note)
        table = QTableWidget(dialog)
        table.setObjectName("history_runs")
        keys = [("查询日期", ("query_date", "查询日期")), ("版本 / 运行ID", ("run_id", "id")),
                ("状态", ("status",)), ("完整", ("complete",)), ("条数", ("row_count", "rows")),
                ("含财务", ("use_tushare",)), ("开始 / 获取时间", ("started_at", "fetched_at", "created_at")),
                ("完成时间", ("finished_at", "completed_at")), ("来源", ("source", "origin", "source_kind")),
                ("错误 / 提示", ("error", "error_message", "warnings"))]
        table.setColumnCount(len(keys))
        table.setHorizontalHeaderLabels([title for title, _ in keys])
        table.setRowCount(len(runs))
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        for i, run in enumerate(runs):
            for j, (_, aliases) in enumerate(keys):
                value = next((run[k] for k in aliases if k in run and run[k] is not None), "未知")
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole + 1, run)
                table.setItem(i, j, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        table.setSortingEnabled(True)
        layout.addWidget(table)
        run_details = QTextEdit()
        run_details.setReadOnly(True)
        run_details.setMaximumHeight(100)
        layout.addWidget(run_details)

        def selected_run():
            item = table.item(table.currentRow(), 0)
            return item.data(Qt.UserRole + 1) if item is not None else None

        def describe_run():
            run = selected_run()
            if run:
                run_details.setPlainText(
                    f"版本 {run.get('run_id', run.get('id', '未知'))}　日期 {run.get('query_date', '未知')}　"
                    f"状态 {run.get('status', '未知')}　条数 {run.get('row_count', '未知')}\n"
                    f"错误：{run.get('error') or '无记录'}\n元数据：{run.get('metadata', {})}")

        def view_snapshot():
            run = selected_run()
            if run and self._show_run_snapshot(run):
                dialog.close()

        table.itemSelectionChanged.connect(describe_run)
        table.itemDoubleClicked.connect(lambda item: view_snapshot())
        view_button = QPushButton("查看选中版本快照")
        view_button.clicked.connect(view_snapshot)
        layout.addWidget(view_button)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)
        self._history_dialog = dialog
        dialog.show()

    @staticmethod
    def _archive_value(row, name):
        # Store 保留原表同名列，并为实际存档信息逐层加“存档_”前缀。
        key = name
        while "存档_" + key in row:
            key = "存档_" + key
        return row.get(key)

    def _show_run_snapshot(self, run):
        day = str(run.get("query_date", ""))
        run_id = run.get("run_id", run.get("id"))
        status = run.get("status", "未知")
        try:
            rows = []
            if status in {"success", "partial"}:
                all_rows = self._ensure_store().query_rows(day, day, latest_only=False, include_partial=True)
                rows = [row for row in all_rows
                        if str(self._archive_value(row, "运行ID")) == str(run_id)]
            self.on_data(pd.DataFrame(rows))
            self.start_date_edit.setDate(QDate.fromString(day, "yyyy-MM-dd"))
            self.end_date_edit.setDate(QDate.fromString(day, "yyyy-MM-dd"))
            message = f"离线版本 {run_id}｜{day}｜状态 {status}｜{len(rows)} 条公告。"
            if status not in {"success", "partial"}:
                message += f"该次没有已完成的快照；错误：{run.get('error') or '无记录'}"
            elif not rows:
                message += "该版本为空日快照。"
            self.lbl_status.setText(message)
            return True
        except Exception as exc:
            self.lbl_status.setText(f"历史快照读取失败（未联网）：{str(exc)[:400]}")
            return False

    def on_data(self, df):
        self.txt_detail.clear()
        self.lbl_detail_head.setText("选中一行查看详情")
        self.btn_open.setEnabled(False)
        if df is None or len(df) == 0:
            self.df = pd.DataFrame(columns=COLS)
            self.lbl_status.setText("当日无业绩类公告")
            self.lbl_info.setText("0 条公告")
            self._rebuild_table(self.df)
            return
        # 排序：分组优先级 + 增速
        order = {"A1 优选（高位+跳空+未回补）★★★": 0,
                 "A1' 高位+措辞瑕疵": 1,
                 "A1 次选（高位+跳空，但回补）": 2,
                 "A1 次选（高位，跳空弱）": 3,
                 "B 高增+中位": 4,
                 "A2 观察（低位，不推荐）": 5,
                 "D 中增": 6, "D 中增(低位)": 7,
                 "E 低增": 8, "快报/待核实": 9}
        df = df.copy()

        def missing(series):
            return series.isna() | series.astype(str).str.strip().eq("")

        def coalesce(column, aliases):
            if column not in df.columns:
                df[column] = pd.Series(None, index=df.index, dtype=object)
            for alias in aliases:
                if alias in df.columns:
                    df[column] = df[column].where(~missing(df[column]), df[alias])

        def preserve(column):
            if column not in df.columns:
                return
            original_key = "原表_" + column
            while original_key in df.columns:
                original_key = "原表_" + original_key
            df[original_key] = df[column]

        for column in ("查询日期", "获取时间", "行情时间", "行情基准", "数据来源", "完整性", "运行ID"):
            archive_key = "存档_" + column
            if archive_key in df.columns:
                preserve(column)
                if column not in df.columns:
                    df[column] = pd.Series(None, index=df.index, dtype=object)
                while archive_key in df.columns:
                    df[column] = df[archive_key].where(~missing(df[archive_key]), df[column])
                    archive_key = "存档_" + archive_key
        for column, aliases in {
            "查询日期": ("query_date",), "获取时间": ("fetched_at",),
            "行情时间": ("market_time", "quote_time"), "行情基准": ("quote_basis",),
            "数据来源": ("source",), "运行ID": ("run_id",),
            "完整性": ("completeness",), "市值亿": ("总市值亿",),
            "p20": ("位置20日%",), "p60": ("位置60日%",), "p120": ("位置120日%",),
            "距高%": ("距120日高%",), "摘要": ("要点",),
        }.items():
            coalesce(column, aliases)
        # 旧 CSV 的“来源=预告/快报”是业务分类；实际数据出处以存档元数据为准。
        preserve("来源")
        df["来源"] = df["数据来源"]
        legacy = df["来源"].astype(str).str.contains("legacy_csv", case=False, na=False)
        legacy |= df["完整性"].astype(str).str.contains("历史CSV", na=False)
        if "发布时间" not in df.columns:
            df["发布时间"] = pd.Series(None, index=df.index, dtype=object)
        if "公告日" in df.columns:
            publication_from_date = missing(df["发布时间"]) & ~missing(df["公告日"])
            preserve("发布时间")
            df.loc[publication_from_date, "发布时间"] = (
                df.loc[publication_from_date, "公告日"].astype(str) + "（仅有日期，时分未知）")
            legacy |= publication_from_date
        if "标题" not in df.columns:
            df["标题"] = pd.Series(None, index=df.index, dtype=object)
        missing_title = missing(df["标题"])
        if missing_title.any():
            preserve("标题")
            df.loc[missing_title & legacy, "标题"] = "旧CSV未保存标题"
            df.loc[missing_title & ~legacy, "标题"] = "未记录标题"
        numeric = {"跳空%", "增速%", "p20", "p60", "p120", "距高%", "现价", "今日%", "换手%", "PE", "市值亿", "PB", "负面数"}
        for column in set(COLS) | {"PB", "负面数", "原因", "摘要", "链接", "正面词"}:
            if column not in df.columns:
                df[column] = np.nan if column in numeric else ""
        for column in numeric:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df["分组"] = df["分组"].fillna("快报/待核实").replace("", "快报/待核实")
        for column in ("查询日期", "行情时间", "完整性", "来源"):
            df[column] = df[column].fillna("未知").replace("", "未知")
        df["_o"] = df["分组"].map(lambda g: order.get(str(g), 9))
        df = df.sort_values(["_o", "增速%"],
                            ascending=[True, False],
                            na_position="last").drop(columns=["_o"])
        df = df.reset_index(drop=True)
        self.df = df

        n = len(df)
        a1 = int(df["分组"].astype(str).str.startswith("A1 优选").sum())
        a1b = int(df["分组"].astype(str).str.startswith("A1 次选").sum())
        a2 = int(df["分组"].astype(str).str.contains("A2").sum())
        fl = int((df["负面数"] >= 2).sum())
        self.lbl_info.setText(
            f"{n} 条公告　★优选 {a1}　次选 {a1b}　"
            f"低位(不推荐) {a2}　措辞瑕疵 {fl}")
        self._apply_filter()

    def _rebuild_table(self, df):
        cols = [c for c in COLS if c in df.columns]
        self.table.setSortingEnabled(False)
        self.table.clear()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(df))

        for i in range(len(df)):
            snapshot = df.iloc[i].to_dict()
            for j, c in enumerate(cols):
                v = df.iloc[i][c]
                if v is None or (isinstance(v, float) and not np.isfinite(v)):
                    s = ""
                elif isinstance(v, float):
                    if c in ("PE", "PB", "p20", "p60", "p120", "距高%",
                             "增速%", "增速上限%"):
                        s = f"{v:.1f}"
                    elif c == "市值亿":
                        s = f"{v:.0f}"
                    else:
                        s = f"{v:.2f}"
                else:
                    s = str(v)
                it = QTableWidgetItem(s)
                it.setData(Qt.UserRole + 1, snapshot)
                # 数值排序用 UserRole
                try:
                    it.setData(Qt.UserRole, float(v)
                               if v is not None and v != "" else -1e18)
                except Exception:
                    it.setData(Qt.UserRole, -1e18)
                # 颜色
                if c == "分组":
                    g = str(v)
                    if g.startswith("A1 优选"):
                        it.setForeground(QBrush(QColor(0, 140, 0)))
                        f = QFont(); f.setBold(True); it.setFont(f)
                    elif g.startswith("A1'"):
                        it.setForeground(QBrush(QColor(200, 100, 0)))
                    elif g.startswith("A1 次选"):
                        it.setForeground(QBrush(QColor(90, 140, 90)))
                    elif g.startswith("A2"):
                        it.setForeground(QBrush(QColor(140, 140, 140)))
                    elif g.startswith("E"):
                        it.setForeground(QBrush(QColor(170, 170, 170)))
                if c == "跳空%":
                    try:
                        x = float(v)
                        if x >= 2:
                            it.setForeground(QBrush(QColor(0, 140, 0)))
                        elif x < 0:
                            it.setForeground(QBrush(QColor(150, 150, 150)))
                    except Exception:
                        pass
                if c == "今日%":
                    try:
                        x = float(v)
                        if x >= 5:
                            it.setForeground(QBrush(QColor(200, 0, 0)))
                        elif x >= 0:
                            it.setForeground(QBrush(QColor(190, 80, 80)))
                        elif x <= -5:
                            it.setForeground(QBrush(QColor(0, 130, 0)))
                        else:
                            it.setForeground(QBrush(QColor(60, 140, 60)))
                    except Exception:
                        pass
                if c == "增速%":
                    try:
                        x = float(v)
                        if x >= 100:
                            it.setForeground(QBrush(QColor(200, 0, 0)))
                            f = QFont(); f.setBold(True); it.setFont(f)
                        elif x >= 50:
                            it.setForeground(QBrush(QColor(190, 80, 80)))
                    except Exception:
                        pass
                if c == "负面词":
                    try:
                        if int(df.iloc[i].get("负面数", 0) or 0) >= 2:
                            it.setForeground(QBrush(QColor(210, 0, 0)))
                            f = QFont(); f.setBold(True); it.setFont(f)
                    except Exception:
                        pass
                if c == "名称":
                    f = QFont(); f.setBold(True); it.setFont(f)
                self.table.setItem(i, j, it)

        for j, c in enumerate(cols):
            self.table.setColumnWidth(j, COL_W.get(c, 90))
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.AscendingOrder)

    def _apply_filter(self):
        kw = self.ed_search.text()
        gmin = self.sp_g.value()
        posmax = self.sp_pos.value()
        mvmax = self.sp_mv.value()
        only_flaw = self.chk_flaw.isChecked()

        groups = {g for g, cb in self.grp_checks.items() if cb.isChecked()}

        df = self.df
        if len(df) == 0:
            return
        mask = pd.Series(True, index=df.index)
        known_groups = df["分组"].isin(self.grp_checks)
        mask &= df["分组"].isin(groups) | (~known_groups & self.chk_other_groups.isChecked())
        if gmin > -1000:
            g = pd.to_numeric(df["增速%"], errors="coerce")
            mask &= (g >= gmin) | g.isna()
        if 0 <= posmax <= 100:
            p = pd.to_numeric(df["p60"], errors="coerce")
            mask &= (p <= posmax) | p.isna()
        if mvmax > 0:
            m = pd.to_numeric(df["市值亿"], errors="coerce")
            mask &= (m <= mvmax) | m.isna()
        if only_flaw:
            mask &= (df["负面数"] >= 2)
        if kw.strip():
            k = kw.strip().lower()
            hay = (df["代码"].astype(str) + " " + df["名称"].astype(str) +
                   " " + df["标题"].astype(str) + " " +
                   df["类型"].astype(str) + " " + df["摘要"].astype(str))
            mask &= hay.str.lower().str.contains(k, na=False, regex=False)
        sub = df[mask].reset_index(drop=True)
        self._rebuild_table(sub)
        self.lbl_info.setText(
            f"显示 {len(sub)} / {len(df)} 条公告　"
            f"★优选 {int(sub['分组'].astype(str).str.startswith('A1 优选').sum())}　"
            f"低位 {int(sub['分组'].astype(str).str.contains('A2').sum())}")

    def _clear_filter(self):
        self.ed_search.setText("")
        self.sp_g.setValue(-1000)
        self.sp_pos.setValue(101)
        self.sp_mv.setValue(0)
        self.chk_flaw.setChecked(False)
        for cb in self.grp_checks.values():
            cb.setChecked(True)
        self.chk_other_groups.setChecked(True)
        self._apply_filter()

    # ---- 选中详情
    def _cur_row(self):
        r = self.table.currentRow()
        if r < 0:
            return None
        item = self.table.item(r, 0)
        if item is None:
            return None
        snapshot = item.data(Qt.UserRole + 1)
        return snapshot if isinstance(snapshot, dict) else None

    def on_select(self):
        row = self._cur_row()
        if row is None:
            return

        def text(value):
            if value is None or (isinstance(value, float) and not np.isfinite(value)):
                return "—"
            return str(value) or "—"

        def h(value):
            return escape(text(value), quote=True).replace("\n", "<br>")

        def f(key, digits=1, suffix=""):
            value = row.get(key)
            try:
                number = float(value)
                return f"{number:.{digits}f}{suffix}" if np.isfinite(number) else "—"
            except (TypeError, ValueError):
                return h(value)

        self.lbl_detail_head.setText(f"{text(row.get('代码'))}  {text(row.get('名称'))}　{ text(row.get('分组'))}　增速 {f('增速%')}%")
        lines = [f"<b>类型</b>　{h(row.get('类型'))}",
                 f"<b>发布时间</b>　{h(row.get('发布时间'))}",
                 f"<b>查询日期</b>　{h(row.get('查询日期', row.get('query_date')))}",
                 f"<b>获取时间</b>　{h(row.get('获取时间', row.get('fetched_at')))}",
                 f"<b>行情时间</b>　{h(row.get('行情时间', row.get('quote_time')))}",
                 f"<b>行情基准</b>　{h(row.get('行情基准', row.get('quote_basis')))}",
                 f"<b>来源</b>　{h(row.get('来源', row.get('source')))}",
                 f"<b>完整性</b>　{h(row.get('完整性'))}",
                 f"<b>运行版本</b>　{h(row.get('运行ID', row.get('run_id')))}",
                 "<span style='color:#9a5a00'><b>时间口径</b>：历史日期查询不等于历史当时可知；行情和财务可能是获取时可见的最新数据，非历史 PIT 数据，不可直接用于无未来信息的回测。</span>",
                 f"<b>标题</b>　{h(row.get('标题'))}", "", "<b>—— 位置 ——</b>",
                 f"20日　{f('p20')}%　　60日　{f('p60')}%　　120日　{f('p120')}%",
                 f"距120日高　{f('距高%')}%", "", "<b>—— 行情 ——</b>",
                 f"现价　{f('现价', 2)}　　今日　{f('今日%', 2, '%')}",
                 f"换手　{f('换手%', 2, '%')}　　PE　{f('PE')}　　PB　{f('PB')}",
                 f"总市值　{f('市值亿', 0, ' 亿')}", "", "<b>—— 措辞扫描 ——</b>",
                 f"负面词　<span style='color:#c00'>{h(row.get('负面词'))}</span>",
                 f"正面词　<span style='color:#080'>{h(row.get('正面词'))}</span>"]
        extra = []
        for key in ("数据核验警告", "财务期间", "财务来源", "标题核验说明", "正文来源"):
            value = text(row.get(key))
            if value != "—":
                color = " style='color:#b00020'" if key == "数据核验警告" else ""
                extra.append(f"<span{color}><b>{h(key)}</b>　{h(value)}</span>")
        lines = extra + lines
        reason, summary = text(row.get('原因')), text(row.get('摘要'))
        if reason != "—":
            lines += ["", "<b>—— 变动原因 ——</b>", h(reason)]
        if summary != "—" and summary != reason:
            lines += ["", "<b>—— 公告摘要 ——</b>", h(summary)]
        self.txt_detail.setHtml("<br>".join(lines))
        self.btn_open.setEnabled(QUrl(str(row.get("链接") or "")).scheme().lower() in {"https", "http"})

    def on_double(self, item):
        self.open_url()

    def open_url(self):
        row = self._cur_row()
        if row is None:
            return
        u = str(row.get("链接", "") or "")
        if QUrl(u).scheme().lower() in {"https", "http"}:
            QDesktopServices.openUrl(QUrl(u))

    def copy_code(self):
        row = self._cur_row()
        if row is None:
            return
        QApplication.clipboard().setText(str(row["代码"]))
        self.status.showMessage(f"已复制 {row['代码']}", 2000)

    # ---- 导出
    def export_csv(self):
        if len(self.df) == 0:
            return
        fn, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV",
            str(HERE / f"业绩公告_{self.date_str().replace('-','')}.csv"),
            "CSV (*.csv)")
        if fn:
            self.df.drop(columns=["摘要"], errors="ignore").to_csv(
                fn, index=False, encoding="utf-8-sig")
            self.status.showMessage(f"已导出 {fn}", 4000)

    def export_excel(self):
        if len(self.df) == 0:
            return
        fn, _ = QFileDialog.getSaveFileName(
            self, "导出 Excel",
            str(HERE / f"业绩公告_{self.date_str().replace('-','')}.xlsx"),
            "Excel (*.xlsx)")
        if fn:
            try:
                with pd.ExcelWriter(fn, engine="openpyxl") as w:
                    d = self.df.drop(columns=["摘要", "原因"], errors="ignore")
                    d.to_excel(w, sheet_name="公告清单", index=False)
                    self.df[["代码", "名称", "标题", "原因"]].to_excel(
                        w, sheet_name="变动原因", index=False)
                self.status.showMessage(f"已导出 {fn}", 4000)
            except Exception as e:
                QMessageBox.warning(self, "导出失败", str(e)[:500])


def main(argv=None):
    parser = argparse.ArgumentParser(description="业绩公告本地数据库与日期区间获取")
    parser.add_argument("--database", help="SQLite 数据库路径；默认 earnings_watch/earnings.sqlite3")
    parser.add_argument("--no-auto-refresh", action="store_true", help="不自动加载今日本地缓存（任何模式均不自动联网）")
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    app = QApplication([sys.argv[0]] + qt_args)
    app.setStyle("Fusion")
    w = MainWindow(store_path=args.database, auto_load=not args.no_auto_refresh)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
