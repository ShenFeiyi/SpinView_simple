"""Aspect-correct preview with display-only crosshair and profile selection."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class PreviewWidget(QWidget):
    pixel_selected = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = QImage()
        self._crosshair = True
        self._profile_row = 0
        self._profile_column = 0
        self._profile_orientation = "horizontal"
        self._profile_selection_enabled = False
        self._message = "Connecting to camera…"
        self.setMinimumSize(360, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Live camera preview")

    def set_image(self, image):
        # Own the pixels independently of the SDK and acquisition thread.
        self._image = image.copy()
        self._clamp_profile_selection()
        self.update()

    def clear(self, message="Camera disconnected"):
        self._image = QImage()
        self._message = message
        self.update()

    def set_crosshair(self, enabled):
        self._crosshair = bool(enabled)
        self.update()

    def set_profile_selection(self, row: int, column: int, orientation: str,
                              enabled: bool = True):
        if orientation not in ("horizontal", "vertical"):
            raise ValueError("Profile orientation must be horizontal or vertical")
        self._profile_row = int(row)
        self._profile_column = int(column)
        self._profile_orientation = orientation
        self._profile_selection_enabled = bool(enabled)
        self._clamp_profile_selection()
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

    def _clamp_profile_selection(self):
        if not self._image.isNull():
            self._profile_row = max(0, min(self._profile_row, self._image.height() - 1))
            self._profile_column = max(0, min(self._profile_column, self._image.width() - 1))

    def _select_pixel(self, position):
        if not self._profile_selection_enabled:
            return False
        target = self.image_rect()
        if target.isEmpty() or not target.contains(position):
            return False
        # Mouse positions and the fitted rectangle are both logical coordinates.
        # QRectF includes its far boundary, which belongs to the final pixel.
        column = min(self._image.width() - 1,
                     int((position.x() - target.left()) * self._image.width() / target.width()))
        row = min(self._image.height() - 1,
                  int((position.y() - target.top()) * self._image.height() / target.height()))
        self._profile_row, self._profile_column = row, column
        self.update()
        self.pixel_selected.emit(column, row)
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._select_pixel(event.position()):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._select_pixel(event.position()):
            event.accept()
            return
        super().mouseMoveEvent(event)

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
        if self._profile_selection_enabled:
            painter.setClipRect(target)
            painter.setPen(QPen(QColor("#f4b860"), 1, Qt.PenStyle.DashLine))
            if self._profile_orientation == "horizontal":
                y = target.top() + (self._profile_row + 0.5) * target.height() / self._image.height()
                painter.drawLine(QPointF(target.left(), y), QPointF(target.right(), y))
            else:
                x = target.left() + (self._profile_column + 0.5) * target.width() / self._image.width()
                painter.drawLine(QPointF(x, target.top()), QPointF(x, target.bottom()))
