# -*- coding: utf-8 -*-
"""fix_gap_date.py — 修正 gap 计算的日期参数。

问题：t0.get("发布日") 不是 cninfo 表的列，返回空字符串，导致 _gap_of 直接返回 None。

修正：反应日 = GUI 查询的日期（与回测一致：ann_date 即反应日）
"""
import io
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
p = HERE / "earnings_gui.py"
s = io.open(p, encoding="utf-8").read()

old = '''            gap, filled = self._gap_of(c6, t0.get("发布日", ""), rt, cache,
                                       pro, c6 + (".SH" if c6.startswith(
                                           ("60", "68", "9")) else ".SZ"))'''

new = '''            # 反应日 = 查询日期（与回测假设一致：ann_date 即股价反应日）
            _reaction = getattr(self, "_reaction_day", None) or self.date_str
            gap, filled = self._gap_of(
                c6, _reaction, rt, cache, pro,
                c6 + (".SH" if c6.startswith(("60", "68", "9")) else ".SZ"))'''

if old in s:
    s = s.replace(old, new)
    print("已替换 gap 调用")
else:
    print("未找到目标，尝试模糊匹配")
    import re
    m = re.search(r"gap, filled = self\._gap_of\([^)]*\)[^)]*\)", s)
    if m:
        print("实际内容:", s[m.start():m.start() + 240])

# 在 _fetch 里设置 _reaction_day
old2 = '''        self.progress.emit("抓取巨潮全市场公告...")
        cn = self._cninfo()'''
new2 = '''        self._reaction_day = self.date_str
        self.progress.emit("抓取巨潮全市场公告...")
        cn = self._cninfo()'''
if old2 in s:
    s = s.replace(old2, new2)
    print("已设置 _reaction_day")

io.open(p, "w", encoding="utf-8").write(s)
print("完成")
