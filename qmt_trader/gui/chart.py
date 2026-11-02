# -*- coding: utf-8 -*-
"""
gui/chart.py — K线图表组件

基于 matplotlib + mplfinance，绘制：
  - 蜡烛图 + 布林带（上/中/下轨）
  - 成交量柱
  - 信号标记（买入点）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import numpy as np

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib import font_manager

# 中文字体
for _fp in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc"]:
    if os.path.exists(_fp):
        try:
            font_manager.fontManager.addfont(_fp)
            matplotlib.rcParams["font.family"] = font_manager.FontProperties(fname=_fp).get_name()
        except Exception:
            pass
        break
matplotlib.rcParams["axes.unicode_minus"] = False


class KLineChart(QWidget):
    """K线图组件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure = Figure(figsize=(10, 6), dpi=100, facecolor="#1e1e1e")
        self.canvas = FigureCanvas(self.figure)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self._show_placeholder("选择标的查看K线")

    # ------------------------------------------------------------
    def _show_placeholder(self, text: str):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor("#1e1e1e")
        ax.text(0.5, 0.5, text, ha="center", va="center",
                color="#888", fontsize=14, transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_color("#444")
        self.canvas.draw()

    def plot(self, data: pd.DataFrame, code: str = "", title_extra: str = ""):
        """
        绘制K线。

        Parameters
        ----------
        data : DataFrame
            需含 date/open/high/low/close/volume/bb_mid/bb_upper/bb_lower
        """
        if data is None or len(data) < 5:
            self._show_placeholder("数据不足")
            return
        d = data.copy()
        d["date"] = pd.to_datetime(d["date"])

        self.figure.clear()
        gs = self.figure.add_gridspec(3, 1, height_ratios=[3.4, 0.35, 1.0],
                                      hspace=0.08)
        ax = self.figure.add_subplot(gs[0])
        axv = self.figure.add_subplot(gs[2], sharex=ax)

        for a in (ax, axv):
            a.set_facecolor("#1e1e1e")
            a.tick_params(colors="#bbb", labelsize=8)
            a.grid(alpha=0.15, color="#666")
            for sp in a.spines.values():
                sp.set_color("#444")

        x = np.arange(len(d))
        o = d["open"].values; h = d["high"].values
        l = d["low"].values; c = d["close"].values
        v = d["volume"].values if "volume" in d.columns else np.zeros(len(d))

        # ---- 蜡烛
        for i in range(len(d)):
            up = c[i] >= o[i]
            color = "#ef5350" if up else "#26a69a"
            ax.plot([i, i], [l[i], h[i]], color=color, linewidth=0.7, alpha=0.9)
            body_h = abs(c[i] - o[i])
            if body_h < (h[i] - l[i]) * 0.003:
                body_h = (h[i] - l[i]) * 0.003
            ax.add_patch(matplotlib.patches.Rectangle(
                (i - 0.32, min(o[i], c[i])), 0.64, body_h,
                facecolor=color, edgecolor=color, linewidth=0.4))

        # ---- 布林带
        if "bb_upper" in d.columns:
            ax.plot(x, d["bb_upper"].values, color="#ff9800", linewidth=1.1,
                    label="上轨", alpha=0.9)
            ax.plot(x, d["bb_mid"].values, color="#42a5f5", linewidth=1.0,
                    label="中轨", alpha=0.9)
            ax.plot(x, d["bb_lower"].values, color="#66bb6a", linewidth=1.1,
                    label="下轨", alpha=0.9)
            ax.fill_between(x, d["bb_lower"].values, d["bb_upper"].values,
                            color="#ffffff", alpha=0.03)

        # ---- 均线
        if "ma60" in d.columns:
            ax.plot(x, d["ma60"].values, color="#ab47bc", linewidth=0.9,
                    label="MA60", alpha=0.8, linestyle="--")

        # ---- 信号标记
        if "signal" in d.columns:
            sig = d["signal"].fillna(False).values
            if sig.any():
                ax.scatter(x[sig], c[sig] * 0.985, marker="^", s=90,
                           color="#ffeb3b", zorder=5, label="买入信号",
                           edgecolors="#000", linewidths=0.6)

        # ---- 触轨标记
        if "lower_streak" in d.columns:
            touch = (d["lower_streak"].values >= 1)
            if touch.any():
                ax.scatter(x[touch], l[touch] * 0.99, marker=".", s=28,
                           color="#66bb6a", alpha=0.6, zorder=4,
                           label="触及下轨")

        ax.legend(loc="upper left", fontsize=8, facecolor="#2d2d2d",
                  edgecolor="#555", labelcolor="#ddd", ncol=3)
        ax.set_title(f"{code}   {title_extra}", color="#eee", fontsize=11, pad=6)
        ax.set_ylabel("价格", color="#bbb", fontsize=9)

        # ---- 成交量
        for i in range(len(d)):
            up = c[i] >= o[i]
            axv.bar(i, v[i], color="#ef5350" if up else "#26a69a",
                    width=0.7, alpha=0.75)
        axv.set_ylabel("成交量", color="#bbb", fontsize=9)

        # ---- X轴日期
        step = max(1, len(d) // 9)
        axv.set_xticks(x[::step])
        axv.set_xticklabels([dd.strftime("%m-%d") for dd in d["date"][::step]],
                            rotation=0, fontsize=8)
        ax.tick_params(labelbottom=False)
        axv.set_xlim(-1, len(d))

        self.figure.subplots_adjust(left=0.055, right=0.985, top=0.945, bottom=0.07)
        self.canvas.draw()


import matplotlib.patches  # noqa: E402  (放在末尾避免循环导入问题)
