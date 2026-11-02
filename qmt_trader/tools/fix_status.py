# -*- coding: utf-8 -*-
"""fix_status.py — 修复 setup_schedule.py 的 status 解析。"""
import io
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
p = HERE / "tools" / "setup_schedule.py"
s = io.open(p, encoding="utf-8").read()

start = s.find("    rows = []\n    for line in out.split(")
end = s.find("    if rows:\n        for r in rows:")
if start < 0 or end < 0:
    print("未找到目标段落")
    raise SystemExit(1)

new_block = '''    rows = []
    pat = re.compile(
        r"(" + TASK_PREFIX + r"[^\\s]*(?:\\s+[^\\s]*)*?)\\s+"
        r"(\\d{4}/\\d{2}/\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})\\s+(\\S+)")
    for line in out.split("\\n"):
        if TASK_PREFIX not in line:
            continue
        m = pat.search(line)
        if m:
            nm = m.group(1).strip()
            # 去掉可能的 "Folder: \\" 前缀
            if "\\\\" in nm:
                nm = nm.split("\\\\")[-1]
            rows.append({"任务名": nm, "下次运行": m.group(2),
                         "状态": m.group(3)})
'''

s = s[:start] + new_block + s[end:]
# 确保 import re
if "\nimport re\n" not in s:
    s = s.replace("import os\nimport sys", "import os\nimport re\nimport sys", 1)

io.open(p, "w", encoding="utf-8").write(s)
print("已修复")
