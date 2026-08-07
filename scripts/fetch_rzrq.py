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
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


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
                end: str | None = None) -> pd.DataFrame:
    """大盘指数日线 (新浪源, 全历史, 无需 token); 返回 [date, index_close]"""
    import akshare as ak
    try:
        df = ak.stock_zh_index_daily(symbol=symbol)
        df["date"] = pd.to_datetime(df["date"])
        df = df[["date", "close"]].rename(columns={"close": "index_close"})
        if start:
            df = df[df["date"] >= pd.to_datetime(start)]
        if end:
            df = df[df["date"] <= pd.to_datetime(end)]
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        print(f"⚠️  上证指数获取失败: {e}")
        return pd.DataFrame(columns=["date", "index_close"])


def print_correlation(df: pd.DataFrame) -> None:
    """打印两融余额与上证指数的相关性 (水平值 + 日变动)"""
    if "index_close" not in df.columns:
        return
    sub = df[["rzrqye_total", "index_close"]].dropna()
    if len(sub) < 30:
        return
    r_level = sub["rzrqye_total"].corr(sub["index_close"])
    r_diff = sub["rzrqye_total"].diff().corr(sub["index_close"].diff())
    print(f"\n🔗 两融余额 vs 上证指数 相关性 (近 {len(sub)} 天):")
    print(f"   水平值相关系数  = {r_level:+.3f}  (长期趋势共线性, 参考)")
    print(f"   日变动相关系数  = {r_diff:+.3f}  (同涨同跌同步性, 更关键)")
    if r_diff > 0.5:
        print("   解读: 两融与大盘高度同向, 杠杆资金追涨/杀跌明显")
    elif r_diff > 0.2:
        print("   解读: 两融与大盘中度同向")
    else:
        print("   解读: 两融与大盘短期同步性弱, 杠杆资金相对独立")


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
    """四子图: 两融合计 / 融资 / 融券 / 上证指数; X 轴按月; 峰值精确到天"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False

    dates = df["date"]
    y_total = df["rzrqye_total"] / 1e12   # 万亿
    y_rzye = df["rzye_total"] / 1e12      # 万亿
    y_rqye = df["rqye_total"] / 1e8       # 亿
    has_idx = "index_close" in df.columns and df["index_close"].notna().any()

    n_panels = 4 if has_idx else 3
    fig, axes = plt.subplots(n_panels, 1, figsize=(14, 3.0 * n_panels), sharex=True)

    # ── 子图0: 两融合计 ──
    ax = axes[0]
    ax.plot(dates, y_total, color="#e74c3c", linewidth=1.1, label="两融余额")
    p25, p75 = y_total.quantile(0.25), y_total.quantile(0.75)
    ax.axhspan(p25, p75, color="#3498db", alpha=0.07,
               label=f"25%–75% 区间 ({p25:.2f}–{p75:.2f} 万亿)")
    peak_val, peak_idx = y_total.max(), y_total.idxmax()
    peak_date = dates[peak_idx]
    ax.axhline(peak_val, color="gray", ls="--", lw=0.8, alpha=0.6)
    ax.annotate(f"峰值 {peak_val:.2f} 万亿 ({peak_date:%Y-%m-%d})",
                xy=(peak_date, peak_val), xytext=(-8, 14), ha="right",
                textcoords="offset points", fontsize=9, color="gray",
                arrowprops=dict(arrowstyle="->", color="gray", lw=0.8))
    cur_val = y_total.iloc[-1]
    ax.scatter([dates.iloc[-1]], [cur_val], color="#e74c3c", zorder=5, s=45)
    ax.annotate(f"当前 {cur_val:.2f} 万亿",
                xy=(dates.iloc[-1], cur_val), xytext=(-90, -28),
                textcoords="offset points", fontsize=10, fontweight="bold",
                color="#e74c3c",
                arrowprops=dict(arrowstyle="->", color="#e74c3c", lw=1))
    ax.set_ylabel("两融余额（万亿元）", fontsize=10)
    ax.set_title("两融合计", fontsize=11, loc="left")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.25)

    # ── 子图1: 融资 ──
    ax = axes[1]
    ax.plot(dates, y_rzye, color="#2980b9", linewidth=1.0, label="融资余额")
    ax.scatter([dates.iloc[-1]], [y_rzye.iloc[-1]], color="#2980b9", zorder=5, s=30)
    ax.annotate(f"{y_rzye.iloc[-1]:.2f} 万亿",
                xy=(dates.iloc[-1], y_rzye.iloc[-1]), xytext=(-72, 10),
                textcoords="offset points", fontsize=9, color="#2980b9")
    ax.set_ylabel("融资余额（万亿元）", fontsize=10)
    ax.set_title("融资", fontsize=11, loc="left")
    ax.grid(True, alpha=0.25)

    # ── 子图2: 融券 ──
    ax = axes[2]
    ax.plot(dates, y_rqye, color="#27ae60", linewidth=1.0, label="融券余额")
    ax.scatter([dates.iloc[-1]], [y_rqye.iloc[-1]], color="#27ae60", zorder=5, s=30)
    ax.annotate(f"{y_rqye.iloc[-1]:.0f} 亿",
                xy=(dates.iloc[-1], y_rqye.iloc[-1]), xytext=(-58, 10),
                textcoords="offset points", fontsize=9, color="#27ae60")
    ax.set_ylabel("融券余额（亿元）", fontsize=10)
    ax.set_title("融券", fontsize=11, loc="left")
    ax.grid(True, alpha=0.25)

    # ── 子图3: 上证指数 (可选) ──
    if has_idx:
        ax = axes[3]
        y_idx = df["index_close"]
        ax.plot(dates, y_idx, color="#8e44ad", linewidth=1.0, label="上证指数")
        ax.scatter([dates.iloc[-1]], [y_idx.iloc[-1]], color="#8e44ad", zorder=5, s=30)
        ax.annotate(f"{y_idx.iloc[-1]:.0f} 点",
                    xy=(dates.iloc[-1], y_idx.iloc[-1]), xytext=(-62, 10),
                    textcoords="offset points", fontsize=9, color="#8e44ad")
        ax.set_ylabel("上证指数（点）", fontsize=10)
        ax.set_title("上证指数", fontsize=11, loc="left")
        ax.grid(True, alpha=0.25)

    # ── X 轴: 按月标记 ──
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    plt.setp(axes[-1].get_xticklabels(), rotation=45, ha="right", fontsize=8)

    fig.suptitle("A 股杠杆水平：两融 / 融资 / 融券 / 上证指数 走势",
                 fontsize=13, fontweight="bold", y=0.997)
    plt.tight_layout(rect=[0, 0, 1, 0.985])

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "rzrq_leverage.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"📊 图表 → {path}")
    return path



# ── 数据落盘 ──────────────────────────────────────────────

def save_csv(df: pd.DataFrame, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    wanted = ["date", "rzye_sh", "rzye_sz", "rzye_total", "rqye_total",
              "rzrqye_sh", "rzrqye_sz", "rzrqye_total", "index_close"]
    out = df[[col for col in wanted if col in df.columns]].copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    yuan_cols = [col for col in out.columns if col not in ("date", "index_close")]
    out[yuan_cols] = (out[yuan_cols] / 1e8).round(2)
    if "index_close" in out.columns:
        out["index_close"] = out["index_close"].round(2)
    hdr = {"date": "日期", "rzye_sh": "沪市融资余额(亿)", "rzye_sz": "深市融资余额(亿)",
           "rzye_total": "两市融资余额(亿)", "rqye_total": "两市融券余额(亿)",
           "rzrqye_sh": "沪市两融余额(亿)", "rzrqye_sz": "深市两融余额(亿)",
           "rzrqye_total": "两市两融余额(亿)", "index_close": "上证指数(点)"}
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
    if not args.no_validate:
        verify_with_csdata(df, days=5, output_dir=output_dir)

    # 6) 合并大盘指数
    idx = fetch_index("sh000001", args.start, args.end)
    if not idx.empty:
        df = df.merge(idx, on="date", how="left")
        print(f"📈 上证指数已合并 ({idx['date'].min().date()} ~ {idx['date'].max().date()})")
    else:
        print("⚠️  上证指数缺失, 跳过")

    # 7) 落盘 + 画图
    save_csv(df, output_dir)
    plot(df, output_dir)
    print_correlation(df)

    # 7) 摘要
    cur = df["rzrqye_total"].iloc[-1] / 1e12
    peak = df["rzrqye_total"].max() / 1e12
    pct_rank = (df["rzrqye_total"] <= df["rzrqye_total"].iloc[-1]).mean() * 100
    print(f"\n📋 当前两融 {cur:.2f} 万亿 | 区间峰值 {peak:.2f} 万亿 | 历史分位 {pct_rank:.0f}%")


if __name__ == "__main__":
    main()
