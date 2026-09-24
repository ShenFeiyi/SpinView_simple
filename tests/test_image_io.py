"""Pixel-level checks for the camera's full-resolution save path."""

from pathlib import Path
import tempfile
import unittest

import numpy as np
from PySide6.QtGui import QImage

from image_io import save_rgb_image


def read_rgb(path: Path) -> np.ndarray:
    image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGB888)
    if image.isNull():
        raise AssertionError(f"Qt could not reopen {path}")
    rows = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
        image.height(), image.bytesPerLine()
    )
    return rows[:, : image.width() * 3].reshape(image.height(), image.width(), 3).copy()


class SaveImageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.directory = Path(self.temp_dir.name)
        # Full camera resolution is 2448 x 2048. An odd width exercises
        # byte-alignment handling, while RGB anchors catch channel swapping.
        self.rgb = np.random.default_rng(51).integers(
            0, 256, size=(23, 17, 3), dtype=np.uint8
        )
        self.rgb[0, :3] = [[255, 0, 0], [0, 255, 0], [0, 0, 255]]

    def test_lossless_formats_preserve_every_pixel_at_odd_width(self):
        for suffix in (".png", ".tif", ".tiff", ".bmp", ".PNG"):
            with self.subTest(suffix=suffix):
                path = self.directory / f"frame{suffix}"
                self.assertEqual(save_rgb_image(self.rgb, path), path)
                np.testing.assert_array_equal(read_rgb(path), self.rgb)

    def test_full_camera_resolution_is_preserved(self):
        rgb = np.random.default_rng(10).integers(
            0, 256, size=(2048, 2448, 3), dtype=np.uint8
        )
        for suffix in (".png", ".tif"):
            with self.subTest(suffix=suffix):
                path = self.directory / f"full_frame{suffix}"
                save_rgb_image(rgb, path)
                np.testing.assert_array_equal(read_rgb(path), rgb)

    def test_noncontiguous_input_preserves_pixels(self):
        rgb = self.rgb[:, ::-1]
        self.assertFalse(rgb.flags.c_contiguous)
        path = self.directory / "reversed.png"
        save_rgb_image(rgb, path)
        np.testing.assert_array_equal(read_rgb(path), rgb)

    def test_jpeg_can_be_reopened_at_original_resolution(self):
        path = self.directory / "frame.jpg"
        save_rgb_image(self.rgb, path)
        self.assertEqual(read_rgb(path).shape, self.rgb.shape)

    def test_unsupported_or_missing_extension_creates_no_file(self):
        for name in ("frame.gif", "frame", "frame.raw"):
            with self.subTest(name=name):
                path = self.directory / name
                with self.assertRaisesRegex(ValueError, "filename ending"):
                    save_rgb_image(self.rgb, path)
                self.assertFalse(path.exists())

    def test_missing_parent_raises_without_creating_directory(self):
        path = self.directory / "missing" / "frame.png"
        with self.assertRaisesRegex(OSError, "Could not save image"):
            save_rgb_image(self.rgb, path)
        self.assertFalse(path.parent.exists())

    def test_directory_as_target_raises(self):
        path = self.directory / "frame.png"
        path.mkdir()
        with self.assertRaisesRegex(OSError, "Could not save image"):
            save_rgb_image(self.rgb, path)
        self.assertTrue(path.is_dir())

    def test_invalid_image_is_rejected(self):
        for rgb, exception in (
            (self.rgb.astype(np.uint16), TypeError),
            (self.rgb[:, :, 0], ValueError),
            (np.empty((0, 17, 3), dtype=np.uint8), ValueError),
            (np.empty((2, 3, 4), dtype=np.uint8), ValueError),
        ):
            with self.subTest(shape=rgb.shape, dtype=rgb.dtype):
                path = self.directory / "invalid.png"
                with self.assertRaises(exception):
                    save_rgb_image(rgb, path)
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
