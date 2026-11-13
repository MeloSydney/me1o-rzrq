#!/usr/bin/env python3
"""
A股融资融券（两融）数据获取 + 交叉验证 + 杠杆趋势可视化

数据源:
  主源  - AKShare macro (jin10): 沪深全历史 2010-至今
  补全  - AKShare stock_margin_sse/szse (交易所): 填补缺口 + 修复异常
  验证  - 主源 vs 交易所 交叉对比

用法:
  python fetch_rzrq.py                     # 默认 2015-至今, 输出到 /Users/melo/doc-Dgf/rzrq/MMDD (当天月日)
  python fetch_rzrq.py --start 2020-01-01  # 自定义起始
  python fetch_rzrq.py --output ~/Desktop  # 自定义输出目录
  python fetch_rzrq.py --no-validate       # 跳过交叉验证
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# ── 简单指标计算 ──────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算融资动量、回撤、分位和指数变化率等简单指标（不做分类/预警）。"""
    required = {"date", "rzye_total"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"缺少字段: {', '.join(sorted(missing))}")

    out = df.copy().sort_values("date").reset_index(drop=True)
    out["date"] = pd.to_datetime(out["date"])
    financing = pd.to_numeric(out["rzye_total"], errors="coerce")

    for days in (5, 10, 20):
        out[f"financing_change_{days}d_pct"] = financing.pct_change(days) * 100

    high60 = financing.rolling(60, min_periods=20).max()
    out["financing_drawdown_60d_pct"] = (financing / high60 - 1) * 100
    out["financing_percentile_252d"] = financing.rolling(
        252, min_periods=60
    ).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)

    if "index_close" in out.columns:
        index = pd.to_numeric(out["index_close"], errors="coerce")
        out["index_change_5d_pct"] = index.pct_change(5) * 100
        out["index_change_20d_pct"] = index.pct_change(20) * 100
    else:
        out["index_change_5d_pct"] = float("nan")
        out["index_change_20d_pct"] = float("nan")

    return out


# ── 工具函数 ──────────────────────────────────────────────

def _find_gaps(dates: pd.Series, min_gap_days: int = 10) -> list[tuple[str, str]]:
    """检测日期序列中 > min_gap_days 的缺口"""
    dates = pd.to_datetime(dates).sort_values().reset_index(drop=True)
    gaps = []
    for i in range(1, len(dates)):
        delta = (dates[i] - dates[i - 1]).days
        if delta > min_gap_days:
            gaps.append((
                (dates[i - 1] + timedelta(days=1)).strftime("%Y-%m-%d"),
                (dates[i] - timedelta(days=1)).strftime("%Y-%m-%d"),
            ))
    return gaps


def _find_anomalies(df: pd.DataFrame, col: str, window: int = 5,
                    threshold: float = 0.3) -> list[int]:
    """检测 col 列中相对前后 window 天均值偏差 > threshold 的异常行索引"""
    s = df[col].astype(float)
    roll_mean = s.rolling(window * 2 + 1, center=True, min_periods=3).mean()
    ratio = (s - roll_mean).abs() / roll_mean.replace(0, float("nan"))
    return df.index[ratio > threshold].tolist()


# ── 数据采集 ──────────────────────────────────────────────

def fetch_index(symbol: str = "sh000001", start: str | None = None,
                end: str | None = None, output_col: str = "index_close",
                display_name: str = "上证指数") -> pd.DataFrame:
    """指数日线 (新浪源, 全历史, 无需 token); 返回 [date, output_col]"""
    import akshare as ak
    try:
        df = ak.stock_zh_index_daily(symbol=symbol)
        df["date"] = pd.to_datetime(df["date"])
        df = df[["date", "close"]].rename(columns={"close": output_col})
        if start:
            df = df[df["date"] >= pd.to_datetime(start)]
        if end:
            df = df[df["date"] <= pd.to_datetime(end)]
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        print(f"⚠️  {display_name}获取失败: {e}")
        return pd.DataFrame(columns=["date", output_col])


def print_correlation(df: pd.DataFrame) -> None:
    """打印融资余额与上证指数、科创50的相关性 (水平值 + 日变动)。"""
    targets = [("index_close", "上证指数"), ("star50_close", "科创50")]
    for col, label in targets:
        if col not in df.columns:
            continue
        sub = df[["rzye_total", col]].dropna()
        if len(sub) < 30:
            continue
        r_level = sub["rzye_total"].corr(sub[col])
        r_diff = sub["rzye_total"].diff().corr(sub[col].diff())
        print(f"\n🔗 融资余额 vs {label} 相关性 (近 {len(sub)} 天):")
        print(f"   水平值相关系数  = {r_level:+.3f}  (长期趋势共线性, 参考)")
        print(f"   日变动相关系数  = {r_diff:+.3f}  (同涨同跌同步性, 更关键)")
        if r_diff > 0.5:
            print(f"   解读: 融资与{label}高度同向, 杠杆资金追涨/杀跌明显")
        elif r_diff > 0.2:
            print(f"   解读: 融资与{label}中度同向")
        else:
            print(f"   解读: 融资与{label}短期同步性弱")


def fetch_jin10() -> tuple[pd.DataFrame, pd.DataFrame]:
    """主源: jin10 沪深两融全历史"""
    import akshare as ak

    print("📡 [主源] 拉取沪市两融 (jin10)...")
    sh = ak.macro_china_market_margin_sh()
    print("📡 [主源] 拉取深市两融 (jin10)...")
    sz = ak.macro_china_market_margin_sz()

    for d in (sh, sz):
        d["日期"] = pd.to_datetime(d["日期"])

    print(f"✅ [主源] 沪市 {len(sh)} 条 ({sh['日期'].min().date()} ~ {sh['日期'].max().date()})")
    print(f"✅ [主源] 深市 {len(sz)} 条 ({sz['日期'].min().date()} ~ {sz['日期'].max().date()})")
    return sh, sz


def fetch_sse_range(start: str, end: str, attempts: int = 2,
                    delay: float = 1.5) -> pd.DataFrame | None:
    """交易所沪市 (支持日期范围), 带重试"""
    import akshare as ak
    import time
    s, e = start.replace("-", ""), end.replace("-", "")
    for i in range(attempts):
        try:
            df = ak.stock_margin_sse(start_date=s, end_date=e)
            if df.empty:
                return None
            df["信用交易日期"] = pd.to_datetime(df["信用交易日期"], format="%Y%m%d")
            return df
        except Exception:
            if i < attempts - 1:
                time.sleep(delay * (i + 1))
    return None


def fetch_szse_single(date_str: str, attempts: int = 1,
                      delay: float = 1.5) -> dict | None:
    """交易所深市 (单日, 单位: 亿元), 带重试; 默认 attempts=1 以免批量调用卡死"""
    import akshare as ak
    import time
    for i in range(attempts):
        try:
            df = ak.stock_margin_szse(date=date_str)
            if df.empty:
                return None
            row = df.iloc[0]
            return {
                "融资余额": float(row.get("融资余额", 0)),
                "融资融券余额": float(row.get("融资融券余额", 0)),
            }
        except Exception:
            if i < attempts - 1:
                time.sleep(delay * (i + 1))
    return None


def _fetch_exchange_for_date(d: pd.Timestamp) -> dict | None:
    """用交易所数据获取某天的沪深两融"""
    ds = d.strftime("%Y%m%d")
    sh_data = None
    sz_data = None

    # 沪市: 单日范围查询
    sse = fetch_sse_range(d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d"))
    if sse is not None and not sse.empty:
        r = sse.iloc[0]
        sh_data = {"rzye_sh": r["融资余额"], "rzrqye_sh": r["融资融券余额"]}

    # 深市
    sz_data = fetch_szse_single(ds)

    if sh_data and sz_data:
        return {
            "date": d,
            "rzye_sh": sh_data["rzye_sh"],
            "rzye_sz": sz_data["融资余额"] * 1e8,
            "rzrqye_sh": sh_data["rzrqye_sh"],
            "rzrqye_sz": sz_data["融资融券余额"] * 1e8,
        }
    return None


def fix_merged(df: pd.DataFrame) -> pd.DataFrame:
    """在合并后数据上: 1) 填补缺口  2) 修复异常值"""
    # ── 1) 填补缺口 ──
    gaps = _find_gaps(df["date"])
    if gaps:
        # 先探测深市接口可用性, 避免接口宕机时逐日重试卡死
        _probe = fetch_szse_single(df["date"].max().strftime("%Y%m%d"), attempts=2)
        if _probe is None:
            miss = ", ".join(f"{a}~{b}" for a, b in gaps)
            print(f"⚠️  [补全] 深市交易所接口当前不可用, 跳过缺口补全 (重跑可恢复)")
            print(f"      缺失段: {miss}")
            gaps = []
        else:
            print(f"🔧 [补全] 发现 {len(gaps)} 个缺口...")
        patches = []
        for gs, ge in gaps:
            rows = []
            sse = fetch_sse_range(gs, ge)
            sse_map = {}
            if sse is not None:
                for _, r in sse.iterrows():
                    sse_map[r["信用交易日期"]] = {
                        "rzye_sh": r["融资余额"], "rzrqye_sh": r["融资融券余额"]}
            from pandas import bdate_range
            for d in bdate_range(gs, ge):
                sh_d = sse_map.get(d)
                sz_d = fetch_szse_single(d.strftime("%Y%m%d"))
                if sh_d and sz_d:
                    rows.append({
                        "date": d,
                        "rzye_sh": sh_d["rzye_sh"],
                        "rzye_sz": sz_d["融资余额"] * 1e8,
                        "rzrqye_sh": sh_d["rzrqye_sh"],
                        "rzrqye_sz": sz_d["融资融券余额"] * 1e8,
                    })
            if rows:
                p = pd.DataFrame(rows)
                p["rzye_total"] = p["rzye_sh"] + p["rzye_sz"]
                p["rzrqye_total"] = p["rzrqye_sh"] + p["rzrqye_sz"]
                patches.append(p)
                print(f"   ✅ {gs} ~ {ge}: +{len(p)} 条")
            else:
                print(f"   ⚠️  {gs} ~ {ge}: 无法补全 (节假日?)")
        if patches:
            df = pd.concat([df, *patches], ignore_index=True)
            df = df.drop_duplicates(subset="date", keep="first")
            df = df.sort_values("date").reset_index(drop=True)

    # ── 2) 修复异常值 (本地线性插值优先, 不依赖外部接口) ──
    anomalies = _find_anomalies(df, "rzrqye_total")
    if anomalies:
        print(f"🔧 [修复] 发现 {len(anomalies)} 个异常数据点, 用前后数据线性插值...")
        num_cols = [c for c in ["rzye_sh", "rzye_sz", "rzye_total",
                                "rzrqye_sh", "rzrqye_sz", "rzrqye_total"]
                    if c in df.columns]
        mask = df.index.isin(anomalies)
        before = df.loc[mask, "rzrqye_total"].copy()
        df.loc[mask, num_cols] = float("nan")
        df[num_cols] = df[num_cols].interpolate(method="linear")
        for idx in anomalies:
            d = df.loc[idx, "date"]
            if pd.notna(df.loc[idx, "rzrqye_total"]):
                print(f"   ✅ {d.date()}: 插值 {before.loc[idx]/1e12:.2f} → "
                      f"{df.loc[idx, 'rzrqye_total']/1e12:.2f} 万亿")
            else:  # 边界极端情况, 插值无效, 交易所兜底
                rep = _fetch_exchange_for_date(d)
                if rep:
                    rep["rzye_total"] = rep["rzye_sh"] + rep["rzye_sz"]
                    rep["rzrqye_total"] = rep["rzrqye_sh"] + rep["rzrqye_sz"]
                    for col in rep:
                        df.loc[idx, col] = rep[col]
                    print(f"   ✅ {d.date()}: 交易所兜底修复")
                else:
                    print(f"   ⚠️  {d.date()}: 插值与交易所均失败")

    df["rqye_total"] = df["rzrqye_total"] - df["rzye_total"]
    return df


# ── csdata 尾部补全 ────────────────────────────────────────

def fill_tail_with_csdata(df: pd.DataFrame,
                           csdata_df: pd.DataFrame | None) -> pd.DataFrame:
    """主源 (jin10) 有 1-3 日延迟时，用中证数据补全末尾缺失的交易日。

    中证数据只有两市合计，无沪/深拆分；补入行的 per-market 列为 NaN。
    """
    if csdata_df is None or csdata_df.empty:
        return df

    cs = csdata_df.copy()
    cs["date"] = pd.to_datetime(cs["date"])
    main_max = pd.to_datetime(df["date"]).max()

    # 找出中证数据中比主源最新日期还晚的交易日
    missing = cs[cs["date"] > main_max].sort_values("date")
    if missing.empty:
        return df

    rows = []
    for _, cs_row in missing.iterrows():
        rows.append({
            "date": cs_row["date"],
            "rzye_sh": float("nan"),
            "rzye_sz": float("nan"),
            "rzye_total": float(cs_row["rzye"]) * 1e8,
            "rqye_total": float(cs_row["rqye"]) * 1e8,
            "rzrqye_sh": float("nan"),
            "rzrqye_sz": float("nan"),
            "rzrqye_total": float(cs_row["rzrqye"]) * 1e8,
        })

    patch = pd.DataFrame(rows)
    df = pd.concat([df, patch], ignore_index=True)
    df = df.sort_values("date").reset_index(drop=True)

    print(f"🔧 [csdata补全] 主源缺失末尾 {len(rows)} 个交易日, 已用中证数据补入:")
    for r in rows:
        print(f"   ✅ {r['date'].date()}: 融资余额={r['rzye_total']/1e8:.2f}亿 "
              f"两融余额={r['rzrqye_total']/1e8:.2f}亿")
    return df


# ── 交叉验证 ──────────────────────────────────────────────

def cross_validate(jin10_sh: pd.DataFrame, start: str, end: str,
                   tolerance: float = 0.01) -> bool:
    """对比 jin10 与交易所的沪市融资融券余额"""
    sse = fetch_sse_range(start, end)
    if sse is None:
        print("⚠️  验证源无数据, 跳过验证")
        return True

    a = jin10_sh[["日期", "融资融券余额"]].rename(
        columns={"日期": "date", "融资融券余额": "jin10"})
    b = sse[["信用交易日期", "融资融券余额"]].rename(
        columns={"信用交易日期": "date", "融资融券余额": "exchange"})

    merged = pd.merge(a, b, on="date", how="inner")
    if merged.empty:
        print("⚠️  两源无重叠日期, 跳过验证")
        return True

    merged["diff_pct"] = (merged["jin10"] - merged["exchange"]).abs() / merged["exchange"]
    bad = merged[merged["diff_pct"] > tolerance]

    if bad.empty:
        print(f"✅ 交叉验证通过 ({len(merged)} 天, 偏差 < {tolerance:.0%})")
        return True

    print(f"⚠️  {len(bad)}/{len(merged)} 天偏差 > {tolerance:.0%}:")
    for _, r in bad.head(5).iterrows():
        print(f"   {r['date'].date()}  jin10={r['jin10']:.0f}  交易所={r['exchange']:.0f}  Δ={r['diff_pct']:.2%}")
    return False


# ── 合并 ─────────────────────────────────────────────────

def merge_total(sh: pd.DataFrame, sz: pd.DataFrame,
                start: str, end: str) -> pd.DataFrame:
    """沪深合并, 计算全市场两融余额"""
    a = sh[["日期", "融资余额", "融资融券余额"]].rename(
        columns={"日期": "date", "融资余额": "rzye_sh", "融资融券余额": "rzrqye_sh"})
    b = sz[["日期", "融资余额", "融资融券余额"]].rename(
        columns={"日期": "date", "融资余额": "rzye_sz", "融资融券余额": "rzrqye_sz"})

    df = pd.merge(a, b, on="date", how="inner").sort_values("date")
    df["rzye_total"] = df["rzye_sh"] + df["rzye_sz"]
    df["rzrqye_total"] = df["rzrqye_sh"] + df["rzrqye_sz"]
    df["rqye_total"] = df["rzrqye_total"] - df["rzye_total"]

    mask = (df["date"] >= start) & (df["date"] <= end)
    df = df.loc[mask].reset_index(drop=True)
    return df


# ── 可视化 ────────────────────────────────────────────────



def plot(df: pd.DataFrame, output_dir: Path) -> Path:
    """最近一年四子图交互式 HTML: 两融合计 / 融资余额 / 科创50 / 上证指数。

    融资与指数拆成上下对齐的独立子图，避免双 Y 轴自动缩放造成视觉误读。
    鼠标悬停某日期时，沿共享 X 轴显示各子图的精确数值。
    自包含单文件：内嵌 plotly.js，离线可用。不绘制融券子图与预警散点。
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    latest_date = pd.to_datetime(df["date"]).max()
    chart_start = latest_date - pd.DateOffset(years=1)
    chart_df = df[pd.to_datetime(df["date"]) >= chart_start].copy()
    chart_df = chart_df.sort_values("date").reset_index(drop=True)
    chart_df["date"] = pd.to_datetime(chart_df["date"])
    dates = chart_df["date"]
    y_total = chart_df["rzrqye_total"] / 1e8   # 亿
    y_rzye = chart_df["rzye_total"] / 1e8      # 亿
    x0, x1 = chart_start, latest_date
    _pad = (x1 - x0) * 0.02
    ax0, ax1 = x0 - _pad, x1 + _pad  # 轴左右留缓冲，改善边缘悬停体验

    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True,
        vertical_spacing=0.065,
        row_heights=[0.30, 0.24, 0.22, 0.24],
        subplot_titles=("两融合计", "融资余额", "科创50", "上证指数"),
    )

    # ── 子图1: 两融合计 ──
    p25, p75 = float(y_total.quantile(0.25)), float(y_total.quantile(0.75))
    fig.add_trace(
        go.Scatter(x=dates, y=y_total, mode="lines", name="两融余额",
                   line=dict(color="#e74c3c", width=1.4),
                   hovertemplate="两融余额 %{y:,.0f} 亿<extra></extra>"),
        row=1, col=1, secondary_y=False)
    # 25%–75% 区间带
    fig.add_shape(type="rect", x0=ax0, x1=ax1, y0=p25, y1=p75,
                  fillcolor="rgba(52,152,219,0.10)", line_width=0, layer="below",
                  row=1, col=1)
    fig.add_annotation(x=x1, y=p75, text=f"25%–75% ({p25:,.0f}–{p75:,.0f} 亿)",
                       xanchor="right", yanchor="bottom", showarrow=False,
                       font=dict(size=10, color="#3498db"), row=1, col=1)
    # 峰值水平虚线 + 标注
    peak_val, peak_idx = float(y_total.max()), int(y_total.idxmax())
    peak_date = dates.iloc[peak_idx]
    fig.add_shape(type="line", x0=ax0, x1=ax1, y0=peak_val, y1=peak_val,
                  line=dict(color="gray", width=1, dash="dash"), layer="below",
                  row=1, col=1)
    fig.add_annotation(x=peak_date, y=peak_val,
                       text=f"峰值 {peak_val:,.0f} 亿 ({peak_date:%Y-%m-%d})",
                       showarrow=True, arrowhead=2, arrowsize=0.6,
                       arrowcolor="gray", font=dict(size=11, color="gray"),
                       row=1, col=1)
    # 当前点
    cur_val = float(y_total.iloc[-1])
    fig.add_trace(go.Scatter(x=[dates.iloc[-1]], y=[cur_val], mode="markers",
                             name="当前", marker=dict(color="#e74c3c", size=8),
                             hovertemplate=f"当前 {cur_val:,.0f} 亿<extra></extra>",
                             showlegend=False),
                  row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="两融余额（亿元）", tickformat=",.0f", row=1, col=1)

    # ── 子图2: 融资余额；子图3/4: 两个指数（独立上下子图）──
    def add_series(row: int, values: pd.Series, name: str, color: str,
                   axis_title: str, hovertemplate: str, tickformat: str | None = None) -> None:
        """在独立子图绘制一条序列及其当前点。"""
        valid = values.notna()
        if not valid.any():
            return
        fig.add_trace(
            go.Scatter(x=dates[valid], y=values[valid], mode="lines", name=name,
                       line=dict(color=color, width=1.4),
                       hovertemplate=hovertemplate),
            row=row, col=1)
        last_idx = values[valid].index[-1]
        fig.add_trace(
            go.Scatter(x=[dates.loc[last_idx]], y=[float(values.loc[last_idx])],
                       mode="markers", marker=dict(color=color, size=7),
                       hovertemplate=hovertemplate, showlegend=False),
            row=row, col=1)
        fig.update_yaxes(title_text=axis_title, color=color, tickformat=tickformat, row=row, col=1)

    add_series(2, y_rzye, "融资余额", "#2980b9",
               "融资余额（亿元）", "融资余额 %{y:,.0f} 亿<extra></extra>", tickformat=",.0f")
    if "star50_close" in chart_df.columns:
        add_series(3, chart_df["star50_close"], "科创50", "#e67e22",
                   "科创50（点）", "科创50 %{y:.0f} 点<extra></extra>")
    if "index_close" in chart_df.columns:
        add_series(4, chart_df["index_close"], "上证指数", "#8e44ad",
                   "上证指数（点）", "上证指数 %{y:.0f} 点<extra></extra>")

    # ── 布局：最近一年逐月 X 轴，共享日期悬停；边缘悬停优化 ──
    fig.update_layout(
        title_text="A 股杠杆水平：融资余额与科创50、上证指数分图对照",
        title_font_size=15, hovermode="x unified", hoversubplots="axis", height=980,
        hoverdistance=-1, spikedistance=-1,
        legend=dict(orientation="h", xanchor="left", yanchor="bottom"),
        margin=dict(l=70, r=35, t=80, b=40), plot_bgcolor="white",
    )
    fig.update_xaxes(range=[ax0, ax1], dtick="M1", tickformat="%Y-%m", tickangle=-60,
                     spikemode="across", spikesnap="cursor", spikethickness=1,
                     spikedash="dot", spikecolor="rgba(0,0,0,0.35)")
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="rgba(0,0,0,0.08)")

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "rzrq_leverage.html"
    # 悬停联动:原生 hoversubplots 只把十字线带到其余子图、数值仍只出现在光标所在子图。
    # 这里用 plotly_hover 事件,在四个子图上各画一个数值标注,实现"悬停一处、四图同显该日数值"。
    _hover_sync_js = r"""
(function () {
  var gd = document.querySelector('.js-plotly-plot');
  if (!gd || !gd.on) return;
  var staticAnn = (gd.layout && gd.layout.annotations ? gd.layout.annotations : []).slice();
  var lastIdx = -1;

  function lineTraces() {
    var out = [];
    for (var t = 0; t < gd.data.length; t++) {
      var f = gd._fullData[t];
      if (f && f.mode && f.mode.indexOf('lines') >= 0) out.push(t);
    }
    return out;
  }
  function fmt(v) { return Math.round(v).toLocaleString('en-US'); }

  function build(idx, skipYref) {
    var anns = staticAnn.slice();
    var lts = lineTraces();
    for (var k = 0; k < lts.length; k++) {
      var ti = lts[k];
      var fd = gd._fullData[ti];
      if (skipYref && fd.yaxis === skipYref) continue; // 光标所在子图已由原生 tooltip 显示
      var yv = fd.y ? fd.y[idx] : undefined;
      if (yv == null || isNaN(yv)) continue;
      var unit = ((fd.hovertemplate || '').indexOf('\u4ebf') >= 0) ? ' \u4ebf' : ' \u70b9';
      anns.push({
        x: fd.x[idx], y: yv, xref: fd.xaxis, yref: fd.yaxis,
        text: (fd.name || '') + ' ' + fmt(yv) + unit,
        showarrow: false, xanchor: 'left', yanchor: 'bottom', xshift: 8,
        font: { size: 11, color: (fd.line && fd.line.color) || '#333' },
        bgcolor: 'rgba(255,255,255,0.9)', bordercolor: 'rgba(0,0,0,0.18)',
        borderwidth: 1, borderpad: 3
      });
    }
    return anns;
  }

  gd.on('plotly_hover', function (ev) {
    if (!ev || !ev.points || !ev.points.length) return;
    var dateStr = ev.points[0].x;
    var lts = lineTraces();
    if (!lts.length) return;
    var xs = gd._fullData[lts[0]].x;
    var tgt = new Date(dateStr).getTime(), idx = 0, bd = Infinity;
    for (var i = 0; i < xs.length; i++) {
      var d = Math.abs(new Date(xs[i]).getTime() - tgt);
      if (d < bd) { bd = d; idx = i; }
    }
    if (idx === lastIdx) return;
    lastIdx = idx;
    var skipYref = gd._fullData[ev.points[0].curveNumber] ? gd._fullData[ev.points[0].curveNumber].yaxis : null;
    Plotly.relayout(gd, { annotations: build(idx, skipYref) });
  });

  gd.on('plotly_unhover', function () {
    lastIdx = -1;
    Plotly.relayout(gd, { annotations: staticAnn });
  });
})();
"""
    fig.write_html(str(path), include_plotlyjs=True, full_html=True, post_script=_hover_sync_js)
    print(f"📊 交互图表 → {path}")
    return path



# ── 数据落盘 ──────────────────────────────────────────────

def save_csv(df: pd.DataFrame, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    wanted = ["date", "rzye_sh", "rzye_sz", "rzye_total", "rqye_total",
              "rzrqye_sh", "rzrqye_sz", "rzrqye_total", "index_close",
              "star50_close",
              "financing_change_5d_pct", "financing_change_10d_pct",
              "financing_change_20d_pct", "financing_drawdown_60d_pct",
              "financing_percentile_252d", "index_change_5d_pct",
              "index_change_20d_pct"]
    out = df[[col for col in wanted if col in df.columns]].copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    index_cols = ("index_close", "star50_close")
    pct_cols = ("financing_change_5d_pct", "financing_change_10d_pct",
                "financing_change_20d_pct", "financing_drawdown_60d_pct",
                "financing_percentile_252d", "index_change_5d_pct",
                "index_change_20d_pct")
    yuan_cols = [col for col in out.columns
                 if col not in ("date", *index_cols, *pct_cols)]
    out[yuan_cols] = (out[yuan_cols] / 1e8).round(2)
    for col in index_cols:
        if col in out.columns:
            out[col] = out[col].round(2)
    for col in pct_cols:
        if col in out.columns:
            out[col] = out[col].round(4)
    hdr = {"date": "日期", "rzye_sh": "沪市融资余额(亿)", "rzye_sz": "深市融资余额(亿)",
           "rzye_total": "两市融资余额(亿)", "rqye_total": "两市融券余额(亿)",
           "rzrqye_sh": "沪市两融余额(亿)", "rzrqye_sz": "深市两融余额(亿)",
           "rzrqye_total": "两市两融余额(亿)", "index_close": "上证指数(点)",
           "star50_close": "科创50指数(点)",
           "financing_change_5d_pct": "融资5日变化率(%)",
           "financing_change_10d_pct": "融资10日变化率(%)",
           "financing_change_20d_pct": "融资20日变化率(%)",
           "financing_drawdown_60d_pct": "融资60日回撤(%)",
           "financing_percentile_252d": "融资252日分位",
           "index_change_5d_pct": "上证5日变化率(%)",
           "index_change_20d_pct": "上证20日变化率(%)"}
    out.columns = [hdr[col] for col in out.columns]
    path = output_dir / "rzrq_data.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"💾 数据 → {path}")
    return path


# ── 主流程 ────────────────────────────────────────────────

# ── 中证数据验证 (追加到 main 流程) ──────────────────────

def verify_with_csdata(df: pd.DataFrame, days: int = 5,
                       output_dir: Path | None = None) -> pd.DataFrame | None:
    """用中证数据做第三源验证 + 补充指标"""
    try:
        # 动态导入同目录下的 fetch_csdata
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fetch_csdata",
            str(Path(__file__).parent / "fetch_csdata.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        cs = mod.fetch_csdata(days)
        if cs.empty:
            return None

        # 对比两融余额 (中证 vs 主源)
        main_sub = df[df["date"].isin(pd.to_datetime(cs["date"]))][["date", "rzrqye_total"]].copy()
        main_sub["date_str"] = main_sub["date"].dt.strftime("%Y-%m-%d")
        cs_cmp = cs[["date", "rzrqye"]].rename(columns={"rzrqye": "cs_rzrqye"})
        # 中证 rzrqye 单位是亿，主源是元
        cs_cmp["cs_rzrqye_yuan"] = cs_cmp["cs_rzrqye"] * 1e8

        merged = main_sub.merge(cs_cmp, left_on="date_str", right_on="date", how="inner", suffixes=("", "_cs"))
        if not merged.empty:
            merged["diff_pct"] = (merged["rzrqye_total"] - merged["cs_rzrqye_yuan"]).abs() / merged["cs_rzrqye_yuan"]
            max_diff = merged["diff_pct"].max()
            if max_diff < 0.01:
                print(f"✅ 中证数据验证通过 ({len(merged)} 天, 最大偏差 {max_diff:.2%})")
            else:
                print(f"⚠️  中证数据偏差较大 (最大 {max_diff:.2%}), 请检查")

        # 输出补充指标
        print(f"\n📊 中证数据补充指标 (最近 {len(cs)} 天):")
        display_cols = ["date", "rzrq_ratio", "avg_maintain_ratio", "rzrq_trade_ratio"]
        available = [c for c in display_cols if c in cs.columns]
        display_df = cs[available].copy()
        if "rzrq_ratio" in display_df.columns:
            display_df["rzrq_ratio"] = (display_df["rzrq_ratio"] * 100).round(2).astype(str) + "%"
        if "rzrq_trade_ratio" in display_df.columns:
            display_df["rzrq_trade_ratio"] = (display_df["rzrq_trade_ratio"] * 100).round(2).astype(str) + "%"
        if "avg_maintain_ratio" in display_df.columns:
            display_df["avg_maintain_ratio"] = display_df["avg_maintain_ratio"].round(2)
        display_df.columns = [c.replace("rzrq_ratio", "两融/流通市值")
                                .replace("avg_maintain_ratio", "维持担保比例")
                                .replace("rzrq_trade_ratio", "两融/交易额")
                                .replace("date", "日期")
                                for c in display_df.columns]
        print(display_df.to_string(index=False))

        # 杠杆判断
        latest_ratio = cs["rzrq_ratio"].iloc[-1]
        latest_maintain = cs["avg_maintain_ratio"].iloc[-1]
        print(f"\n📋 杠杆判断:")
        print(f"   两融/流通市值 = {latest_ratio*100:.2f}%", end="")
        if latest_ratio > 0.035:
            print("  🔴 偏高 (>3.5%)")
        elif latest_ratio > 0.025:
            print("  🟡 中等 (2.5%-3.5%)")
        else:
            print("  🟢 正常 (<2.5%)")

        print(f"   维持担保比例 = {latest_maintain:.2f}", end="")
        if latest_maintain < 2.0:
            print("  🔴 偏低 (<2.0, 爆仓风险)")
        elif latest_maintain < 2.5:
            print("  🟡 中等 (2.0-2.5)")
        else:
            print("  🟢 安全 (>2.5)")

        # 中证数据落盘到本次输出目录 (MMDD)
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            cs_path = output_dir / "csdata_rzrq.csv"
            cs.to_csv(cs_path, index=False, encoding="utf-8-sig")
            print(f"💾 中证数据已保存 → {cs_path}")

        return cs
    except Exception as e:
        print(f"⚠️  中证数据验证跳过: {e}")
        return None


def main():
    ap = argparse.ArgumentParser(description="A股两融数据获取与杠杆趋势可视化")
    ap.add_argument("--start", default="2015-01-01", help="起始日期 YYYY-MM-DD")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="结束日期")
    _default_out = f"/Users/melo/doc-Dgf/rzrq/{datetime.now().strftime('%m%d')}"
    ap.add_argument("--output", default=_default_out,
                    help=f"输出目录 (默认按当天日期: {_default_out})")
    ap.add_argument("--no-validate", action="store_true", help="跳过交叉验证")
    ap.add_argument("--no-fix", action="store_true", help="跳过缺口补全和异常修复")
    args = ap.parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1) 主源
    sh, sz = fetch_jin10()

    # 2) 合并
    df = merge_total(sh, sz, args.start, args.end)
    if df.empty:
        print("❌ 无数据"); sys.exit(1)
    print(f"📦 合并后 {len(df)} 个交易日 ({df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()})")

    # 3) 补全 + 修复
    if not args.no_fix:
        df = fix_merged(df)

    # 4) 交叉验证
    if not args.no_validate:
        try:
            cross_validate(sh, args.start, args.end)
        except Exception as e:
            print(f"⚠️  验证源失败 (不影响主数据): {e}")

    # 5) 中证数据验证 (第三源 + 补充指标)
    csdata_df = None
    if not args.no_validate:
        csdata_df = verify_with_csdata(df, days=10, output_dir=output_dir)

    # 5.5) 用中证数据补全主源末尾缺失的交易日 (jin10 通常延迟 1-3 日)
    # live fetch 失败时回退到最近的 csdata_rzrq.csv 缓存
    if csdata_df is None and not args.no_validate:
        cached = output_dir / "csdata_rzrq.csv"
        if not cached.is_file():
            # 搜索同级目录下最近的 csdata 缓存
            parent = output_dir.parent
            candidates = sorted(parent.glob("*/csdata_rzrq.csv"),
                                key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                cached = candidates[0]
        if cached.is_file():
            print(f"⚠️  中证 live fetch 失败, 回退缓存: {cached}")
            csdata_df = pd.read_csv(cached, encoding="utf-8-sig")

    if csdata_df is not None:
        df = fill_tail_with_csdata(df, csdata_df)

    # 6) 合并上证指数与科创50指数
    idx = fetch_index("sh000001", args.start, args.end,
                      output_col="index_close", display_name="上证指数")
    if not idx.empty:
        df = df.merge(idx, on="date", how="left")
        print(f"📈 上证指数已合并 ({idx['date'].min().date()} ~ {idx['date'].max().date()})")
    else:
        print("⚠️  上证指数缺失, 跳过")

    star50 = fetch_index("sh000688", args.start, args.end,
                         output_col="star50_close", display_name="科创50")
    if not star50.empty:
        df = df.merge(star50, on="date", how="left")
        print(f"📈 科创50已合并 ({star50['date'].min().date()} ~ {star50['date'].max().date()})")
    else:
        print("⚠️  科创50缺失, 跳过")

    # 7) 计算简单指标，落盘并画图
    df = add_indicators(df)
    save_csv(df, output_dir)
    plot(df, output_dir)
    print_correlation(df)

    # 8) 摘要
    cur = df["rzrqye_total"].iloc[-1] / 1e12
    peak = df["rzrqye_total"].max() / 1e12
    pct_rank = (df["rzrqye_total"] <= df["rzrqye_total"].iloc[-1]).mean() * 100
    print(f"\n📋 当前两融 {cur:.2f} 万亿 | 区间峰值 {peak:.2f} 万亿 | 历史分位 {pct_rank:.0f}%")


if __name__ == "__main__":
    main()
