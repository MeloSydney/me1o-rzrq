---
name: rzrq-leverage
description: 获取A股融资融券（两融）数据并生成杠杆趋势折线图，判断当前市场杠杆水平。当用户提到"两融数据"、"融资融券"、"杠杆水平"、"两融趋势"、"市场杠杆"、"rzrq"时应触发此 skill。
---

# A股两融数据接口

从三个平台获取沪深两市融资融券余额，交叉验证后输出逐日数据（含简单计算指标）和交互式图表。本 skill 是**数据接口**，只做数据获取和简单指标计算，不做预警分类、形态分析或投资建议。

## 前置条件

- Python venv 位于 skill 目录下 `.venv/`
- 依赖：akshare, tushare, matplotlib, pandas, requests, openpyxl
- 若 venv 不存在或依赖缺失：
  ```bash
  python3 -m venv <skill-dir>/.venv
  <skill-dir>/.venv/bin/pip install akshare tushare matplotlib pandas requests openpyxl
  ```

## 执行

```bash
<skill-dir>/.venv/bin/python <skill-dir>/scripts/fetch_rzrq.py [OPTIONS]
```

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--start` | 2015-01-01 | 起始日期 |
| `--end` | 今天 | 结束日期 |
| `--output` | /Users/melo/doc-Dgf/rzrq/MMDD | 输出目录，MMDD 为运行当天月日，自动创建 |
| `--no-validate` | false | 跳过交叉验证（含中证数据） |
| `--no-fix` | false | 跳过缺口补全和异常修复 |

### 示例

```bash
# 默认全流程
<skill-dir>/.venv/bin/python <skill-dir>/scripts/fetch_rzrq.py

# 只看近 3 年，输出到桌面
<skill-dir>/.venv/bin/python <skill-dir>/scripts/fetch_rzrq.py --start 2023-01-01 --output ~/Desktop
```

## 输出

默认输出到 `/Users/melo/doc-Dgf/rzrq/MMDD/`（可用 `--output` 覆盖）：

| 文件 | 说明 |
|------|------|
| `rzrq_data.csv` | 逐日两融数据 + 计算指标 |
| `rzrq_leverage.html` | 交互式图表（自包含，离线可用） |
| `csdata_rzrq.csv` | 中证监测补充指标（最近 N 天） |

> 同一天多次运行会覆盖同名文件，历史日期目录不受影响。

### `rzrq_data.csv` 字段说明

**原始数据（10列）：**

| 列名 | 含义 | 单位 |
|------|------|------|
| 日期 | 交易日期 | YYYY-MM-DD |
| 沪市融资余额 | 上海市场融资余额 | 亿元 |
| 深市融资余额 | 深圳市场融资余额 | 亿元 |
| 两市融资余额 | 沪深合计融资余额 | 亿元 |
| 两市融券余额 | 沪深合计融券余额 | 亿元 |
| 沪市两融余额 | 上海市场融资+融券 | 亿元 |
| 深市两融余额 | 深圳市场融资+融券 | 亿元 |
| 两市两融余额 | 沪深合计两融余额 | 亿元 |
| 上证指数 | 上证指数收盘点位 | 点 |
| 科创50指数 | 科创50收盘点位 | 点 |

**计算指标（7列）：**

| 列名 | 计算方式 | 含义 | 下游用途 |
|------|----------|------|----------|
| 融资5日变化率(%) | `rzye_total.pct_change(5) × 100` | 融资余额5个交易日变化率 | doBuy F3 杠杆趋势评分；doEscape 短期融资动量信号 |
| 融资10日变化率(%) | `rzye_total.pct_change(10) × 100` | 融资余额约两周变化率 | 补充趋势参考 |
| 融资20日变化率(%) | `rzye_total.pct_change(20) × 100` | 融资余额约一个月变化率 | doEscape 中期融资趋势判断 |
| 融资60日回撤(%) | `rzye / rolling_max(60) - 1` | 距60个交易日融资高点的回撤 | doEscape leverage_regime 计算 |
| 融资252日分位 | `rolling_rank_pct(252)` | 融资余额在252个交易日中的分位(0-1) | doEscape leverage_regime 计算 |
| 上证5日变化率(%) | `index_close.pct_change(5) × 100` | 上证指数5日变化率 | 融资-指数背离判断 |
| 上证20日变化率(%) | `index_close.pct_change(20) × 100` | 上证指数20日变化率 | 融资-指数背离判断 |

> 20日融资-指数背离（百分点）= 融资20日变化率 − 上证20日变化率，可由下游自行计算。

### `csdata_rzrq.csv` 字段说明

中证监测系统补充指标（来自 csdata.cn），提供杠杆绝对水位信息：

| 字段 | 含义 |
|------|------|
| 两融/流通市值比重 | 两融余额占A股流通市值百分比 |
| 维持担保比例 | 全市场平均维持担保比例 |
| 两融交易额占比 | 两融交易额占市场成交额百分比 |

> **历史锚点**：两融余额占流通市值比重的近年低点约 1.8%，2015 年杠杆牛峰值超过 4%。
>
> **口径提示**：本 skill 的两融比重是**两融余额（融资+融券）/流通市值**；外部研报常用**融资余额/流通市值**（分子不含融券，通常低 0.1—0.2 个百分点）。

### 终端摘要

- 当前两融余额（万亿）、区间峰值、历史分位
- 融资-指数相关性：水平值相关系数（长期共线性）和日变动相关系数（同涨同跌同步性）

## 输出模板（LLM 回复格式，严格遵循）

以下为固定输出骨架，`{}` 内为每次填充的变量。数据来自脚本确定产出，表格列数与表头不得增减改。本 skill 是数据接口，模板只做数据呈现，不做预警分类或投资建议。

### 当前两融概况

（截至 `{latest_date}`，最近可用交易日）

| 指标 | 值 | 状态 |
|---|---|---|
| 两市两融余额 | `{total}` 万亿（`{total_yi}` 亿） | 历史分位 `{pct}` |
| 两市融资余额 | `{rzye}` 万亿（`{rzye_yi}` 亿） | — |
| 两市融券余额 | `{rqye}` 亿 | — |
| 区间峰值 | `{peak}` 万亿 | — |
| 两融/流通市值 | `{ratio}` | `{ratio_status}` |
| 维持担保比例 | `{maintenance}` | `{maint_status}` |
| 两融/交易额 | `{trade_ratio}` | — |

> csdata 不可用时（`--no-validate` 或下载失败），两融/流通市值、维持担保比例、两融/交易额三行写"不可用"，并跳过下方"中证监测"小节。

### 融资趋势指标

（`{latest_date}`）

| 指标 | 值 | 含义 |
|---|---|---|
| 融资5日变化率 | `{r5}` | `{r5_desc}` |
| 融资10日变化率 | `{r10}` | `{r10_desc}` |
| 融资20日变化率 | `{r20}` | `{r20_desc}` |
| 融资60日回撤 | `{drawdown60}` | 距60日高点回撤 |
| 融资252日分位 | `{pct252}` | 252日中分位（0=最低，1=最高） |
| 上证5日变化率 | `{idx5}` | — |
| 上证20日变化率 | `{idx20}` | — |

> 20日融资-指数背离 = 融资20日变化率 − 上证20日变化率 = `{divergence}` pp

### 中证监测近 `{N}` 日趋势

| 日期 | 两融/流通市值 | 维持担保比例 | 两融/交易额 |
|---|---|---|---|

csdata 有数据的每个交易日一行；维持担保比例连续多日下降时用一句标注变化幅度。

### 融资-指数相关性

| 对比 | 水平值相关系数 | 日变动相关系数 | 解读 |
|---|---|---|---|
| 融资 vs 上证指数 | `{corr_idx_level}` | `{corr_idx_daily}` | `{corr_idx_desc}` |
| 融资 vs 科创50 | `{corr_kcb_level}` | `{corr_kcb_daily}` | `{corr_kcb_desc}` |

### 数据质量

- 主源 jin10：`{days}` 个交易日（`{start}` ~ `{end}`）
- 缺口补全：`{gaps_filled}` 处填补，`{gaps_holiday}` 处为节假日
- 异常修复：`{anomalies}` 个点
- 交叉验证：`{validation_status}`
- 中证补全：尾部 `{csdata_days}` 个交易日由 csdata 补入

### 输出文件

| 文件 | 大小 | 说明 |
|---|---|---|
| `{output_dir}/rzrq_data.csv` | `{size}` | 逐日两融数据 + 计算指标 |
| `{output_dir}/rzrq_leverage.html` | `{size}` | 交互式图表（离线可用） |
| `{output_dir}/csdata_rzrq.csv` | `{size}` | 中证监测补充指标 |

交互图表可在浏览器打开：`file://{output_dir}/rzrq_leverage.html`

## 交互图表 `rzrq_leverage.html`

最近一年四个上下对齐子图：两融合计、融资余额、科创50、上证指数。各子图独立 Y 轴、共享日期轴，鼠标悬停沿同一日期读取各子图精确数值；X 轴左右留缓冲并启用跨子图十字线（`hoverdistance`/`spikedistance=-1`），光标在图表左右边缘轻微移动时数据不消失。不绘制融券子图，不叠加预警点。

- 两融合计图含 25%-75% 区间带、峰值标注和当前值
- 融资余额（蓝色，亿元）
- 科创50（橙色，指数点位）
- 上证指数（紫色，指数点位）
- 最底部时间轴逐月显示 YYYY-MM

## 数据源（三源交叉验证）

| 源 | 接口 | 角色 | 覆盖 |
|----|------|------|------|
| AKShare → jin10 | `macro_china_market_margin_sh/sz` | 主源 | 2010 至今全历史 |
| AKShare → 交易所 | `stock_margin_sse/szse` | 补全+验证 | 近期，逐日/范围 |
| 中证监测 csdata.cn | xlsx 下载 | 第三验证+补充指标 | 最近 N 天 |
| AKShare → 新浪指数 | `stock_zh_index_daily(sh000001/sh000688)` | 上证与科创50趋势对照 | 全历史 |

质量保障：
1. **缺口补全**：合并数据 >10 天缺口 → 交易所数据填补；尾部缺口 → csdata 补全
2. **异常修复**：偏差 >30% 的异常点 → 交易所数据替换
3. **交叉验证**：jin10 vs 交易所 偏差 <1%；jin10 vs 中证 偏差 <1%

**人工核对兜底渠道**：三源接口全部失败时，用东方财富 `https://data.eastmoney.com/rzrq/` 手工核对。

## 下游消费方

本 skill 输出的数据供以下 skill 消费（分析逻辑由各 skill 自行实现）：

| 消费方 | 读取文件 | 使用字段 |
|--------|----------|----------|
| doEscape | rzrq_data.csv | 融资5/10/20日变化率、60日回撤、252日分位、上证5/20日变化率 |
| doEscape | csdata_rzrq.csv | 两融/流通市值比重、维持担保比例 |
| doBuy | rzrq_data.csv | 融资5日变化率、60日回撤、252日分位 |
| doBuy | csdata_rzrq.csv | 两融/流通市值比重、维持担保比例 |

> 下游 skill 从 CSV 读取原始指标后，自行计算分类/评分逻辑。本 skill 不提供 warning_code、crash template 或 advisory。

## 独立使用中证数据脚本

```bash
# 只拉中证数据（含维持担保比例等补充指标）
<skill-dir>/.venv/bin/python <skill-dir>/scripts/fetch_csdata.py --days 10
```

## 板块流向分析（fetch_sector.py）

交易所只发布个股级两融明细，本脚本将其与申万一级行业成分股聚合为板块级统计（全部免费源）：

- 个股明细：AKShare `stock_margin_detail_sse/szse`（交易所法定披露）
- 行业分类：申万宏源一级行业成分股（本地缓存 7 天：`~/.cache/rzrq/sw_industry_map.csv`）
- 验证源：东方财富 datacenter 个股两融（独立管道，`--validate` 开启）

```bash
# 最近交易日板块聚合 + 东财独立源交叉验证
<skill-dir>/.venv/bin/python <skill-dir>/scripts/fetch_sector.py --validate

# 指定日期 / 自定义输出 / 刷新行业缓存
<skill-dir>/.venv/bin/python <skill-dir>/scripts/fetch_sector.py --date 2026-09-01 --output ~/Desktop --refresh-map
```

### 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--date` | 最近交易日 | 目标日期 YYYY-MM-DD |
| `--output` | /Users/melo/doc-Dgf/rzrq/MMDD | 输出目录 |
| `--validate` | false | 用东财独立源逐股交叉验证融资余额 |
| `--refresh-map` | false | 强制刷新申万成分股缓存 |

### 输出

- `<output>/rzrq_by_sector.csv` — 申万一级聚合表（余额亿 / 净买入亿 / 标的数 / 占比%）
- 终端摘要：个股 vs ETF 结构、当日毛流入/净变动、余额 TOP10 行业、净流入/流出 TOP5

### 口径说明

- 融资余额为**存量**；净买入 = 当日余额 − 前一交易日余额（近似流量）
- ETF/基金（代码前缀 51/56/58/15/16/18）单独统计，不计入行业聚合（多为对冲/套利仓）
- `--validate` 预期结果：匹配率 100%、偏差中位 0.000%（东财与交易所同源法定数据，验证的是解析/聚合管道）
