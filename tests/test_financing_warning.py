#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "financing_warning.py"
SPEC = importlib.util.spec_from_file_location("financing_warning", MODULE_PATH)
warning = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(warning)


class FinancingWarningTests(unittest.TestCase):
    def test_warning_levels_match_template_stages(self):
        base = {
            "financing_change_10d_pct": 1.0,
            "financing_change_20d_pct": 4.0,
            "index_change_20d_pct": 1.0,
            "financing_index_divergence_20d_pp": 3.0,
            "financing_percentile_252d": 0.98,
            "financing_drawdown_60d_pct": -0.2,
        }
        crowded = pd.Series({**base, "financing_change_5d_pct": 1.0,
                             "index_change_5d_pct": 0.5})
        reversal = pd.Series({**base, "financing_change_5d_pct": -0.4,
                              "index_change_5d_pct": -0.5})
        crash = pd.Series({**base, "financing_change_5d_pct": -3.0,
                           "financing_change_10d_pct": -6.0,
                           "index_change_5d_pct": -3.0})
        self.assertEqual(warning._classify_row(crowded)[0], 1)
        self.assertEqual(warning._classify_row(reversal)[0], 2)
        self.assertEqual(warning._classify_row(crash)[0], 3)

    def test_template_uses_actual_peak_and_trough(self):
        dates = pd.to_datetime([
            "2026-01-05", "2026-06-25", "2026-07-10",
            "2026-08-03", "2026-08-31",
        ])
        df = pd.DataFrame({
            "date": dates,
            "rzye_total": [25352.84e8, 30009.43e8, 29137.69e8,
                            25721.74e8, 26271.20e8],
            "index_close": [4023.42, 4120.28, 3996.16, 3809.66, 3986.30],
            "warning_code": [0, 1, 2, 3, 0],
            "warning_level": [warning.LEVEL_NAMES[x] for x in [0, 1, 2, 3, 0]],
            "warning_reasons": ["test"] * 5,
        })
        for col in [
            "financing_change_5d_pct", "financing_change_10d_pct",
            "financing_change_20d_pct", "financing_drawdown_60d_pct",
            "financing_percentile_252d", "index_change_5d_pct",
            "index_change_20d_pct", "financing_index_divergence_20d_pp",
        ]:
            df[col] = 0.0

        template, summary = warning.build_2026_crash_template(df)
        self.assertEqual(summary["financing_peak_date"], "2026-06-25")
        self.assertEqual(summary["financing_trough_date"], "2026-08-03")
        self.assertAlmostEqual(summary["rise_start_to_peak_pct"], 18.37, places=2)
        self.assertAlmostEqual(summary["decline_peak_to_trough_pct"], -14.29, places=2)
        self.assertEqual(template.loc[template["date"] == pd.Timestamp("2026-07-10"),
                                      "template_phase"].iloc[0], "暴跌去杠杆")

        with tempfile.TemporaryDirectory() as tmp:
            template_path, summary_path = warning.save_warning_outputs(df, Path(tmp))
            self.assertFalse((Path(tmp) / "rzrq_financing_warning.csv").exists())
            self.assertIsNotNone(template_path)
            self.assertTrue(template_path.exists())
            self.assertTrue(summary_path.exists())
            saved_template = pd.read_csv(template_path, encoding="utf-8-sig")
            saved_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            descriptions = saved_summary["crash_template_csv"]["column_descriptions"]
            self.assertEqual(saved_summary["crash_template_csv"]["file"],
                             "rzrq_crash_template_2026.csv")
            self.assertEqual(set(saved_template.columns), set(descriptions))
            self.assertIn("单位亿元", descriptions["rzye_total"])
            self.assertIn("0=正常", descriptions["warning_code"])

    def _template_frame(self, dates, financing, index=None):
        df = pd.DataFrame({
            "date": pd.to_datetime(dates),
            "rzye_total": financing,
            "index_close": index if index is not None else [4000.0] * len(dates),
            "warning_code": [0] * len(dates),
            "warning_level": [warning.LEVEL_NAMES[0]] * len(dates),
            "warning_reasons": ["test"] * len(dates),
        })
        for col in [
            "financing_change_5d_pct", "financing_change_10d_pct",
            "financing_change_20d_pct", "financing_drawdown_60d_pct",
            "financing_percentile_252d", "index_change_5d_pct",
            "index_change_20d_pct", "financing_index_divergence_20d_pp",
        ]:
            df[col] = 0.0
        return df

    def test_template_marks_weak_consolidation_when_trough_is_breached(self):
        # 窗口内“低点”2026-08-03 被窗口之后的 2026-10-13 跌破（长尾缓慢去杠杆）。
        df = self._template_frame(
            ["2026-01-05", "2026-06-25", "2026-07-10", "2026-08-03", "2026-08-31",
             "2026-09-30", "2026-10-13"],
            [25352.84e8, 30009.43e8, 29137.69e8, 25721.74e8, 26271.20e8,
             25404.34e8, 25162.69e8],
        )
        template, summary = warning.build_2026_crash_template(df)
        self.assertEqual(summary["financing_trough_date"], "2026-08-03")
        self.assertTrue(summary["window_trough_breached_after_template"])
        self.assertEqual(summary["post_template_low_date"], "2026-10-13")
        self.assertEqual(summary["post_template_low_financing_yi"], 25162.69)
        self.assertAlmostEqual(
            summary["post_template_low_vs_window_trough_pct"], -2.17, places=2)
        self.assertEqual(summary["tail_phase_label"], "弱势整理")
        self.assertIn("不应读作修复完成", summary["note"])
        # 模板只覆盖窗口内日期；尾部不得再标为修复观察。
        tail = template.loc[template["date"] == pd.Timestamp("2026-08-31"),
                            "template_phase"].iloc[0]
        self.assertEqual(tail, "弱势整理")

    def test_template_marks_recovery_when_window_trough_holds(self):
        df = self._template_frame(
            ["2026-01-05", "2026-06-25", "2026-07-10", "2026-08-03", "2026-08-31",
             "2026-09-30"],
            [25352.84e8, 30009.43e8, 29137.69e8, 25721.74e8, 26271.20e8, 26400.00e8],
        )
        template, summary = warning.build_2026_crash_template(df)
        self.assertFalse(summary["window_trough_breached_after_template"])
        self.assertEqual(summary["tail_phase_label"], "修复观察")
        self.assertEqual(
            template.loc[template["date"] == pd.Timestamp("2026-08-31"),
                         "template_phase"].iloc[0],
            "修复观察",
        )

    def _csdata_frame(self, rows):
        return pd.DataFrame(rows, columns=[
            "date", "rzrq_ratio", "rzrq_trade_ratio", "avg_maintain_ratio",
            "rzmre", "rqmce",
        ])

    def test_csdata_advisory_real_october_window_is_neutral(self):
        cs = self._csdata_frame([
            ["2026-09-30", 0.025733, 0.079219, 2.683814, 1139.81, 8.94],
            ["2026-10-08", 0.026078, 0.082848, 2.644060, 1391.25, 12.44],
            ["2026-10-12", 0.026086, 0.079174, 2.618183, 1217.24, 10.57],
            ["2026-10-13", 0.025980, 0.080956, 2.621965, 1358.67, 8.60],
        ])
        advisory = warning.build_csdata_advisory(cs, 2)
        self.assertEqual(advisory["rzrq_ratio_level"], "🟡 中等 (2.5%-3.5%)")
        self.assertEqual(advisory["suggested_adjustment"], 0)
        self.assertEqual(advisory["adjusted_warning_code"], 2)
        self.assertAlmostEqual(advisory["net_financing_buy_yi"], 1350.07, places=2)
        self.assertAlmostEqual(advisory["rzrq_trade_ratio_change_pp"], 0.1737, places=3)
        self.assertIn("两融余额/A股流通市值", advisory["note"])

    def test_csdata_advisory_leverage_bands(self):
        low = self._csdata_frame([
            ["2026-10-12", 0.0210, 0.0790, 2.60, 1200.0, 10.0],
            ["2026-10-13", 0.0200, 0.0780, 2.62, 1350.0, 9.0],
        ])
        high = self._csdata_frame([
            ["2026-10-12", 0.0380, 0.0900, 2.60, 1200.0, 10.0],
            ["2026-10-13", 0.0400, 0.0910, 2.62, 1350.0, 9.0],
        ])
        down = warning.build_csdata_advisory(low, 2)
        up = warning.build_csdata_advisory(high, 2)
        self.assertEqual(down["suggested_adjustment"], -1)
        self.assertEqual(down["adjusted_warning_code"], 1)
        self.assertEqual(down["rzrq_ratio_level"], "🟢 正常 (<2.5%)")
        self.assertEqual(up["suggested_adjustment"], 1)
        self.assertEqual(up["adjusted_warning_code"], 3)
        self.assertEqual(up["rzrq_ratio_level"], "🔴 偏高 (>3.5%)")
        # 已到顶档时不得越界。
        self.assertEqual(warning.build_csdata_advisory(high, 3)["adjusted_warning_code"], 3)
        self.assertEqual(warning.build_csdata_advisory(low, 0)["adjusted_warning_code"], 0)

    def test_csdata_advisory_margin_call_and_incremental_retreat(self):
        margin_call = self._csdata_frame([
            ["2026-10-12", 0.0210, 0.0790, 1.90, 1200.0, 10.0],
            ["2026-10-13", 0.0200, 0.0780, 1.85, 1350.0, 9.0],
        ])
        retreat = self._csdata_frame([
            ["2026-10-12", 0.0280, 0.0850, 2.60, 1200.0, 10.0],
            ["2026-10-13", 0.0280, 0.0780, 2.62, 1350.0, 9.0],
        ])
        # 维持担保比例低于 2.0 优先于低杠杆降档。
        self.assertEqual(warning.build_csdata_advisory(margin_call, 1)["suggested_adjustment"], 1)
        self.assertEqual(warning.build_csdata_advisory(margin_call, 1)["adjusted_warning_code"], 2)
        # 中段杠杆 + 两融交易额占比回落 ≥0.5pp → 上调一档。
        adv = warning.build_csdata_advisory(retreat, 1)
        self.assertEqual(adv["suggested_adjustment"], 1)
        self.assertEqual(adv["rzrq_trade_ratio_trend"], "回落")
        self.assertAlmostEqual(adv["rzrq_trade_ratio_change_pp"], -0.70, places=3)

    def test_csdata_advisory_is_optional(self):
        self.assertIsNone(warning.build_csdata_advisory(None, 2))
        self.assertIsNone(warning.build_csdata_advisory(pd.DataFrame(), 2))
        incomplete = pd.DataFrame({"date": ["2026-10-13"], "rzrq_ratio": [0.026]})
        self.assertIsNone(warning.build_csdata_advisory(incomplete, 2))

    def test_summary_records_csdata_advisory(self):
        df = self._template_frame(
            ["2026-01-05", "2026-06-25", "2026-08-03", "2026-08-31"],
            [25352.84e8, 30009.43e8, 25721.74e8, 26271.20e8],
        )
        cs = self._csdata_frame([
            ["2026-10-12", 0.0210, 0.0790, 2.60, 1200.0, 10.0],
            ["2026-10-13", 0.0200, 0.0780, 2.62, 1350.0, 9.0],
        ])
        with tempfile.TemporaryDirectory() as tmp:
            warning.save_warning_outputs(df, Path(tmp), csdata=cs)
            saved = json.loads(
                (Path(tmp) / "rzrq_warning_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["csdata_advisory"]["suggested_adjustment"], -1)
            self.assertEqual(saved["csdata_advisory"]["base_warning_code"],
                             saved["warning_code"])

            warning.save_warning_outputs(df, Path(tmp))
            without = json.loads(
                (Path(tmp) / "rzrq_warning_summary.json").read_text(encoding="utf-8"))
            self.assertIsNone(without["csdata_advisory"])


if __name__ == "__main__":
    unittest.main()
