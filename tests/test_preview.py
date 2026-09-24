"""Verify aspect ratio, display-only overlays, and source-pixel selection."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

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

    def mouse_event(self, position, *, move=False, button=Qt.MouseButton.LeftButton,
                    buttons=None):
        event = QMouseEvent(
            QEvent.Type.MouseMove if move else QEvent.Type.MouseButtonPress,
            position, position,
            Qt.MouseButton.NoButton if move else button,
            button if buttons is None else buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(self.preview, event)

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


if __name__ == "__main__":
    unittest.main()
