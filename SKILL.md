---
name: rzrq-leverage
description: 获取A股融资融券（两融）数据并生成杠杆趋势折线图，判断当前市场杠杆水平。当用户提到"两融数据"、"融资融券"、"杠杆水平"、"两融趋势"、"市场杠杆"、"rzrq"时应触发此 skill。
---

# A股两融杠杆趋势

从三个平台获取沪深两市融资融券余额，交叉验证后绘制折线图（含上证指数对照），输出杠杆判断与两融-大盘相关性。

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

### 图表
默认输出到 `/Users/melo/doc-Dgf/rzrq/MMDD/`（MMDD = 运行当天月日，目录自动创建），可用 `--output` 覆盖：

- `<output>/rzrq_leverage.png` — 四子图（两融合计 / 融资 / 融券 / 上证指数 独立走势，共享时间轴）；X 轴按月标记；峰值精确到天；合计图含当前值与 25%-75% 区间带

### 数据
- `<output>/rzrq_data.csv` — 沪深两市逐日两融数据（亿元）
- `<output>/csdata_rzrq.csv` — 中证监测补充指标（两融/流通市值比重、维持担保比例等，最近 5 个交易日）

> 所有数据文件默认随本次运行落入当天 `MMDD` 目录；同一天多次运行会覆盖同名文件，历史日期目录不受影响。

### 终端摘要
- 当前两融余额（万亿）、区间峰值、历史分位
- **两融-大盘相关性**：水平值相关系数（长期共线性）+ 日变动相关系数（同涨同跌同步性）
- **杠杆判断**（来自中证数据官方源）：
  - 两融/流通市值比重：🟢 <2.5% 正常 | 🟡 2.5%-3.5% 中等 | 🔴 >3.5% 偏高
  - 维持担保比例：🟢 >2.5 安全 | 🟡 2.0-2.5 中等 | 🔴 <2.0 爆仓风险

## 数据源（三源交叉验证）

| 源 | 接口 | 角色 | 覆盖 |
|----|------|------|------|
| AKShare → jin10 | `macro_china_market_margin_sh/sz` | 主源 | 2010 至今全历史 |
| AKShare → 交易所 | `stock_margin_sse/szse` | 补全+验证 | 近期，逐日/范围 |
| 中证监测 csdata.cn | xlsx 下载 | 第三验证+补充指标 | 最近 N 天 |

质量保障：
1. **缺口补全**：合并数据 >10 天缺口 → 交易所数据填补
2. **异常修复**：偏差 >30% 的异常点 → 交易所数据替换
3. **交叉验证**：jin10 vs 交易所 偏差 <1%；jin10 vs 中证 偏差 <1%

## 看图方法

- 合计图当前线在 25%-75% 区间带内 → 杠杆正常
- 融资图 ≈ 合计图（融资占两融 95%+）；融券图反映做空情绪与政策影响（如 2023.10 融券新规后断崖下跌）
- 接近或超过历史峰值 → 杠杆过高，警惕
- 持续下行 → 杠杆收缩

## 独立使用中证数据脚本

```bash
# 只拉中证数据（含维持担保比例等补充指标），默认保存到 /Users/melo/doc-Dgf/rzrq/MMDD/csdata_rzrq.csv
<skill-dir>/.venv/bin/python <skill-dir>/scripts/fetch_csdata.py --days 10
```
