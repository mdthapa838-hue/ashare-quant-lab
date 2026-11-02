# -*- coding: utf-8 -*-
"""
gui.py — GUI 启动入口

用法：
    python gui.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(HERE)


def main():
    # 高DPI支持
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
    except ImportError:
        print("需要 PySide6：pip install PySide6")
        return 1

    from core.config import load_config
    from core.logger import setup_logger

    cfg = load_config()
    setup_logger("qmt_trader", log_dir=cfg.get("logging.dir"),
                 level=cfg.get("logging.level", "INFO"),
                 console=cfg.get("logging.console", True),
                 keep_days=cfg.get("logging.keep_days", 60))

    app = QApplication(sys.argv)
    app.setApplicationName("布林带下轨反弹策略交易终端")

    from gui.main_window import MainWindow
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
