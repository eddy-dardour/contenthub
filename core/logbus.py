"""Bus de logs en mémoire : capte les enregistrements logging pour l'UI.

Un handler conserve un anneau des derniers messages et notifie les abonnés
(la vue Logs) à chaque nouveau message, sans coupler le cœur à Qt.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from threading import Lock
from typing import Callable


class LogBus(logging.Handler):
    def __init__(self, capacity: int = 2000):
        super().__init__()
        self._records: deque[dict] = deque(maxlen=capacity)
        self._subscribers: list[Callable[[dict], None]] = []
        self._lock = Lock()
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": record.levelname,
            "name": record.name.split(".")[-1],
            "message": self.format(record),
        }
        with self._lock:
            self._records.append(entry)
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(entry)
            except Exception:
                pass

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._records)

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._subscribers.append(callback)


_bus: LogBus | None = None


def get_bus() -> LogBus:
    global _bus
    if _bus is None:
        _bus = LogBus()
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(_bus)
        # Console aussi, utile en dev.
        if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
            root.addHandler(logging.StreamHandler())
    return _bus
