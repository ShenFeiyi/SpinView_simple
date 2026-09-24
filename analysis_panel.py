"""Compact RGB histogram and selected-line plots, rendered with native Qt."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget,
)


CHANNEL_COLORS = ("#c34242", "#1e8653", "#2468c5")
PROFILE_HINT = "Click or drag on the preview to select a line; hover over the profile for RGB values."


def _count_label(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{value:.0f}"


class RGBChart(QWidget):
    """One chart with pixel-accurate hover readout for line profiles."""

    hover_text_changed = Signal(str)

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.values = None
        self.logarithmic = False
        self.orientation = "horizontal"
        self.row = self.column = 0
        self.sequence = None
        self._hover_index = None
        self._hover_position = None
        self.setMouseTracking(kind == "profile")
        self.setMinimumSize(270, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("RGB image histogram" if kind == "histogram" else "RGB line intensity profile")

    def clear(self):
        self.values = None
        self.sequence = None
        self._hover_index = None
        self._hover_position = None
        self.update()

    def set_histogram(self, histogram, sequence):
        self.values = np.asarray(histogram).T
        self.sequence = sequence
        self.update()

    def set_profile(self, values, orientation, row, column, sequence):
        self.values = np.asarray(values)
        self.orientation = orientation
        self.row, self.column, self.sequence = row, column, sequence
        if self._hover_position is not None:
            self._hover_index = self.index_at(self._hover_position)
        if self._hover_index is not None:
            self.hover_text_changed.emit(self.hover_text(self._hover_index))
        self.update()

    def set_logarithmic(self, enabled):
        self.logarithmic = bool(enabled)
        self.update()

    def plot_rect(self):
        return QRectF(62, 34, max(1, self.width() - 80), max(1, self.height() - 70))

    def index_at(self, point: QPointF):
        """Map a point inside the chart to its nearest actual source pixel."""
        if self.values is None or not len(self.values) or not self.plot_rect().contains(point):
            return None
        rect = self.plot_rect()
        fraction = (point.x() - rect.left()) / rect.width()
        return min(len(self.values) - 1, max(0, int(fraction * (len(self.values) - 1) + 0.5)))

    def values_at(self, index: int):
        if self.values is None or index < 0 or index >= len(self.values):
            raise IndexError("No profile sample at that pixel position")
        return tuple(int(value) for value in self.values[index])

    def hover_text(self, index: int):
        red, green, blue = self.values_at(index)
        axis = "x" if self.orientation == "horizontal" else "y"
        fixed = f"row y = {self.row}" if self.orientation == "horizontal" else f"column x = {self.column}"
        return f"{axis} = {index}  ·  R {red}  G {green}  B {blue}  ·  {fixed}  ·  frame {self.sequence}"

    def mouseMoveEvent(self, event):
        self._hover_index = self.index_at(event.position())
        self._hover_position = event.position() if self._hover_index is not None else None
        self.hover_text_changed.emit(
            self.hover_text(self._hover_index) if self._hover_index is not None else ""
        )
        self.update()

    def leaveEvent(self, event):
        self._hover_index = None
        self._hover_position = None
        self.hover_text_changed.emit("")
        self.update()
        super().leaveEvent(event)

    def _title(self):
        if self.kind == "histogram":
            title = "Full-image histogram"
        else:
            title = f"Row y = {self.row}" if self.orientation == "horizontal" else f"Column x = {self.column}"
        return f"{title} · frame {self.sequence}" if self.sequence is not None else title

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("white"))
        painter.setPen(QPen(QColor("#dfe5ed"), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        font = QFont(painter.font())
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QColor("#34465d"))
        title_rect = QRectF(11, 5, max(20, self.width() - 116), 22)
        title = painter.fontMetrics().elidedText(self._title(), Qt.TextElideMode.ElideRight, int(title_rect.width()))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, title)
        for channel, color in enumerate(CHANNEL_COLORS):
            left = self.width() - 99 + channel * 31
            painter.setPen(QPen(QColor(color), 2))
            painter.drawLine(QPointF(left, 16), QPointF(left + 9, 16))
            painter.drawText(QRectF(left + 12, 5, 15, 22), Qt.AlignmentFlag.AlignVCenter, "RGB"[channel])

        rect = self.plot_rect()
        profile = self.kind == "profile"
        if profile:
            upper = 255.0
        else:
            peak = float(np.max(self.values)) if self.values is not None and self.values.size else 1.0
            upper = max(1.0, np.log10(1 + peak) if self.logarithmic else peak)
        x_max = max(0, len(self.values) - 1) if self.values is not None else (255 if not profile else 1)
        for fraction in (0.0, 0.5, 1.0):
            y = rect.bottom() - fraction * rect.height()
            painter.setPen(QPen(QColor("#edf0f5"), 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            amount = fraction * upper
            if not profile and self.logarithmic:
                amount = 10 ** amount - 1
            label = str(round(amount)) if profile else _count_label(amount)
            painter.setPen(QColor("#657387"))
            painter.drawText(QRectF(18, y - 8, 37, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
            index = round(fraction * x_max)
            x = rect.left() + fraction * rect.width()
            painter.drawText(QRectF(x - 24, rect.bottom() + 3, 48, 16), Qt.AlignmentFlag.AlignCenter, str(index))
        painter.setPen(QPen(QColor("#bac5d3"), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.topLeft())
        painter.setPen(QColor("#657387"))
        xlabel = ("Pixel x (0-based)" if self.orientation == "horizontal" else "Pixel y (0-based)") if profile else "Intensity (0–255)"
        painter.drawText(QRectF(rect.left(), self.height() - 17, rect.width(), 16), Qt.AlignmentFlag.AlignCenter, xlabel)
        painter.save()
        painter.translate(12, rect.center().y())
        painter.rotate(-90)
        ylabel = "Intensity" if profile else ("Count (log)" if self.logarithmic else "Pixel count")
        painter.drawText(QRectF(-rect.height() / 2, -8, rect.height(), 16), Qt.AlignmentFlag.AlignCenter, ylabel)
        painter.restore()

        if self.values is None or not len(self.values):
            painter.setPen(QColor("#8290a2"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Waiting for a color frame")
            return

        painter.save()
        painter.setClipRect(rect.adjusted(-1, -1, 1, 1))
        values = self.values.astype(np.float64, copy=False)
        if not profile and self.logarithmic:
            values = np.log10(1 + values)
        xscale = rect.width() / max(1, len(values) - 1)
        for channel, color in enumerate(CHANNEL_COLORS):
            path = QPainterPath()
            for index, value in enumerate(values[:, channel]):
                point = QPointF(rect.left() + index * xscale, rect.bottom() - float(value) / upper * rect.height())
                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            painter.setPen(QPen(QColor(color), 1.2))
            if len(values) == 1:
                painter.drawEllipse(path.currentPosition(), 2, 2)
            else:
                painter.drawPath(path)
        if profile and self._hover_index is not None:
            x = rect.left() + self._hover_index * xscale
            painter.setPen(QPen(QColor("#66758a"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            for channel, color in enumerate(CHANNEL_COLORS):
                y = rect.bottom() - float(values[self._hover_index, channel]) / upper * rect.height()
                painter.setPen(QPen(QColor(color), 1))
                painter.setBrush(QColor(color))
                painter.drawEllipse(QPointF(x, y), 2.5, 2.5)
        painter.restore()


class AnalysisPanel(QWidget):
    selection_changed = Signal(int, int)
    orientation_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._width = self._height = 0
        self._result = None
        self.setMinimumHeight(235)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 5)
        layout.setSpacing(5)
        controls = QHBoxLayout()
        controls.setSpacing(9)
        controls.addWidget(QLabel("Line profile"))
        self.orientation_combo = QComboBox()
        self.orientation_combo.addItem("Horizontal row", "horizontal")
        self.orientation_combo.addItem("Vertical column", "vertical")
        self.orientation_combo.setAccessibleName("Profile orientation")
        self.orientation_combo.currentIndexChanged.connect(self._orientation_updated)
        controls.addWidget(self.orientation_combo)
        self.row_spin = QSpinBox()
        self.column_spin = QSpinBox()
        for label, spin in (("Row y", self.row_spin), ("Column x", self.column_spin)):
            controls.addWidget(QLabel(label))
            spin.setRange(0, 0)
            spin.setKeyboardTracking(False)
            spin.setAccessibleName(f"{label}, zero based")
            spin.setToolTip("Pixel index, starting at 0")
            spin.setMinimumWidth(70)
            spin.valueChanged.connect(self._selection_updated)
            controls.addWidget(spin)
        note = QLabel("0-based")
        note.setObjectName("hint")
        controls.addWidget(note)
        controls.addStretch()
        self.log_check = QCheckBox("Log count")
        self.log_check.setToolTip("Use a logarithmic histogram count axis")
        controls.addWidget(self.log_check)
        controls.addSpacing(12)
        self.live_label = QLabel()
        self.live_label.setObjectName("hint")
        controls.addWidget(self.live_label)
        layout.addLayout(controls)
        charts = QHBoxLayout()
        charts.setSpacing(9)
        self.histogram_chart = RGBChart("histogram")
        self.profile_chart = RGBChart("profile")
        self.log_check.toggled.connect(self.histogram_chart.set_logarithmic)
        charts.addWidget(self.histogram_chart, 1)
        charts.addWidget(self.profile_chart, 1)
        layout.addLayout(charts, 1)
        self.hover_label = QLabel(PROFILE_HINT)
        self.hover_label.setObjectName("hint")
        self.profile_chart.hover_text_changed.connect(self._hover_updated)
        layout.addWidget(self.hover_label)
        self.clear()

    def sizeHint(self):
        return QSize(960, 270)

    @property
    def row(self):
        return self.row_spin.value()

    @property
    def column(self):
        return self.column_spin.value()

    @property
    def orientation(self):
        return self.orientation_combo.currentData()

    def set_image_size(self, width, height):
        if width <= 0 or height <= 0:
            raise ValueError("Image dimensions must be positive")
        first_image = self._width == 0 or self._height == 0
        old_selection = (self.row, self.column)
        self._width, self._height = int(width), int(height)
        blockers = [QSignalBlocker(self.row_spin), QSignalBlocker(self.column_spin)]
        self.row_spin.setRange(0, self._height - 1)
        self.column_spin.setRange(0, self._width - 1)
        if first_image:
            self.row_spin.setValue(self._height // 2)
            self.column_spin.setValue(self._width // 2)
        del blockers
        for control in (self.row_spin, self.column_spin, self.orientation_combo, self.log_check):
            control.setEnabled(True)
        if old_selection != (self.row, self.column):
            self.selection_changed.emit(self.row, self.column)

    def set_selection(self, row, column):
        old_selection = (self.row, self.column)
        blockers = [QSignalBlocker(self.row_spin), QSignalBlocker(self.column_spin)]
        self.row_spin.setValue(int(row))
        self.column_spin.setValue(int(column))
        del blockers
        if old_selection != (self.row, self.column):
            self._selection_updated()

    def _selection_updated(self):
        self.selection_changed.emit(self.row, self.column)
        self._hover_updated("")

    def _orientation_updated(self):
        self._update_profile()
        self.orientation_changed.emit(self.orientation)

    def set_result(self, result):
        if (self._width, self._height) != (result.width, result.height):
            self.set_image_size(result.width, result.height)
        self._result = result
        self.histogram_chart.set_histogram(result.histogram, result.sequence)
        self._update_profile()

    def _update_profile(self):
        if self._result is not None:
            result = self._result
            values = result.horizontal if self.orientation == "horizontal" else result.vertical
            self.profile_chart.set_profile(values, self.orientation, result.row, result.column, result.sequence)
        index = self.profile_chart._hover_index
        self._hover_updated(self.profile_chart.hover_text(index) if index is not None else "")

    def _hover_updated(self, text):
        if not text:
            text = PROFILE_HINT
            if self._result is not None:
                pending = self.row != self._result.row if self.orientation == "horizontal" else self.column != self._result.column
                if pending:
                    text = "Updating selected line… chart labels identify the displayed frame and line."
        self.hover_label.setText(text)

    def set_live(self, live):
        self.live_label.setText("Live · up to 5 Hz" if live else "Last frame")

    def clear(self):
        self._result = None
        self._width = self._height = 0
        blockers = [QSignalBlocker(self.row_spin), QSignalBlocker(self.column_spin)]
        self.row_spin.setRange(0, 0)
        self.column_spin.setRange(0, 0)
        del blockers
        for control in (self.row_spin, self.column_spin, self.orientation_combo, self.log_check):
            control.setEnabled(False)
        self.histogram_chart.clear()
        self.profile_chart.clear()
        self.live_label.setText("Waiting for frame")
        self._hover_updated("")
