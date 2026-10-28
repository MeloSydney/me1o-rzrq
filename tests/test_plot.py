#!/usr/bin/env python3

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import matplotlib.pyplot as plt
import pandas as pd


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("fetch_rzrq", SCRIPTS / "fetch_rzrq.py")
fetch_rzrq = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(fetch_rzrq)


class PlotTests(unittest.TestCase):
    def test_chart_uses_separate_financing_comparisons_for_each_index(self):
        dates = pd.date_range("2022-01-31", "2026-01-31", freq="ME")
        count = len(dates)
        df = pd.DataFrame({
            "date": dates,
            "rzrqye_total": [2.0e12 + i * 1e9 for i in range(count)],
            "rzye_total": [1.9e12 + i * 1e9 for i in range(count)],
            "rqye_total": [1.0e11] * count,
            "index_close": [3000 + i for i in range(count)],
            "star50_close": [1000 + i * 2 for i in range(count)],
            "warning_code": [3] * count,
        })

        with tempfile.TemporaryDirectory() as tmp, patch.object(plt, "close"):
            path = fetch_rzrq.plot(df, Path(tmp))
            csv_path = fetch_rzrq.save_csv(df, Path(tmp))
            saved = pd.read_csv(csv_path, encoding="utf-8-sig")
            fig = plt.gcf()
            self.assertTrue(path.exists())
            self.assertIn("科创50指数(点)", saved.columns)
            self.assertEqual(len(fig.axes), 5)  # 三个主子图 + 两个独立指数右 Y 轴
            self.assertEqual(fig.axes[1].get_title(loc="left"),
                             "融资余额 vs 科创50")
            self.assertEqual(fig.axes[2].get_title(loc="left"),
                             "融资余额 vs 上证指数")
            star_legend = [text.get_text() for text in fig.axes[1].get_legend().get_texts()]
            sse_legend = [text.get_text() for text in fig.axes[2].get_legend().get_texts()]
            self.assertEqual(star_legend, ["融资余额", "科创50"])
            self.assertEqual(sse_legend, ["融资余额", "上证指数"])
            for labels in (star_legend, sse_legend):
                self.assertNotIn("顶部转弱预警", labels)
                self.assertNotIn("去杠杆/暴跌预警", labels)
            self.assertEqual(fig.axes[1].lines[0].get_color(), "#2980b9")
            self.assertEqual(fig.axes[2].lines[0].get_color(), "#2980b9")
            self.assertEqual(fig.axes[3].lines[0].get_color(), "#e67e22")
            self.assertEqual(fig.axes[4].lines[0].get_color(), "#8e44ad")
            plotted_dates = pd.to_datetime(fig.axes[1].lines[0].get_xdata())
            self.assertGreaterEqual(plotted_dates.min(), dates.max() - pd.DateOffset(years=3))
            locator = fig.axes[2].xaxis.get_major_locator()
            self.assertEqual(locator.__class__.__name__, "MonthLocator")
            ticks = locator.tick_values(plotted_dates.min().to_pydatetime(),
                                        plotted_dates.max().to_pydatetime())
            self.assertGreaterEqual(len(ticks), 36)
            self.assertLessEqual(len(ticks), 38)
            plt.close(fig)


if __name__ == "__main__":
    unittest.main()
