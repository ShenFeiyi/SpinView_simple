"""Save full-resolution RGB camera frames with Qt's image plugins."""

from pathlib import Path

import numpy as np
from PySide6.QtGui import QImage, QImageWriter


IMAGE_FILTERS = (
    "TIFF image (*.tif *.tiff);;PNG image (*.png);;"
    "JPEG image (*.jpg *.jpeg);;Bitmap image (*.bmp)"
)

_FORMATS = {
    ".tif": b"tiff",
    ".tiff": b"tiff",
    ".png": b"png",
    ".jpg": b"jpeg",
    ".jpeg": b"jpeg",
    ".bmp": b"bmp",
}


def supported_image_filters() -> str:
    """Return the save dialog filters for the app's supported formats."""
    return IMAGE_FILTERS


def save_rgb_image(rgb: np.ndarray, filename: str | Path) -> Path:
    """Save an RGB8 frame without resizing or adding preview overlays.

    TIFF, PNG, and BMP preserve every RGB pixel. JPEG uses quality 95.
    The filename must include a supported extension. Its parent directory
    must already exist. Errors are raised rather than silently choosing a
    different file format or creating directories.
    """
    output_path = Path(filename).expanduser()
    image_format = _FORMATS.get(output_path.suffix.lower())
    if image_format is None:
        raise ValueError(
            "Choose a filename ending in .tif, .tiff, .png, .jpg, .jpeg, or .bmp."
        )
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8:
        raise TypeError("The image must be a NumPy array with dtype uint8 (RGB8).")
    if rgb.ndim != 3 or rgb.shape[2] != 3 or not all(rgb.shape[:2]):
        raise ValueError("The image must have nonzero height and width and three RGB channels.")

    # An explicit row stride also handles widths whose RGB byte count is not
    # divisible by four. Detaching gives Qt ownership of the pixel buffer.
    pixels = np.ascontiguousarray(rgb)
    height, width, _ = pixels.shape
    image = QImage(
        pixels.data, width, height, pixels.strides[0], QImage.Format.Format_RGB888
    ).copy()
    if image.isNull():
        raise ValueError("Qt could not create an image from the supplied RGB frame.")

    writer = QImageWriter(str(output_path), image_format)
    if image_format == b"jpeg":
        writer.setQuality(95)
    if not writer.write(image):
        raise OSError(f"Could not save image to '{output_path}': {writer.errorString()}")
    return output_path
