"""Aspect-correct preview with display-only crosshair and profile selection."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget


class PreviewWidget(QWidget):
    pixel_selected = Signal(int, int)
    pixel_info_changed = Signal(object)
    zoom_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = QImage()
        self._zoom = 1.0
        self._view_center = QPointF(0.5, 0.5)
        self._drag_origin = None
        self._drag_center = None
        self._panning = False
        self._crosshair = True
        self._profile_row = 0
        self._profile_column = 0
        self._profile_orientation = "horizontal"
        self._profile_selection_enabled = False
        self._pointer_position = None
        self._pointer_global_position = None
        self._pixel_info = None
        self._message = "Connecting to camera…"
        self.setMouseTracking(True)
        self.setMinimumSize(360, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Live camera preview")

    def set_image(self, image):
        # Own the pixels independently of the SDK and acquisition thread.
        resolution_changed = image.size() != self._image.size()
        self._image = image.copy()
        self._clamp_profile_selection()
        if resolution_changed:
            self.reset_zoom()
        else:
            self._update_pixel_info()
            self.update()

    def clear(self, message="Camera disconnected"):
        self._image = QImage()
        self._message = message
        # Keep a stationary pointer anchored across a reconnect; the empty
        # image clears its readout until a new frame arrives.
        self.reset_zoom()

    @property
    def zoom(self):
        return self._zoom

    def set_zoom(self, value):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Zoom must be a finite number")
        value = max(1.0, min(8.0, value))
        if value == self._zoom:
            return
        self._zoom = value
        self._cancel_drag()
        self._refresh_view()
        self.zoom_changed.emit(value)

    def reset_zoom(self):
        changed = self._zoom != 1.0
        self._zoom = 1.0
        self._view_center = QPointF(0.5, 0.5)
        self._cancel_drag()
        self._refresh_view()
        if changed:
            self.zoom_changed.emit(self._zoom)

    def _clamp_view_center(self):
        target, viewport = self.image_rect(), self.viewport_rect()
        if target.isEmpty():
            self._view_center = QPointF(0.5, 0.5)
            return
        half_x = min(0.5, viewport.width() / (2 * target.width()))
        half_y = min(0.5, viewport.height() / (2 * target.height()))
        self._view_center = QPointF(
            max(half_x, min(1 - half_x, self._view_center.x())),
            max(half_y, min(1 - half_y, self._view_center.y())),
        )

    def _refresh_view(self):
        self._clamp_view_center()
        self._update_cursor()
        self._update_pixel_info()
        self.update()

    def _update_cursor(self):
        if self._zoom > 1:
            shape = Qt.CursorShape.ClosedHandCursor if self._panning else Qt.CursorShape.OpenHandCursor
        else:
            shape = Qt.CursorShape.CrossCursor if self._profile_selection_enabled else Qt.CursorShape.ArrowCursor
        self.setCursor(shape)

    def _cancel_drag(self):
        self._drag_origin = None
        self._drag_center = None
        self._panning = False
        self._update_cursor()

    def _pan_to(self, position):
        delta = position - self._drag_origin
        if self._panning or delta.manhattanLength() >= QApplication.startDragDistance():
            self._panning = True
            target = self.image_rect()
            self._view_center = QPointF(
                self._drag_center.x() - delta.x() / target.width(),
                self._drag_center.y() - delta.y() / target.height(),
            )
            self._refresh_view()

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
        self._update_cursor()
        self.update()

    def _clamp_profile_selection(self):
        if not self._image.isNull():
            self._profile_row = max(0, min(self._profile_row, self._image.height() - 1))
            self._profile_column = max(0, min(self._profile_column, self._image.width() - 1))

    def _pixel_at(self, position):
        target = self.image_rect()
        if target.isEmpty() or not self.viewport_rect().contains(position) or not target.contains(position):
            return None
        # Mouse positions and the fitted rectangle are both logical coordinates.
        # QRectF includes its far boundary, which belongs to the final pixel.
        column = min(self._image.width() - 1,
                     int((position.x() - target.left()) * self._image.width() / target.width()))
        row = min(self._image.height() - 1,
                  int((position.y() - target.top()) * self._image.height() / target.height()))
        return column, row

    def _select_pixel(self, position):
        if not self._profile_selection_enabled:
            return False
        pixel = self._pixel_at(position)
        if pixel is None:
            return False
        column, row = pixel
        self._profile_row, self._profile_column = row, column
        self.update()
        self.pixel_selected.emit(column, row)
        return True

    def _update_pixel_info(self):
        if self._pointer_global_position is not None:
            # A dock or parent window can move under a stationary pointer.
            # Anchor the last event globally, then map into the current widget.
            self._pointer_position = self.mapFromGlobal(self._pointer_global_position)
        pixel = self._pixel_at(self._pointer_position) if self._pointer_position is not None else None
        info = None
        if pixel is not None:
            column, row = pixel
            color = self._image.pixelColor(column, row)
            info = column, row, color.red(), color.green(), color.blue()
        if info != self._pixel_info:
            self._pixel_info = info
            self.pixel_info_changed.emit(info)

    def _track_pointer(self, position):
        self._pointer_position = QPointF(position)
        self._pointer_global_position = self.mapToGlobal(self._pointer_position)
        self._update_pixel_info()

    def _clear_pixel_info(self):
        self._pointer_position = None
        self._pointer_global_position = None
        self._update_pixel_info()

    def mousePressEvent(self, event):
        self._track_pointer(event.position())
        if event.button() == Qt.MouseButton.LeftButton:
            if (self._zoom > 1 and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                    and self._pixel_at(event.position()) is not None):
                self._drag_origin = QPointF(event.position())
                self._drag_center = QPointF(self._view_center)
                event.accept()
                return
            if self._select_pixel(event.position()):
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._track_pointer(event.position())
        if event.buttons() & Qt.MouseButton.LeftButton:
            if self._drag_origin is not None:
                self._pan_to(event.position())
                event.accept()
                return
            if ((self._zoom == 1 or event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    and self._select_pixel(event.position())):
                event.accept()
                return
        elif self._drag_origin is not None:
            self._cancel_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._track_pointer(event.position())
        if event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            self._pan_to(event.position())
            was_panning = self._panning
            self._cancel_drag()
            if not was_panning:
                self._select_pixel(event.position())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self._track_pointer(event.position())
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._clear_pixel_info()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self._cancel_drag()
        self._clear_pixel_info()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cancel_drag()
        self._refresh_view()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._cancel_drag()
        self._update_pixel_info()

    def viewport_rect(self):
        return QRectF(self.rect()).adjusted(12, 12, -12, -12)

    def image_rect(self):
        if self._image.isNull():
            return QRectF()
        available = self.viewport_rect()
        size = self._image.size().scaled(
            available.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio
        )
        return QRectF(
            available.center().x() - size.width() * self._zoom * self._view_center.x(),
            available.center().y() - size.height() * self._zoom * self._view_center.y(),
            size.width() * self._zoom, size.height() * self._zoom,
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
        painter.setClipRect(self.viewport_rect().intersected(target))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, self._image)
        if self._crosshair:
            center = target.center()
            # Dark outline keeps the fine cyan crosshair visible on bright scenes.
            for color, width in ((QColor(0, 0, 0, 160), 3), (QColor("#62e7e2"), 1)):
                painter.setPen(QPen(color, width))
                painter.drawLine(target.left(), center.y(), target.right(), center.y())
                painter.drawLine(center.x(), target.top(), center.x(), target.bottom())
        if self._profile_selection_enabled:
            painter.setPen(QPen(QColor("#f4b860"), 1, Qt.PenStyle.DashLine))
            if self._profile_orientation == "horizontal":
                y = target.top() + (self._profile_row + 0.5) * target.height() / self._image.height()
                painter.drawLine(QPointF(target.left(), y), QPointF(target.right(), y))
            else:
                x = target.left() + (self._profile_column + 0.5) * target.width() / self._image.width()
                painter.drawLine(QPointF(x, target.top()), QPointF(x, target.bottom()))
