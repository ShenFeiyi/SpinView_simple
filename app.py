"""SpinView Simple — a focused desktop interface for a Blackfly S camera."""

import sys
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QAction, QImage, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDockWidget, QDoubleSpinBox, QFileDialog, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from camera import CameraWorker
from analysis_panel import AnalysisPanel
from image_analysis import AnalysisWorker
from image_io import IMAGE_FILTERS, save_rgb_image
from preview import PreviewWidget


STYLE = """
QMainWindow, QWidget#root { background: #f3f5f8; color: #202c3b; }
QWidget { font-family: '.AppleSystemUIFont', 'Helvetica Neue'; font-size: 13px; }
QLabel { color: #263345; background: transparent; }
QLabel#title { font-size: 24px; font-weight: 700; color: #172436; }
QLabel#subtitle, QLabel#hint { color: #657387; }
QLabel#hint { font-size: 11px; }
QLabel#pixelReadout { color: #34465d; font-size: 11px; font-family: 'Menlo', monospace; }
QLabel#sectionTitle { font-size: 11px; font-weight: 700; color: #718095; }
QLabel#liveBadge { color: #197e68; background: #e1f1eb; border-radius: 10px;
                   padding: 5px 10px; font-size: 11px; font-weight: 600; }
QPushButton { color: #344256; background: white; border: 1px solid #d7dee7;
              border-radius: 7px; padding: 9px 15px; font-weight: 500; }
QPushButton:hover { background: #edf2f7; border-color: #bbc8d7; }
QPushButton:pressed { background: #e1e8f1; }
QPushButton:checked { background: #e8f0fc; border-color: #9ebbe6; color: #195fc5; }
QPushButton:disabled { color: #99a5b3; background: #f1f3f6; border-color: #e1e6ed; }
QPushButton#primary { color: white; background: #216cdb; border-color: #216cdb; }
QPushButton#primary:hover { background: #195fc5; }
QPushButton#primary:disabled { color: #e7edf6; background: #a5bddf; border-color: #a5bddf; }
QPushButton#autoButton { padding: 3px 10px; font-size: 11px; }
QPushButton#zoomReset { padding: 4px 10px; font-size: 12px; }
QGroupBox { background: white; border: 1px solid #dfe5ed; border-radius: 10px;
            margin-top: 15px; padding: 16px 14px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px;
                   color: #34465d; }
QDoubleSpinBox, QSpinBox, QComboBox { background: #f8fafc; color: #24364e; border: 1px solid #d8e0eb;
                 border-radius: 6px; padding: 7px 10px; min-height: 20px;
                 selection-background-color: #216cdb; }
QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #216cdb; }
QDoubleSpinBox:disabled, QSpinBox:disabled { color: #9ba5b2; background: #f2f4f7; }
QDockWidget { color: #34465d; font-weight: 600; }
QDockWidget::title { background: #edf1f6; padding: 5px 12px; }
QCheckBox { color: #35455a; spacing: 8px; }
QScrollArea { background: transparent; border: none; }
QWidget#controlsPanel { background: transparent; }
QFrame#previewFrame { background: #10151d; border: 1px solid #dfe5ed; border-radius: 10px; }
QLabel#errorBanner { background: #fff0e9; color: #963b20; padding: 10px 14px;
                     border: 1px solid #f0c9b8; border-radius: 7px; }
"""


class ElidedLabel(QLabel):
    """Keep footer messages on one line, with their full text in a tooltip."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width()
        )
        painter.drawText(self.contentsRect(), self.alignment(), text)


class ControlField(QWidget):
    def __init__(self, label, suffix, decimals, step, parent=None, *, auto_label=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.label = QLabel(label)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step)
        self.spin.setSuffix(suffix)
        self.spin.setKeyboardTracking(False)
        self.spin.setEnabled(False)
        self.spin.setAccessibleName(label)
        self.label.setBuddy(self.spin)
        label_row = QHBoxLayout()
        label_row.setContentsMargins(0, 0, 0, 0)
        label_row.addWidget(self.label)
        label_row.addStretch()
        self.auto_button = None
        if auto_label:
            self.auto_button = QPushButton("Auto")
            self.auto_button.setObjectName("autoButton")
            self.auto_button.setAccessibleName(auto_label)
            self.auto_button.setToolTip(f"{auto_label}. Adjust once, then return to manual control.")
            self.auto_button.setEnabled(False)
            label_row.addWidget(self.auto_button)
        layout.addLayout(label_row)
        layout.addWidget(self.spin)


class MainWindow(QMainWindow):
    def __init__(self, worker_factory=CameraWorker, auto_connect=True):
        super().__init__()
        self._worker_factory = worker_factory
        self._worker = None
        self._frame = None
        self._last_sequence = -1
        self._closing = False
        self._stopping = False
        self._analysis_generation = 0
        self._analysis_request = None
        self._analysis_sequence = -1
        self._analysis_worker = AnalysisWorker(self)
        self._analysis_worker.result_ready.connect(self._on_analysis_result)
        self._analysis_worker.error.connect(self._on_analysis_error)
        self._analysis_worker.finished.connect(self._on_analysis_finished)
        self._last_directory = Path.home() / "Pictures"
        if not self._last_directory.exists():
            self._last_directory = Path.home()
        self.setWindowTitle("SpinView Simple")
        self.resize(1240, 940)
        self.setMinimumSize(880, 650)
        self._build_ui()
        self.setStyleSheet(STYLE)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._update_preview)
        self._timer.start()
        self._analysis_timer = QTimer(self)
        self._analysis_timer.setInterval(200)
        self._analysis_timer.timeout.connect(self._request_analysis)
        self._analysis_timer.start()
        if auto_connect:
            QTimer.singleShot(0, self.connect_camera)

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(15)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(4)
        title = QLabel("SpinView Simple")
        title.setObjectName("title")
        self.camera_label = QLabel("Blackfly S · USB 3 camera")
        self.camera_label.setObjectName("subtitle")
        heading.addWidget(title)
        heading.addWidget(self.camera_label)
        header.addLayout(heading)
        header.addStretch()
        self.analysis_button = QPushButton("Histogram && profile")
        self.analysis_button.setToolTip("Show full-image RGB histogram and selectable row/column profiles")
        self.analysis_button.setCheckable(True)
        self.analysis_button.setChecked(True)
        header.addWidget(self.analysis_button)
        self.connection_button = QPushButton("Connect camera")
        self.connection_button.clicked.connect(self.toggle_connection)
        self.save_button = QPushButton("Save image…")
        self.save_button.setObjectName("primary")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_image)
        header.addWidget(self.connection_button)
        header.addWidget(self.save_button)
        layout.addLayout(header)

        self.error_banner = QLabel()
        self.error_banner.setObjectName("errorBanner")
        self.error_banner.setWordWrap(True)
        self.error_banner.hide()
        layout.addWidget(self.error_banner)

        body = QHBoxLayout()
        body.setSpacing(18)
        preview_column = QVBoxLayout()
        preview_column.setSpacing(8)
        preview_heading = QHBoxLayout()
        label = QLabel("LIVE COLOR PREVIEW")
        label.setObjectName("sectionTitle")
        preview_heading.addWidget(label)
        preview_heading.addStretch()
        self.live_badge = QLabel("OFFLINE")
        self.live_badge.setObjectName("liveBadge")
        preview_heading.addWidget(self.live_badge)
        preview_column.addLayout(preview_heading)
        frame = QFrame()
        frame.setObjectName("previewFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        self.preview = PreviewWidget()
        self.preview.setMinimumHeight(160)
        frame_layout.addWidget(self.preview)
        preview_column.addWidget(frame, 1)
        footer = QVBoxLayout()
        footer.setSpacing(3)
        preview_column.addLayout(footer)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Zoom"))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(10, 80)
        self.zoom_slider.setValue(10)
        self.zoom_slider.setPageStep(10)
        self.zoom_slider.setEnabled(False)
        self.zoom_slider.setAccessibleName("Preview zoom")
        self.zoom_slider.setToolTip("1× fits the full image; zoom up to 8× and drag to pan.")
        self.zoom_slider.valueChanged.connect(lambda value: self.preview.set_zoom(value / 10))
        zoom_row.addWidget(self.zoom_slider, 1)
        self.zoom_label = QLabel("1.0×")
        self.zoom_label.setMinimumWidth(42)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        zoom_row.addWidget(self.zoom_label)
        self.zoom_reset_button = QPushButton("1×")
        self.zoom_reset_button.setObjectName("zoomReset")
        self.zoom_reset_button.setAccessibleName("Reset preview zoom to 1×")
        self.zoom_reset_button.setToolTip("Reset zoom and center the full image")
        self.zoom_reset_button.setEnabled(False)
        self.zoom_reset_button.clicked.connect(self.preview.reset_zoom)
        zoom_row.addWidget(self.zoom_reset_button)
        self.preview.zoom_changed.connect(self._on_preview_zoom)
        footer.addLayout(zoom_row)
        info_row = QHBoxLayout()
        info_row.setSpacing(6)
        self.frame_label = QLabel("Waiting for the first frame")
        self.frame_label.setObjectName("hint")
        self.frame_label.setToolTip("Full image dimensions · RGB 8-bit · Received frame rate. Preview updates up to 30 fps.")
        info_row.addWidget(self.frame_label)
        info_row.addWidget(QLabel("|"))
        self.pixel_info_label = QLabel("Hover over the image for x/y + RGB")
        self.pixel_info_label.setObjectName("pixelReadout")
        self.pixel_info_label.setAccessibleName("Preview pixel information")
        self.pixel_info_label.setToolTip(
            "Original image coordinates, starting at (0, 0) in the top-left. "
            "RGB values use the displayed full-resolution color frame, from 0 to 255."
        )
        self.preview.pixel_info_changed.connect(self._on_preview_pixel_info)
        info_row.addWidget(self.pixel_info_label)
        info_row.addStretch()
        footer.addLayout(info_row)
        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self.status_label = ElidedLabel("Ready to connect")
        self.status_label.setObjectName("hint")
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(QLabel("|"))
        self.zoom_hint = ElidedLabel("1× fits the image · Zoom in to pan")
        self.zoom_hint.setObjectName("hint")
        status_row.addWidget(self.zoom_hint, 1)
        footer.addLayout(status_row)
        body.addLayout(preview_column, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(302)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_widget = QWidget()
        controls_widget.setObjectName("controlsPanel")
        controls = QVBoxLayout(controls_widget)
        controls.setContentsMargins(0, 0, 3, 0)
        controls.setSpacing(14)
        self.controls = {}
        self.auto_buttons = {}
        acquisition = QGroupBox("Acquisition")
        acquisition_layout = QVBoxLayout(acquisition)
        acquisition_layout.setSpacing(10)
        for name, label, suffix, decimals, step in (
            ("exposure_us", "Exposure time", " ms", 3, 0.1),
            ("gain_db", "Gain", " dB", 2, 0.5),
            ("frame_rate", "Frame rate", " fps", 2, 1.0),
        ):
            auto_label = {"exposure_us": "Auto exposure", "gain_db": "Auto gain"}.get(name)
            field = ControlField(label, suffix, decimals, step, auto_label=auto_label)
            field.spin.valueChanged.connect(
                lambda value, key=name: self._set_control(key, value)
            )
            if field.auto_button is not None:
                self.auto_buttons[name] = field.auto_button
                field.auto_button.clicked.connect(lambda checked=False, key=name: self._auto_once(key))
            self.controls[name] = field
            acquisition_layout.addWidget(field)
        hint = QLabel("Longer exposure can limit the actual frame rate.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        acquisition_layout.addWidget(hint)
        controls.addWidget(acquisition)

        white_balance = QGroupBox("White balance")
        balance_layout = QVBoxLayout(white_balance)
        balance_layout.setSpacing(10)
        for name, label in (("wb_red", "Red ratio"), ("wb_blue", "Blue ratio")):
            field = ControlField(label, " ×", 3, 0.05)
            field.spin.valueChanged.connect(
                lambda value, key=name: self._set_control(key, value)
            )
            self.controls[name] = field
            balance_layout.addWidget(field)
        self.balance_button = QPushButton("Auto white balance once")
        self.balance_button.setEnabled(False)
        self.balance_button.clicked.connect(self._white_balance_once)
        balance_layout.addWidget(self.balance_button)
        hint = QLabel("For auto balance, fill the view with a neutral white or gray surface.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        balance_layout.addWidget(hint)
        controls.addWidget(white_balance)

        display = QGroupBox("Preview overlay")
        display_layout = QVBoxLayout(display)
        self.crosshair_check = QCheckBox("Center crosshair")
        self.crosshair_check.setChecked(True)
        self.crosshair_check.toggled.connect(self.preview.set_crosshair)
        display_layout.addWidget(self.crosshair_check)
        hint = QLabel("Overlay is shown only in the preview.")
        hint.setObjectName("hint")
        display_layout.addWidget(hint)
        controls.addWidget(display)

        save_note = QLabel("Snapshots save the full-resolution color image.\nTIFF · PNG · JPEG · BMP")
        save_note.setObjectName("hint")
        save_note.setWordWrap(True)
        controls.addWidget(save_note)
        controls.addStretch()
        scroll.setWidget(controls_widget)
        body.addWidget(scroll)
        layout.addLayout(body, 1)

        action = QAction("Save image…", self)
        action.setShortcut(QKeySequence.StandardKey.Save)
        action.triggered.connect(self.save_image)
        self.addAction(action)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(action)
        quit_action = QAction("Quit SpinView Simple", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        self.analysis_panel = AnalysisPanel()
        self.analysis_dock = QDockWidget("Image analysis", self)
        self.analysis_dock.setObjectName("imageAnalysisDock")
        self.analysis_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.analysis_dock.setWidget(self.analysis_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.analysis_dock)
        self.analysis_button.toggled.connect(self.analysis_dock.setVisible)
        self.analysis_dock.visibilityChanged.connect(self._analysis_visibility_changed)
        self.analysis_panel.selection_changed.connect(self._analysis_selection_changed)
        self.analysis_panel.orientation_changed.connect(self._analysis_orientation_changed)
        self.preview.pixel_selected.connect(self._select_profile_pixel)
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.analysis_dock.toggleViewAction())

    def toggle_connection(self):
        if self._worker is not None and self._worker.isRunning():
            self._stopping = True
            self.connection_button.setEnabled(False)
            self.connection_button.setText("Disconnecting…")
            self.status_label.setText("Stopping acquisition…")
            self._disable_controls()
            self._worker.stop()
        else:
            self.connect_camera()

    def connect_camera(self):
        if self._worker is not None:
            return
        self.error_banner.hide()
        self._stopping = False
        self._frame = None
        self._last_sequence = -1
        self._analysis_generation += 1
        self._analysis_request = None
        self._analysis_sequence = -1
        self.analysis_panel.clear()
        self.analysis_panel.set_live(False)
        self.preview.clear("Connecting to camera…")
        self.zoom_slider.setEnabled(False)
        self.zoom_reset_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.connection_button.setEnabled(False)
        self.connection_button.setText("Connecting…")
        self.status_label.setText("Opening the camera and starting color acquisition…")
        worker = self._worker_factory()
        self._worker = worker
        worker.connected.connect(self._on_connected)
        worker.settings_changed.connect(self._on_settings)
        worker.error.connect(self._on_error)
        worker.status.connect(self.status_label.setText)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _on_connected(self, info):
        if self._stopping or self._closing:
            return
        self.camera_label.setText(f"{info['model']} · Serial {info['serial']} · USB 3")
        self.connection_button.setText("Disconnect")
        self.connection_button.setEnabled(True)
        self.status_label.setText("Camera connected. Waiting for the first color frame…")

    def _on_settings(self, settings):
        if self._stopping or self._closing:
            self._disable_controls()
            return
        for name, field in self.controls.items():
            state = settings.get(name, {})
            enabled = state.get("enabled", False)
            field.spin.setEnabled(enabled)
            field.spin.setToolTip(state.get("reason", ""))
            if "value" in state:
                divisor = 1000.0 if name == "exposure_us" else 1.0
                blocker = QSignalBlocker(field.spin)
                minimum, maximum = state["min"] / divisor, state["max"] / divisor
                if field.spin.minimum() != round(minimum, field.spin.decimals()) or field.spin.maximum() != round(maximum, field.spin.decimals()):
                    field.spin.setRange(minimum, maximum)
                value = round(state["value"] / divisor, field.spin.decimals())
                # Frequent auto readbacks must not erase an uncommitted edit in
                # an unrelated control whose actual value has not changed.
                if field.spin.value() != value:
                    field.spin.setValue(value)
                del blocker
        for name, prefix in (("exposure_us", "exposure"), ("gain_db", "gain")):
            button = self.auto_buttons[name]
            busy = settings.get(f"{prefix}_busy", False)
            button.setText("Adjusting…" if busy else "Auto")
            button.setEnabled(settings.get(f"{prefix}_once_enabled", False) and not busy)
        self.balance_button.setEnabled(settings.get("white_balance_once_enabled", False))
        self.balance_button.setText(
            "Adjusting white balance…" if settings.get("white_balance_busy", False)
            else "Auto white balance once"
        )

    def _set_control(self, name, value):
        if self._worker is not None and self._worker.isRunning() and not self._stopping:
            if name == "exposure_us":
                value *= 1000.0
            self._worker.set_control(name, value)

    def _white_balance_once(self):
        if self._worker is not None:
            self.balance_button.setEnabled(False)
            self._worker.white_balance_once()

    def _auto_once(self, name):
        if self._worker is None or not self._worker.isRunning() or self._stopping or self._closing:
            return
        self.controls[name].spin.setEnabled(False)
        self.auto_buttons[name].setEnabled(False)
        self.auto_buttons[name].setText("Adjusting…")
        if name == "exposure_us":
            self.status_label.setText("Automatically adjusting exposure…")
            self._worker.exposure_once()
        else:
            self.status_label.setText("Automatically adjusting gain…")
            self._worker.gain_once()

    def _update_preview(self):
        if self._worker is None or self._closing or self._stopping:
            return
        frame = self._worker.latest_frame()
        if frame is None or frame.sequence == self._last_sequence:
            return
        self._frame = frame
        self._last_sequence = frame.sequence
        rgb = frame.rgb
        height, width, _ = rgb.shape
        image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888)
        self.preview.set_image(image)
        self.zoom_slider.setEnabled(True)
        self.zoom_reset_button.setEnabled(True)
        self.analysis_panel.set_image_size(width, height)
        self.analysis_panel.set_live(True)
        self._update_profile_guide()
        self.live_badge.setText("● LIVE")
        self.frame_label.setText(
            f"{width}×{height} · RGB8 · {frame.fps:.1f} fps"
        )
        self.save_button.setEnabled(True)

    def _update_profile_guide(self):
        self.preview.set_profile_selection(
            self.analysis_panel.row, self.analysis_panel.column,
            self.analysis_panel.orientation,
            enabled=self.analysis_dock.isVisible() and self._frame is not None,
        )

    def _on_preview_pixel_info(self, info):
        if info is None:
            self.pixel_info_label.setText("Hover over the image for x/y + RGB")
            return
        x, y, red, green, blue = info
        self.pixel_info_label.setText(f"x {x}, y {y} · RGB {red}/{green}/{blue}")

    def _on_preview_zoom(self, zoom):
        blocker = QSignalBlocker(self.zoom_slider)
        self.zoom_slider.setValue(round(zoom * 10))
        del blocker
        self.zoom_label.setText(f"{zoom:.1f}×")
        self._update_zoom_hint()

    def _update_zoom_hint(self):
        if self.preview.zoom <= 1:
            self.zoom_hint.setText("1× fits the image · Zoom in to pan")
        elif self.analysis_dock.isVisible():
            self.zoom_hint.setText("Drag to pan · Shift-drag selects profile")
        else:
            self.zoom_hint.setText("Drag to pan · 1× resets the view")

    def _analysis_visibility_changed(self, visible):
        blocker = QSignalBlocker(self.analysis_button)
        self.analysis_button.setChecked(visible)
        del blocker
        self._update_profile_guide()
        self._update_zoom_hint()
        # Keep the preview's coordinate viewport fully visible at small window
        # sizes, including when the dock and its controls are shown.
        self.setMinimumHeight(max(650, self.minimumSizeHint().height()))
        if visible:
            self._request_analysis()

    def _analysis_selection_changed(self, row, column):
        self._update_profile_guide()
        # The 5 Hz timer picks up the latest selection, so dragging does not
        # queue a full-image histogram for every mouse movement.

    def _analysis_orientation_changed(self, orientation):
        self._update_profile_guide()

    def _select_profile_pixel(self, column, row):
        self.analysis_panel.set_selection(row, column)

    def _request_analysis(self):
        frame = self._frame
        if self._closing or frame is None or not self.analysis_dock.isVisible():
            return
        row, column = self.analysis_panel.row, self.analysis_panel.column
        request = (self._analysis_generation, frame.sequence, row, column)
        if request == self._analysis_request:
            return
        self._analysis_request = request
        if not self._analysis_worker.isRunning():
            self._analysis_worker.start()
        self._analysis_worker.submit(frame, row, column, generation=self._analysis_generation)

    def _on_analysis_result(self, result):
        try:
            if (self._closing or self._frame is None or
                    result.generation != self._analysis_generation or
                    result.sequence < self._analysis_sequence or
                    result.row != self.analysis_panel.row or
                    result.column != self.analysis_panel.column):
                return
            self._analysis_sequence = result.sequence
            self.analysis_panel.set_result(result)
        finally:
            self._analysis_worker.acknowledge_result(result.sequence)

    def _on_analysis_error(self, message):
        if not self._closing:
            self.status_label.setText(f"Image analysis: {message}")

    def _on_analysis_finished(self):
        if self._closing:
            QTimer.singleShot(0, self.close)

    def _on_error(self, message):
        self.error_banner.setText(message)
        self.error_banner.show()
        self.status_label.setText("Camera reported an error. See the message above.")

    def _disable_controls(self):
        for field in self.controls.values():
            field.spin.setEnabled(False)
        self.balance_button.setEnabled(False)
        for button in self.auto_buttons.values():
            button.setEnabled(False)

    def _on_finished(self):
        worker = self._worker
        self._worker = None
        self._stopping = False
        if worker is not None:
            worker.deleteLater()
        self._disable_controls()
        self.balance_button.setText("Auto white balance once")
        for button in self.auto_buttons.values():
            button.setText("Auto")
        self.connection_button.setEnabled(True)
        self.connection_button.setText("Connect camera")
        self.live_badge.setText("OFFLINE")
        self.analysis_panel.set_live(False)
        if self._frame is None:
            self.preview.clear("Camera unavailable\n\nConnect a USB 3 camera, close SpinView, and click Connect camera.")
        else:
            self.live_badge.setText("LAST FRAME")
            height, width = self._frame.rgb.shape[:2]
            self.frame_label.setText(f"{width}×{height} · RGB8 · Last frame")
        if not self.error_banner.isVisible():
            self.status_label.setText("Camera disconnected. You can reconnect at any time.")
        if self._closing:
            QTimer.singleShot(0, self.close)

    def save_image(self):
        # Freeze the displayed, full-resolution frame before opening the dialog.
        frame = self._frame
        if frame is None:
            return
        stamp = frame.captured_at.strftime("%Y%m%d_%H%M%S_%f")
        dialog = QFileDialog(self, "Save full-resolution color image", str(self._last_directory))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setNameFilters(IMAGE_FILTERS.split(";;"))
        dialog.selectFile(f"Blackfly_{stamp}.tif")
        dialog.setDefaultSuffix("tif")
        dialog.filterSelected.connect(
            lambda selected: dialog.setDefaultSuffix(
                {"TIFF": "tif", "PNG": "png", "JPEG": "jpg", "Bitmap": "bmp"}.get(selected.split()[0], "tif")
            )
        )
        if not dialog.exec():
            return
        filename = dialog.selectedFiles()[0]
        try:
            saved = save_rgb_image(frame.rgb, filename)
        except Exception as error:
            QMessageBox.warning(self, "Image could not be saved", str(error))
            return
        self._last_directory = Path(saved).parent
        self.status_label.setText(f"Saved {Path(saved).name} · {frame.rgb.shape[1]} × {frame.rgb.shape[0]} · {Path(saved).parent}")

    def closeEvent(self, event):
        self._closing = True
        self._analysis_timer.stop()
        self._analysis_worker.stop()
        camera_running = self._worker is not None and self._worker.isRunning()
        if camera_running or self._analysis_worker.isRunning():
            self._stopping = True
            self.connection_button.setEnabled(False)
            self.save_button.setEnabled(False)
            self._disable_controls()
            self.status_label.setText("Closing the camera and restoring its previous settings…")
            if camera_running:
                self._worker.stop()
            event.ignore()
        else:
            self._timer.stop()
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SpinView Simple")
    app.setOrganizationName("SpinView Simple")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
