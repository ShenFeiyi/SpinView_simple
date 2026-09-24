"""Verify aspect ratio, display-only overlays, and source-pixel selection."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QHideEvent, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from preview import PreviewWidget


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.preview = PreviewWidget()
        self.preview.resize(800, 600)
        self.addCleanup(self.preview.close)

    def source_image(self, width, height):
        image = QImage(width, height, QImage.Format.Format_RGB888)
        image.fill(QColor("#384e76"))
        self.preview.set_image(image)
        return image

    def screen_color(self, rendered, x, y):
        scale = rendered.devicePixelRatio()
        return rendered.pixelColor(int(x * scale), int(y * scale)).name()

    def mouse_event(self, position, *, move=False, release=False,
                    button=Qt.MouseButton.LeftButton, buttons=None,
                    modifiers=Qt.KeyboardModifier.NoModifier):
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease if release else
            QEvent.Type.MouseMove if move else QEvent.Type.MouseButtonPress,
            position, position,
            Qt.MouseButton.NoButton if move else button,
            (Qt.MouseButton.NoButton if release else button) if buttons is None else buttons,
            modifiers,
        )
        QApplication.sendEvent(self.preview, event)

    def hover(self, position):
        self.mouse_event(position, move=True, buttons=Qt.MouseButton.NoButton)

    def pixel_position(self, column, row):
        target = self.preview.image_rect()
        return QPointF(target.left() + (column + 0.5) * target.width() / self.preview._image.width(),
                       target.top() + (row + 0.5) * target.height() / self.preview._image.height())

    def pan(self, delta):
        start = self.preview.viewport_rect().center()
        self.mouse_event(start)
        self.mouse_event(start + delta, move=True)
        self.mouse_event(start + delta, release=True)

    def test_wide_and_tall_images_fit_without_stretching_or_cropping(self):
        for width, height in ((1000, 500), (400, 1000), (2448, 2048)):
            with self.subTest(size=(width, height)):
                self.source_image(width, height)
                target = self.preview.image_rect()
                self.assertEqual(target.center(), QPointF(400, 300))
                self.assertGreaterEqual(target.left(), 12)
                self.assertGreaterEqual(target.top(), 12)
                self.assertLessEqual(target.right(), 788)
                self.assertLessEqual(target.bottom(), 588)
                # QSize scaling rounds to whole display pixels.
                self.assertLessEqual(abs(target.width() - target.height() * width / height), 2)
                self.assertTrue(target.width() == 776 or target.height() == 576)

    def test_crosshair_is_centered_and_clipped_to_image(self):
        self.source_image(1000, 500)
        rendered = self.preview.grab().toImage()
        self.assertEqual(self.screen_color(rendered, 400, 300), "#62e7e2")
        self.assertEqual(self.screen_color(rendered, 300, 300), "#62e7e2")
        self.assertEqual(self.screen_color(rendered, 400, 200), "#62e7e2")
        self.assertEqual(self.screen_color(rendered, 400, 50), "#10151d")
        self.assertEqual(self.screen_color(rendered, 400, 550), "#10151d")

    def test_overlay_toggle_does_not_change_source_pixels(self):
        image = self.source_image(1000, 500)
        before = image.copy()
        self.preview.grab()
        self.assertEqual(image, before)
        self.assertEqual(self.preview._image, before)
        self.preview.set_crosshair(False)
        rendered = self.preview.grab().toImage()
        self.assertEqual(self.screen_color(rendered, 400, 300), "#384e76")
        self.assertEqual(image, before)
        self.assertEqual(self.preview._image, before)

    def test_preview_owns_pixels_after_source_changes(self):
        image = self.source_image(1000, 500)
        image.fill(QColor("#ff0000"))
        self.preview.set_crosshair(False)
        rendered = self.preview.grab().toImage()
        self.assertEqual(self.screen_color(rendered, 400, 300), "#384e76")
        self.preview.clear()
        self.assertTrue(self.preview.image_rect().isEmpty())

    def test_click_maps_wide_and_tall_images_to_zero_based_source_pixels(self):
        selected = []
        self.preview.pixel_selected.connect(lambda column, row: selected.append((column, row)))
        for width, height in ((1000, 500), (400, 1000)):
            with self.subTest(size=(width, height)):
                self.source_image(width, height)
                self.preview.set_profile_selection(0, 0, "horizontal")
                target = self.preview.image_rect()
                column, row = width // 3, height // 4
                position = QPointF(
                    target.left() + (column + 0.75) * target.width() / width,
                    target.top() + (row + 0.25) * target.height() / height,
                )
                self.mouse_event(position)
                self.assertEqual(selected[-1], (column, row))
                self.assertEqual((self.preview._profile_row, self.preview._profile_column),
                                 (row, column))

    def test_mapping_uses_logical_positions_with_high_dpi_source_image(self):
        image = self.source_image(1000, 500)
        image.setDevicePixelRatio(2)
        self.preview.set_image(image)
        self.preview.set_profile_selection(0, 0, "vertical")
        selected = []
        self.preview.pixel_selected.connect(lambda x, y: selected.append((x, y)))
        target = self.preview.image_rect()
        self.mouse_event(QPointF(target.left() + target.width() * 0.75,
                                 target.top() + target.height() * 0.25))
        self.assertEqual(selected, [(750, 125)])

    def test_edges_clamp_and_letterboxes_and_outside_positions_are_ignored(self):
        selected = []
        self.preview.pixel_selected.connect(lambda x, y: selected.append((x, y)))
        for width, height in ((1000, 500), (400, 1000)):
            with self.subTest(size=(width, height)):
                self.source_image(width, height)
                self.preview.set_profile_selection(0, 0, "horizontal")
                target = self.preview.image_rect()
                selected.clear()
                self.mouse_event(target.topLeft())
                self.mouse_event(target.bottomRight())
                self.assertEqual(selected, [(0, 0), (width - 1, height - 1)])
                for position in (
                    QPointF(target.left() - 0.001, target.center().y()),
                    QPointF(target.right() + 0.001, target.center().y()),
                    QPointF(target.center().x(), target.top() - 0.001),
                    QPointF(target.center().x(), target.bottom() + 0.001),
                    QPointF(1, 1), QPointF(400, 1), QPointF(1, 300),
                ):
                    self.mouse_event(position)
                self.assertEqual(selected, [(0, 0), (width - 1, height - 1)])

    def test_left_drag_selects_inside_image_but_hover_and_other_buttons_do_not(self):
        self.source_image(1000, 500)
        self.preview.set_profile_selection(0, 0, "horizontal")
        selected = []
        self.preview.pixel_selected.connect(lambda x, y: selected.append((x, y)))
        target = self.preview.image_rect()
        self.mouse_event(target.center())
        self.mouse_event(QPointF(target.right(), target.top()), move=True)
        self.mouse_event(QPointF(target.center().x(), target.top() - 5), move=True)
        self.mouse_event(target.center(), move=True, buttons=Qt.MouseButton.NoButton)
        self.mouse_event(target.center(), button=Qt.MouseButton.RightButton)
        self.mouse_event(target.center(), move=True, button=Qt.MouseButton.RightButton)
        self.assertEqual(selected, [(500, 250), (999, 0)])

    def test_disabled_selection_does_not_emit_move_selection_or_show_overlay(self):
        self.source_image(1000, 500)
        self.preview.set_crosshair(False)
        self.preview.set_profile_selection(10, 20, "vertical", enabled=False)
        selected = []
        self.preview.pixel_selected.connect(lambda x, y: selected.append((x, y)))
        self.mouse_event(self.preview.image_rect().center())
        self.mouse_event(self.preview.image_rect().center(), move=True)
        self.assertEqual(selected, [])
        self.assertEqual((self.preview._profile_row, self.preview._profile_column), (10, 20))
        self.assertEqual(self.preview.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertEqual(self.screen_color(self.preview.grab().toImage(), 400, 300), "#384e76")
        self.preview.set_profile_selection(10, 20, "vertical")
        self.assertEqual(self.preview.cursor().shape(), Qt.CursorShape.CrossCursor)

    def test_profile_overlay_is_dashed_at_pixel_center_and_clipped_to_image(self):
        self.source_image(100, 50)
        self.preview.set_crosshair(False)
        target = self.preview.image_rect()
        for orientation in ("horizontal", "vertical"):
            with self.subTest(orientation=orientation):
                self.preview.set_profile_selection(12, 25, orientation)
                rendered = self.preview.grab().toImage()
                x = target.left() + 25.5 * target.width() / 100
                y = target.top() + 12.5 * target.height() / 50
                if orientation == "horizontal":
                    colors = {self.screen_color(rendered, sample, y)
                              for sample in range(int(target.left()), int(target.right()))}
                    self.assertEqual(self.screen_color(rendered, 400, y + 5), "#384e76")
                    self.assertEqual(self.screen_color(rendered, 1, y), "#10151d")
                else:
                    colors = {self.screen_color(rendered, x, sample)
                              for sample in range(int(target.top()), int(target.bottom()))}
                    self.assertEqual(self.screen_color(rendered, x + 5, 300), "#384e76")
                    self.assertEqual(self.screen_color(rendered, x, 50), "#10151d")
                self.assertIn("#f4b860", colors)
                self.assertIn("#384e76", colors)

    def test_profile_overlay_and_crosshair_are_independent_and_do_not_change_pixels(self):
        image = self.source_image(100, 50)
        before = image.copy()
        self.preview.set_profile_selection(12, 25, "horizontal")
        rendered = self.preview.grab().toImage()
        self.assertEqual(self.screen_color(rendered, 400, 300), "#62e7e2")
        self.preview.set_crosshair(False)
        self.preview.grab()
        self.preview.set_profile_selection(12, 25, "vertical")
        self.preview.grab()
        self.assertEqual(image, before)
        self.assertEqual(self.preview._image, before)

    def test_selection_clamps_on_new_images_and_empty_preview_cannot_select(self):
        self.source_image(1000, 500)
        self.preview.set_profile_selection(-5, 2000, "horizontal")
        self.assertEqual((self.preview._profile_row, self.preview._profile_column), (0, 999))
        self.preview.set_profile_selection(499, 999, "vertical")
        self.source_image(100, 50)
        self.assertEqual((self.preview._profile_row, self.preview._profile_column), (49, 99))
        selected = []
        self.preview.pixel_selected.connect(lambda x, y: selected.append((x, y)))
        self.preview.clear()
        self.mouse_event(QPointF(400, 300))
        self.assertEqual(selected, [])
        self.assertTrue(self.preview.image_rect().isEmpty())
        self.preview.grab()

    def test_invalid_profile_orientation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.preview.set_profile_selection(0, 0, "diagonal")

    def test_hover_reports_original_rgb_pixels_without_enabling_profile_selection(self):
        info = []
        selected = []
        self.preview.pixel_info_changed.connect(info.append)
        self.preview.pixel_selected.connect(lambda x, y: selected.append((x, y)))
        self.assertTrue(self.preview.hasMouseTracking())
        for width, height, scale in ((1000, 500, 1), (400, 1000, 1), (1000, 500, 2)):
            with self.subTest(size=(width, height), scale=scale):
                image = self.source_image(width, height)
                column, row = width // 3, height // 4
                image.setPixelColor(column, row, QColor(17, 43, 211))
                image.setPixelColor(column + 1, row, QColor(250, 2, 30))
                image.setDevicePixelRatio(scale)
                self.preview.set_image(image)
                self.preview.set_profile_selection(0, 0, "horizontal", enabled=False)
                self.hover(self.pixel_position(column, row))
                self.assertEqual(info[-1], (column, row, 17, 43, 211))
                self.hover(self.pixel_position(column + 1, row))
                self.assertEqual(info[-1], (column + 1, row, 250, 2, 30))
        self.assertEqual(selected, [])
        self.assertEqual(self.preview.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_hover_edges_and_letterboxes_share_selection_mapping(self):
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        for width, height in ((1000, 500), (400, 1000)):
            with self.subTest(size=(width, height)):
                image = self.source_image(width, height)
                image.setPixelColor(0, 0, QColor(1, 2, 3))
                image.setPixelColor(width - 1, height - 1, QColor(4, 5, 6))
                self.preview.set_image(image)
                target = self.preview.image_rect()
                self.hover(target.topLeft())
                self.assertEqual(info[-1], (0, 0, 1, 2, 3))
                self.hover(target.bottomRight())
                self.assertEqual(info[-1], (width - 1, height - 1, 4, 5, 6))
                for position in (
                    QPointF(target.left() - 0.001, target.center().y()),
                    QPointF(target.right() + 0.001, target.center().y()),
                    QPointF(target.center().x(), target.top() - 0.001),
                    QPointF(target.center().x(), target.bottom() + 0.001),
                    QPointF(1, 1),
                ):
                    self.hover(target.center())
                    self.hover(position)
                    self.assertIsNone(info[-1])

    def test_stationary_pointer_refreshes_for_new_pixels_and_image_dimensions(self):
        self.source_image(1000, 500)
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        self.hover(self.pixel_position(200, 100))
        image = QImage(1000, 500, QImage.Format.Format_RGB888)
        image.fill(QColor(71, 82, 93))
        self.preview.set_image(image)
        self.assertEqual(info[-1], (200, 100, 71, 82, 93))
        smaller = QImage(500, 250, QImage.Format.Format_RGB888)
        smaller.fill(QColor(94, 105, 116))
        self.preview.set_image(smaller)
        self.assertEqual(info[-1], (100, 50, 94, 105, 116))
        self.preview.set_image(QImage())
        self.assertIsNone(info[-1])

    def test_stationary_pointer_recomputes_after_preview_resize(self):
        self.preview.show()
        self.app.processEvents()
        self.source_image(1000, 500)
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        self.hover(QPointF(400, 300))
        self.assertEqual(info[-1][:2], (500, 250))
        self.preview.resize(800, 1000)
        self.assertIsNone(info[-1])  # The old pointer position is now in the top letterbox.
        self.preview.resize(800, 600)
        self.assertEqual(info[-1][:2], (500, 250))

    def test_stationary_pointer_remaps_when_preview_or_parent_window_moves(self):
        container = QWidget()
        container.resize(1000, 700)
        container.move(100, 100)
        self.addCleanup(container.close)
        self.preview = PreviewWidget(container)
        self.preview.setGeometry(0, 0, 800, 600)
        container.show()
        self.app.processEvents()
        self.source_image(1000, 500)
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        self.hover(QPointF(400, 300))
        self.assertEqual(info[-1][:2], (500, 250))
        self.preview.move(100, 0)
        self.assertEqual(info[-1][:2], (371, 250))
        container.move(200, 100)
        self.preview.set_image(self.preview._image)
        self.assertEqual(info[-1][:2], (242, 250))

    def test_leave_and_hide_remove_hover_and_stale_pointer(self):
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        for action in (lambda: QApplication.sendEvent(self.preview, QEvent(QEvent.Type.Leave)),
                       lambda: QApplication.sendEvent(self.preview, QHideEvent())):
            self.source_image(1000, 500)
            self.hover(self.preview.image_rect().center())
            self.assertIsNotNone(info[-1])
            action()
            self.assertIsNone(info[-1])
            count = len(info)
            self.source_image(1000, 500)
            self.assertEqual(len(info), count)
        self.preview.clear()
        self.hover(QPointF(400, 300))
        self.assertIsNone(info[-1])

    def test_clear_hides_readout_until_next_frame_under_stationary_pointer(self):
        self.source_image(1000, 500)
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        self.hover(self.preview.image_rect().center())
        self.assertEqual(info[-1], (500, 250, 56, 78, 118))
        self.preview.clear()
        self.assertIsNone(info[-1])
        self.source_image(1000, 500)
        self.assertEqual(info[-1], (500, 250, 56, 78, 118))

    def test_hover_ignores_overlays_and_keeps_owned_image_pixels(self):
        image = self.source_image(1000, 500)
        image.setPixelColor(500, 250, QColor(12, 34, 56))
        self.preview.set_image(image)
        before = image.copy()
        self.preview.set_profile_selection(250, 500, "horizontal")
        self.preview.grab()
        image.fill(QColor(250, 251, 252))
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        self.hover(self.pixel_position(500, 250))
        self.assertEqual(info[-1], (500, 250, 12, 34, 56))
        self.assertEqual(self.preview._image, before)
        self.preview.set_crosshair(False)
        self.preview.set_profile_selection(250, 500, "vertical")
        self.preview.grab()
        self.assertEqual(self.preview._image, before)
        self.assertEqual(info[-1], (500, 250, 12, 34, 56))

    def test_hover_deduplicates_identical_info_but_continues_during_selection_drag(self):
        self.source_image(1000, 500)
        self.preview.set_profile_selection(0, 0, "horizontal")
        info = []
        selected = []
        self.preview.pixel_info_changed.connect(info.append)
        self.preview.pixel_selected.connect(lambda x, y: selected.append((x, y)))
        self.hover(self.pixel_position(200, 100))
        self.hover(self.pixel_position(200, 100))
        self.preview.set_image(self.preview._image)
        self.assertEqual(len(info), 1)
        self.mouse_event(self.pixel_position(201, 101), move=True)
        self.assertEqual(info[-1], (201, 101, 56, 78, 118))
        self.assertEqual(selected, [(201, 101)])

    def test_zoom_limits_validation_and_change_signal(self):
        self.source_image(1000, 500)
        changes = []
        self.preview.zoom_changed.connect(changes.append)
        self.assertEqual(self.preview.zoom, 1.0)
        self.preview.set_zoom(2.5)
        self.preview.set_zoom(2.5)
        self.preview.set_zoom(99)
        self.preview.set_zoom(-1)
        self.assertEqual(changes, [2.5, 8.0, 1.0])
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):
                self.preview.set_zoom(value)
        self.assertEqual(self.preview.zoom, 1.0)

    def test_zoom_scales_about_viewport_center_and_preserves_original_pixel_mapping(self):
        for width, height in ((1000, 500), (400, 1000)):
            with self.subTest(size=(width, height)):
                image = self.source_image(width, height)
                column, row = width // 2 + 10, height // 2 + 5
                image.setPixelColor(column, row, QColor(12, 34, 56))
                self.preview.set_image(image)
                fitted = self.preview.image_rect()
                self.preview.set_zoom(3)
                zoomed = self.preview.image_rect()
                self.assertEqual(zoomed.center(), self.preview.viewport_rect().center())
                self.assertEqual(zoomed.size(), fitted.size() * 3)
                info = []
                self.preview.pixel_info_changed.connect(info.append)
                self.hover(self.pixel_position(column, row))
                self.assertEqual(info[-1], (column, row, 12, 34, 56))

    def test_panning_clamps_large_axes_and_keeps_small_axis_centered(self):
        self.source_image(1000, 500)
        self.preview.set_zoom(1.2)
        self.pan(QPointF(5000, 5000))
        target, viewport = self.preview.image_rect(), self.preview.viewport_rect()
        self.assertAlmostEqual(target.left(), viewport.left())
        self.assertAlmostEqual(target.center().y(), viewport.center().y())
        self.pan(QPointF(-5000, -5000))
        target = self.preview.image_rect()
        self.assertAlmostEqual(target.right(), viewport.right())
        self.assertAlmostEqual(target.center().y(), viewport.center().y())
        self.preview.set_zoom(3)
        self.pan(QPointF(5000, 5000))
        target = self.preview.image_rect()
        self.assertAlmostEqual(target.left(), viewport.left())
        self.assertAlmostEqual(target.top(), viewport.top())
        self.pan(QPointF(-5000, -5000))
        target = self.preview.image_rect()
        self.assertAlmostEqual(target.right(), viewport.right())
        self.assertAlmostEqual(target.bottom(), viewport.bottom())

    def test_zoom_change_retains_source_point_at_viewport_center_after_pan(self):
        self.source_image(1000, 500)
        self.preview.set_zoom(2)
        self.pan(QPointF(100, 30))
        center = self.preview.viewport_rect().center()
        before = self.preview._pixel_at(center)
        self.preview.set_zoom(4)
        self.assertEqual(self.preview._pixel_at(center), before)
        self.preview.set_zoom(2)
        self.assertEqual(self.preview._pixel_at(center), before)
        self.preview.reset_zoom()
        self.assertEqual(self.preview.zoom, 1)
        self.assertEqual(self.preview._pixel_at(center), (500, 250))
        self.assertEqual(self.preview.image_rect().center(), center)

    def test_zoomed_click_selects_on_release_and_pan_does_not_select(self):
        self.source_image(1000, 500)
        self.preview.set_profile_selection(0, 0, "horizontal")
        self.preview.set_zoom(2)
        selected = []
        self.preview.pixel_selected.connect(lambda x, y: selected.append((x, y)))
        point = self.pixel_position(500, 250)
        self.mouse_event(point)
        self.assertEqual(selected, [])
        self.mouse_event(point + QPointF(1, 0), move=True)
        self.assertEqual(self.preview.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        self.mouse_event(point + QPointF(1, 0), release=True)
        self.assertEqual(selected, [self.preview._pixel_at(point + QPointF(1, 0))])
        selected.clear()
        self.mouse_event(point)
        self.mouse_event(point + QPointF(100, 30), move=True)
        self.assertEqual(self.preview.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        self.preview.set_profile_selection(0, 0, "vertical")
        self.assertEqual(self.preview.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        self.mouse_event(point + QPointF(100, 30), release=True)
        self.assertEqual(selected, [])
        self.assertEqual(self.preview.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        # Release displacement still distinguishes a drag if a move was coalesced.
        self.mouse_event(point)
        self.mouse_event(point + QPointF(-100, -30), release=True)
        self.assertEqual(selected, [])

    def test_shift_drag_selects_profile_at_zoom_without_panning(self):
        self.source_image(1000, 500)
        self.preview.set_profile_selection(0, 0, "horizontal")
        self.preview.set_zoom(2)
        before = self.preview.image_rect()
        selected = []
        self.preview.pixel_selected.connect(lambda x, y: selected.append((x, y)))
        shift = Qt.KeyboardModifier.ShiftModifier
        self.mouse_event(self.pixel_position(400, 200), modifiers=shift)
        self.mouse_event(self.pixel_position(600, 300), move=True, modifiers=shift)
        self.mouse_event(self.pixel_position(600, 300), release=True, modifiers=shift)
        self.assertEqual(selected, [(400, 200), (600, 300)])
        self.assertEqual(self.preview.image_rect(), before)

    def test_zoom_pan_persist_across_live_frames_and_profile_panel_visibility(self):
        image = self.source_image(1000, 500)
        before_pixels = image.copy()
        self.preview.set_zoom(3)
        self.pan(QPointF(100, 30))
        before_target = self.preview.image_rect()
        for enabled in (True, False, True):
            self.preview.set_profile_selection(200, 400, "horizontal", enabled=enabled)
            self.preview.set_image(image)
            self.assertEqual(self.preview.zoom, 3)
            self.assertEqual(self.preview.image_rect(), before_target)
            self.assertEqual(self.preview.cursor().shape(), Qt.CursorShape.OpenHandCursor)
        QApplication.sendEvent(self.preview, QHideEvent())
        self.assertEqual(self.preview.image_rect(), before_target)
        self.assertEqual(image, before_pixels)
        self.assertEqual(self.preview._image, before_pixels)
        self.source_image(500, 250)
        self.assertEqual(self.preview.zoom, 1)
        self.assertEqual(self.preview.image_rect().center(), self.preview.viewport_rect().center())
        self.preview.set_zoom(3)
        self.preview.clear()
        self.assertEqual(self.preview.zoom, 1)

    def test_zoomed_image_overlays_and_readout_are_clipped_to_viewport(self):
        self.source_image(1000, 500)
        self.preview.set_zoom(3)
        self.preview.set_profile_selection(250, 500, "horizontal")
        target = self.preview.image_rect()
        self.assertTrue(target.contains(QPointF(2, 300)))
        self.assertIsNone(self.preview._pixel_at(QPointF(2, 300)))
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        self.hover(QPointF(400, 300))
        self.hover(QPointF(2, 300))
        self.assertIsNone(info[-1])
        rendered = self.preview.grab().toImage()
        for point in ((2, 300), (798, 300), (400, 2), (400, 598)):
            self.assertEqual(self.screen_color(rendered, *point), "#10151d")
        self.assertEqual(self.screen_color(rendered, 100, 100), "#384e76")

    def test_panned_source_edges_report_corner_rgb_and_profile_uses_transformed_pixel_center(self):
        image = self.source_image(1000, 500)
        image.setPixelColor(0, 0, QColor(1, 2, 3))
        image.setPixelColor(999, 499, QColor(4, 5, 6))
        self.preview.set_image(image)
        self.preview.set_zoom(3)
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        self.pan(QPointF(5000, 5000))
        self.hover(self.preview.viewport_rect().topLeft())
        self.assertEqual(info[-1], (0, 0, 1, 2, 3))
        self.pan(QPointF(-5000, -5000))
        self.hover(self.preview.viewport_rect().bottomRight())
        self.assertEqual(info[-1], (999, 499, 4, 5, 6))
        self.preview.set_crosshair(False)
        self.preview.set_profile_selection(350, 800, "horizontal")
        row_y = self.pixel_position(800, 350).y()
        rendered = self.preview.grab().toImage()
        colors = {self.screen_color(rendered, x, row_y) for x in range(12, 788)}
        self.assertIn("#f4b860", colors)
        self.assertIn("#384e76", colors)
        self.assertEqual(self.preview._image, image)

    def test_stationary_hover_recomputes_after_zoom_and_pan(self):
        self.source_image(1000, 500)
        info = []
        self.preview.pixel_info_changed.connect(info.append)
        point = QPointF(500, 300)
        self.hover(point)
        self.assertEqual(info[-1][:2], (628, 250))
        self.preview.set_zoom(2)
        self.assertEqual(info[-1][:2], (564, 250))
        self.mouse_event(point)
        self.mouse_event(point + QPointF(100, 0), move=True)
        # Dragging the image keeps its grabbed source pixel under the pointer.
        self.assertEqual(info[-1][:2], (564, 250))
        self.mouse_event(point + QPointF(100, 0), release=True)

    def test_hide_clear_new_resolution_and_resize_cancel_pending_pan(self):
        self.preview.show()
        self.app.processEvents()
        for action in (
            lambda: QApplication.sendEvent(self.preview, QHideEvent()),
            self.preview.clear,
            lambda: self.source_image(500, 250),
            lambda: self.preview.resize(900, 600),
        ):
            self.source_image(1000, 500)
            self.preview.set_profile_selection(0, 0, "horizontal")
            self.preview.set_zoom(2)
            self.mouse_event(self.preview.viewport_rect().center())
            self.assertIsNotNone(self.preview._drag_origin)
            action()
            self.assertIsNone(self.preview._drag_origin)
            self.assertFalse(self.preview._panning)


if __name__ == "__main__":
    unittest.main()
