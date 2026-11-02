# -*- coding: utf-8 -*-
"""
setup_schedule.py — 业绩公告监控 - 多时点调度配置

核心修正：
  原方案 18:30 太晚 —— 公告往往在盘前/盘中披露，当天就涨完了。

新方案（多时点）：
  ① 08:30  盘前扫描          （抓隔夜+盘前公告，为开盘决策准备）
  ② 09:25  集合竞价前扫描     （确认竞价强度）
  ③ 12:00  午间扫描          （上午新增公告）
  ④ 15:30  收盘扫描          （全天公告归档）
  ⑤ 21:00  晚间扫描          （为次日准备，公告最集中）

用法：
    python tools/setup_schedule.py install           # 安装全部时点
    python tools/setup_schedule.py install --simple  # 只装 3 个关键时点
    python tools/setup_schedule.py install --times=08:30,12:00,21:00
    python tools/setup_schedule.py uninstall
    python tools/setup_schedule.py status
    python tools/setup_schedule.py run-now
    python tools/setup_schedule.py clean             # 清除旧任务
"""
from __future__ import annotations

import os
import re
import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
BAT_NAME = "run_earnings_watch.bat"
TASK_PREFIX = "A股业绩公告监控"

# 默认多时点（按重要性排序）
DEFAULT_TIMES = [
    ("08:30", "盘前"),
    ("09:25", "竞价前"),
    ("12:00", "午间"),
    ("15:30", "收盘"),
    ("21:00", "晚间"),
]
ALL_TASK_NAMES = [f"{TASK_PREFIX}-{lab}" for _, lab in DEFAULT_TIMES]
SIMPLE_TIMES = [
    ("08:30", "盘前"),
    ("15:30", "收盘"),
    ("21:00", "晚间"),
]


def make_bat() -> Path:
    py = sys.executable
    p = HERE / BAT_NAME
    content = f"""@echo off
chcp 65001 >nul
cd /d "{HERE}"
set PYTHONIOENCODING=utf-8
"{py}" tools\\earnings_watch.py %* >> "{HERE}\\earnings_watch\\run.log" 2>&1
set RC=%ERRORLEVEL%
if %RC%==2 (
  echo [%DATE% %TIME%] 发现 A1/A2 信号 >> "{HERE}\\earnings_watch\\run.log"
)
exit /b %RC%
"""
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


def run(cmd, timeout=30, enc="utf-8"):
    """
    执行命令。

    注意：schtasks 在中文 Windows 上输出 UTF-8（实测），
    但某些命令输出 GBK。这里直接用 UTF-8 + replace，不重试。
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding=enc, errors="replace", timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        # 若不是 UTF-8，尝试 GBK 重解一次
        if "业绩" in cmd.__str__() or TASK_PREFIX not in cmd.__str__():
            return r.returncode, out
        if TASK_PREFIX not in out:
            try:
                r2 = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding="gbk", errors="replace",
                                    timeout=timeout)
                out2 = (r2.stdout or "") + (r2.stderr or "")
                if TASK_PREFIX in out2:
                    return r2.returncode, out2
            except Exception:
                pass
        return r.returncode, out
    except Exception as e:
        return -1, str(e)


def header():
    print("=" * 112)
    print("  业绩公告监控 - 多时点调度配置")
    print("=" * 112)
    print()


def install(times=None, quiet=False):
    if times is None:
        times = DEFAULT_TIMES
    if not quiet:
        header()
    bat = make_bat()
    if not quiet:
        print(f"[1] 启动脚本: {bat}")
        print()
        print(f"[2] 注册 {len(times)} 个时点计划任务")
        print()

    ok = 0
    fail = 0
    for t, label in times:
        try:
            h, m = t.split(":")
            hh, mm = int(h), int(m)
            assert 0 <= hh < 24 and 0 <= mm < 60
        except Exception:
            if not quiet:
                print(f"    ✗ {t} 格式错误")
            fail += 1
            continue
        tn = f"{TASK_PREFIX}-{label}"
        rc, out = run(["schtasks", "/Create", "/TN", tn,
                       "/TR", f'"{bat}"',
                       "/SC", "DAILY", "/ST", f"{hh:02d}:{mm:02d}", "/F"])
        if rc == 0:
            if not quiet:
                print(f"    ✅ {t}  {label}  （任务名：{tn}）")
            ok += 1
        else:
            if not quiet:
                print(f"    ✗ {t}  {label}  失败: {out.strip()[:100]}")
            fail += 1
    if not quiet:
        print()
        print(f"    成功 {ok}  失败 {fail}")
        print()

    # 自选池模板
    wl = HERE / "earnings_watch" / "watchlist.csv"
    wl.parent.mkdir(parents=True, exist_ok=True)
    if not wl.exists():
        with open(wl, "w", encoding="utf-8-sig") as f:
            f.write("代码,名称,备注\n")
            f.write("300747,锐科激光,业绩快报已兑现\n")
            f.write("300857,协创数据,缩量筑底\n")
            f.write("688385,复旦微电,10-16 A1组\n")
        if not quiet:
            print(f"[3] 自选池模板: {wl}")
            print()

    if not quiet:
        print("=" * 112)
        print("  完成")
        print("=" * 112)
        print()
        print("  监控时点安排：")
        print()
        print(f"  {'时间':<10}{'阶段':<10}{'用途'}")
        print("  " + "-" * 60)
        uses = {
            "08:30": "抓隔夜+盘前公告，开盘前决策",
            "09:25": "确认集合竞价强度",
            "12:00": "上午新增公告",
            "15:30": "全天公告归档",
            "21:00": "公告最集中，为次日准备",
        }
        for t, label in times:
            print(f"  {t:<10}{label:<10}{uses.get(t,'')}")
        print()
        print("  输出：")
        print("    · 报告:  earnings_watch/report_YYYYMMDD.md")
        print("    · 数据:  earnings_watch/history/earnings_YYYYMMDD.csv")
        print("    · 日志:  earnings_watch/run.log")
        print()
        print("  ⚠ 提示：同一天多次运行会覆盖当日报告（保留最新）")
        print()
    return 0


def uninstall():
    header()
    print("删除所有监控任务...")
    print()
    rc, out = run(["schtasks", "/Query", "/FO", "CSV", "/NH"])
    names = []
    if rc == 0:
        for line in out.split("\n"):
            if TASK_PREFIX in line:
                parts = line.split('","')
                if parts:
                    nm = parts[0].strip('"')
                    names.append(nm)
    if not names:
        print("    未找到相关任务")
        return 0
    for nm in names:
        rc2, out2 = run(["schtasks", "/Delete", "/TN", nm, "/F"])
        mark = "✅" if rc2 == 0 else "✗"
        print(f"    {mark} {nm}")
    print()
    print(f"    共处理 {len(names)} 个任务")
    return 0


def clean():
    """清除旧版单任务。"""
    header()
    print("清除旧版单任务（18:30）...")
    rc, out = run(["schtasks", "/Delete", "/TN", TASK_PREFIX, "/F"])
    if rc == 0:
        print("    ✅ 已删除旧任务")
    else:
        print(f"    未找到或已删除: {out.strip()[:80]}")
    return 0


def status():
    header()
    print("当前监控任务：")
    print()
    rc, out = run(["schtasks", "/Query", "/FO", "TABLE"])
    if rc != 0:
        print("    查询失败")
        return 1
    rows = []
    for t in ALL_TASK_NAMES:
        rc2, out2 = run(["schtasks", "/Query", "/TN", t, "/FO", "LIST"])
        if rc2 != 0:
            continue
        d2 = {}
        for line in out2.split("\n"):
            ls = line.strip()
            if ls.startswith("TaskName:"):
                d2["任务名"] = ls.split(":", 1)[1].strip().lstrip("\\")
            elif ls.startswith("Next Run Time:"):
                d2["下次运行"] = ls.split(":", 1)[1].strip()
            elif ls.startswith("Status:"):
                d2["状态"] = ls.split(":", 1)[1].strip()
        if d2:
            rows.append(d2)
    if rows:
        for r in rows:
            print(f"    {r.get('任务名',''):<32}"
                  f"{r.get('下次运行',''):<24}{r.get('状态','')}")
    else:
        print("    无监控任务（可用 install 安装）")
    print()

    # 产出
    d = HERE / "earnings_watch"
    if d.exists():
        print("  最近报告:")
        for f in sorted(d.glob("report_*.md"))[-7:]:
            print(f"    {f.name}  ({f.stat().st_size} B)")
        lg = d / "log.csv"
        if lg.exists():
            try:
                import pandas as pd
                L = pd.read_csv(lg)
                print()
                print("  运行记录（最近12次）:")
                cols = [c for c in ["运行时间", "日期", "公告数", "A1", "A2",
                                    "自选命中"] if c in L.columns]
                print("    " + L[cols].tail(12).to_string(index=False)
                      .replace("\n", "\n    "))
            except Exception:
                pass
    return 0


def run_now():
    header()
    bat = make_bat()
    print(f"立即运行: {bat}")
    print()
    return os.system(f'"{bat}"')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    act = sys.argv[1]
    if act == "install":
        times = None
        for a in sys.argv[2:]:
            if a == "--simple":
                times = SIMPLE_TIMES
            if a.startswith("--times="):
                ts = a.split("=")[1].split(",")
                times = [(t.strip(), f"时点{i+1}")
                         for i, t in enumerate(ts)]
        if times is None:
            times = DEFAULT_TIMES
        return install(times)
    if act == "uninstall":
        return uninstall()
    if act == "clean":
        return clean()
    if act == "status":
        return status()
    if act == "run-now":
        return run_now()
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
