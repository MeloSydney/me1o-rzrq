#!/usr/bin/env python3
"""
A股两融板块流向分析: 个股两融明细 × 申万一级行业 聚合

数据源 (全部免费):
  个股明细 - AKShare stock_margin_detail_sse/szse (交易所法定披露, 逐日逐股)
  行业分类 - 申万宏源研究 一级行业成分股 (31 行业)
  验证源   - 东方财富 datacenter 个股两融 (独立管道, --validate 开启)

用法:
  python fetch_sector.py                      # 最近交易日板块聚合, 输出到 /Users/melo/doc-Dgf/rzrq/MMDD
  python fetch_sector.py --validate           # 聚合 + 东财独立源交叉验证
  python fetch_sector.py --date 2026-09-01    # 指定日期
  python fetch_sector.py --output ~/Desktop   # 自定义输出目录
  python fetch_sector.py --refresh-map        # 强制刷新申万成分股缓存

输出:
  <output>/rzrq_by_sector.csv  - 申万一级聚合表 (余额/净买入/标的数/占比)
  终端摘要                     - 个股 vs ETF 结构、余额 TOP 行业、当日净流入/流出 TOP

说明:
  - 融资余额为存量口径; 净买入 = 当日余额 - 前一交易日余额 (近似流量)
  - 交易所只发布个股级明细, 板块汇总为本脚本聚合结果
  - 申万成分股缓存 7 天 (~/.cache/rzrq/sw_industry_map.csv)
"""

import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

SW_CACHE = Path.home() / ".cache" / "rzrq" / "sw_industry_map.csv"
SW_CACHE_TTL_DAYS = 7
ETF_RE = r"^(51|56|58|15|16|18)"  # ETF/基金代码前缀
UA = {"User-Agent": "Mozilla/5.0"}


# ── 数据采集 ──────────────────────────────────────────────

def _norm_detail(df: pd.DataFrame) -> pd.DataFrame:
    d = df.rename(columns={"标的证券代码": "code", "证券代码": "code",
                           "融资余额": "bal", "融资买入额": "buy"})
    d = d[["code", "bal", "buy"]].copy()
    d["code"] = d["code"].astype(str).str.zfill(6)
    return d


def fetch_exchange_detail(date: str) -> pd.DataFrame:
    """交易所个股两融明细 (date 形如 20260901); 返回 [code, bal, buy]"""
    import akshare as ak
    sse = ak.stock_margin_detail_sse(date=date)
    szse = ak.stock_margin_detail_szse(date=date)
    return pd.concat([_norm_detail(sse), _norm_detail(szse)], ignore_index=True)


def latest_trade_date(back_days: int = 10) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """探测最近两个有数据的交易日, 返回 (目标日明细, 前一交易日明细, 目标日 YYYYMMDD)"""
    d = datetime.now()
    target = None
    target_date = None
    for i in range(back_days):
        cand = (d - timedelta(days=i)).strftime("%Y%m%d")
        try:
            cur = fetch_exchange_detail(cand)
        except Exception:
            continue
        if len(cur) == 0:
            continue
        if target is None:
            target, target_date = cur, cand
            continue
        return target, cur, target_date  # target=最近交易日, cur=前一交易日
    raise SystemExit("❌ 近 %d 天未取到两融明细" % back_days)


def fetch_sw_industry_map(refresh: bool = False) -> pd.DataFrame:
    """申万一级成分股映射 [code, industry]; 本地缓存 7 天"""
    if not refresh and SW_CACHE.exists():
        age = time.time() - SW_CACHE.stat().st_mtime
        if age < SW_CACHE_TTL_DAYS * 86400:
            return pd.read_csv(SW_CACHE, dtype={"code": str})
    import akshare as ak
    fi = ak.sw_index_first_info()
    rows = []
    for _, r in fi.iterrows():
        code = str(r["行业代码"]).replace(".SI", "")
        for _ in range(3):
            try:
                resp = requests.get(
                    "https://www.swsresearch.com/institute-sw/api/index_publish/details/component_stocks/",
                    params={"swindexcode": code, "page": "1", "page_size": "10000"},
                    headers=UA, timeout=15, verify=False).json()["data"]["results"]
                rows += [(x["stockcode"].zfill(6), r["行业名称"]) for x in resp]
                break
            except Exception:
                time.sleep(1)
    ind = pd.DataFrame(rows, columns=["code", "industry"]).drop_duplicates("code")
    SW_CACHE.parent.mkdir(parents=True, exist_ok=True)
    ind.to_csv(SW_CACHE, index=False)
    return ind


def fetch_eastmoney_detail(date: str) -> pd.DataFrame:
    """东财 datacenter 个股两融 (独立验证源); date 形如 20260901 -> [code, em_bal]"""
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    rows = []
    for pg in range(1, 15):
        r = requests.get("https://datacenter-web.eastmoney.com/api/data/v1/get", params={
            "sortColumns": "DATE", "sortTypes": "-1", "pageSize": "500", "pageNumber": str(pg),
            "reportName": "RPTA_WEB_RZRQ_GGMX", "columns": "ALL",
            "filter": f"(DATE='{iso}')"}, headers=UA, timeout=20)
        data = (r.json().get("result") or {}).get("data")
        if not data:
            break
        rows += data
    em = pd.DataFrame(rows)
    em = em.rename(columns={"SCODE": "code", "RZYE": "em_bal"})
    em["code"] = em["code"].astype(str).str.zfill(6)
    return em[["code", "em_bal"]]


# ── 聚合与验证 ────────────────────────────────────────────

def aggregate(cur: pd.DataFrame, prev_bal: pd.Series,
              ind: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    cur = cur.copy()
    cur["net"] = cur["bal"] - cur["code"].map(prev_bal).fillna(0)
    tot = cur["bal"].sum()
    etf = cur["code"].str.match(ETF_RE)
    summary = {
        "total": tot,
        "stock_bal": cur.loc[~etf, "bal"].sum(),
        "etf_bal": cur.loc[etf, "bal"].sum(),
        "gross_buy": cur["buy"].sum(),
        "net_chg": cur["net"].sum(),
    }
    m = cur[~etf].merge(ind, on="code", how="left").fillna({"industry": "未映射"})
    g = m.groupby("industry").agg(
        余额亿=("bal", lambda s: round(s.sum() / 1e8, 1)),
        净买入亿=("net", lambda s: round(s.sum() / 1e8, 2)),
        标的数=("code", "count"),
    ).sort_values("余额亿", ascending=False)
    g["占比%"] = (g["余额亿"] / g["余额亿"].sum() * 100).round(1)
    return g, summary


def validate(cur: pd.DataFrame, date: str) -> None:
    print("🔍 [验证] 拉取东财 datacenter 独立源...")
    try:
        em = fetch_eastmoney_detail(date)
    except Exception as e:
        print(f"   ⚠️  东财源不可用, 跳过验证: {type(e).__name__}")
        return
    m = cur.merge(em, on="code", how="inner")
    rel = (m["bal"] - m["em_bal"]).abs() / m["em_bal"].replace(0, pd.NA)
    bad = int((rel > 0.01).sum())
    print(f"   匹配 {len(m)}/{len(cur)} 只 | 融资余额偏差: 中位 {rel.median()*100:.3f}% | "
          f">1% 偏差 {bad} 只 | 最大 {rel.max()*100:.2f}%")
    print("   ✅ 板块聚合原料与独立源一致" if bad == 0 else "   ⚠️  存在偏差, 检查上游数据")


# ── 主流程 ────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="两融板块流向: 个股明细 × 申万一级聚合")
    ap.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD (默认最近交易日)")
    ap.add_argument("--output", default=f"/Users/melo/doc-Dgf/rzrq/{datetime.now().strftime('%m%d')}",
                    help="输出目录 (默认按当天日期)")
    ap.add_argument("--validate", action="store_true", help="用东财独立源交叉验证个股明细")
    ap.add_argument("--refresh-map", action="store_true", help="强制刷新申万成分股缓存")
    args = ap.parse_args()

    if args.date:
        date = datetime.strptime(args.date, "%Y-%m-%d").strftime("%Y%m%d")
        print(f"📡 拉取 {date} 个股两融明细 (交易所)...")
        cur = fetch_exchange_detail(date)
        if len(cur) == 0:
            raise SystemExit(f"❌ {date} 无数据 (非交易日?)")
        # 前一交易日: 往回找
        prev_df = None
        for i in range(1, 10):
            prev_date = (datetime.strptime(date, "%Y%m%d") - timedelta(days=i)).strftime("%Y%m%d")
            try:
                prev_df = fetch_exchange_detail(prev_date)
            except Exception:
                continue
            if len(prev_df) > 0:
                break
        if prev_df is None or len(prev_df) == 0:
            raise SystemExit("❌ 未找到前一交易日数据")
        prev_bal = prev_df.set_index("code")["bal"]
    else:
        print("📡 探测最近交易日并拉取个股两融明细 (交易所)...")
        cur, prev_df, date = latest_trade_date()
        prev_bal = prev_df.set_index("code")["bal"]
    print(f"   ✅ {date}: {len(cur)} 只标的")

    if args.validate:
        validate(cur, date)

    print("📡 加载申万一级行业映射...")
    ind = fetch_sw_industry_map(refresh=args.refresh_map)
    print(f"   ✅ {ind['industry'].nunique()} 个行业, {len(ind)} 只股票")

    g, s = aggregate(cur, prev_bal, ind)

    print(f"\n📋 结构: 融资余额 {s['total']/1e8:.0f} 亿 = "
          f"个股 {s['stock_bal']/1e8:.0f} 亿 ({s['stock_bal']/s['total']*100:.1f}%) + "
          f"ETF/基金 {s['etf_bal']/1e8:.0f} 亿 ({s['etf_bal']/s['total']*100:.1f}%)")
    print(f"   当日融资买入(毛流入) {s['gross_buy']/1e8:.0f} 亿 | 净变动 {s['net_chg']/1e8:.0f} 亿")
    print("\n=== 融资余额 TOP10 行业 ===")
    print(g.head(10).to_string())
    print("\n=== 当日融资净流入 TOP5 ===")
    print(g.sort_values("净买入亿", ascending=False).head(5).to_string())
    print("\n=== 当日融资净流出 TOP5 ===")
    print(g.sort_values("净买入亿").head(5).to_string())

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    g.to_csv(out / "rzrq_by_sector.csv")
    print(f"\n💾 板块聚合 → {out / 'rzrq_by_sector.csv'}")


if __name__ == "__main__":
    main()
