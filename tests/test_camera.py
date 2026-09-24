"""Worker boundaries and failure cleanup without requiring connected hardware."""

import math
import os
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from camera import CameraWorker


class FakeSpinnakerError(Exception):
    errorcode = -1


class CameraWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_rapid_updates_are_coalesced_without_sdk_calls_from_caller(self):
        worker = CameraWorker()
        worker._spin = SimpleNamespace(SpinnakerException=FakeSpinnakerError)
        writes = Mock()
        worker._write = writes
        worker._end = Mock()
        worker._begin = Mock()
        worker._refresh_settings = Mock()
        for value in range(50):
            worker.set_control("exposure_us", value + 6)
        worker.set_control("gain_db", 3)
        writes.assert_not_called()
        worker._apply_commands()
        self.assertEqual(writes.call_count, 4)
        writes.assert_any_call("ExposureTime", 55.0)
        writes.assert_any_call("Gain", 3.0)
        worker._refresh_settings.assert_called_once()

    def test_shutdown_cancels_pending_changes_and_does_not_start_acquisition(self):
        worker = CameraWorker()
        worker._camera = Mock()
        worker._write = Mock()
        worker.set_control("gain_db", 4)
        worker.stop()
        worker._apply_commands()
        worker._begin()
        worker._write.assert_not_called()
        worker._camera.BeginAcquisition.assert_not_called()

    def test_nonfinite_controls_are_rejected_before_reaching_sdk(self):
        worker = CameraWorker()
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker.set_control("exposure_us", value)
        self.assertFalse(worker._pending)

    def auto_worker(self):
        """A camera whose automatic modes and resulting values we can advance."""
        worker = CameraWorker()
        worker._spin = SimpleNamespace(SpinnakerException=FakeSpinnakerError)
        values = {"ExposureAuto": "Off", "GainAuto": "Off", "ExposureTime": 10_000.0,
                  "Gain": 2.0, "AcquisitionFrameRate": 20.0,
                  "AcquisitionResultingFrameRate": 20.0,
                  "AutoExposureExposureTimeUpperLimit": 100_000.0,
                  "BalanceRatioSelector": "Red", "BalanceRatio": 1.0}
        worker._read = lambda name, *args, **kwargs: values.get(name)

        def write(name, value, *args, **kwargs):
            values[name] = value

        worker._write = Mock(side_effect=write)

        def setting(name):
            mode = {"ExposureTime": "ExposureAuto", "Gain": "GainAuto"}.get(name)
            return {"value": values[name], "min": 0.0, "max": 30_000_000.0,
                    "enabled": mode is None or values[mode] == "Off"}

        worker._float_setting = setting
        worker._auto_once_available = lambda name: True
        worker._wb_once_available = lambda: True
        worker._end = Mock()
        worker._begin = Mock()
        worker._refresh_settings()
        return worker, values

    def test_auto_readbacks_do_not_restart_stream_or_change_cached_limits(self):
        worker, values = self.auto_worker()
        worker.exposure_once()
        worker._apply_commands()
        self.assertEqual(values["ExposureAuto"], "Once")
        self.assertEqual(values["GainAuto"], "Off")
        previous = worker._settings
        self.assertTrue(previous["exposure_busy"])
        self.assertFalse(previous["exposure_us"]["enabled"])
        values["ExposureTime"] = 25_000.0
        worker._write.reset_mock()
        worker._end.reset_mock()
        worker._begin.reset_mock()
        worker._poll_auto_controls(time.monotonic())
        self.assertEqual(worker._settings["exposure_us"]["value"], 25_000.0)
        self.assertEqual(previous["exposure_us"]["value"], 10_000.0)
        self.assertEqual(worker._settings["frame_rate"], previous["frame_rate"])
        self.assertTrue(worker._settings["gain_db"]["enabled"])
        worker._write.assert_not_called()
        worker._end.assert_not_called()
        worker._begin.assert_not_called()

    def test_each_once_mode_returns_actual_chosen_value_to_manual_on_completion(self):
        for kind, node, control, chosen in (
            ("exposure", "ExposureTime", "exposure_us", 22_000.0),
            ("gain", "Gain", "gain_db", 8.0),
        ):
            with self.subTest(kind=kind):
                worker, values = self.auto_worker()
                getattr(worker, f"{kind}_once")()
                worker._apply_commands()
                values[node] = chosen
                values[worker.AUTO_CONTROLS[kind][0]] = "Off"
                worker._poll_auto_controls(time.monotonic())
                self.assertFalse(worker._settings[f"{kind}_busy"])
                self.assertTrue(worker._settings[f"{kind}_once_enabled"])
                self.assertTrue(worker._settings[control]["enabled"])
                self.assertEqual(worker._settings[control]["value"], chosen)
                self.assertFalse(worker._auto_deadlines)

    def test_timeout_stops_auto_and_keeps_latest_value(self):
        worker, values = self.auto_worker()
        messages = []
        worker.status.connect(messages.append)
        worker.gain_once()
        worker._apply_commands()
        values["Gain"] = 12.0
        worker._poll_auto_controls(worker._auto_deadlines["gain"] + 1)
        self.assertEqual(values["GainAuto"], "Off")
        self.assertEqual(worker._settings["gain_db"]["value"], 12.0)
        self.assertFalse(worker._settings["gain_busy"])
        self.assertTrue(worker._settings["gain_db"]["enabled"])
        self.assertIn("time limit", messages[-1])

    def test_completed_auto_explicitly_writes_off_to_unlock_cached_manual_access(self):
        worker, values = self.auto_worker()
        # The device has converged, but GenICam can retain the previous access
        # mode until an explicit enum write invalidates its dependent nodes.
        values["GainAuto"] = "Off"
        gain_access = {"writable": False}
        auto_node = Mock()
        auto_node.GetEntryByName.return_value.GetValue.return_value = 0
        auto_node.SetIntValue.side_effect = lambda value: gain_access.update(writable=True)
        worker._node = lambda *args, **kwargs: auto_node
        worker._spin.IsWritable = lambda node: True
        worker._spin.IsReadable = lambda node: True
        worker._write = CameraWorker._write.__get__(worker, CameraWorker)
        worker._cancel_auto("gain")
        self.assertTrue(gain_access["writable"])
        auto_node.SetIntValue.assert_called_once_with(0)

    def test_restore_unlocks_cached_gain_before_restoring_original_value(self):
        worker, values = self.auto_worker()
        values.update(GainAuto="Off", Gain=17.9)
        worker._original = {"GainAuto": ("enum", "Off", False), "Gain": ("float", 0.0, False)}
        gain_access = {"writable": False}
        auto_node = Mock()
        auto_node.GetEntryByName.return_value.GetValue.return_value = 0
        auto_node.SetIntValue.side_effect = lambda value: gain_access.update(writable=True)
        gain_node = Mock()
        gain_node.GetMin.return_value = 0.0
        gain_node.GetMax.return_value = 48.0
        gain_node.SetValue.side_effect = lambda value: values.update(Gain=value)
        worker._node = lambda name, *args, **kwargs: auto_node if name == "GainAuto" else gain_node
        worker._spin.IsWritable = lambda node: node is auto_node or gain_access["writable"]
        worker._spin.IsReadable = lambda node: True
        worker._write = CameraWorker._write.__get__(worker, CameraWorker)
        worker._restore()
        self.assertEqual(values["Gain"], 0.0)
        auto_node.SetIntValue.assert_called_once_with(0)

    def test_manual_override_cancels_running_or_queued_auto(self):
        worker, values = self.auto_worker()
        worker.exposure_once()
        worker._apply_commands()
        worker.set_control("exposure_us", 16_000.0)
        worker._apply_commands()
        self.assertEqual(values["ExposureAuto"], "Off")
        self.assertEqual(values["ExposureTime"], 16_000.0)
        self.assertFalse(worker._settings["exposure_busy"])
        worker.gain_once()
        worker.set_control("gain_db", 6.0)
        worker._apply_commands()
        self.assertEqual(values["GainAuto"], "Off")
        self.assertEqual(values["Gain"], 6.0)
        self.assertFalse(worker._auto_deadlines)

    def test_switching_auto_controls_keeps_only_clicked_parameter_automatic(self):
        worker, values = self.auto_worker()
        worker.exposure_once()
        worker._apply_commands()
        values["ExposureTime"] = 25_000.0
        worker.gain_once()
        worker._apply_commands()
        self.assertEqual(values["ExposureAuto"], "Off")
        self.assertEqual(values["ExposureTime"], 25_000.0)
        self.assertEqual(values["GainAuto"], "Once")
        self.assertFalse(worker._settings["exposure_busy"])
        self.assertTrue(worker._settings["exposure_us"]["enabled"])
        self.assertTrue(worker._settings["gain_busy"])

    def test_failed_once_write_returns_to_manual_without_stranding_busy(self):
        worker, values = self.auto_worker()
        errors = []
        worker.error.connect(errors.append)

        def write(name, value, *args, **kwargs):
            values[name] = value
            if name == "GainAuto" and value == "Once":
                raise FakeSpinnakerError("Once command failed")

        worker._write.side_effect = write
        worker.gain_once()
        worker._apply_commands()
        self.assertEqual(values["GainAuto"], "Off")
        self.assertFalse(worker._settings["gain_busy"])
        self.assertTrue(worker._settings["gain_db"]["enabled"])
        self.assertEqual(len(errors), 1)

    def test_unavailable_auto_state_cancels_instead_of_waiting_until_timeout(self):
        worker, values = self.auto_worker()
        errors = []
        worker.error.connect(errors.append)
        worker.gain_once()
        worker._apply_commands()
        values["GainAuto"] = None
        worker._poll_auto_controls(time.monotonic())
        self.assertEqual(values["GainAuto"], "Off")
        self.assertFalse(worker._settings["gain_busy"])
        self.assertEqual(len(errors), 1)

    def test_failed_return_to_manual_stops_worker_and_clears_busy(self):
        worker, values = self.auto_worker()
        worker.gain_once()
        worker._apply_commands()

        def write(name, value, *args, **kwargs):
            if name == "GainAuto" and value == "Off":
                raise FakeSpinnakerError("Camera unplugged")
            values[name] = value

        worker._write.side_effect = write
        with self.assertRaisesRegex(RuntimeError, "Could not return gain to manual"):
            worker._poll_auto_controls(worker._auto_deadlines["gain"] + 1)
        # A fatal failure is distinct from a requested stop, so run() reports
        # its error before cleaning up the camera.
        self.assertFalse(worker._stop_event.is_set())
        self.assertFalse(worker._auto_deadlines)

    def test_stop_removes_pending_auto_and_rejects_late_clicks(self):
        worker, _ = self.auto_worker()
        worker.exposure_once()
        worker.stop()
        worker.gain_once()
        worker.set_control("exposure_us", 10_000)
        self.assertFalse(worker._pending)

    def test_acquisition_failure_releases_camera_before_sdk(self):
        self.assert_failure_cleanup(auto_failure=False)

    def test_fatal_auto_off_failure_is_reported_once_and_releases_camera(self):
        self.assert_failure_cleanup(auto_failure=True)

    def assert_failure_cleanup(self, auto_failure):
        calls = []
        camera = Mock()
        camera.Init.side_effect = lambda: calls.append("init")
        camera.BeginAcquisition.side_effect = lambda: calls.append("begin")
        camera.GetNextImage.side_effect = FakeSpinnakerError("USB connection lost")
        camera.EndAcquisition.side_effect = lambda: calls.append("end")
        camera.DeInit.side_effect = lambda: calls.append("deinit")
        camera.TLDevice.DeviceModelName.GetValue.return_value = "Test camera"
        camera.TLDevice.DeviceSerialNumber.GetValue.return_value = "123"
        cameras = Mock()
        cameras.GetSize.return_value = 1
        cameras.GetByIndex.return_value = camera
        cameras.Clear.side_effect = lambda: calls.append("clear")
        system = Mock()
        system.GetCameras.return_value = cameras
        system.ReleaseInstance.side_effect = lambda: calls.append("release")
        sdk = SimpleNamespace(
            System=SimpleNamespace(GetInstance=lambda: system),
            ImageProcessor=Mock(),
            SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR=1,
            SPINNAKER_ERR_TIMEOUT=-1011,
            SpinnakerException=FakeSpinnakerError,
        )
        worker = CameraWorker()
        worker._configure = Mock()
        if auto_failure:
            worker._configure.side_effect = lambda: worker._auto_deadlines.update(gain=0.0)
            worker._read = lambda *args, **kwargs: "Once"
            worker._write = Mock(side_effect=FakeSpinnakerError("Auto Off failed"))
        worker._restore = lambda: calls.append("restore")
        errors = []
        worker.error.connect(errors.append)
        with patch.dict(sys.modules, {"PySpin": sdk}):
            worker.run()
        self.assertEqual(calls, ["init", "begin", "end", "restore", "deinit", "clear", "release"])
        self.assertEqual(len(errors), 1)
        self.assertIn("Could not return gain to manual" if auto_failure else "USB connection lost", errors[0])
        self.assertFalse(worker._auto_deadlines)
        self.assertIsNone(worker._camera)
        self.assertIsNone(worker._nodes)


if __name__ == "__main__":
    unittest.main()
