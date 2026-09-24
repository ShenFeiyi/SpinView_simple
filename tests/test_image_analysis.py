"""Pixel accuracy, bounded analysis queues, and responsive worker shutdown."""

from dataclasses import FrozenInstanceError
from datetime import datetime
import os
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from camera import Frame
from image_analysis import AnalysisWorker, compute_analysis


def frame(sequence, shape=(4, 5, 3)):
    rgb = np.full(shape, sequence % 256, dtype=np.uint8)
    rgb.setflags(write=False)
    return Frame(rgb, sequence, datetime.now(), 30.0)


class ImageAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.rgb = np.array([
            [[255, 0, 0], [0, 255, 0], [0, 0, 255]],
            [[10, 20, 30], [10, 20, 30], [255, 255, 255]],
        ], dtype=np.uint8)

    def test_exact_channel_counts_and_row_column_orientation(self):
        result = compute_analysis(self.rgb, 1, 1, sequence=42)
        expected = np.zeros((3, 256), dtype=np.int64)
        for channel, middle in enumerate((10, 20, 30)):
            expected[channel, [0, middle, 255]] = 2
        np.testing.assert_array_equal(result.histogram, expected)
        np.testing.assert_array_equal(result.horizontal,
                                      [[10, 20, 30], [10, 20, 30], [255, 255, 255]])
        np.testing.assert_array_equal(result.vertical, [[0, 255, 0], [10, 20, 30]])
        self.assertEqual((result.sequence, result.width, result.height, result.row, result.column),
                         (42, 3, 2, 1, 1))
        self.assertEqual(result.histogram.dtype, np.int64)
        self.assertEqual(result.horizontal.dtype, np.uint8)
        self.assertEqual(result.vertical.dtype, np.uint8)
        self.assertEqual(result.generation, 0)

    def test_results_own_immutable_storage_and_leave_source_unchanged(self):
        before = self.rgb.copy()
        result = compute_analysis(self.rgb, 1, 1)
        np.testing.assert_array_equal(self.rgb, before)
        for array in (result.histogram, result.horizontal, result.vertical):
            self.assertTrue(array.flags.owndata)
            self.assertFalse(array.flags.writeable)
            self.assertFalse(np.shares_memory(array, self.rgb))
            with self.assertRaises(ValueError):
                array.flat[0] = 0
        self.rgb[:] = 77
        np.testing.assert_array_equal(result.horizontal, before[1])
        np.testing.assert_array_equal(result.vertical, before[:, 1])
        with self.assertRaises(FrozenInstanceError):
            result.row = 0

    def test_strided_source_and_last_sensor_indices(self):
        source = self.rgb[:, ::-1, ::-1]
        self.assertFalse(source.flags.c_contiguous)
        result = compute_analysis(source, np.int64(1), np.int32(2))
        np.testing.assert_array_equal(result.horizontal, source[1])
        np.testing.assert_array_equal(result.vertical, source[:, 2])
        for channel in range(3):
            for value in range(256):
                self.assertEqual(result.histogram[channel, value],
                                 np.count_nonzero(source[:, :, channel] == value))

    def test_first_last_and_single_pixel_profiles(self):
        for shape in ((1, 1, 3), (1, 5, 3), (4, 1, 3), (2, 3, 3)):
            source = np.arange(np.prod(shape), dtype=np.uint8).reshape(shape)
            for row, column in ((0, 0), (shape[0] - 1, shape[1] - 1)):
                with self.subTest(shape=shape, row=row, column=column):
                    result = compute_analysis(source, row, column)
                    np.testing.assert_array_equal(result.horizontal, source[row])
                    np.testing.assert_array_equal(result.vertical, source[:, column])
                    self.assertEqual(result.horizontal.shape, (shape[1], 3))
                    self.assertEqual(result.vertical.shape, (shape[0], 3))

    def test_histogram_includes_every_full_resolution_pixel(self):
        source = np.empty((2048, 2448, 3), dtype=np.uint8)
        source[:] = [12, 34, 56]
        source[-1, -1] = [255, 0, 128]
        result = compute_analysis(source, 1024, 1224)
        pixels = 2048 * 2448
        np.testing.assert_array_equal(result.histogram.sum(axis=1), [pixels] * 3)
        for channel, base, final in ((0, 12, 255), (1, 34, 0), (2, 56, 128)):
            self.assertEqual(result.histogram[channel, base], pixels - 1)
            self.assertEqual(result.histogram[channel, final], 1)

    def test_invalid_images_and_indices_are_rejected(self):
        for source, error in (([[1, 2, 3]], TypeError),
                              (self.rgb.astype(np.uint16), TypeError),
                              (self.rgb[:, :, 0], ValueError),
                              (np.zeros((2, 3, 4), dtype=np.uint8), ValueError),
                              (np.zeros((0, 3, 3), dtype=np.uint8), ValueError)):
            with self.subTest(source=type(source), error=error), self.assertRaises(error):
                compute_analysis(source, 0, 0)
        for row, column in ((-1, 0), (2, 0), (0, -1), (0, 3)):
            with self.subTest(row=row, column=column), self.assertRaises(IndexError):
                compute_analysis(self.rgb, row, column)
        for row, column in ((0.0, 0), (0, "1"), (True, 0), (0, np.bool_(False))):
            with self.subTest(row=row, column=column), self.assertRaises(TypeError):
                compute_analysis(self.rgb, row, column)


class AnalysisWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def worker(self):
        worker = AnalysisWorker()
        self.addCleanup(self.stop_and_join, worker)
        return worker

    def stop_and_join(self, worker):
        worker.stop()
        self.assertTrue(worker.wait(2000), "Analysis worker did not finish")

    def collect(self, worker):
        results = []
        available = threading.Event()

        def receive(result):
            results.append(result)
            available.set()

        worker.result_ready.connect(receive, Qt.ConnectionType.DirectConnection)
        return results, available

    def test_pending_requests_coalesce_before_start_and_preserve_generation(self):
        worker = self.worker()
        results, available = self.collect(worker)
        for sequence in range(100):
            worker.submit(frame(sequence), 3, 4, generation=7)
        worker.start()
        self.assertTrue(available.wait(2))
        self.assertEqual(len(results), 1)
        self.assertEqual((results[0].sequence, results[0].generation), (99, 7))

    def test_running_job_is_superseded_by_only_latest_pending_request(self):
        worker = self.worker()
        results, available = self.collect(worker)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def compute(rgb, row, column, sequence=0):
            calls.append(sequence)
            if sequence == 1:
                entered.set()
                release.wait(2)
            return compute_analysis(rgb, row, column, sequence)

        with patch("image_analysis.compute_analysis", side_effect=compute):
            worker.submit(frame(1), 0, 0)
            worker.start()
            self.assertTrue(entered.wait(2))
            for sequence in (2, 3, 4):
                worker.submit(frame(sequence), 1, 2)
            release.set()
            self.assertTrue(available.wait(2))
            self.stop_and_join(worker)
        self.assertEqual(calls, [1, 4])
        self.assertEqual([result.sequence for result in results], [4])

    def test_unacknowledged_results_are_bounded_and_newest_result_replaces_older(self):
        worker = self.worker()
        results, available = self.collect(worker)
        computed = {2: threading.Event(), 3: threading.Event()}

        def compute(rgb, row, column, sequence=0):
            result = compute_analysis(rgb, row, column, sequence)
            if sequence in computed:
                computed[sequence].set()
            return result

        with patch("image_analysis.compute_analysis", side_effect=compute):
            worker.submit(frame(1), 0, 0)
            worker.start()
            self.assertTrue(available.wait(2))
            available.clear()
            for sequence in (2, 3):
                worker.submit(frame(sequence), 0, 0)
                self.assertTrue(computed[sequence].wait(2))
            self.assertEqual([result.sequence for result in results], [1])
            worker.acknowledge_result(1)
            self.assertTrue(available.wait(2))
            self.stop_and_join(worker)
        self.assertEqual([result.sequence for result in results], [1, 3])

    def test_stop_wakes_idle_and_acknowledgement_waits(self):
        for awaiting_ack in (False, True):
            with self.subTest(awaiting_ack=awaiting_ack):
                worker = self.worker()
                results, available = self.collect(worker)
                worker.start()
                if awaiting_ack:
                    worker.submit(frame(1), 0, 0)
                    self.assertTrue(available.wait(2))
                self.stop_and_join(worker)
                worker.submit(frame(2), 0, 0)
                self.assertEqual(len(results), int(awaiting_ack))

    def test_stop_during_compute_discards_result_and_pending_job(self):
        worker = self.worker()
        results, _ = self.collect(worker)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def compute(rgb, row, column, sequence=0):
            calls.append(sequence)
            entered.set()
            release.wait(2)
            return compute_analysis(rgb, row, column, sequence)

        with patch("image_analysis.compute_analysis", side_effect=compute):
            worker.submit(frame(1), 0, 0)
            worker.start()
            self.assertTrue(entered.wait(2))
            worker.submit(frame(2), 0, 0)
            worker.stop()
            release.set()
            self.stop_and_join(worker)
        self.assertEqual(calls, [1])
        self.assertEqual(results, [])

    def test_invalid_request_reports_error_and_next_valid_request_succeeds(self):
        worker = self.worker()
        results, available = self.collect(worker)
        errors, failed = [], threading.Event()

        def receive_error(message):
            errors.append(message)
            failed.set()

        worker.error.connect(receive_error, Qt.ConnectionType.DirectConnection)
        worker.submit(frame(1), -1, 0)
        worker.start()
        self.assertTrue(failed.wait(2))
        worker.submit(frame(2), 0, 0)
        self.assertTrue(available.wait(2))
        self.assertEqual(len(errors), 1)
        self.assertIn("Row", errors[0])
        self.assertEqual(results[0].sequence, 2)


if __name__ == "__main__":
    unittest.main()
