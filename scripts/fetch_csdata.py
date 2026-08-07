#!/usr/bin/env python3
"""
中证监测 (csdata.cn) 融资融券每日数据获取

从 csdata.cn 抓取最近 N 个交易日的 xlsx 文件，解析为 DataFrame。
提供 jin10 没有的关键指标：两融占流通市值比重、维持担保比例等。

用法:
  python fetch_csdata.py                  # 最近 5 个交易日, 保存到 /Users/melo/doc-Dgf/rzrq/MMDD (当天月日)
  python fetch_csdata.py --days 20        # 最近 20 个交易日
  python fetch_csdata.py --output ./data  # 自定义输出目录
"""

import argparse
import re
import sys
import tempfile
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

BASE_URL = "https://www.csdata.cn"
LIST_PAGES = [
    "/cmsmc/cgfb/rzrqsj/rzrqscmrsjtj/index.html",
    "/cmsmc/cgfb/rzrqsj/rzrqsclssj/rzrqscmrsjtj/index.html",
]

FIELD_MAP = {
    "融资余额（亿）": "rzye",
    "融券余额（亿）": "rqye",
    "融资融券余额（亿）": "rzrqye",
    "两融余额占A股流通市值比重": "rzrq_ratio",
    "融资买入额（亿）": "rzmre",
    "融券卖出额（亿）": "rqmce",
    "融资融券交易额（亿）": "rzrqje",
    "两融交易额占A股交易额比重": "rzrq_trade_ratio",
    "平均维持担保比例": "avg_maintain_ratio",
    "可充抵保证金证券市值（亿元）": "collateral_value",
    "担保物总价值（亿元）": "collateral_total",
}


def _fetch_page_links(url: str) -> list[dict]:
    """从列表页解析 xlsx 下载链接"""
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    pattern = re.compile(
        r'<span>(\d{4}-\d{2}-\d{2})</span>'
        r'<a\s+href="([^"]+\.xlsx)"[^>]*title="([^"]+)"',
        re.DOTALL,
    )
    return [{"date": m.group(1), "href": m.group(2), "title": m.group(3)}
            for m in pattern.finditer(resp.text)]


def _parse_xlsx(filepath: str) -> dict:
    """解析单个 xlsx 文件"""
    df = pd.read_excel(filepath, header=None)
    result = {}
    for _, row in df.iterrows():
        key = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
        val = row.iloc[2] if len(row) > 2 and pd.notna(row.iloc[2]) else None
        if key in FIELD_MAP and val is not None:
            result[FIELD_MAP[key]] = float(val)
        # 日期: datetime.datetime 或 pd.Timestamp
        if val is not None and hasattr(val, "strftime") and "date" not in result:
            result["date"] = val.strftime("%Y-%m-%d")
    return result


def fetch_csdata(days: int = 5) -> pd.DataFrame:
    """抓取最近 N 个交易日的中证数据"""
    all_links = []
    for page_url in LIST_PAGES:
        try:
            links = _fetch_page_links(BASE_URL + page_url)
            all_links.extend(links)
        except Exception as e:
            print(f"️  页面 {page_url} 获取失败: {e}")

    seen = set()
    unique = []
    for lk in all_links:
        if lk["date"] not in seen:
            seen.add(lk["date"])
            unique.append(lk)
    unique.sort(key=lambda x: x["date"], reverse=True)
    unique = unique[:days]

    if not unique:
        print("❌ 未找到任何 xlsx 链接")
        return pd.DataFrame()

    print(f"📡 [中证数据] 下载 {len(unique)} 个文件...")
    rows = []
    for lk in unique:
        url = BASE_URL + lk["href"]
        try:
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                f.write(resp.content)
                tmp = f.name
            data = _parse_xlsx(tmp)
            if data.get("date"):
                rows.append(data)
                print(f"   ✅ {data['date']}")
            Path(tmp).unlink(missing_ok=True)
        except Exception as e:
            print(f"   ⚠️  {lk['date']}: {e}")

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    print(f"✅ [中证数据] 解析完成 {len(df)} 条")
    return df


def main():
    ap = argparse.ArgumentParser(description="中证监测融资融券数据获取")
    ap.add_argument("--days", type=int, default=5, help="最近 N 个交易日")
    _default_out = f"/Users/melo/doc-Dgf/rzrq/{datetime.now().strftime('%m%d')}"
    ap.add_argument("--output", default=_default_out,
                    help=f"输出 CSV 目录 (默认按当天日期: {_default_out})")
    args = ap.parse_args()

    df = fetch_csdata(args.days)
    if df.empty:
        sys.exit(1)

    print(f"\n📋 数据预览:")
    print(df.to_string(index=False))

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "csdata_rzrq.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n💾 保存 → {path}")


if __name__ == "__main__":
    main()
