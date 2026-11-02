@echo off
chcp 65001 >nul
cd /d "F:\deepseek\股市\股市2\qmt_trader"
set PYTHONIOENCODING=utf-8
"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" tools\earnings_watch.py %* >> "F:\deepseek\股市\股市2\qmt_trader\earnings_watch\run.log" 2>&1
set RC=%ERRORLEVEL%
if %RC%==2 (
  echo [%DATE% %TIME%] 发现 A1/A2 信号 >> "F:\deepseek\股市\股市2\qmt_trader\earnings_watch\run.log"
)
exit /b %RC%
