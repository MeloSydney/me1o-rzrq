#!/usr/bin/env python3
"""融资余额风险预警：以 2026 年 1—8 月上涨—去杠杆事件为模板。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


TEMPLATE_START = pd.Timestamp("2026-01-01")
TEMPLATE_RISE_WINDOW_END = pd.Timestamp("2026-07-31")
TEMPLATE_DECLINE_WINDOW_START = pd.Timestamp("2026-07-01")
TEMPLATE_END = pd.Timestamp("2026-08-31")

LEVEL_NAMES = {
    0: "🟢 正常",
    1: "🟡 杠杆拥挤观察",
    2: "🟠 顶部转弱预警",
    3: "🔴 去杠杆/暴跌预警",
}

CRASH_TEMPLATE_COLUMN_DESCRIPTIONS = {
    "date": "交易日期，格式 YYYY-MM-DD。",
    "rzye_total": "沪深两市融资余额合计，单位亿元；预警机制的核心存量指标。",
    "index_close": "当日上证指数收盘点位；2026 暴跌模板的市场压力基准。",
    "financing_change_5d_pct": "融资余额相对 5 个交易日前的变化率，单位百分比（%），用于观察短期加速流入或撤退。",
    "financing_change_10d_pct": "融资余额相对 10 个交易日前的变化率，单位百分比（%），用于观察约两周趋势。",
    "financing_change_20d_pct": "融资余额相对 20 个交易日前的变化率，单位百分比（%），用于观察约一个月趋势。",
    "financing_drawdown_60d_pct": "当前融资余额相对最近 60 个交易日最高值的回撤，单位百分比（%）；0 表示位于 60 日高点，负值越小表示离高点越远。",
    "financing_percentile_252d": "当前融资余额在最近 252 个交易日中的分位，范围 0–1；例如 0.95 表示高于约 95% 的观察值。",
    "index_change_5d_pct": "上证指数相对 5 个交易日前的变化率，单位百分比（%）。",
    "index_change_20d_pct": "上证指数相对 20 个交易日前的变化率，单位百分比（%）。",
    "financing_index_divergence_20d_pp": "20 日融资变化率减去 20 日上证指数变化率，单位百分点；正值表示融资强于指数，负值表示融资弱于指数。",
    "warning_code": "机器可读预警等级：0=正常，1=杠杆拥挤，2=顶部转弱，3=快速去杠杆/暴跌预警。",
    "warning_level": "带颜色图标的中文预警等级，便于人工阅读。",
    "warning_reasons": "当日触发预警的具体规则；未触发时显示“未触发模板信号”。",
    "template_phase": "该日在 2026 模板中的阶段：上涨蓄势、暴跌去杠杆、修复观察或弱势整理（后两者以模板窗口低点是否被后续数据跌破区分）。",
    "in_jan_jul_review_window": "是否属于 2026 年 1—7 月复盘窗口，布尔值 true/false。",
    "in_jul_aug_review_window": "是否属于 2026 年 7—8 月复盘窗口，布尔值 true/false；7 月与前一窗口重叠。",
}


def _classify_row(row: pd.Series) -> tuple[int, str]:
    """按 2026-07 去杠杆前后的形态分级，不把单一信号当作确定性预测。"""
    f5 = row.get("financing_change_5d_pct")
    f10 = row.get("financing_change_10d_pct")
    f20 = row.get("financing_change_20d_pct")
    i5 = row.get("index_change_5d_pct")
    i20 = row.get("index_change_20d_pct")
    dd60 = row.get("financing_drawdown_60d_pct")
    divergence = row.get("financing_index_divergence_20d_pp")
    pct252 = row.get("financing_percentile_252d")

    def ok(value) -> bool:
        return pd.notna(value)

    market_pressure = ((ok(i5) and i5 <= -2.5) or
                       (ok(i20) and i20 <= -5.0))
    severe_deleveraging = ((ok(f5) and f5 <= -2.5 and market_pressure) or
                           (ok(f10) and f10 <= -5.0) or
                           (ok(f20) and f20 <= -8.0))
    mild_deleveraging = ((ok(f5) and f5 <= -1.5) or
                         (ok(f10) and f10 <= -3.0))
    crowded = (ok(f20) and f20 >= 3.0 and
               ok(dd60) and dd60 >= -1.0 and
               ok(pct252) and pct252 >= 0.90)
    divergence_risk = (ok(f20) and f20 > 0 and
                       ok(divergence) and divergence >= 5.0 and
                       ok(dd60) and dd60 >= -3.0)
    top_reversal = (ok(f20) and f20 >= 2.0 and
                    ok(f5) and f5 < 0 and
                    (not ok(i5) or i5 < 0) and
                    ok(dd60) and dd60 >= -3.0)

    reasons: list[str] = []
    if crowded:
        reasons.append("融资20日快速上升且接近60日高位")
    if divergence_risk:
        reasons.append("融资强于指数≥5个百分点")
    if top_reversal:
        reasons.append("高位融资由升转降")
    if mild_deleveraging:
        reasons.append("融资余额短期明显收缩")
    if market_pressure:
        reasons.append("指数同步承压")
    if severe_deleveraging:
        reasons.append("已进入快速去杠杆形态")

    if severe_deleveraging:
        level = 3
    elif top_reversal or (mild_deleveraging and
                          (market_pressure or (ok(dd60) and dd60 <= -2.5))):
        level = 2
    elif crowded or divergence_risk or mild_deleveraging:
        level = 1
    else:
        level = 0
    return level, "；".join(reasons) if reasons else "未触发模板信号"


def add_warning_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """增加融资动量、回撤、融资-指数背离和四级预警字段。"""
    required = {"date", "rzye_total"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"融资预警缺少字段: {', '.join(sorted(missing))}")

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

    out["financing_index_divergence_20d_pp"] = (
        out["financing_change_20d_pct"] - out["index_change_20d_pct"]
    )
    classified = out.apply(_classify_row, axis=1)
    out["warning_code"] = [item[0] for item in classified]
    out["warning_level"] = out["warning_code"].map(LEVEL_NAMES)
    out["warning_reasons"] = [item[1] for item in classified]
    return out


def build_2026_crash_template(df: pd.DataFrame) -> tuple[pd.DataFrame, dict] | None:
    """提取 2026-01—08 数据，并以真实峰值/低点划分上涨、去杠杆和修复阶段。"""
    sub = df[(df["date"] >= TEMPLATE_START) & (df["date"] <= TEMPLATE_END)].copy()
    if sub.empty:
        return None

    rise_window = sub[sub["date"] <= TEMPLATE_RISE_WINDOW_END]
    if rise_window.empty:
        return None
    peak_idx = rise_window["rzye_total"].idxmax()
    peak_date = sub.loc[peak_idx, "date"]
    post_peak = sub[sub["date"] >= peak_date]
    trough_idx = post_peak["rzye_total"].idxmin()
    trough_date = sub.loc[trough_idx, "date"]

    sub["template_phase"] = "上涨蓄势"
    sub.loc[(sub["date"] > peak_date) & (sub["date"] <= trough_date),
            "template_phase"] = "暴跌去杠杆"
    sub.loc[sub["date"] > trough_date, "template_phase"] = "修复观察"
    sub["in_jan_jul_review_window"] = sub["date"] <= TEMPLATE_RISE_WINDOW_END
    sub["in_jul_aug_review_window"] = sub["date"] >= TEMPLATE_DECLINE_WINDOW_START

    start_row = sub.iloc[0]
    peak_row = sub.loc[peak_idx]
    trough_row = sub.loc[trough_idx]
    end_row = sub.iloc[-1]
    pct = lambda start, end: (float(end) / float(start) - 1) * 100

    # 模板窗口内的“低点”并不等于整轮去杠杆的低点：若窗口之后融资余额继续
    # 创新低，则窗口尾部不应计作“修复观察”，而属于长尾缓慢去杠杆的“弱势整理”。
    trough_value = float(trough_row["rzye_total"])
    after_template = df[df["date"] > TEMPLATE_END]
    post_low_date = None
    post_low_value = None
    trough_breached = False
    if not after_template.empty:
        post_low_idx = after_template["rzye_total"].idxmin()
        post_low_date = df.loc[post_low_idx, "date"]
        post_low_value = float(df.loc[post_low_idx, "rzye_total"])
        trough_breached = post_low_value < trough_value
    recovery_confirmed = (not trough_breached) and float(end_row["rzye_total"]) > trough_value
    if not recovery_confirmed:
        sub.loc[sub["date"] > trough_date, "template_phase"] = "弱势整理"

    summary = {
        "template": "2026-01至08融资上涨—暴跌模板",
        "data_start": start_row["date"].strftime("%Y-%m-%d"),
        "financing_peak_date": peak_date.strftime("%Y-%m-%d"),
        "financing_trough_date": trough_date.strftime("%Y-%m-%d"),
        "data_end": end_row["date"].strftime("%Y-%m-%d"),
        "start_financing_yi": round(float(start_row["rzye_total"]) / 1e8, 2),
        "peak_financing_yi": round(float(peak_row["rzye_total"]) / 1e8, 2),
        "trough_financing_yi": round(float(trough_row["rzye_total"]) / 1e8, 2),
        "end_financing_yi": round(float(end_row["rzye_total"]) / 1e8, 2),
        "rise_start_to_peak_pct": round(
            pct(start_row["rzye_total"], peak_row["rzye_total"]), 2),
        "decline_peak_to_trough_pct": round(
            pct(peak_row["rzye_total"], trough_row["rzye_total"]), 2),
        "index_rise_start_to_peak_pct": None,
        "index_decline_peak_to_trough_pct": None,
        "window_trough_breached_after_template": bool(trough_breached),
        "post_template_low_date": post_low_date.strftime("%Y-%m-%d") if post_low_date is not None else None,
        "post_template_low_financing_yi": round(post_low_value / 1e8, 2) if post_low_value is not None else None,
        "post_template_low_vs_window_trough_pct": round(
            pct(trough_value, post_low_value), 2) if post_low_value is not None else None,
        "tail_phase_label": "修复观察" if recovery_confirmed else "弱势整理",
        "note": (
            "单次历史事件模板，仅用于风险提示，不构成收益或暴跌预测。"
            + (
                "注意：模板窗口低点已被窗口之后数据跌破，尾部属长尾缓慢去杠杆（弱势整理），不应读作修复完成。"
                if trough_breached
                else ""
            )
        ),
    }
    if "index_close" in sub.columns and sub["index_close"].notna().any():
        summary["index_rise_start_to_peak_pct"] = round(
            pct(start_row["index_close"], peak_row["index_close"]), 2)
        summary["index_decline_peak_to_trough_pct"] = round(
            pct(peak_row["index_close"], trough_row["index_close"]), 2)
    return sub, summary


RZRQ_RATIO_LOW = 0.025
RZRQ_RATIO_HIGH = 0.035
MAINTAIN_RATIO_DANGER = 2.0
TRADE_RATIO_RETREAT_PP = 0.5
CSDATA_ADVISORY_COLUMNS = {
    "date", "rzrq_ratio", "rzrq_trade_ratio", "avg_maintain_ratio", "rzmre", "rqmce",
}


def _ratio_level(ratio: float) -> str:
    if ratio > RZRQ_RATIO_HIGH:
        return f"🔴 偏高 (>3.5%)"
    if ratio > RZRQ_RATIO_LOW:
        return "🟡 中等 (2.5%-3.5%)"
    return "🟢 正常 (<2.5%)"


def build_csdata_advisory(csdata: pd.DataFrame | None, warning_code: int) -> dict | None:
    """用中证监测的存量/增量杠杆指标给 warning_code 一个 ±1 档的建议修正。

    warning_code 只看融资余额的变化速率，不知道当前杠杆绝对水位；中证数据提供
    存量（占流通市值比重、维持担保比例）与增量（两融交易额占比、融资净买入）视角。
    修正只作用于摘要层，不回写历史 warning_code 序列。
    """
    if csdata is None or len(csdata) == 0:
        return None
    if not CSDATA_ADVISORY_COLUMNS.issubset(csdata.columns):
        return None

    data = csdata.sort_values("date")
    latest = data.iloc[-1]
    ratio = latest["rzrq_ratio"]
    maintain = latest["avg_maintain_ratio"]
    if pd.isna(ratio):
        return None
    ratio = float(ratio)

    trade = data["rzrq_trade_ratio"].dropna()
    trade_change_pp = None
    trade_trend = "数据不足"
    if len(trade) >= 2:
        trade_change_pp = (float(trade.iloc[-1]) - float(trade.iloc[0])) * 100
        trade_trend = (
            "回落" if trade_change_pp < 0 else ("上升" if trade_change_pp > 0 else "持平")
        )

    adjustment = 0
    reason = "中证存量/增量指标未触发修正条件"
    if pd.notna(maintain) and float(maintain) < MAINTAIN_RATIO_DANGER:
        adjustment = 1
        reason = f"平均维持担保比例 {float(maintain):.2f} 已低于 2.0，距强平线最近，风险应上调"
    elif ratio > RZRQ_RATIO_HIGH:
        adjustment = 1
        reason = f"两融余额占流通市值 {ratio * 100:.2f}% 处于高杠杆区，强平链条真实存在，风险应上调"
    elif ratio < RZRQ_RATIO_LOW:
        adjustment = -1
        reason = (
            f"两融余额占流通市值 {ratio * 100:.2f}% 处于低杠杆区，强平链条已大体出清，"
            "融资收缩更可能是主动减仓，风险可下调一档"
        )
    elif trade_change_pp is not None and trade_change_pp <= -TRADE_RATIO_RETREAT_PP:
        adjustment = 1
        reason = (
            f"杠杆处于中段但两融交易额占比在观测窗口内回落 {trade_change_pp:.2f}pp，"
            "增量杠杆正在撤退，风险应上调"
        )

    base = int(warning_code)
    adjusted = max(0, min(3, base + adjustment))
    net_buy = latest["rzmre"] - latest["rqmce"]
    return {
        "as_of": pd.Timestamp(latest["date"]).strftime("%Y-%m-%d"),
        "rzrq_ratio_pct": round(ratio * 100, 4),
        "rzrq_ratio_level": _ratio_level(ratio),
        "avg_maintain_ratio": _json_number(maintain),
        "rzrq_trade_ratio_pct": _json_number(latest["rzrq_trade_ratio"] * 100),
        "rzrq_trade_ratio_change_pp": _json_number(trade_change_pp),
        "rzrq_trade_ratio_trend": trade_trend,
        "net_financing_buy_yi": _json_number(net_buy),
        "base_warning_code": base,
        "suggested_adjustment": adjustment,
        "adjusted_warning_code": adjusted,
        "adjusted_warning_level": LEVEL_NAMES[adjusted],
        "adjustment_reason": reason,
        "note": (
            "口径为两融余额/A股流通市值（中证监测），与外部常用的融资余额口径存在细微差异；"
            "历史锚点：近年低点约 1.8%，2015 年峰值超 4%。"
            "本修正仅为建议档，不回写历史 warning_code 序列。"
        ),
    }


def save_warning_outputs(
    df: pd.DataFrame,
    output_dir: Path,
    csdata: pd.DataFrame | None = None,
) -> tuple[Path | None, Path]:
    """保存 2026 模板和机器可读预警摘要；csdata 存在时附上建议修正档。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    warning_cols = [
        "date", "rzye_total", "index_close",
        "financing_change_5d_pct", "financing_change_10d_pct",
        "financing_change_20d_pct", "financing_drawdown_60d_pct",
        "financing_percentile_252d", "index_change_5d_pct",
        "index_change_20d_pct", "financing_index_divergence_20d_pp",
        "warning_code", "warning_level", "warning_reasons",
    ]
    template_result = build_2026_crash_template(df)
    template_path: Path | None = None
    template_summary: dict | None = None
    if template_result is not None:
        template, template_summary = template_result
        cols = warning_cols + ["template_phase", "in_jan_jul_review_window",
                               "in_jul_aug_review_window"]
        template = template[[c for c in cols if c in template.columns]].copy()
        template["date"] = template["date"].dt.strftime("%Y-%m-%d")
        template["rzye_total"] = (template["rzye_total"] / 1e8).round(2)
        numeric_cols = template.select_dtypes(include="number").columns
        template[numeric_cols] = template[numeric_cols].round(4)
        template_path = output_dir / "rzrq_crash_template_2026.csv"
        template.to_csv(template_path, index=False, encoding="utf-8-sig")

    latest = df.iloc[-1]
    summary = {
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "warning_code": int(latest["warning_code"]),
        "warning_level": latest["warning_level"],
        "warning_reasons": latest["warning_reasons"],
        "financing_balance_yi": round(float(latest["rzye_total"]) / 1e8, 2),
        "financing_change_5d_pct": _json_number(latest["financing_change_5d_pct"]),
        "financing_change_10d_pct": _json_number(latest["financing_change_10d_pct"]),
        "financing_change_20d_pct": _json_number(latest["financing_change_20d_pct"]),
        "financing_drawdown_60d_pct": _json_number(latest["financing_drawdown_60d_pct"]),
        "template_2026": template_summary,
        "csdata_advisory": build_csdata_advisory(csdata, int(latest["warning_code"])),
        "crash_template_csv": {
            "file": "rzrq_crash_template_2026.csv",
            "column_descriptions": CRASH_TEMPLATE_COLUMN_DESCRIPTIONS,
        },
        "disclaimer": "预警只识别与 2026-07 去杠杆事件相似的融资形态，不保证预测暴跌。",
    }
    summary_path = output_dir / "rzrq_warning_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return template_path, summary_path


def _json_number(value) -> float | None:
    return round(float(value), 4) if pd.notna(value) else None


def print_financing_warning(df: pd.DataFrame) -> None:
    """输出当前预警及与 2026 模板的关键对照。"""
    latest = df.iloc[-1]
    print("\n🚨 融资暴跌模板预警 (2026-07 模板):")
    print(f"   当前级别: {latest['warning_level']}")
    print(f"   触发原因: {latest['warning_reasons']}")
    print(f"   融资变化: 5日 {_fmt(latest['financing_change_5d_pct'])} | "
          f"10日 {_fmt(latest['financing_change_10d_pct'])} | "
          f"20日 {_fmt(latest['financing_change_20d_pct'])}")
    print(f"   距60日高点: {_fmt(latest['financing_drawdown_60d_pct'])}")
    template_result = build_2026_crash_template(df)
    if template_result is not None:
        _, summary = template_result
        print("   模板事实: "
              f"{summary['data_start']}→{summary['financing_peak_date']} "
              f"融资 {summary['rise_start_to_peak_pct']:+.2f}%；"
              f"峰值→{summary['financing_trough_date']} "
              f"融资 {summary['decline_peak_to_trough_pct']:+.2f}%")
    print("   注意: 单次历史事件只能作为风险模板，不能单独预测暴跌。")


def _fmt(value) -> str:
    return f"{float(value):+.2f}%" if pd.notna(value) else "N/A"
