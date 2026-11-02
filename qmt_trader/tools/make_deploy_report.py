# -*- coding: utf-8 -*-
"""生成业绩监控部署报告。"""
from __future__ import annotations
import sys, glob, os
from pathlib import Path
import pandas as pd

HERE = Path(__file__).resolve().parent.parent


def W(writer, name, widths):
    ws = writer.sheets[name]
    from openpyxl.utils import get_column_letter
    for i, x in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = x


def main():
    xlsx = HERE / "业绩监控部署说明_20261016.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:

        # 1 已配置
        s1 = pd.DataFrame([
            ["✅ 部署完成", ""],
            ["", ""],
            ["计划任务名", "A股业绩公告监控"],
            ["触发时间", "每日 18:30"],
            ["状态", "Ready（下次运行 2026-10-16 18:30）"],
            ["启动脚本", "run_earnings_watch.bat"],
            ["", ""],
            ["★ 三种使用方式", ""],
            ["① 全自动", "每天 18:30 自动运行，无需操作"],
            ["② 手动跑一次", "双击 run_earnings_watch.bat"],
            ["③ 查历史", "python tools\\earnings_watch.py --days=5"],
            ["", ""],
            ["★ 输出位置", ""],
            ["当日报告（Markdown）", "earnings_watch\\report_YYYYMMDD.md"],
            ["当日数据（CSV）", "earnings_watch\\history\\earnings_YYYYMMDD.csv"],
            ["运行日志", "earnings_watch\\run.log"],
            ["历史汇总", "earnings_watch\\log.csv"],
            ["自选池", "earnings_watch\\watchlist.csv"],
            ["", ""],
            ["★ 信号机制", ""],
            ["退出码 2", "发现 A 组信号（高增+低位）"],
            ["退出码 0", "无信号"],
            ["日志标记", "[警告] 发现 A 组信号，请查看报告"],
        ], columns=["项目", "内容"])
        s1.to_excel(w, sheet_name="1_部署完成", index=False)
        W(w, "1_部署完成", [26, 62])

        # 2 筛选规则
        s2 = pd.DataFrame([
            ["A 高增+低位 ★★★", "增速 ≥50%  且  位置 ≤40%",
             "预告后20日胜率60%，位置低赔率高", "★ 重点关注"],
            ["B 高增+中位 ★★", "增速 ≥50%  且  位置 40~70%",
             "胜率60%，但已涨一部分", "次选"],
            ["C 高增+已涨 ★", "增速 ≥50%  且  位置 >70%",
             "位置高，追高风险大", "观察"],
            ["D 中增", "增速 20~50%",
             "20~50%档反而最好（20日+6.01%、胜率64.8%）", "可选"],
            ["E 低增/负增长", "增速 <20%",
             "低于随机基准", "排除"],
            ["快报", "—",
             "需人工核实（因快报无增速字段）", "人工看"],
            ["", "", "", ""],
            ["阈值来源", "5,770 个业绩预告样本回测", "", ""],
            ["位置定义", "(现价 - 130日最低) / (130日最高 - 130日最低)", "", ""],
            ["", "", "", ""],
            ["⚠ 修正说明", "express 接口的 yoy_net_profit 是绝对金额，不是增速",
             "上一轮误用导致快报结论有误", ""],
        ], columns=["分组", "筛选条件", "回测依据", "优先级"])
        s2.to_excel(w, sheet_name="2_筛选规则", index=False)
        W(w, "2_筛选规则", [20, 36, 42, 12])

        # 3 命令速查
        s3 = pd.DataFrame([
            ["每日自动", "（已配置，无需操作）", "18:30 自动运行"],
            ["立即运行", "双击 run_earnings_watch.bat", "或命令行调用"],
            ["查今日", "python tools\\earnings_watch.py", "默认当日"],
            ["查指定日", "python tools\\earnings_watch.py --date=20261016", ""],
            ["查最近N天", "python tools\\earnings_watch.py --days=3", ""],
            ["静默模式", "python tools\\earnings_watch.py --quiet", "只在有信号时输出"],
            ["", "", ""],
            ["查看任务", "python tools\\setup_schedule.py status", ""],
            ["改时间", "python tools\\setup_schedule.py install --time=19:00",
             "重新注册"],
            ["删除任务", "python tools\\setup_schedule.py uninstall", ""],
            ["查看报告", "type earnings_watch\\report_20261016.md", ""],
            ["查看日志", "type earnings_watch\\run.log", ""],
        ], columns=["用途", "命令", "说明"])
        s3.to_excel(w, sheet_name="3_命令速查", index=False)
        W(w, "3_命令速查", [14, 52, 26])

        # 4 今日结果示例
        mp = HERE / "earnings_watch" / "report_20261016.md"
        if mp.exists():
            lines = mp.read_text(encoding="utf-8").split("\n")
            s4 = pd.DataFrame({"报告内容": lines[:60]})
            s4.to_excel(w, sheet_name="4_今日报告示例", index=False)
            W(w, "4_今日报告示例", [110])

        # 5 自选池
        s5 = pd.DataFrame([
            ["代码", "名称", "备注"],
            ["300747", "锐科激光", "业绩快报已兑现（+18.9%）"],
            ["300857", "协创数据", "缩量筑底，等事件"],
            ["", "", "（在此加入你想盯的股票）"],
            ["", "", ""],
            ["说明", "把代码填进 earnings_watch\\watchlist.csv", ""],
            ["", "脚本会自动检测这些股票是否出公告", ""],
            ["", "命中后单独列出", ""],
        ], columns=["代码", "名称", "备注"])
        s5.to_excel(w, sheet_name="5_自选池", index=False)
        W(w, "5_自选池", [10, 14, 46])

        # 6 注意事项
        s6 = pd.DataFrame([
            ["1", "运行时间", "公告一般收盘后披露，18:30 后数据较全",
             "若 18:30 无数据可改 20:00"],
            ["2", "数据源延迟", "Tushare 有延迟，偶尔当日数据不全",
             "次日重跑 --days=2 补齐"],
            ["3", "位置数据依赖本地缓存", "本地K线需更新到最新",
             "先运行 python tools\\step29_append_daily.py"],
            ["4", "快报无增速字段", "express 接口只给绝对金额",
             "需人工核实或另算同比"],
            ["5", "20日是有效期", "预告后 120日转负超额",
             "持有期控制在 20-60 日"],
            ["6", "不迷信高增速", "20~50% 档反而最好",
             "Spearman +0.0176 不显著"],
            ["7", "逐个核实", "是否一次性收益、低基数",
             "看公告原文 change_reason"],
            ["", "", "", ""],
            ["维护", "", "", ""],
            ["更新K线", "python tools\\step29_append_daily.py", "每日先跑", ""],
            ["更新财务", "python tools\\step9b_download_fundamental.py",
             "季度更新", ""],
        ], columns=["#", "项目", "说明", "备注"])
        s6.to_excel(w, sheet_name="6_注意事项", index=False)
        W(w, "6_注意事项", [4, 22, 40, 30])

    print(f"已生成: {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
