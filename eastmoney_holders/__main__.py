# -*- coding: utf-8 -*-
"""支持 `python -m eastmoney_holders <command>`。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
