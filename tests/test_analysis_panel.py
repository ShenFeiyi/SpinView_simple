"""Plot selection, coordinates, and rendering without a camera."""

import os
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from analysis_panel import AnalysisPanel


def result(width=7, height=5, row=2, column=3):
    return SimpleNamespace(
        width=width, height=height, row=row, column=column, sequence=31,
        histogram=np.full((3, 256), 17, dtype=np.int64),
        horizontal=np.arange(width * 3, dtype=np.uint8).reshape(width, 3),
        vertical=(np.arange(height * 3, dtype=np.uint8).reshape(height, 3) + 100),
    )


class AnalysisPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = AnalysisPanel()
        self.panel.resize(1000, 270)
        self.addCleanup(self.panel.close)

    def test_center_selection_ranges_and_one_signal_per_update(self):
        spy = QSignalSpy(self.panel.selection_changed)
        self.panel.set_image_size(2448, 2048)
        self.assertEqual((self.panel.row, self.panel.column), (1024, 1224))
        self.assertEqual((self.panel.row_spin.maximum(), self.panel.column_spin.maximum()), (2047, 2447))
        self.assertEqual(spy.count(), 1)
        self.panel.set_selection(1700, 2300)
        self.assertEqual(spy.count(), 2)
        self.assertEqual(spy.at(1), [1700, 2300])
        self.panel.set_image_size(100, 80)
        self.assertEqual((self.panel.row, self.panel.column), (79, 99))
        self.assertEqual(spy.count(), 3)
        self.panel.set_image_size(100, 80)
        self.assertEqual(spy.count(), 3)

    def test_orientation_uses_the_correct_original_profile(self):
        data = result()
        self.panel.set_result(data)
        np.testing.assert_array_equal(self.panel.profile_chart.values, data.horizontal)
        spy = QSignalSpy(self.panel.orientation_changed)
        self.panel.orientation_combo.setCurrentIndex(1)
        self.assertEqual(self.panel.orientation, "vertical")
        self.assertEqual(spy.at(0), ["vertical"])
        np.testing.assert_array_equal(self.panel.profile_chart.values, data.vertical)
        self.assertEqual(self.panel.profile_chart.values_at(4), (112, 113, 114))
        self.assertIn("column x = 3", self.panel.profile_chart.hover_text(4))

    def test_hover_maps_both_endpoints_to_exact_source_pixels(self):
        self.panel.set_result(result())
        self.panel.show()
        self.app.processEvents()
        chart = self.panel.profile_chart
        rect = chart.plot_rect()
        self.assertEqual(chart.index_at(QPointF(rect.left(), rect.center().y())), 0)
        self.assertEqual(chart.index_at(QPointF(rect.right(), rect.center().y())), 6)
        self.assertEqual(chart.index_at(rect.center()), 3)
        self.assertIsNone(chart.index_at(QPointF(rect.left() - 1, rect.center().y())))
        self.assertEqual(chart.values_at(6), (18, 19, 20))
        self.assertIn("x = 6  ·  R 18  G 19  B 20", chart.hover_text(6))
        self.assertIn("frame 31", chart.hover_text(6))

    def test_pending_selection_keeps_chart_labeled_with_analyzed_line(self):
        self.panel.set_result(result())
        self.panel.set_selection(4, 6)
        self.assertEqual((self.panel.row, self.panel.column), (4, 6))
        self.assertEqual((self.panel.profile_chart.row, self.panel.profile_chart.column), (2, 3))
        self.assertIn("Updating selected line", self.panel.hover_label.text())
        self.assertIn("Row y = 2", self.panel.profile_chart._title())

    def test_stationary_hover_updates_with_new_frame_and_orientation(self):
        data = result()
        self.panel.set_result(data)
        self.panel.show()
        self.app.processEvents()
        chart = self.panel.profile_chart
        QTest.mouseMove(chart, chart.plot_rect().center().toPoint())
        self.assertIn("R 9  G 10  B 11", self.panel.hover_label.text())
        data = result()
        data.sequence = 32
        data.horizontal += 10
        self.panel.set_result(data)
        self.assertIn("R 19  G 20  B 21", self.panel.hover_label.text())
        self.assertIn("frame 32", self.panel.hover_label.text())
        self.panel.orientation_combo.setCurrentIndex(1)
        self.assertIn("y = 2  ·  R 106  G 107  B 108", self.panel.hover_label.text())

    def test_empty_zero_and_uniform_plots_render(self):
        self.assertFalse(self.panel.row_spin.isEnabled())
        self.assertFalse(self.panel.grab().isNull())
        for level in (0, 255):
            for size in ((1, 1), (7, 5)):
                with self.subTest(level=level, size=size):
                    data = result(*size, row=0, column=0)
                    data.horizontal.fill(level)
                    data.vertical.fill(level)
                    data.histogram.fill(0)
                    data.histogram[:, level] = size[0] * size[1]
                    self.panel.set_result(data)
                    self.assertFalse(self.panel.grab().isNull())
                    self.panel.log_check.setChecked(True)
                    self.assertFalse(self.panel.grab().isNull())
                    self.panel.log_check.setChecked(False)
        self.panel.set_live(True)
        self.assertIn("5 Hz", self.panel.live_label.text())
        self.panel.set_live(False)
        self.assertEqual(self.panel.live_label.text(), "Last frame")
        self.panel.clear()
        self.assertIsNone(self.panel.profile_chart.values)
        self.assertIsNone(self.panel.histogram_chart.values)
        self.assertFalse(self.panel.orientation_combo.isEnabled())


if __name__ == "__main__":
    unittest.main()
