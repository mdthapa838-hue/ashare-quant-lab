# -*- coding: utf-8 -*-
"""fix_gui_imports.py — 修正 earnings_gui.py 的导入与模型类。"""
import io
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
p = HERE / "earnings_gui.py"
s = io.open(p, encoding="utf-8").read()

# 1) 修正 QtCore 导入
s = s.replace(
    "from PySide6.QtCore import (Qt, QThread, Signal, QTimer, QAbstractTableModel,\n"
    "                            QModelIndex, QSortFilterProxyModel)",
    "from PySide6.QtCore import (Qt, QThread, Signal, QTimer, QAbstractItemModel,\n"
    "                            QModelIndex, QSortFilterProxyModel, QDate)")

# 2) 删除不再使用的 DFModel 类（整段）
start = s.find("class DFModel(QAbstractItemModel):")
end = s.find("class NumSortProxy(QSortFilterProxyModel):")
if start > 0 and end > start:
    s = s[:start] + s[end:]

# 3) 删除 NumSortProxy 中依赖 DFModel 的部分（改为简单实现）
start2 = s.find("class NumSortProxy(QSortFilterProxyModel):")
end2 = s.find("# ============================================================ 主窗口")
if start2 > 0 and end2 > start2:
    s = s[:start2] + s[end2:]

# 4) 移除主窗口里对 model/proxy 的引用
s = s.replace(
    """        self.model = DFModel(pd.DataFrame(columns=COLS))
        self.proxy = NumSortProxy()
        self.proxy.setSourceModel(self.model)

        self.table = QTableWidget()""",
    """        self.table = QTableWidget()""")

# 5) QDate 已在 QtCore 导入，移除函数内的重复导入
s = s.replace("""        try:
            from PySide6.QtCore import QDate
            self.date_edit.setDate(self.date_edit.date().addDays(n))""",
"""        try:
            self.date_edit.setDate(self.date_edit.date().addDays(n))""")

io.open(p, "w", encoding="utf-8").write(s)
print("已修正")
