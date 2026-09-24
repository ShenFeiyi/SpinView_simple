"""Exercise the UI-to-worker boundary without requiring a connected camera."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
import time
from datetime import datetime

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import MainWindow
from camera import Frame


class FakeWorker(QObject):
    connected = Signal(dict)
    settings_changed = Signal(dict)
    error = Signal(str)
    status = Signal(str)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.running = False
        self.stopped = False
        self.writes = []
        self.frame = None

    def start(self):
        self.running = True
        self.connected.emit({"model": "BFS-U3-51S5C", "serial": "test"})

    def isRunning(self):
        return self.running

    def stop(self):
        self.stopped = True

    def finish(self):
        self.running = False
        self.finished.emit()

    def set_control(self, key, value):
        self.writes.append((key, value))

    def latest_frame(self):
        return self.frame

    def white_balance_once(self):
        self.writes.append(("white_balance_once", True))

    def exposure_once(self):
        self.writes.append(("exposure_once", True))

    def gain_once(self):
        self.writes.append(("gain_once", True))


def settings():
    result = {
        key: {"value": value, "min": minimum, "max": maximum, "enabled": True}
        for key, value, minimum, maximum in (
            ("exposure_us", 20000., 6., 30000000.),
            ("gain_db", 0., 0., 47.99),
            ("frame_rate", 15., 1., 75.79),
            ("wb_red", 1.5, .125, 8.),
            ("wb_blue", 1.2, .125, 8.),
        )
    }
    result.update(
        white_balance_once_enabled=True, white_balance_busy=False,
        exposure_once_enabled=True, exposure_busy=False,
        gain_once_enabled=True, gain_busy=False,
    )
    return result


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.worker = FakeWorker()
        self.window = MainWindow(worker_factory=lambda: self.worker, auto_connect=False)
        self.window.show()
        self.window.connect_camera()
        self.worker.settings_changed.emit(settings())
        self.app.processEvents()

    def tearDown(self):
        if self.worker.running:
            self.worker.finish()
        self.window.close()
        self.window._analysis_worker.wait(2000)
        self.app.processEvents()

    def wait_for_analysis(self, row, column):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.app.processEvents()
            result = self.window.analysis_panel._result
            if result is not None and (result.row, result.column) == (row, column):
                return result
            QTest.qWait(5)
        self.fail("Image analysis did not arrive")

    def test_profiles_follow_preview_selection_and_remain_available_offline(self):
        rgb = np.arange(4 * 7 * 3, dtype=np.uint8).reshape(4, 7, 3)
        rgb.setflags(write=False)
        self.worker.frame = Frame(rgb, 1, datetime.now(), 30.)
        self.window._update_preview()
        self.window.preview.pixel_selected.emit(6, 3)
        result = self.wait_for_analysis(3, 6)
        np.testing.assert_array_equal(result.horizontal, rgb[3])
        np.testing.assert_array_equal(result.vertical, rgb[:, 6])
        np.testing.assert_array_equal(result.histogram.sum(axis=1), [28, 28, 28])
        self.assertEqual(self.worker.writes, [])
        self.worker.finish()
        self.window.analysis_panel.set_selection(0, 0)
        result = self.wait_for_analysis(0, 0)
        np.testing.assert_array_equal(result.horizontal, rgb[0])
        self.assertEqual(self.window.live_badge.text(), "LAST FRAME")
        self.window.analysis_button.click()
        self.assertFalse(self.window.analysis_dock.isVisible())
        self.window.analysis_button.click()
        self.assertTrue(self.window.analysis_dock.isVisible())

    def test_reconnect_clears_analysis_and_rejects_previous_camera_result(self):
        rgb = np.full((4, 7, 3), 42, dtype=np.uint8)
        self.worker.frame = Frame(rgb, 1, datetime.now(), 30.)
        self.window._update_preview()
        result = self.wait_for_analysis(self.window.analysis_panel.row, self.window.analysis_panel.column)
        self.worker.finish()
        self.worker.frame = None
        self.window.connect_camera()
        self.window._on_analysis_result(result)
        self.assertIsNone(self.window.analysis_panel._result)

    def test_preview_hover_readout_is_live_and_works_without_analysis(self):
        rgb = np.arange(6 * 10 * 3, dtype=np.uint8).reshape(6, 10, 3)
        rgb.setflags(write=False)
        self.worker.frame = Frame(rgb, 1, datetime.now(), 30.)
        self.window._update_preview()
        self.window.analysis_button.click()
        self.app.processEvents()
        target = self.window.preview.image_rect()
        point = QPointF(target.left() + 7.5 * target.width() / 10,
                        target.top() + 2.5 * target.height() / 6)
        event = QMouseEvent(QEvent.Type.MouseMove, point, point,
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.window.preview, event)
        self.assertEqual(self.window.pixel_info_label.text(), "x 7, y 2 · RGB 81/82/83")
        self.assertEqual((self.window.analysis_panel.row, self.window.analysis_panel.column), (3, 5))
        updated = np.full_like(rgb, (200, 100, 50))
        updated.setflags(write=False)
        self.worker.frame = Frame(updated, 2, datetime.now(), 30.)
        self.window._update_preview()
        self.assertEqual(self.window.pixel_info_label.text(), "x 7, y 2 · RGB 200/100/50")
        self.assertEqual(self.worker.writes, [])
        self.worker.finish()
        self.assertEqual(self.window.pixel_info_label.text(), "x 7, y 2 · RGB 200/100/50")
        QApplication.sendEvent(self.window.preview, QEvent(QEvent.Type.Leave))
        self.assertIn("Hover over the image", self.window.pixel_info_label.text())

    def test_preview_pixel_readout_clears_on_reconnect(self):
        rgb = np.full((4, 7, 3), (12, 34, 56), dtype=np.uint8)
        self.worker.frame = Frame(rgb, 1, datetime.now(), 30.)
        self.window._update_preview()
        point = self.window.preview.image_rect().center()
        event = QMouseEvent(QEvent.Type.MouseMove, point, point,
                            Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(self.window.preview, event)
        self.assertIn("RGB 12/34/56", self.window.pixel_info_label.text())
        self.worker.finish()
        self.worker.frame = None
        self.window.connect_camera()
        self.assertIn("Hover over the image", self.window.pixel_info_label.text())

    def test_zoom_slider_pan_and_reset_preserve_full_image_and_profile_selection(self):
        rgb = np.arange(16 * 32 * 3, dtype=np.uint16).astype(np.uint8).reshape(16, 32, 3)
        rgb.setflags(write=False)
        self.worker.frame = Frame(rgb, 1, datetime.now(), 30.)
        self.window._update_preview()
        self.app.processEvents()
        preview = self.window.preview
        fitted = preview.image_rect()
        selected = (self.window.analysis_panel.row, self.window.analysis_panel.column)
        self.window.zoom_slider.setValue(30)
        self.assertEqual(preview.zoom, 3.0)
        self.assertEqual(self.window.zoom_label.text(), "3.0×")
        self.assertAlmostEqual(preview.image_rect().width(), fitted.width() * 3)
        center = preview.viewport_rect().center()
        destination = center + QPointF(70, 30)
        for kind, point, button, buttons in (
            (QEvent.Type.MouseButtonPress, center, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseMove, destination, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, destination, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
        ):
            QApplication.sendEvent(preview, QMouseEvent(kind, point, point, button, buttons,
                                                        Qt.KeyboardModifier.NoModifier))
        self.assertNotEqual(preview.image_rect().center(), center)
        self.assertEqual((self.window.analysis_panel.row, self.window.analysis_panel.column), selected)
        self.assertIs(self.window._frame.rgb, rgb)
        result = self.wait_for_analysis(*selected)
        np.testing.assert_array_equal(result.histogram.sum(axis=1), [512, 512, 512])
        np.testing.assert_array_equal(result.horizontal, rgb[selected[0]])
        self.assertEqual(self.worker.writes, [])
        self.window.zoom_reset_button.click()
        self.assertEqual(preview.zoom, 1.0)
        self.assertEqual(self.window.zoom_slider.value(), 10)
        self.assertEqual(self.window.zoom_label.text(), "1.0×")
        self.assertEqual(preview.image_rect(), fitted)

    def test_zoom_remains_available_offline_and_resets_on_reconnect(self):
        self.assertFalse(self.window.zoom_slider.isEnabled())
        rgb = np.zeros((4, 7, 3), dtype=np.uint8)
        self.worker.frame = Frame(rgb, 1, datetime.now(), 30.)
        self.window._update_preview()
        self.window.zoom_slider.setValue(40)
        self.worker.finish()
        self.assertTrue(self.window.zoom_slider.isEnabled())
        self.assertTrue(self.window.zoom_reset_button.isEnabled())
        self.assertEqual(self.window.preview.zoom, 4.0)
        self.window.analysis_button.click()
        self.assertEqual(self.window.preview.zoom, 4.0)
        self.assertIn("Drag to pan", self.window.zoom_hint.text())
        self.worker.frame = None
        self.window.connect_camera()
        self.assertEqual(self.window.preview.zoom, 1.0)
        self.assertEqual(self.window.zoom_slider.value(), 10)
        self.assertFalse(self.window.zoom_slider.isEnabled())
        self.assertFalse(self.window.zoom_reset_button.isEnabled())

    def test_small_window_keeps_the_entire_preview_viewport_visible(self):
        self.window.resize(880, 650)
        self.app.processEvents()
        preview = self.window.preview
        self.assertTrue(preview.parentWidget().contentsRect().contains(preview.geometry()))
        self.window.analysis_button.click()
        self.window.resize(880, 650)
        self.app.processEvents()
        self.assertTrue(preview.parentWidget().contentsRect().contains(preview.geometry()))

    def test_exposure_units_and_readback_do_not_echo_writes(self):
        self.assertEqual(self.window.controls["exposure_us"].spin.value(), 20.)
        self.assertEqual(self.worker.writes, [])
        self.window.controls["exposure_us"].spin.setValue(12.5)
        self.assertEqual(self.worker.writes, [("exposure_us", 12500.)])
        updated = settings()
        updated["exposure_us"]["value"] = 12499.
        self.worker.settings_changed.emit(updated)
        self.assertEqual(self.window.controls["exposure_us"].spin.value(), 12.499)
        self.assertEqual(len(self.worker.writes), 1)

    def test_gain_fps_and_white_balance_reach_worker(self):
        for key, value in (("gain_db", 2.5), ("frame_rate", 20.), ("wb_red", 1.6), ("wb_blue", 1.8)):
            self.window.controls[key].spin.setValue(value)
        self.assertEqual(self.worker.writes, [("gain_db", 2.5), ("frame_rate", 20.), ("wb_red", 1.6), ("wb_blue", 1.8)])

    def test_white_balance_once_uses_camera_capability(self):
        data = settings()
        data["white_balance_once_enabled"] = False
        self.worker.settings_changed.emit(data)
        self.assertFalse(self.window.balance_button.isEnabled())
        self.worker.settings_changed.emit(settings())
        self.window.balance_button.click()
        self.assertEqual(self.worker.writes, [("white_balance_once", True)])
        self.assertFalse(self.window.balance_button.isEnabled())

    def test_auto_buttons_request_selected_parameter_and_show_final_readback(self):
        for key, prefix, chosen, displayed in (
            ("exposure_us", "exposure", 43210., 43.21),
            ("gain_db", "gain", 6.25, 6.25),
        ):
            with self.subTest(control=key):
                button = self.window.auto_buttons[key]
                button.click()
                self.assertEqual(self.worker.writes[-1], (f"{prefix}_once", True))
                self.assertFalse(button.isEnabled())
                self.assertFalse(self.window.controls[key].spin.isEnabled())
                data = settings()
                data[f"{prefix}_busy"] = True
                data[f"{prefix}_once_enabled"] = False
                data[key].update(value=chosen, enabled=False)
                self.worker.settings_changed.emit(data)
                self.assertEqual(button.text(), "Adjusting…")
                self.assertEqual(self.window.controls[key].spin.value(), displayed)
                data[f"{prefix}_busy"] = False
                data[f"{prefix}_once_enabled"] = True
                data[key]["enabled"] = True
                self.worker.settings_changed.emit(data)
                self.assertEqual(button.text(), "Auto")
                self.assertTrue(button.isEnabled())
                self.assertTrue(self.window.controls[key].spin.isEnabled())
        self.assertEqual(self.worker.writes, [("exposure_once", True), ("gain_once", True)])

    def test_auto_unsupported_and_disconnect_disable_buttons(self):
        data = settings()
        data["exposure_once_enabled"] = False
        self.worker.settings_changed.emit(data)
        self.assertFalse(self.window.auto_buttons["exposure_us"].isEnabled())
        self.assertTrue(self.window.auto_buttons["gain_db"].isEnabled())
        self.window.toggle_connection()
        self.worker.settings_changed.emit(settings())
        self.assertTrue(all(not b.isEnabled() for b in self.window.auto_buttons.values()))

    def test_auto_readbacks_do_not_erase_edit_in_another_control(self):
        gain = self.window.controls["gain_db"].spin
        gain.lineEdit().setText("7.5 dB")
        data = settings()
        data["exposure_busy"] = True
        data["exposure_us"].update(value=31000., enabled=False)
        self.worker.settings_changed.emit(data)
        self.assertEqual(gain.lineEdit().text(), "7.5 dB")

    def test_preview_keeps_full_resolution_frame_and_offline_label(self):
        rgb = np.full((48, 65, 3), (15, 90, 200), dtype=np.uint8)
        rgb.setflags(write=False)
        self.worker.frame = Frame(rgb, 1, datetime.now(), 19.8)
        self.window._update_preview()
        self.assertTrue(self.window.save_button.isEnabled())
        self.assertIs(self.window._frame.rgb, rgb)
        self.assertEqual(self.window.preview._image.width(), 65)
        self.assertIn("19.8 fps", self.window.frame_label.text())
        self.worker.finish()
        self.assertEqual(self.window.live_badge.text(), "LAST FRAME")
        self.assertTrue(self.window.save_button.isEnabled())

    def test_close_waits_for_worker_and_ignores_late_settings(self):
        self.window.close()
        self.assertTrue(self.worker.stopped)
        self.assertTrue(self.window.isVisible())
        self.worker.settings_changed.emit(settings())
        self.assertFalse(self.window.controls["gain_db"].spin.isEnabled())
        self.worker.finish()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_error_allows_reconnect_after_worker_finishes(self):
        self.worker.error.emit("Camera unplugged")
        self.worker.finish()
        self.assertEqual(self.window.error_banner.text(), "Camera unplugged")
        self.assertTrue(self.window.connection_button.isEnabled())
        self.assertFalse(self.window.controls["gain_db"].spin.isEnabled())


if __name__ == "__main__":
    unittest.main()
