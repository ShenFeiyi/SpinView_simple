"""Aspect-correct camera preview with a display-only center crosshair."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class PreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = QImage()
        self._crosshair = True
        self._message = "Connecting to camera…"
        self.setMinimumSize(360, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Live camera preview")

    def set_image(self, image):
        # Own the pixels independently of the SDK and acquisition thread.
        self._image = image.copy()
        self.update()

    def clear(self, message="Camera disconnected"):
        self._image = QImage()
        self._message = message
        self.update()

    def set_crosshair(self, enabled):
        self._crosshair = bool(enabled)
        self.update()

    def image_rect(self):
        if self._image.isNull():
            return QRectF()
        available = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        size = self._image.size().scaled(
            available.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio
        )
        return QRectF(
            available.center().x() - size.width() / 2,
            available.center().y() - size.height() / 2,
            size.width(), size.height(),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#10151d"))
        if self._image.isNull():
            painter.setPen(QColor("#99a5b5"))
            painter.drawText(
                self.rect().adjusted(30, 30, -30, -30),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._message,
            )
            return
        target = self.image_rect()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, self._image)
        if self._crosshair:
            painter.setClipRect(target)
            center = target.center()
            # Dark outline keeps the fine cyan crosshair visible on bright scenes.
            for color, width in ((QColor(0, 0, 0, 160), 3), (QColor("#62e7e2"), 1)):
                painter.setPen(QPen(color, width))
                painter.drawLine(target.left(), center.y(), target.right(), center.y())
                painter.drawLine(center.x(), target.top(), center.x(), target.bottom())
