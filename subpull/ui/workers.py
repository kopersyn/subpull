"""Background execution helpers. Workers never touch widgets: they only emit signals,
which Qt delivers to the GUI thread through queued connections."""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal


class TaskThread(QThread):
    """Runs ``fn(progress)`` in a thread. ``progress(text)`` may be called from inside ``fn``."""

    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[Callable[[str], None]], Any], parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit)
        except Exception as e:   # reported to the GUI, never raised inside Qt
            logging.getLogger(__name__).debug("Task failed", exc_info=True)
            self.failed.emit(e)
        else:
            self.succeeded.emit(result)


class LogBridge(QObject):
    """Forwards Python logging records to the GUI thread."""
    record = Signal(float, int, str, str)


class QtLogHandler(logging.Handler):
    def __init__(self, bridge: LogBridge, level=logging.INFO):
        super().__init__(level)
        self.bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            source = record.name.split(".")[-1] if record.name.startswith("subpull") else record.name
            msg = record.getMessage()
            if record.exc_info and record.levelno >= logging.ERROR:
                msg += f" ({record.exc_info[1]})"
            self.bridge.record.emit(record.created or time.time(), record.levelno, source, msg)
        except Exception:
            pass
