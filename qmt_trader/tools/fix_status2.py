# -*- coding: utf-8 -*-
"""fix_status2.py — 用逐个查询方式重写 status 的任务列表部分。"""
import io
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
p = HERE / "tools" / "setup_schedule.py"
s = io.open(p, encoding="utf-8").read()

start = s.find("    rows = []\n")
end = s.find("    # 产出\n    d = HERE / \"earnings_watch\"")
if start < 0 or end < 0:
    print("未找到段落")
    raise SystemExit(1)

new = '''    rows = []
    for t in ALL_TASK_NAMES:
        rc2, out2 = run(["schtasks", "/Query", "/TN", t, "/FO", "LIST"])
        if rc2 != 0:
            continue
        d2 = {}
        for line in out2.split("\\n"):
            ls = line.strip()
            if ls.startswith("TaskName:"):
                d2["任务名"] = ls.split(":", 1)[1].strip().lstrip("\\\\")
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

'''
s = s[:start] + new + s[end:]

# 加入 ALL_TASK_NAMES 常量
if "ALL_TASK_NAMES" not in s.split("def status()")[0]:
    s = s.replace(
        'SIMPLE_TIMES = [',
        'ALL_TASK_NAMES = [f"{TASK_PREFIX}-{lab}" for _, lab in DEFAULT_TIMES]\n'
        'SIMPLE_TIMES = [', 1)

io.open(p, "w", encoding="utf-8").write(s)
print("已修复")
