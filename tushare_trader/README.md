# tushare_trader — 基于 Tushare 的 A 股量化交易系统

> 一套**可离线验证、可直接跑模拟盘、可回测**的 A 股交易系统骨架。
> 数据全部来自 Tushare Pro，策略为仓库内已验证的「布林带下轨反弹」，
> 交易通道是可插拔的（内置模拟盘，实盘接口见「接入实盘」章节）。

---

## 一、这套系统能做什么

| 能力 | 命令 | 说明 |
|---|---|---|
| 环境体检 | `doctor` | 检查依赖、token、接口连通、配置 |
| 盘前准备 | `prepare` | 更新标的池（30–80 亿小盘股）、检查市场择时 |
| 信号扫描 | `scan` | 按策略条件扫描入场信号（只读，不交易） |
| 模拟建仓 | `execute` | 按上一交易日信号，今日开盘价成交 |
| 持仓监控 | `monitor` | 检查止损/止盈/超时，模拟盘卖出 |
| 收盘结算 | `settle` | 按收盘价估值、记录权益曲线、生成日报 |
| 数据更新 | `update` | 刷新本地 K 线缓存 |
| 组合回测 | `backtest` | 与模拟盘共用同一策略与成本模型 |
| 账户状态 | `status` | 查看计划、账户、持仓 |

**默认是模拟盘（`app.mode: simulation`），不连接任何券商接口，不会产生真实委托。**

---

## 二、一条命令跑起来

### 2.1 全自动（推荐）

```bash
./setup.sh 你的token              # 建虚拟环境 + 装依赖 + 配 token + 体检
./setup.sh 你的token --run        # 再跑一遍完整流程（含回测）
./setup.sh --run --limit 60       # 用已有 token，只跑前 60 只（快速验证）
```

`setup.sh` 做的事：建 `.venv` → 装 `pandas/numpy/pyyaml/tushare/pytest` →
把 token 写入 `~/.tushare/token`（权限 600，不在仓库内）→ 跑 `doctor` 体检 →
可选跑完整流程。任何一步失败都会停下并指出失败项。

### 2.2 全流程一条命令

```bash
.venv/bin/python -m tushare_trader all            # 体检->盘前->扫描->建仓->监控->结算->回测
.venv/bin/python -m tushare_trader all --dry-run  # 只演练，不改账户
.venv/bin/python -m tushare_trader all --no-backtest
.venv/bin/python -m tushare_trader all --date 20220425 --limit 60
```

任一步失败会打印「已完成步骤」并退出，便于定位。

### 2.3 手工安装

```bash
pip install pandas numpy pyyaml tushare
# 若 numpy>=2 与 pandas<2 混用会报 "numpy.dtype size changed"，此时：
# pip install "numpy<2" --upgrade
```

### 2.4 配置 token（不写入仓库）

任选一种：

```bash
# 方式 1：环境变量（CI / 服务器推荐）
export TUSHARE_TOKEN=你的token

# 方式 2：用户目录文件（本地开发推荐）
mkdir -p ~/.tushare && echo 你的token > ~/.tushare/token && chmod 600 ~/.tushare/token

# 方式 3：项目内文件（已在 .gitignore 中排除）
echo 你的token > .tushare_token && chmod 600 .tushare_token
```

> ⚠️ **永远不要把 token 提交进仓库。** `.gitignore` 已排除 `.tushare_token`、
> `.env`、`credentials*.json`。`doctor` 输出只显示脱敏后的 token（`e95a****ee5e`）。

### 2.5 分步执行

```bash
python -m tushare_trader doctor                    # 先确认接口通
python -m tushare_trader prepare                   # 盘前：标的池 + 择时
python -m tushare_trader scan                      # 扫描信号
python -m tushare_trader execute --dry-run         # 建仓演练（不下单）
python -m tushare_trader monitor                   # 持仓监控
python -m tushare_trader settle                    # 收盘结算
python -m tushare_trader status                    # 看账户
```

### 2.6 单独回测

```bash
python -m tushare_trader prepare                              # 先建标的池
python -m tushare_trader backtest --limit 80 --start 2021-01-01
```

结果写入 `output/backtest_<时间戳>/`：`equity.csv`（权益曲线）、
`trades.csv`（逐笔交易）、`metrics.json`（绩效指标）。

---

## 三、策略逻辑

策略来自 `strategy/`（研究版）并已在仓库中完成稳健性验证。

```
在「长期趋势向上 + 中期下跌 + 短期超卖」的位置买入布林带下轨，
持有至布林带上轨或止损离场。
```

| 层级 | 条件 | 参数 |
|---|---|---|
| 择时（**默认关闭**） | 中证1000 60 日收益 < −20% | `market_ret_max` |
| 长期趋势 | 月线收盘 > 月线 MA6 或 MA6 向上 | `monthly_ma` |
| 中期趋势 | MA60 的 20 日斜率 < −1% | `ma60_slope_max` |
| 相对位置 | 120 日区间分位 < 0.30 | `pos120_max` |
| 短期信号 | 连续 ≥2 天收在布林带下轨 | `streak_min` |
| 流动性 | 20 日均成交额 ≥ 2000 万 | `min_avg_amount` |

出场：上轨止盈 / −12% 止损 / 持有满 60 个交易日。

> **2026-11 优化**：市场择时默认**关闭**，`max_positions` 由 5 提到 10。
> 原因是择时把 8.8 年压缩到只剩 16 笔交易。改动在固定标的池上样本内外
> 同时改善（全周期 CAGR 0.62% → 4.04%，回撤 −20.35% → −21.96%），
> 详见 [`research/README.md`](../research/README.md)。
> 想恢复"只在急跌时出手"的旧行为：`use_market_filter: true` +
> `max_positions: 5`。注意只关择时、不同时加仓位，回撤会到 −27.8%。

全部参数在 [`config/system.yaml`](../config/system.yaml)，代码不硬编码。

### 关于「择时」（默认已关闭）

若把 `use_market_filter` 改回 `true`，`prepare` 会打印 `market_passed: false`，
`execute` 直接空仓退出——这是**正常状态**，不是 bug。实测中证1000 在
2015–2026 的 2814 个交易日中，仅 106 天（**3.8%**）满足「60 日收益 < −20%」。

在关闭择时的默认配置下，8.8 年约 293 笔交易、年均 33 笔，
不再出现长期空仓。

---

## 四、目录结构

```
tushare_trader/
├── credentials.py   token 解析（环境变量 / 文件），绝不入库
├── data.py          Tushare 数据源：日线、复权、指数、每日指标、交易日历
│                    （限频、重试、分页、CSV 缓存）
├── indicators.py    技术指标（与研究库 strategy/lib/indicators.py 逐值一致）
├── strategy.py      策略：入场条件、出场判定、仓位计算
├── costs.py         A 股交易成本：佣金 / 印花税 / 滑点 / 最低佣金
├── broker.py        模拟盘经纪商：下单、成交、持仓、结算、状态持久化
├── backtest.py      组合级事件驱动回测（与模拟盘共用策略与成本）
├── runner.py        每日任务编排
├── cli.py           命令行入口
└── __main__.py      支持 python -m tushare_trader

config/system.yaml   唯一参数入口
tests/               pytest 单元测试（全部离线，不访问网络）
```

---

## 五、关键设计

### 5.1 无未来函数

- 信号在 **T 日收盘后** 生成，**T+1 开盘** 成交
- 回测与模拟盘使用同一套代码路径（`BollBandStrategy` + `CostModel`）
- 月线/周线指标的「周期是否结束」由**交易日历**判定，不用数据末行判定
- `tests/test_backtest.py::test_no_lookahead_entry_uses_next_open` 会验证成交价
  是「信号次日开盘价」而非「信号日收盘价」

### 5.2 成本一致

回测与模拟盘共用 `CostModel`：

| 项目 | 默认 |
|---|---|
| 佣金 | 万 2.5（单边，最低 5 元） |
| 印花税 | 千 0.5（仅卖出） |
| 滑点 | 万 0.75（单边） |

往返约 0.25%。

### 5.3 状态可恢复

模拟盘账户写入 `state/paper_account.json`（现金、持仓、成交、订单、权益历史），
进程重启后继续。每日任务流水写入 `state/runs.jsonl`。

### 5.4 数据缓存

K 线、指数、股票列表缓存在 `data/cache/`。缓存「末行日期距今 ≤7 天」时直接复用，
否则重新拉取。日线按 600 天一段分页，规避 Tushare 单次行数上限。

### 5.5 接口的时间特性（实测）

- `daily_basic`（市值数据）**收盘后才发布**，盘中调用当日返回 0 行。
  系统会回退到最近一个可用交易日（`daily_basic_with_fallback`），
  因此盘前 `prepare` 不会因为当日无市值数据而失败。
- 单只股票 3 年日线约 925 行，超过单次上限，故必须分页。

---

## 六、测试

```bash
pip install pytest
python -m pytest tests -q
```

全部单元测试**离线运行**（用假接口/合成行情），覆盖：

| 测试文件 | 覆盖内容 |
|---|---|
| `test_indicators.py` | 指标数值正确性 + 与研究库逐值一致性 + 无未来函数 |
| `test_strategy.py` | 入场/出场条件、止损模式、仓位计算、成本模型 |
| `test_broker.py` | 涨跌停约束、整手、不超支、状态持久化 |
| `test_backtest.py` | 成交价不超前、持仓上限、成本拖累、绩效指标 |
| `test_data.py` | 列名标准化、缓存命中、分页、标的池筛选、token 解析 |

真实接口的测试标记为 `network`，默认跳过。

---

## 七、接入实盘

模拟盘与实盘的边界是 `PaperBroker` 的 `buy/sell` 两个方法。接券商接口时：

1. 实现一个与 `PaperBroker` 同接口的类（`buy/sell/positions/cash/equity`）
2. 在 `runner._broker()` 里按 `app.mode` 返回对应实现
3. **保持 simulation 为默认**，实盘模式需要显式配置 + 二次确认

仓库内的 `qmt_trader/` 已有 miniQMT（xtquant）实现，可作为实盘通道的参考，
但本包的 `runner.py` 目前只接模拟盘——这是有意的安全默认值。

### 实盘前的检查清单

- [ ] 模拟盘至少完整跑通 1 个月，核对信号与成交价
- [ ] 用 `--dry-run` 核对每一次建仓信号
- [ ] 确认券商侧资金、涨跌停、停牌处理与预期一致
- [ ] 明确最大回撤熔断由谁执行（本系统当前**不含自动熔断**）

---

## 八、已知限制

1. **不含自动熔断**：`risk.max_portfolio_drawdown` 目前只是配置项，代码未读取，
   不会自动减仓。实盘前必须另行决定回撤熔断由谁执行。
2. **不含自动调度**：需要 cron / 计划任务调用 CLI（示例见下）。
3. **不含行业集中度校验**：`risk.sector_concentration_max` 目前仅在配置中保留，
   代码未使用——Tushare 的行业映射需要额外取数且行业分类口径有多个版本，
   属于需要产品决策的部分，未在本版本实现。
4. **幸存者偏差**：标的池是「当前存续」的股票，已退市股未纳入。实盘收益建议打 6–8 折。
5. **容量限制**：实测年均仅约 2 笔成交（见下），单笔资金规模受小盘股流动性限制，
   建议总资金 ≤500 万。

### 定时任务示例（crontab，Asia/Shanghai）

```cron
0 9  * * 1-5  cd /path/to/repo && python -m tushare_trader prepare  >> logs/cron.log 2>&1
25 9 * * 1-5  cd /path/to/repo && python -m tushare_trader execute  >> logs/cron.log 2>&1
45 14 * * 1-5 cd /path/to/repo && python -m tushare_trader monitor  >> logs/cron.log 2>&1
10 15 * * 1-5 cd /path/to/repo && python -m tushare_trader settle   >> logs/cron.log 2>&1
```

> 15:10 的 `settle` 在收盘后运行，因此 `execute` 用的是**当日收盘后已确定的
> 上一交易日信号**——与回测口径一致。

---

## 九、与仓库其他部分的关系

| 目录 | 定位 |
|---|---|
| `strategy/` | 研究版：回测、稳健性验证、参数对比。**策略结论的来源** |
| `qmt_trader/` | miniQMT 实盘版：对接迅投 QMT 交易终端（Windows） |
| `tushare_trader/` | **本包**：Tushare 原生、跨平台、模拟盘优先的完整闭环 |

三者的策略参数与指标算法保持一致，`tests/test_indicators.py` 会验证本包与
研究库的指标逐值相同，防止两份实现漂移。
