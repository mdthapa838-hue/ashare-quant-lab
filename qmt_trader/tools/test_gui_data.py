# -*- coding: utf-8 -*-
"""test_gui_data.py — 测试 GUI 的数据层（不开窗口）。"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tools"))

from PySide6.QtCore import QCoreApplication, QTimer

import earnings_gui

app = QCoreApplication(sys.argv)
w = earnings_gui.FetchWorker("2026-10-16", use_tushare=True)
res = {}


def on_done(df):
    res["df"] = df
    app.quit()


def on_fail(m):
    res["err"] = m
    app.quit()


w.done.connect(on_done)
w.failed.connect(on_fail)
w.progress.connect(lambda s: print("  ", s, flush=True))
w.start()
QTimer.singleShot(600000, app.quit)
app.exec()

if "df" in res:
    df = res["df"]
    print()
    print(f"OK 抓取成功: {len(df)} 只")
    print(f"列: {list(df.columns)}")
    if len(df):
        print()
        cols = [c for c in ["代码", "名称", "分组", "增速%", "p20", "p60",
                            "p120", "今日%", "负面词"] if c in df.columns]
        print(df[cols].head(15).to_string(index=False))
        print()
        print("分组统计:")
        print(df["分组"].value_counts().to_string())
        # 测试导出
        p = HERE / "output" / "gui_test.csv"
        df.drop(columns=["摘要"], errors="ignore").to_csv(
            p, index=False, encoding="utf-8-sig")
        print()
        print(f"导出测试: {p}  ({p.stat().st_size} B)")
else:
    print("FAIL:", str(res.get("err", "超时"))[:600])
