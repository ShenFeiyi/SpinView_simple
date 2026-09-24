"""Spinnaker acquisition and controls, owned exclusively by one worker thread.

Only plain data crosses the thread boundary. The preview polls one owned RGB
frame, so a slow UI cannot accumulate a queue of full-resolution images.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import math
import threading
import time

import numpy as np
from PySide6.QtCore import QThread, Signal


@dataclass(frozen=True)
class Frame:
    rgb: np.ndarray
    sequence: int
    captured_at: datetime
    fps: float


class _AutoModeError(RuntimeError):
    """An automatic mode could not be disabled; acquisition must stop."""


class CameraWorker(QThread):
    connected = Signal(dict)
    settings_changed = Signal(dict)
    error = Signal(str)
    status = Signal(str)

    CONTROL_NODES = {
        "exposure_us": "ExposureTime",
        "gain_db": "Gain",
        "frame_rate": "AcquisitionFrameRate",
        "wb_red": "BalanceRatio",
        "wb_blue": "BalanceRatio",
    }
    AUTO_CONTROLS = {
        "exposure": ("ExposureAuto", "exposure_us"),
        "gain": ("GainAuto", "gain_db"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pending: dict[str, float | bool] = {}
        self._latest: Frame | None = None
        self._camera = None
        self._nodes = None
        self._stream_nodes = None
        self._spin = None
        self._acquiring = False
        self._original: dict[str, object] = {}
        self._settings: dict = {}
        self._wb_deadline: float | None = None
        self._next_wb_poll = 0.0
        self._auto_deadlines: dict[str, float] = {}
        self._auto_capabilities = {"exposure": False, "gain": False}
        self._next_auto_poll = 0.0

    def set_control(self, name: str, value: float) -> None:
        """Thread-safe, coalescing enqueue; never touches the SDK from the UI."""
        if name not in self.CONTROL_NODES:
            raise ValueError(f"Unknown camera control: {name}")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Camera controls require a finite number")
        with self._lock:
            if self._stop_event.is_set():
                return
            for kind, (_, control) in self.AUTO_CONTROLS.items():
                if name == control:
                    self._pending.pop(f"{kind}_once", None)
            self._pending[name] = value

    def white_balance_once(self) -> None:
        with self._lock:
            if not self._stop_event.is_set():
                self._pending["white_balance_once"] = True

    def exposure_once(self) -> None:
        self._enqueue_auto("exposure")

    def gain_once(self) -> None:
        self._enqueue_auto("gain")

    def _enqueue_auto(self, kind):
        with self._lock:
            if self._stop_event.is_set():
                return
            # The most recent click wins, including clicks before the worker
            # drains the queue. The other parameter always remains manual.
            for other in self.AUTO_CONTROLS:
                self._pending.pop(f"{other}_once", None)
            self._pending.pop(self.AUTO_CONTROLS[kind][1], None)
            self._pending[f"{kind}_once"] = True

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self._pending.clear()

    def latest_frame(self) -> Frame | None:
        with self._lock:
            return self._latest

    def _node(self, name, kind="float", stream=False):
        casts = {
            "float": self._spin.CFloatPtr,
            "int": self._spin.CIntegerPtr,
            "enum": self._spin.CEnumerationPtr,
            "bool": self._spin.CBooleanPtr,
        }
        node_map = self._stream_nodes if stream else self._nodes
        return casts[kind](node_map.GetNode(name))

    def _read(self, name, kind="float", stream=False, *, fresh=False):
        try:
            node = self._node(name, kind, stream)
            if not self._spin.IsReadable(node):
                return None
            # Automatic controls change on the camera without a host write.
            # GenICam can otherwise keep returning cached Once/value entries
            # until we stop acquisition, hiding completion from the UI.
            if kind == "enum":
                return node.GetCurrentEntry(False, fresh).GetSymbolic()
            return node.GetValue(False, fresh)
        except self._spin.SpinnakerException:
            return None

    def _write(self, name, value, kind="float", stream=False, *, force=False):
        node = self._node(name, kind, stream)
        current = self._read(name, kind, stream, fresh=True)
        if current == value and not force:
            return
        if not self._spin.IsWritable(node):
            raise RuntimeError(f"{name} is not currently writable")
        if kind == "enum":
            entry = node.GetEntryByName(str(value))
            if not self._spin.IsReadable(entry):
                raise RuntimeError(f"{name} does not support {value}")
            node.SetIntValue(entry.GetValue())
        else:
            if kind in ("float", "int"):
                value = max(node.GetMin(), min(value, node.GetMax()))
            node.SetValue(value)

    def _remember(self, name, kind="float", stream=False):
        value = self._read(name, kind, stream, fresh=True)
        if value is not None:
            self._original[name] = (kind, value, stream)

    def _configure(self):
        for name in (
            "ExposureAuto", "ExposureMode", "GainAuto", "GainSelector",
            "BalanceWhiteAuto", "BalanceRatioSelector", "AcquisitionMode",
            "TriggerMode", "PixelFormat",
        ):
            self._remember(name, "enum")
        for name in ("ExposureTime", "Gain", "AcquisitionFrameRate"):
            self._remember(name)
        self._remember("AcquisitionFrameRateEnable", "bool")
        for name in ("OffsetX", "OffsetY", "Width", "Height"):
            self._remember(name, "int")
        self._remember("StreamBufferHandlingMode", "enum", stream=True)

        # Read both selector-dependent values before changing camera controls.
        for key, selector in (("wb_red", "Red"), ("wb_blue", "Blue")):
            try:
                self._write("BalanceRatioSelector", selector, "enum")
                self._original[key] = self._read("BalanceRatio", fresh=True)
            except (self._spin.SpinnakerException, RuntimeError):
                pass

        self._write("TriggerMode", "Off", "enum")
        self._write("AcquisitionMode", "Continuous", "enum")
        self._write("ExposureMode", "Timed", "enum")
        for name in ("ExposureAuto", "GainAuto", "BalanceWhiteAuto"):
            if self._read(name, "enum") is not None:
                self._write(name, "Off", "enum", force=True)
        if self._read("GainSelector", "enum") is not None:
            self._write("GainSelector", "All", "enum")
        if self._read("AcquisitionFrameRateEnable", "bool") is not None:
            self._write("AcquisitionFrameRateEnable", True, "bool")

        # Full sensor resolution and a color Bayer transport format. Conversion
        # happens on the host, retaining USB bandwidth for acquisition.
        for name in ("OffsetX", "OffsetY"):
            if self._read(name, "int") is not None:
                self._write(name, 0, "int")
        for name in ("Width", "Height"):
            self._write(name, self._node(name, "int").GetMax(), "int")
        self._write("PixelFormat", "BayerRG8", "enum")
        self._write("StreamBufferHandlingMode", "NewestOnly", "enum", stream=True)
        # Apply app defaults on every connection, after capture format is set.
        self._write("ExposureTime", 33_000.0)
        self._write("AcquisitionFrameRate", 30.0)
        self._refresh_settings()

    def _float_setting(self, name):
        try:
            node = self._node(name)
            if not self._spin.IsReadable(node):
                raise RuntimeError("Not available on this camera")
            enabled = self._spin.IsWritable(node)
            result = {"value": float(node.GetValue(False, True)), "min": float(node.GetMin()),
                      "max": float(node.GetMax()), "enabled": enabled}
            if not enabled:
                result["reason"] = "Camera currently locks this control"
            return result
        except (self._spin.SpinnakerException, RuntimeError) as exc:
            return {"value": 0.0, "min": 0.0, "max": 0.0,
                    "enabled": False, "reason": str(exc)}

    def _refresh_settings(self):
        # Called while acquisition is stopped, because some settings are locked
        # only during streaming. Our write path also stops streaming briefly.
        settings = {
            key: self._float_setting(node)
            for key, node in self.CONTROL_NODES.items() if not key.startswith("wb_")
        }
        previous = self._read("BalanceRatioSelector", "enum")
        for key, selector in (("wb_red", "Red"), ("wb_blue", "Blue")):
            try:
                self._write("BalanceRatioSelector", selector, "enum")
                settings[key] = self._float_setting("BalanceRatio")
                if self._wb_deadline is not None:
                    settings[key].update(enabled=False, reason="White balance is adjusting")
            except (self._spin.SpinnakerException, RuntimeError) as exc:
                settings[key] = {"value": 1.0, "min": 0.0, "max": 1.0,
                                 "enabled": False, "reason": str(exc)}
        if previous:
            self._write("BalanceRatioSelector", previous, "enum")
        # Additional metadata is useful for explaining an exposure-limited rate.
        settings["resulting_frame_rate"] = self._read("AcquisitionResultingFrameRate", fresh=True)
        settings["white_balance_busy"] = self._wb_deadline is not None
        settings["white_balance_once_enabled"] = self._wb_once_available()
        for kind, (name, _) in self.AUTO_CONTROLS.items():
            self._auto_capabilities[kind] = self._auto_once_available(name)
        self._auto_metadata(settings)
        self._settings = settings
        self.settings_changed.emit(settings)

    def _auto_once_available(self, name):
        try:
            node = self._node(name, "enum")
            return bool(self._spin.IsWritable(node) and
                        self._spin.IsReadable(node.GetEntryByName("Once")))
        except self._spin.SpinnakerException:
            return False

    def _auto_metadata(self, settings):
        for kind, (_, control) in self.AUTO_CONTROLS.items():
            busy = kind in self._auto_deadlines
            settings[f"{kind}_busy"] = busy
            settings[f"{kind}_once_enabled"] = self._auto_capabilities[kind] and not busy
            if busy:
                settings[control].update(enabled=False, reason=f"Automatic {kind} is adjusting")

    def _cancel_auto(self, kind):
        try:
            # Even when Once has autonomously returned to Off, an explicit
            # write invalidates GenICam's cached dependent access modes so the
            # manual float node becomes writable immediately on the host.
            self._write(self.AUTO_CONTROLS[kind][0], "Off", "enum", force=True)
        except (self._spin.SpinnakerException, RuntimeError) as exc:
            # Do not keep streaming with an automatic mode we cannot disable.
            # Cleanup gets one more chance to restore the original controls.
            raise _AutoModeError(f"Could not return {kind} to manual: {exc}") from exc
        finally:
            self._auto_deadlines.pop(kind, None)

    def _start_auto(self, kind):
        # SDK CameraDefs.h documents Once returning to Off on convergence.
        # These modes are independent: only the clicked parameter may change.
        for other in self.AUTO_CONTROLS:
            self._cancel_auto(other)
        try:
            self._write(self.AUTO_CONTROLS[kind][0], "Once", "enum")
        except (self._spin.SpinnakerException, RuntimeError):
            self._cancel_auto(kind)
            raise
        exposure_s = float(self._read("ExposureTime", fresh=True) or 0) / 1e6
        if kind == "exposure":
            upper_s = float(self._read("AutoExposureExposureTimeUpperLimit") or 0) / 1e6
            exposure_s = max(exposure_s, upper_s)
        rate = max(float(self._read("AcquisitionFrameRate") or 1), 0.01)
        # Allow several frames at long exposures but bound a dark/unreachable
        # target. Timeout keeps the latest chosen value and returns to manual.
        duration = min(120.0, max(15.0, 12 * max(exposure_s, 1 / rate) + 2))
        self._auto_deadlines[kind] = time.monotonic() + duration
        self._next_auto_poll = 0.0
        self.status.emit(f"Adjusting {kind} automatically…")

    def _publish_auto_readbacks(self):
        # Do not run _refresh_settings here: selecting WB channels or using
        # streaming writability would disturb acquisition or disable controls
        # that our stopped-acquisition write path can edit normally.
        settings = {key: value.copy() if isinstance(value, dict) else value
                    for key, value in self._settings.items()}
        for control in ("exposure_us", "gain_db", "frame_rate"):
            value = self._read(self.CONTROL_NODES[control], fresh=True)
            if value is not None:
                settings[control]["value"] = float(value)
        settings["resulting_frame_rate"] = self._read("AcquisitionResultingFrameRate", fresh=True)
        self._auto_metadata(settings)
        self._settings = settings
        self.settings_changed.emit(settings)

    def _poll_auto_controls(self, now):
        if not self._auto_deadlines or now < self._next_auto_poll or self._stop_event.is_set():
            return
        self._next_auto_poll = now + 0.25
        for kind, deadline in list(self._auto_deadlines.items()):
            mode = self._read(self.AUTO_CONTROLS[kind][0], "enum", fresh=True)
            finished, expired, unavailable = mode == "Off", now >= deadline, mode is None
            if finished or expired or unavailable:
                # One transition at completion, not a stop/restart on every
                # readback. The full refresh restores manual bounds/enabled.
                try:
                    self._end()
                    self._cancel_auto(kind)
                finally:
                    self._auto_deadlines.pop(kind, None)
                self._refresh_settings()
                if unavailable:
                    self.error.emit(f"Could not read automatic {kind} state; returned to manual")
                elif expired and not finished:
                    self.status.emit(f"Automatic {kind} reached its time limit; keeping the current value")
                else:
                    self.status.emit(f"Automatic {kind} adjusted; returned to manual")
                self._begin()
                return
        self._publish_auto_readbacks()

    def _wb_once_available(self):
        try:
            node = self._node("BalanceWhiteAuto", "enum")
            return bool(self._spin.IsWritable(node) and
                        self._spin.IsReadable(node.GetEntryByName("Once")) and
                        self._wb_deadline is None)
        except self._spin.SpinnakerException:
            return False

    def _begin(self):
        if not self._stop_event.is_set():
            self._camera.BeginAcquisition()
            self._acquiring = True

    def _end(self):
        if self._acquiring:
            self._camera.EndAcquisition()
            self._acquiring = False

    def _apply_commands(self):
        with self._lock:
            pending, self._pending = self._pending, {}
        if not pending or self._stop_event.is_set():
            return
        self._end()
        for key, value in pending.items():
            if self._stop_event.is_set():
                break
            try:
                if key in ("exposure_once", "gain_once"):
                    self._start_auto(key.removesuffix("_once"))
                elif key == "white_balance_once":
                    self._write("BalanceWhiteAuto", "Once", "enum")
                    exposure_s = float(self._read("ExposureTime", fresh=True) or 0) / 1e6
                    rate = float(self._read("AcquisitionFrameRate") or 1)
                    self._wb_deadline = time.monotonic() + max(12.0, 15 * max(exposure_s, 1 / rate))
                    self.status.emit("Adjusting white balance…")
                else:
                    for kind, (_, control) in self.AUTO_CONTROLS.items():
                        if key == control:
                            self._cancel_auto(kind)
                    if key.startswith("wb_"):
                        self._write("BalanceWhiteAuto", "Off", "enum", force=True)
                        self._wb_deadline = None
                        self._write("BalanceRatioSelector", "Red" if key == "wb_red" else "Blue", "enum")
                    self._write(self.CONTROL_NODES[key], value)
            except (self._spin.SpinnakerException, RuntimeError) as exc:
                if isinstance(exc, _AutoModeError):
                    raise
                self.error.emit(f"Could not change {key.replace('_', ' ')}: {exc}")
        self._refresh_settings()
        self._begin()

    def _poll_white_balance(self, now):
        if self._wb_deadline is None or now < self._next_wb_poll:
            return
        self._next_wb_poll = now + 0.25
        finished = self._read("BalanceWhiteAuto", "enum", fresh=True) == "Off"
        expired = now >= self._wb_deadline
        if finished or expired:
            self._end()
            self._write("BalanceWhiteAuto", "Off", "enum", force=True)
            self._wb_deadline = None
            self._refresh_settings()
            self.status.emit("White balance adjusted" if finished else "White balance stopped; try again with a neutral, well-lit target")
            self._begin()

    def _restore(self):
        """Restore volatile settings only; never write a persistent user set."""
        failures = []
        deadline = time.monotonic() + 1.5

        def write(name, value, kind="float", stream=False, *, force=False):
            # An unplugged camera can make each transaction time out. Limit
            # best-effort restoration instead of multiplying that wait by the
            # number of controls during app shutdown.
            if time.monotonic() >= deadline:
                if not failures:
                    failures.append("Restoration timed out")
                return
            try:
                self._write(name, value, kind, stream, force=force)
            except (self._spin.SpinnakerException, RuntimeError) as exc:
                failures.append(f"{name}: {exc}")

        for name in ("ExposureAuto", "GainAuto", "BalanceWhiteAuto"):
            if name in self._original:
                write(name, "Off", "enum", force=True)
        # Restore values before re-enabling the user's original auto modes.
        for name in ("PixelFormat", "AcquisitionFrameRateEnable", "AcquisitionFrameRate",
                     "ExposureTime", "Gain"):
            if name in self._original:
                kind, value, stream = self._original[name]
                if name == "AcquisitionFrameRateEnable":
                    value = True  # unlock rate for restoration below
                write(name, value, kind, stream)
        for key, selector in (("wb_red", "Red"), ("wb_blue", "Blue")):
            if self._original.get(key) is not None:
                write("BalanceRatioSelector", selector, "enum")
                write("BalanceRatio", self._original[key])
        for name in ("OffsetX", "OffsetY"):
            if name in self._original:
                write(name, 0, "int")
        for name in ("Width", "Height", "OffsetX", "OffsetY", "BalanceRatioSelector",
                     "GainSelector", "ExposureMode", "AcquisitionMode", "TriggerMode",
                     "AcquisitionFrameRateEnable", "ExposureAuto", "GainAuto",
                     "BalanceWhiteAuto", "StreamBufferHandlingMode"):
            if name in self._original:
                kind, value, stream = self._original[name]
                write(name, value, kind, stream)
        if failures:
            self.status.emit("Camera disconnected; some temporary settings could not be restored" if
                             len(failures) > 3 else "Some original camera settings could not be restored")

    def run(self):
        system = cameras = processor = None
        initialized = False
        try:
            import PySpin
            self._spin = PySpin
            self.status.emit("Connecting to camera…")
            system = PySpin.System.GetInstance()
            cameras = system.GetCameras()
            if cameras.GetSize() == 0:
                raise RuntimeError("No camera found. Check the USB 3 data cable and close SpinView or other camera apps, then reconnect.")
            self._camera = cameras.GetByIndex(0)
            self._camera.Init()
            initialized = True
            self._nodes = self._camera.GetNodeMap()
            self._stream_nodes = self._camera.GetTLStreamNodeMap()
            self._configure()
            info = self._camera.TLDevice
            details = {"model": info.DeviceModelName.GetValue(),
                       "serial": info.DeviceSerialNumber.GetValue()}
            del info
            self.connected.emit(details)
            processor = PySpin.ImageProcessor()
            processor.SetColorProcessing(PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)
            self._begin()
            self.status.emit("Live preview")
            sequence = 0
            acquired_times = deque(maxlen=240)
            next_conversion = 0.0
            last_good = time.monotonic()
            warned = False
            while not self._stop_event.is_set():
                self._apply_commands()
                if self._stop_event.is_set():
                    break
                now = time.monotonic()
                self._poll_auto_controls(now)
                self._poll_white_balance(now)
                if self._stop_event.is_set():
                    break
                raw = converted = None
                try:
                    # Short SDK waits make closing the app responsive even with
                    # long exposures. Timeouts alone are not a disconnect.
                    raw = self._camera.GetNextImage(200)
                    now = time.monotonic()
                    if raw.IsIncomplete():
                        if not warned:
                            self.status.emit("Incomplete USB frame; waiting for the next frame")
                            warned = True
                        continue
                    sequence += 1
                    acquired_times.append(now)
                    while len(acquired_times) > 2 and now - acquired_times[0] > 3:
                        acquired_times.popleft()
                    duration = acquired_times[-1] - acquired_times[0]
                    fps = (len(acquired_times) - 1) / duration if duration > 0 else 0.0
                    last_good = now
                    if warned:
                        self.status.emit("Live preview")
                        warned = False
                    if now >= next_conversion:
                        converted = processor.Convert(raw, PySpin.PixelFormat_RGB8)
                        rgb = np.array(converted.GetNDArray(), dtype=np.uint8, order="C", copy=True)
                        rgb.setflags(write=False)
                        frame = Frame(rgb, sequence, datetime.now().astimezone(), fps)
                        with self._lock:
                            self._latest = frame
                        next_conversion = now + 1 / 30
                except PySpin.SpinnakerException as exc:
                    if exc.errorcode != PySpin.SPINNAKER_ERR_TIMEOUT:
                        raise
                    exposure = self._settings.get("exposure_us", {}).get("value", 0) / 1e6
                    target_fps = self._settings.get("frame_rate", {}).get("value", 1)
                    grace = max(5.0, 2 * exposure + 2, 2 / max(target_fps, 0.01) + 2)
                    if time.monotonic() - last_good > grace:
                        # Reading a device node distinguishes a slow exposure
                        # from an unplugged device on SDKs that only time out.
                        self._camera.DeviceTemperature.GetValue()
                        if not warned:
                            self.status.emit("Waiting for camera frames; check USB connection")
                            warned = True
                finally:
                    if converted is not None:
                        converted.Release()
                    if raw is not None:
                        raw.Release()
        except Exception as exc:
            if not self._stop_event.is_set():
                self.error.emit(f"Camera error: {exc}")
        finally:
            self._auto_deadlines.clear()
            self._wb_deadline = None
            with self._lock:
                self._pending.clear()
            try:
                self._end()
            except Exception:
                self._acquiring = False
            if initialized:
                try:
                    self._restore()
                except Exception:
                    pass
                try:
                    self._camera.DeInit()
                except Exception:
                    pass
            # Drop every camera/node reference before clearing the list and
            # releasing the singleton; Spinnaker enforces this ordering.
            processor = None
            self._nodes = self._stream_nodes = self._camera = None
            if cameras is not None:
                cameras.Clear()
            if system is not None:
                try:
                    system.ReleaseInstance()
                except Exception as exc:
                    self.status.emit(f"Camera cleanup: {exc}")
