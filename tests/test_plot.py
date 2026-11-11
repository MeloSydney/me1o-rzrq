#!/usr/bin/env python3

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import plotly.graph_objects as go


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("fetch_rzrq", SCRIPTS / "fetch_rzrq.py")
fetch_rzrq = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(fetch_rzrq)


class PlotTests(unittest.TestCase):
    def test_chart_uses_four_aligned_single_axis_subplots(self):
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
        captured = {}

        def capture_html(fig, file, **_kwargs):
            captured["fig"] = fig
            Path(file).write_text("<!doctype html>", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp, patch.object(
                go.Figure, "write_html", new=capture_html):
            path = fetch_rzrq.plot(df, Path(tmp))
            csv_path = fetch_rzrq.save_csv(df, Path(tmp))
            saved = pd.read_csv(csv_path, encoding="utf-8-sig")
            self.assertTrue(path.exists())

        self.assertIn("科创50指数(点)", saved.columns)
        fig = captured["fig"]
        line_traces = [trace for trace in fig.data if trace.mode == "lines"]
        self.assertEqual(
            [(trace.name, trace.yaxis) for trace in line_traces],
            [("两融余额", "y"), ("融资余额", "y2"),
             ("科创50", "y3"), ("上证指数", "y4")],
        )
        self.assertEqual(
            [trace.line.color for trace in line_traces],
            ["#e74c3c", "#2980b9", "#e67e22", "#8e44ad"],
        )
        for axis_name in ("yaxis", "yaxis2", "yaxis3", "yaxis4"):
            self.assertIsNone(getattr(fig.layout, axis_name).overlaying)
        self.assertEqual(fig.layout.hovermode, "x unified")
        self.assertEqual(fig.layout.hoversubplots, "axis")
        self.assertEqual(fig.layout.xaxis4.dtick, "M1")
        plotted_dates = pd.to_datetime(line_traces[1].x)
        self.assertGreaterEqual(plotted_dates.min(), dates.max() - pd.DateOffset(years=3))
        for label in ("顶部转弱预警", "去杠杆/暴跌预警"):
            self.assertNotIn(label, [trace.name for trace in fig.data])


if __name__ == "__main__":
    unittest.main()
