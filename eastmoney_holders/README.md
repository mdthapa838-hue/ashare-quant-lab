# eastmoney_holders — 东方财富「股东持股」全量整理

> 把 https://datapc.eastmoney.com/pc_dc/gdcg/index.html 这个页面上的**全部分页**抓下来，
> 整理成「每个股东现在的持股 / 过去的持股 / 持股变化」的完整表格。

页面本身是 Vue 单页应用，表格数据全部来自东方财富数据中心接口。
本模块直接对接同一套接口，逐页翻完全部分页，再按股东维度重新聚合，
所以拿到的不只是页面首屏的 20 行，而是**全部报告期、全部股东、全部持股明细**。

---

## 一、页面到底有什么

页面顶部有两个下拉：**股东类型**（十大流通股东 / 十大股东）和**报告期**（20 个季度）。
选定后首屏是一张股东列表，每行是一个股东：

| 页面列 | 含义 | 接口字段 |
|---|---|---|
| 股东名称 | 股东全称 | `HOLDER_NAME` |
| 股东类型 | 个人/投资公司/QFII/社保… | `HOLDER_TYPE` |
| 持股家数 | 该股东本期进入了多少只股票的前十大 | `HOLDER_NUM` |
| 新进 / 加仓 / 不变 / 减仓 | 相对上期在**各股票**上是新进还是增减 | `HOLDADD_NUM` / `HOLDUP_NUM` / `HOLDUNCHANGED_NUM` / `HOLDDOWN_NUM` |
| 流通市值 / 持股市值 | 本期持股合计市值 | `HOLDER_MARKET_CAP` |
| 持股平均涨幅 / 上涨比例 | 所持股票下一季度表现 | `AVG_CHANGE_RATE` / `RISE_RATIO` |

点进某个股东，是它的**股东详情页**：每个报告期持有哪几只股票、各持有多少股、环比增减。
点进某只股票，是它每个报告期的**十大（流通）股东名单**。

这两层在接口里是同一张明细表（`RPT_F10_EH_FREEHOLDERS` / `RPT_F10_EH_HOLDERS`），
区别只是 `filter` 用 `HOLDER_NEW` 还是 `SECUCODE`。本模块抓的就是这张明细表的全量。

## 二、抓了什么

| 口径 | 报告期 | 明细行数 | 说明 |
|---|---|---|---|
| 十大流通股东 | 20 期（2021年报 ~ 2026三季报） | 约 107 万行 | 每期约 5.4 万行 = 5000+ 只股票 × 10 |
| 十大股东 | 20 期 | 约 107 万行 | 同上，含非流通股东 |

单期明细约 5.6 万行，接口单次最多返回 2500 行，因此每期要翻 **29 页**；
两个口径 × 20 期 = **1160 次分页请求**，全部抓完并落盘。

## 三、一条命令跑起来

```bash
# 全量抓取 + 整理（约 20~30 分钟，取决于网络）
python -m eastmoney_holders all

# 只抓取（可随时中断，已抓好的报告期会跳过）
python -m eastmoney_holders crawl

# 已有 CSV，只重新整理
python -m eastmoney_holders report

# 先看看有哪些报告期
python -m eastmoney_holders dates
```

常用参数：

```bash
python -m eastmoney_holders all --type free        # 只抓十大流通股东
python -m eastmoney_holders all --periods 4        # 只抓最近 4 个报告期（快速验证）
python -m eastmoney_holders report --top 50        # 报告榜单从 30 条改成 50 条
python -m eastmoney_holders all --refresh          # 忽略已有 CSV 重新抓
```

只依赖 Python 标准库，无需安装任何第三方包。

## 四、产物

默认写到 `output/eastmoney_holders/`（已在 `.gitignore` 中排除，不入库）。

### 4.1 整理后的表（report 步骤生成）

| 文件 | 粒度 | 用途 |
|---|---|---|
| `holders_current_free.csv` / `_total.csv` | 最新报告期 × 股东 | **现在的持股**：持股家数、新进/加仓/减仓/不变家数、持股市值、数据区间 |
| `holders_all_periods_*.csv` | 报告期 × 股东 | **逐期变化**：每期持股家数、新进/加仓/减仓/不变/**清仓**家数、市值环比 |
| `holder_stock_history_*.csv` | 股东 × 股票 × 报告期 | **过去的持股**：每只股票每期的股数、上一期股数、环比变化、状态 |
| `holder_report_free.md` / `_total.md` | 报告 | Markdown 分析：榜单、逐期总量、口径核对、头部机构轨迹 |

字段说明（`holder_stock_history_*.csv`）：

| 字段 | 含义 |
|---|---|
| `hold_num` | 该报告期末持有股数 |
| `prev_hold_num` | 紧邻上一报告期的持股数（上期未上榜为空） |
| `period_change` | 相对上一报告期的股数变化（非连续期为空） |
| `period_status` | `新进` / `重回` / `加仓` / `减仓` / `不变` |
| `last_seen_hold_num` | 若中间掉出过榜单，缺口前最近一次的持股数 |
| `change_ratio` | 接口给定的环比变化率（%） |
| `hold_ratio` | 占流通股/总股本比例（%） |
| `market_cap` | 报告期末持股市值（元） |

### 4.2 原始分页（crawl 步骤生成）

```
raw/dates_free.csv                        报告期列表
raw/detail_free_2026-09-30.csv            股东明细（全部 29 页合并）
raw/basic_free_2026-09-30.csv             页面首屏的股东列表（用于口径核对）
raw/changerate_free_2026-09-30.csv        持股平均涨幅 / 上涨比例（默认只抓最新期）
crawl_summary.json                        本次抓取的请求数/行数/重试数
```

CSV 均为 UTF-8 BOM，Excel 双击即可正常显示中文。

## 五、怎么用这些数据

```python
import csv

# 1) 最新报告期持股家数最多的股东
with open("output/eastmoney_holders/holders_current_free.csv", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))
top = sorted(rows, key=lambda r: -int(r["holder_num"]))[:10]
for r in top:
    print(r["holder_name"], r["holder_num"], r["new_num"], r["up_num"], r["down_num"])

# 2) 某个股东的历史：它每一期都持有哪些股票
with open("output/eastmoney_holders/holder_stock_history_free.csv", encoding="utf-8-sig") as f:
    hist = [r for r in csv.DictReader(f) if r["holder_name"] == "香港中央结算有限公司"]
for r in sorted(hist, key=lambda x: (x["end_date"], x["secucode"])):
    print(r["end_date"], r["security_name"], r["hold_num"], r["period_status"])
```

## 六、口径与局限

1. **只有「前十大」**：页面只披露每只股票的前十名（流通）股东，不是完整股东名册；
   未进前十的持仓无法从该页面获得。
2. **报告期滞后**：定期报告披露后数据才更新，最新一期可能仍有未披露的个股；
   `IS_DISCLOSURE_COMPLETE` 标记该期是否已披露完整。
3. **股东身份**：接口用 `HOLDER_NEW`（股东唯一编码）区分股东，自然人同名不合并；
   同一机构的不同产品（如「XX基金-XX号」）按产品分行，不自动汇总到基金公司。
4. **新进/加仓/减仓/不变是“名次口径”**：相对上一报告期在**该股票**上的名次变化，
   `不变` 表示持股数与上期一致，不代表没有参与分红送转。
5. **清仓是本模块补算的**：上期在榜、本期不在榜，可能是真减持，也可能是被挤出前十，
   该页面无法区分，需结合公告判断。
6. **市值口径**：`HOLDER_MARKET_CAP` = 报告期末持股数 × 期末收盘价，与页面一致。

## 七、测试

```bash
python -m pytest tests/test_eastmoney_holders.py -q   # 31 个用例，全部离线
```

覆盖：数值归一、变化分类、股东画像聚合、历史环比、缺口期「重回」、
清仓计算、分页遍历、断页提前结束、CSV 读写、缓存命中、报告生成。
