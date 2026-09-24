"""Exact RGB image statistics and a bounded background analysis worker."""

from __future__ import annotations

from dataclasses import dataclass, replace
import threading
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    from camera import Frame


@dataclass(frozen=True)
class ImageAnalysis:
    sequence: int
    width: int
    height: int
    row: int
    column: int
    histogram: np.ndarray
    horizontal: np.ndarray
    vertical: np.ndarray
    generation: int = 0


def compute_analysis(rgb: np.ndarray, row: int, column: int, sequence: int = 0) -> ImageAnalysis:
    """Count every RGB pixel and copy profiles at zero-based sensor indices.

    Results own their storage and are read-only. Strided source images are
    supported; neither a preview resize nor any overlay enters this calculation.
    """
    if not isinstance(rgb, np.ndarray):
        raise TypeError("RGB image must be a NumPy array")
    if rgb.dtype != np.uint8:
        raise TypeError("RGB image must use uint8 samples")
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("RGB image must have shape (height, width, 3)")
    height, width, _ = rgb.shape
    if not height or not width:
        raise ValueError("RGB image must not be empty")
    for name, index, size in (("row", row, height), ("column", column, width)):
        if isinstance(index, (bool, np.bool_)) or not isinstance(index, (int, np.integer)):
            raise TypeError(f"{name.capitalize()} must be an integer")
        if not 0 <= index < size:
            raise IndexError(f"{name.capitalize()} must be between 0 and {size - 1}")
    row, column = int(row), int(column)
    histogram = np.empty((3, 256), dtype=np.int64)
    for channel in range(3):
        histogram[channel] = np.bincount(rgb[:, :, channel].reshape(-1), minlength=256)
    horizontal = np.array(rgb[row, :, :], dtype=np.uint8, order="C", copy=True)
    vertical = np.array(rgb[:, column, :], dtype=np.uint8, order="C", copy=True)
    for array in (histogram, horizontal, vertical):
        array.setflags(write=False)
    return ImageAnalysis(int(sequence), width, height, row, column,
                         histogram, horizontal, vertical)


class AnalysisWorker(QThread):
    """Coalesce requests and keep at most one queued, unacknowledged result.

    The GUI result slot must call ``acknowledge_result(result.sequence)`` in a
    finally block, including when discarding a stale result. While that signal
    awaits delivery, the worker retains only the newest computed result and
    newest pending request. ``stop()`` wakes both idle and acknowledgement waits.
    Instances are single-use after stopping.
    """

    result_ready = Signal(object)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._condition = threading.Condition()
        self._stopped = False
        self._pending: tuple[Frame, int, int, int] | None = None
        self._ready: ImageAnalysis | None = None
        self._outstanding: ImageAnalysis | None = None
        self._last_error: str | None = None

    def submit(self, frame: Frame, row: int, column: int, generation: int = 0) -> None:
        """Replace any waiting request; camera frames already own immutable RGB."""
        with self._condition:
            if self._stopped:
                return
            self._pending = (frame, row, column, generation)
            self._ready = None
            self._condition.notify()

    def acknowledge_result(self, sequence: int) -> None:
        with self._condition:
            if self._outstanding is not None and self._outstanding.sequence == sequence:
                self._outstanding = None
                self._condition.notify()

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._pending = self._ready = self._outstanding = None
            self._condition.notify_all()

    def run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._stopped or self._pending is not None or
                    (self._ready is not None and self._outstanding is None)
                )
                if self._stopped:
                    return
                if self._pending is None:
                    result, self._ready = self._ready, None
                    self._outstanding = result
                    # Holding the reentrant lock makes stop() a boundary after
                    # which no new result can be emitted. Queued GUI delivery
                    # happens later and must still honor the GUI's close guard.
                    self.result_ready.emit(result)
                    continue
                request, self._pending = self._pending, None

            frame, row, column, generation = request
            try:
                result = compute_analysis(frame.rgb, row, column, frame.sequence)
                result = replace(result, generation=int(generation))
            except Exception as exc:
                with self._condition:
                    if self._stopped:
                        return
                    if self._pending is None:
                        message = f"Image analysis failed: {exc}"
                        if message != self._last_error:
                            self._last_error = message
                            self.error.emit(message)
                continue
            with self._condition:
                if self._stopped:
                    return
                self._last_error = None
                if self._pending is None:
                    self._ready = result
