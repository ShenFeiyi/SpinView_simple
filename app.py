"""SpinView Simple — a focused desktop interface for a Blackfly S camera."""

import sys
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QAction, QImage, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from camera import CameraWorker
from image_io import IMAGE_FILTERS, save_rgb_image
from preview import PreviewWidget


STYLE = """
QMainWindow, QWidget#root { background: #f3f5f8; color: #202c3b; }
QWidget { font-family: '.AppleSystemUIFont', 'Helvetica Neue'; font-size: 13px; }
QLabel { color: #263345; background: transparent; }
QLabel#title { font-size: 24px; font-weight: 700; color: #172436; }
QLabel#subtitle, QLabel#hint { color: #657387; }
QLabel#hint { font-size: 11px; }
QLabel#sectionTitle { font-size: 11px; font-weight: 700; color: #718095; }
QLabel#liveBadge { color: #197e68; background: #e1f1eb; border-radius: 10px;
                   padding: 5px 10px; font-size: 11px; font-weight: 600; }
QPushButton { color: #344256; background: white; border: 1px solid #d7dee7;
              border-radius: 7px; padding: 9px 15px; font-weight: 500; }
QPushButton:hover { background: #edf2f7; border-color: #bbc8d7; }
QPushButton:pressed { background: #e1e8f1; }
QPushButton:disabled { color: #99a5b3; background: #f1f3f6; border-color: #e1e6ed; }
QPushButton#primary { color: white; background: #216cdb; border-color: #216cdb; }
QPushButton#primary:hover { background: #195fc5; }
QPushButton#primary:disabled { color: #e7edf6; background: #a5bddf; border-color: #a5bddf; }
QPushButton#autoButton { padding: 3px 10px; font-size: 11px; }
QGroupBox { background: white; border: 1px solid #dfe5ed; border-radius: 10px;
            margin-top: 15px; padding: 16px 14px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px;
                   color: #34465d; }
QDoubleSpinBox { background: #f8fafc; color: #24364e; border: 1px solid #d8e0eb;
                 border-radius: 6px; padding: 7px 10px; min-height: 20px;
                 selection-background-color: #216cdb; }
QDoubleSpinBox:focus { border: 1px solid #216cdb; }
QDoubleSpinBox:disabled { color: #9ba5b2; background: #f2f4f7; }
QCheckBox { color: #35455a; spacing: 8px; }
QScrollArea { background: transparent; border: none; }
QWidget#controlsPanel { background: transparent; }
QFrame#previewFrame { background: #10151d; border: 1px solid #dfe5ed; border-radius: 10px; }
QLabel#errorBanner { background: #fff0e9; color: #963b20; padding: 10px 14px;
                     border: 1px solid #f0c9b8; border-radius: 7px; }
"""


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
        self._last_directory = Path.home() / "Pictures"
        if not self._last_directory.exists():
            self._last_directory = Path.home()
        self.setWindowTitle("SpinView Simple")
        self.resize(1240, 850)
        self.setMinimumSize(880, 650)
        self._build_ui()
        self.setStyleSheet(STYLE)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._update_preview)
        self._timer.start()
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
        frame_layout.addWidget(self.preview)
        preview_column.addWidget(frame, 1)
        self.frame_label = QLabel("Waiting for the first frame")
        self.frame_label.setObjectName("hint")
        preview_column.addWidget(self.frame_label)
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

        self.status_label = QLabel("Ready to connect")
        self.status_label.setObjectName("subtitle")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
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
        self.preview.clear("Connecting to camera…")
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
        self.live_badge.setText("● LIVE")
        self.frame_label.setText(
            f"{width:,} × {height:,} px  ·  RGB 8-bit  ·  {frame.fps:.1f} fps received  ·  Preview up to 30 fps"
        )
        self.save_button.setEnabled(True)

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
        if self._frame is None:
            self.preview.clear("Camera unavailable\n\nConnect a USB 3 camera, close SpinView, and click Connect camera.")
        else:
            self.live_badge.setText("LAST FRAME")
            self.frame_label.setText(self.frame_label.text().split("  ·  ")[0] + "  ·  Last received frame")
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
        if self._worker is not None and self._worker.isRunning():
            self._closing = True
            self._stopping = True
            self.connection_button.setEnabled(False)
            self.save_button.setEnabled(False)
            self._disable_controls()
            self.status_label.setText("Closing the camera and restoring its previous settings…")
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
