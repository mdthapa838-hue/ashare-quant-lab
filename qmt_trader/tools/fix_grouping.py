# -*- coding: utf-8 -*-
"""fix_grouping.py — 按新规则修正 earnings_gui.py 与 earnings_watch.py 的分组。

新规则（基于 step41/step42 回测）：
  【A1 优选】p60≥60 或 p120≥60  且  当日跳空≥2%  且  未回补
  【A2 观察】p60/p120<60（低位）→ 明确标注「不推荐」
  【无跳空数据时】退化为仅用位置判断，并标注数据缺失
"""
import io
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent

# ---------------- GUI ----------------
p = HERE / "earnings_gui.py"
s = io.open(p, encoding="utf-8").read()

old = '''            p20, p60, p120 = ps["p20"], ps["p60"], ps["p120"]
            # 分组
            if pd.isna(g) or g is None:
                grp = "快报/待核实"
            elif float(g) < 20:
                grp = "E 低增"
            elif float(g) < 50:
                grp = "D 中增"
            elif ((p60 is not None and p60 >= 60) or
                  (p120 is not None and p120 >= 60)):
                grp = ("A1' 高位+措辞瑕疵" if len(neg) >= 2
                       else "A1 高增+中期高位 ★★")
            elif p20 is not None and p20 < 20:
                grp = "A2 高增+低位"
            else:
                grp = "B 高增+中位"'''

new = '''            p20, p60, p120 = ps["p20"], ps["p60"], ps["p120"]
            # 分组（v3：加入「跳空/回补」硬门槛）
            gap, filled = self._gap_of(c6, t0.get("发布日", ""), rt, cache,
                                       pro, c6 + (".SH" if c6.startswith(
                                           ("60", "68", "9")) else ".SZ"))
            hi_mid = ((p60 is not None and p60 >= 60) or
                      (p120 is not None and p120 >= 60))
            if pd.isna(g) or g is None:
                grp = "快报/待核实"
            elif float(g) < 20:
                grp = "E 低增"
            elif float(g) < 50:
                grp = "D 中增" if hi_mid else "D 中增(低位)"
            elif hi_mid and gap is not None and gap >= 2 and not filled:
                grp = ("A1' 高位+措辞瑕疵" if len(neg) >= 2
                       else "A1 优选（高位+跳空+未回补）★★★")
            elif hi_mid and gap is not None and gap >= 2:
                grp = "A1 次选（高位+跳空，但回补）"
            elif hi_mid:
                grp = "A1 次选（高位，跳空弱）"
            elif p20 is not None and p20 < 20:
                grp = "A2 观察（低位，不推荐）"
            else:
                grp = "B 高增+中位"'''

if old in s:
    s = s.replace(old, new)
    print("GUI 分组逻辑已替换")
else:
    print("GUI 未找到目标段落")

# 插入 _gap_of 方法
anchor = "    # ---- 位置（本地缓存 → Tushare 兜底）"
gapfn = '''    # ---- 跳空/回补（公告反应日）
    def _gap_of(self, c6, ann_date, rt, cache, pro=None, ts_code=None):
        """返回 (跳空%, 是否回补)。ann_date 即反应日。"""
        if not ann_date:
            return None, False
        try:
            d = None
            if pro is not None and ts_code:
                st = (pd.Timestamp(ann_date) -
                      pd.Timedelta(days=15)).strftime("%Y%m%d")
                en = (pd.Timestamp(ann_date) +
                      pd.Timedelta(days=5)).strftime("%Y%m%d")
                d = pro.daily(ts_code=ts_code, start_date=st, end_date=en)
            if d is None or len(d) < 2:
                return None, False
            d = d.copy()
            d["date"] = pd.to_datetime(d["trade_date"], format="%Y%m%d")
            d = d.sort_values("date").reset_index(drop=True)
            for c in ("open", "high", "low", "close"):
                d[c] = pd.to_numeric(d[c], errors="coerce")
            ad = pd.Timestamp(ann_date)
            idx = d.index[d["date"] == ad]
            if not len(idx):
                # 找第一个 >= 的交易日
                nxt = d.index[d["date"] >= ad]
                if not len(nxt):
                    return None, False
                i = int(nxt[0])
            else:
                i = int(idx[0])
            if i < 1:
                return None, False
            pre = float(d["close"].iloc[i - 1])
            o = float(d["open"].iloc[i])
            lo = float(d["low"].iloc[i])
            if pre <= 0:
                return None, False
            gap = (o / pre - 1) * 100
            return round(gap, 2), bool(lo <= pre)
        except Exception:
            return None, False

'''
if "_gap_of" not in s.split("def _fetch")[0]:
    s = s.replace(anchor, gapfn + anchor, 1)
    print("已插入 _gap_of")

# 表格列与着色
s = s.replace(
    'COLS = ["代码", "名称", "分组", "增速%", "类型", "发布时间",',
    'COLS = ["代码", "名称", "分组", "跳空%", "增速%", "类型", "发布时间",')
s = s.replace(
    'COL_W = {"代码": 62, "名称": 86, "分组": 128, "增速%": 66, "类型": 52,',
    'COL_W = {"代码": 62, "名称": 86, "分组": 190, "跳空%": 60,\n'
    '         "增速%": 66, "类型": 52,')
s = s.replace(
    '''            rows.append({
                "代码": c6,
                "名称": rt.get("name", t0["名称"]),''',
    '''            rows.append({
                "代码": c6,
                "名称": rt.get("name", t0["名称"]),
                "跳空%": gap,
                "回补": ("是" if filled else "否") if gap is not None else "—",''')

# 分组下拉（颜色）
s = s.replace(
    '''        for g, color in [("A1 高增+中期高位 ★★", "#0a7a0a"),
                         ("A1' 高位+措辞瑕疵", "#c86400"),
                         ("A2 高增+低位", "#0a5ac8"),
                         ("B 高增+中位", "#444"),
                         ("D 中增", "#666"),
                         ("E 低增", "#999"),
                         ("快报/待核实", "#888")]:''',
    '''        for g, color in [("A1 优选（高位+跳空+未回补）★★★", "#0a7a0a"),
                         ("A1' 高位+措辞瑕疵", "#c86400"),
                         ("A1 次选（高位+跳空，但回补）", "#3a8a3a"),
                         ("A1 次选（高位，跳空弱）", "#6a9a6a"),
                         ("A2 观察（低位，不推荐）", "#999999"),
                         ("B 高增+中位", "#444"),
                         ("D 中增", "#666"),
                         ("D 中增(低位)", "#888"),
                         ("E 低增", "#aaa"),
                         ("快报/待核实", "#888")]:''')

# 排序优先级
s = s.replace(
    '''        order = {"A1 高增+中期高位 ★★": 0, "A1' 高位+措辞瑕疵": 1,
                 "A2 高增+低位": 2, "B 高增+中位": 3, "D 中增": 4,
                 "E 低增": 5, "快报/待核实": 6}''',
    '''        order = {"A1 优选（高位+跳空+未回补）★★★": 0,
                 "A1' 高位+措辞瑕疵": 1,
                 "A1 次选（高位+跳空，但回补）": 2,
                 "A1 次选（高位，跳空弱）": 3,
                 "B 高增+中位": 4,
                 "A2 观察（低位，不推荐）": 5,
                 "D 中增": 6, "D 中增(低位)": 7,
                 "E 低增": 8, "快报/待核实": 9}''')

# info 栏
s = s.replace(
    '''        a1 = int((df["分组"] == "A1 高增+中期高位 ★★").sum())
        a2 = int((df["分组"] == "A2 高增+低位").sum())
        fl = int((df["负面数"] >= 2).sum())
        self.lbl_info.setText(
            f"{n} 只　A1 {a1}　A2 {a2}　措辞瑕疵 {fl}")''',
    '''        a1 = int(df["分组"].astype(str).str.startswith("A1 优选").sum())
        a1b = int(df["分组"].astype(str).str.startswith("A1 次选").sum())
        a2 = int(df["分组"].astype(str).str.contains("A2").sum())
        fl = int((df["负面数"] >= 2).sum())
        self.lbl_info.setText(
            f"{n} 只　★优选 {a1}　次选 {a1b}　"
            f"低位(不推荐) {a2}　措辞瑕疵 {fl}")''')

s = s.replace(
    '''        self.lbl_info.setText(
            f"显示 {len(sub)} / {len(df)} 只　"
            f"A1 {int((sub['分组']=='A1 高增+中期高位 ★★').sum())}　"
            f"A2 {int((sub['分组']=='A2 高增+低位').sum())}")''',
    '''        self.lbl_info.setText(
            f"显示 {len(sub)} / {len(df)} 只　"
            f"★优选 {int(sub['分组'].astype(str).str.startswith('A1 优选').sum())}　"
            f"低位 {int(sub['分组'].astype(str).str.contains('A2').sum())}")''')

# 着色（表格内）
s = s.replace(
    '''                if c == "分组":
                    g = str(v)
                    if g.startswith("A1 "):
                        it.setForeground(QBrush(QColor(0, 130, 0)))
                    elif g.startswith("A1'"):
                        it.setForeground(QBrush(QColor(200, 100, 0)))
                    elif g.startswith("A2"):
                        it.setForeground(QBrush(QColor(0, 90, 200)))
                    elif g.startswith("E"):
                        it.setForeground(QBrush(QColor(150, 150, 150)))''',
    '''                if c == "分组":
                    g = str(v)
                    if g.startswith("A1 优选"):
                        it.setForeground(QBrush(QColor(0, 140, 0)))
                        f = QFont(); f.setBold(True); it.setFont(f)
                    elif g.startswith("A1'"):
                        it.setForeground(QBrush(QColor(200, 100, 0)))
                    elif g.startswith("A1 次选"):
                        it.setForeground(QBrush(QColor(90, 140, 90)))
                    elif g.startswith("A2"):
                        it.setForeground(QBrush(QColor(140, 140, 140)))
                    elif g.startswith("E"):
                        it.setForeground(QBrush(QColor(170, 170, 170)))
                if c == "跳空%":
                    try:
                        x = float(v)
                        if x >= 2:
                            it.setForeground(QBrush(QColor(0, 140, 0)))
                        elif x < 0:
                            it.setForeground(QBrush(QColor(150, 150, 150)))
                    except Exception:
                        pass''')

io.open(p, "w", encoding="utf-8").write(s)
print("GUI 已更新")
