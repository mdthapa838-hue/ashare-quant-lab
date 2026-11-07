# snapshots — 已抓好的数据快照（2026Q3，即最新报告期）

仓库 `.gitignore` 排除了 `*.csv`（避免把上百万行原始数据塞进 git），
但「每个股东现在的持股」和「这一期谁加仓/减仓/新进/清仓」是这份工作的核心产物，
不跑代码也应该能直接拿到，因此以 `.csv.gz` 形式入库。

| 文件 | 行数 | 内容 |
|---|---|---|
| `holders_current_free.csv.gz` | 35,177 | 十大流通股东口径：每个股东本期持股家数、新进/加仓/减仓/不变家数、持股市值、数据区间 |
| `holders_current_total.csv.gz` | 36,715 | 十大股东口径，字段同上 |
| `changes_2026Q3_free.csv.gz` | 30,684 | 十大流通股东口径：本期发生变化的每条 股东×股票（不含「不变」） |
| `changes_2026Q3_total.csv.gz` | 26,596 | 十大股东口径，字段同上 |

## 怎么用

```bash
gunzip -k holders_current_free.csv.gz          # 解压后用 Excel / pandas 打开
zcat changes_2026Q3_free.csv.gz | head         # 或者直接看
```

CSV 带 UTF-8 BOM，Excel 双击不乱码。

## 字段

`holders_current_*`：

| 字段 | 含义 |
|---|---|
| `holder_key` / `holder_name` | 股东唯一编码 / 名称 |
| `holder_type` | 个人 / QFII / 证券投资基金 / … （十大股东口径无此字段，为空） |
| `holder_num` | 本期持股家数（进入多少只股票的前十大） |
| `new_num` / `up_num` / `down_num` / `unchanged_num` | 新进 / 加仓 / 减仓 / 不变 的家数 |
| `market_cap` | 本期持股市值合计（元） |
| `hold_num_total` | 本期持股数合计（股） |
| `first_period` / `last_period` / `period_num` | 该股东在数据里出现过的区间与期数 |

`changes_2026Q3_*`：

| 字段 | 含义 |
|---|---|
| `secucode` / `security_name` | 股票代码 / 名称 |
| `hold_num` | 本期持有股数 |
| `prev_hold_num` | 上期持有股数（上期未上榜则为空） |
| `period_change` | 环比变化股数（新进 = 本期持股数；「重回」为空，因为中间断过） |
| `period_status` | `新进` / `重回` / `加仓` / `减仓` |
| `hold_ratio` | 占流通股比例（%） |
| `market_cap` | 本期持股市值（元） |

## 说明

- 只有 **2026Q3** 这一期。其余 19 期（2021年报 ~ 2026半年报）的完整数据
  （共 212 万行）体积太大，需要时用下面这条命令重新生成，约 25 分钟：

  ```bash
  python -m eastmoney_holders all
  ```

- 这批快照对应抓取时的接口状态；页面后续更新后同一条命令可以重新生成。
- 「清仓」不在这两个文件里 —— 那是「上期在榜、本期不在榜」的推断，
  在本地生成的 `holders_all_periods_*.csv`（`exit_num` 列）里。
