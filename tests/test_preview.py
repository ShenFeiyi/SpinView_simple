"""Verify camera aspect ratio and the display-only crosshair."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage
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


if __name__ == "__main__":
    unittest.main()
