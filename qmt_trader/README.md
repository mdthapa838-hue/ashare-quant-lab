# 布林带下轨反弹策略交易系统

> 基于 Tushare 数据源 + miniQMT 交易接口的 A 股小盘股量化交易软件

---

## 一、系统概述

本系统把已验证的策略（见 `strategy/` 目录的回测报告）封装为可实际运行、可接实盘的交易软件。

**核心特性**

| 特性 | 说明 |
|---|---|
| 数据源 | Tushare Pro（自动读取本机凭据，token 不落盘） |
| 交易通道 | miniQMT（xtquant），支持本机已装的两个终端 |
| 策略 | 布林带下轨反弹（长期向上 + 中期下跌 + 短期超卖 + 市场急跌） |
| 安全 | simulation 模式只读；live 模式需显式配置 |
| 风控 | 涨跌停约束、行业集中度、单笔上限、日委托上限 |
| 调度 | 内置定时任务（盘前/开盘/盘中/收盘/数据更新） |

---

## 二、目录结构

```
qmt_trader/
├── config/
│   └── config.yaml            # 唯一配置入口
├── core/
│   ├── config.py              # 配置加载与校验
│   ├── logger.py              # 日志（含脱敏）
│   ├── tushare_source.py      # Tushare 数据源
│   ├── qmt_trader.py          # miniQMT 交易接口
│   ├── strategy.py            # 策略引擎
│   └── engine.py              # 交易引擎（编排层）
├── main.py                    # 命令行入口
├── data_cache/                # 数据缓存（自动创建）
├── logs/                      # 运行日志
├── state/                     # 状态台账
└── output/                    # 信号导出
```

---

## 三、环境要求

| 组件 | 要求 | 本机状态 |
|---|---|---|
| Python | 3.9+ | ✅ 3.11.9 |
| pandas / numpy | 任意近期版本 | ✅ 2.3.3 / 1.26.4 |
| PyYAML | 任意 | ✅ 6.0.2 |
| tushare | 任意 | ✅ 1.4.24 |
| xtquant | 随 miniQMT 安装 | ✅ xtquant_250516 |
| miniQMT 终端 | 需启动并登录 | ⚠ 当前未启动 |

**可选**：`apscheduler`（用于 `serve` 定时调度）

---

## 四、快速开始

### 4.1 环境体检（先做这一步）

```bash
cd F:\deepseek\股市\股市2\qmt_trader
python main.py doctor
```

输出示例：

```
[1] Python 依赖
    ✓ pandas               2.3.3
    ✓ numpy                1.26.4
    ✓ PyYAML               6.0.2
    ✓ tushare              1.4.24
    ✓ xtquant(miniQMT)     xtquant_250516

[2] Tushare 凭据
    ✓ token 获取成功（长度 56）
    ✓ API 连通，返回 5 个交易日

[3] miniQMT
    ✓ xtquant 可用: True
    ✓ userdata_mini: D:\迅投QMT交易终端浙商证券版\userdata_mini
    ○ 连接失败（QMT 终端可能未启动/未登录）

[4] 配置
    模式        : simulation
    择时门槛    : 指数60日 < -20.0%
    止盈/止损    : upper / -12%
    最大持仓    : 5 只
```

### 4.2 常用命令

```bash
python main.py prepare        # 盘前：更新标的池、检查择时
python main.py scan           # 扫描信号（只读，不下单）
python main.py status         # 查看当前状态
python main.py entry --dry-run    # 建仓演练（不下单）
python main.py monitor --dry-run  # 持仓监控演练
python main.py settle         # 收盘结算
python main.py update-data    # 更新K线缓存
python main.py serve          # 启动定时调度
```

---

## 五、凭据管理（重要）

### 5.1 Tushare Token

**Token 不写入任何配置文件。** 程序按以下顺序自动获取：

1. 环境变量 `TUSHARE_TOKEN`
2. `stock_suite/credentials.py`（读取 Windows 凭据管理器）
3. 直接读 Windows 凭据管理器 `Codex/Tushare/Default`

本机验证结果：

```
✓ token 获取成功（来源 stock_suite.credentials，长度 56）
✓ API 连通
```

**如果在其他机器部署**，需设置环境变量：

```powershell
$env:TUSHARE_TOKEN = "你的token"
# 或永久设置
[Environment]::SetEnvironmentVariable("TUSHARE_TOKEN", "你的token", "User")
```

### 5.2 miniQMT 账号

在 `config/config.yaml` 中配置：

```yaml
qmt:
  userdata_path: ""          # 留空则自动探测
  account_id: ""             # 留空则从 QMT 查询
  session_id: 20261014       # 同目录多进程需不同
```

**自动探测的路径**（本机已找到前两个）：

```
D:\迅投QMT交易终端浙商证券版\userdata_mini
D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\userdata_mini
```

---

## 六、策略参数

配置文件 `config/config.yaml` 的 `strategy` 节：

```yaml
strategy:
  entry:
    pos120_max: 0.30          # 120日区间分位上限（低位）
    streak_min: 2             # 连续收在下轨的最少天数
    ma60_slope_max: -0.01     # MA60斜率上限（中期下跌）
    use_monthly_filter: true  # 月线趋势过滤（实测 +5.90pct）
    use_market_filter: true   # 市场择时（实测 +10.38pct，最强）
    market_ret_max: -20.0     # 择时门槛
    use_j_filter: false       # J值（实测无效，关闭）
    use_weekly_filter: false  # 周线（实测仅+0.33pct，关闭）

  exit:
    take_profit: "upper"      # 上轨止盈（中轨扣成本后无利润）
    stop_loss_pct: -0.12      # 止损
    max_hold_days: 60         # 最长持有

  portfolio:
    max_positions: 5          # 最多持仓
    max_position_pct: 0.25    # 单只上限
```

**回测验证结果**（详见 `strategy/README.md`）：

| 配置 | CAGR | Sharpe | 最大回撤 |
|---|---|---|---|
| 完整策略（含择时+月线） | 9.18% | 0.62 | −19.02% |
| 无择时 | 52.29% | 2.02 | −21.32% |
| 仅基础三条件 | 5.79% | 0.27 | −65.06% |

---

## 七、运行流程

### 7.1 手动运行（推荐首次使用）

```
09:00  python main.py prepare          # 盘前准备
09:31  python main.py entry --dry-run  # 先演练
       python main.py entry            # 确认后执行（需 live 模式）
14:50  python main.py monitor          # 持仓监控
15:10  python main.py settle           # 收盘结算
17:00  python main.py update-data      # 更新数据
```

### 7.2 自动调度

```bash
pip install apscheduler
python main.py serve
```

调度时间在 `config.yaml` 的 `schedule` 节配置。

### 7.3 状态文件

| 文件 | 内容 |
|---|---|
| `state/plan.json` | 最近一次盘前计划 |
| `state/universe.json` | 标的池 |
| `state/signals.json` | 最近信号 |
| `state/positions.json` | 持仓台账 |
| `state/report.json` | 收盘报告 |
| `state/runs.jsonl` | 运行日志（追加） |

---

## 八、安全机制

### 8.1 模式门禁

```yaml
app:
  mode: "simulation"     # simulation=只读 / live=可下单
```

- **simulation**：所有 `order_*` 方法抛出 `PermissionError`
- **live**：允许下单，启动时打印警告

**默认 simulation。切换到 live 前请务必先跑通 dry-run。**

### 8.2 风控约束

| 约束 | 默认值 |
|---|---|
| 单只上限 | 25% |
| 最多持仓 | 5 只 |
| 同行业上限 | 2 只 |
| 日委托上限 | 20 笔 |
| 涨停不买 | 开盘涨幅 > 9.5% |
| 跌停不卖 | 开盘跌幅 < −9.5% |
| 组合回撤预警 | 30% |

### 8.3 日志脱敏

日志自动过滤：token、password、bearer、account_id。

---

## 九、常见问题

### Q1: `连接 miniQMT 失败，返回码 -1`

**原因**：QMT 终端未启动或未登录。

**解决**：
1. 启动 QMT 交易终端并登录
2. 确认已勾选「极速交易」/「miniQMT」模式
3. 重新运行 `python main.py doctor`

### Q2: `Tushare token 获取失败`

**解决**：
```powershell
$env:TUSHARE_TOKEN = "你的token"
python main.py doctor
```

### Q3: 数据只返回 429 行

**已修复**：数据源已实现自动分页（单次接口上限 429 行）。如仍有问题，删除 `data_cache/index/` 后重试。

### Q4: 择时一直不通过

**这是正常的**。择时门槛为「中证1000 60日 < −20%」，历史上仅 95/3107 天满足（约 3%）。

当前状态（2026-10-14）：市场 60日 −12.87%，**未通过 → 策略空仓**。

如需放宽，修改 `config.yaml`：
```yaml
market_ret_max: -15.0    # 放宽门槛（但历史期望收益会下降）
```

### Q5: 账户显示「可用资金 0」，但总资产有几十万

**原因**：券商**余额理财**把闲置资金自动申购了货币基金。

实测本机账户：

```
total_asset     501,358.63   ← 账户总资产（含理财）
cash                   0.00   ← 股票账户可用资金
frozen_cash            0.00
market_value           0.00
current_balance      200.00   ← 股票账户保留的底仓
```

诊断方法：

```bash
python tools/inspect_account.py          # 查资产/持仓/委托/成交
```

**如果确认是余额理财**，下单时券商端会自动赎回，不阻塞交易。

系统已内置购买力口径适配（`config.yaml`）：

```yaml
strategy:
  portfolio:
    purchasing_power_mode: "auto"   # auto=优先cash，不足时回退总资产
```

三种口径：

| 模式 | 行为 | 适用 |
|---|---|---|
| `cash` | 只用可用资金 | 无余额理财的账户 |
| `auto` | 优先 cash，不足回退 total_asset | **余额理财账户（默认）** |
| `total` | 始终用 total_asset | 确定有自动申赎 |

**注意**：`auto` 模式会让系统按总资产计算仓位。如果你的余额理财**不支持**自动赎回，
实际下单仍会因资金不足失败。**建议先小额实测一笔确认。**

### Q6: 如何确认余额理财是否支持自动申赎

```bash
# 1. 查看账户结构
python tools/inspect_account.py

# 2. 小额实测（live 模式下）
python main.py entry        # 观察是否成功
```

或者在券商 APP 的【理财】/【余额理财】设置里查看「自动赎回」开关。

### Q5: 如何只扫描不下单

```bash
python main.py scan                    # 只读扫描
python main.py entry --dry-run         # 演练建仓
python main.py monitor --dry-run       # 演练监控
```

---

## 十、重要风险提示

### 10.1 策略层面的已知风险

| 风险 | 说明 |
|---|---|
| **幸存者偏差** | 回测用现存标的池，已退市股未纳入。**实盘收益建议打 6-8 折评估** |
| **容量限制** | 年约 30 笔交易，资金建议 ≤500 万 |
| **择时苛刻** | 仅在市场急跌时出手，可能长期空仓 |
| **样本局限** | 标的池为 30-80 亿小盘股，不可外推 |

### 10.2 软件层面的注意事项

- 本系统**不含自动止损熔断**，需人工监控极端行情
- 建议先用 simulation 模式**完整跑通至少 1 个月**再考虑实盘
- live 模式下单前**务必用 dry-run 核对信号**
- 定期备份 `state/` 目录

---

## 十一、扩展开发

### 添加新策略

1. 在 `core/strategy.py` 中新增策略类（参考 `BollLowerRebound`）
2. 在 `engine.py` 中接入
3. 在 `config.yaml` 中增加参数节

### 接入其他数据源

修改 `core/tushare_source.py` 或新增 `core/xxx_source.py`，保持接口一致：

```python
class DataSource:
    def daily(self, ts_code, start, end, adj) -> pd.DataFrame: ...
    def index_daily(self, ts_code, start, end) -> pd.Series: ...
    def build_universe(self, trade_date, **kwargs) -> list[str]: ...
```

### 独立回测

回测请使用 `strategy/` 目录（已验证版本）：

```bash
cd F:\deepseek\股市\股市2\strategy
python run_backtest.py --compare
python validate.py
python benchmark_test.py
python audit_trades.py
```

---

## 十二、技术细节

### 数据流程图

```
Tushare Pro API
      ↓ (core/tushare_source.py, 自动分页+缓存+限频)
   K线/指数/市值
      ↓ (core/strategy.py, 指标计算)
   信号/条件
      ↓ (core/engine.py, 组合+风控)
  下单指令
      ↓ (core/qmt_trader.py, 模式门禁)
 miniQMT 终端
      ↓
   券商
```

### 分页机制

Tushare 单次接口上限 429 行，系统自动按 650 天分段拉取并合并去重。

### 缓存策略

| 数据类型 | 缓存位置 | 有效期 |
|---|---|---|
| 股票列表 | `data_cache/basic/` | 7 天 |
| 交易日历 | `data_cache/basic/` | 永久 |
| 个股K线 | `data_cache/kline/` | 覆盖到 end−7天 |
| 指数日线 | `data_cache/index/` | 覆盖到 end−7天 |

---

*生成时间：2026-10-14*
*策略回测详见：`F:\deepseek\股市\股市2\strategy\README.md`*
