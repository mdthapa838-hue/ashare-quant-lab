@echo off
chcp 65001 >nul
cd /d "F:\deepseek\股市\股市2\qmt_trader"
set PYTHONIOENCODING=utf-8
start "" "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\pythonw.exe" earnings_gui.py
exit
