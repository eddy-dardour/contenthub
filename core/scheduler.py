"""Planificateur quotidien intégré (indépendant de l'UI).

Exécute un job (typiquement : générer N vidéos puis distribuer) chaque jour à
HH:MM, dans un thread daemon, réveil toutes les 30 s pour rester réactif au stop.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable

logger = logging.getLogger(__name__)

_TICK = 30


class DailyScheduler:
    def __init__(self, hour: int, minute: int, job: Callable[[], None],
                 on_event: Callable[[str, dict], None] | None = None):
        self.hour = hour
        self.minute = minute
        self.job = job
        self.on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_run: datetime | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._next_run = self._compute_next()
        self._emit("scheduled", {"next_run": self._next_run.isoformat()})
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Planificateur démarré — prochain run : %s", self._next_run)

    def stop(self):
        self._stop.set()
        self._emit("stopped", {})
        logger.info("Planificateur arrêté.")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def next_run(self) -> datetime | None:
        return self._next_run

    def _compute_next(self) -> datetime:
        now = datetime.now()
        candidate = now.replace(hour=self.hour, minute=self.minute,
                                second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _loop(self):
        while not self._stop.is_set():
            if datetime.now() >= self._next_run:
                self._emit("running", {"started_at": datetime.now().isoformat()})
                try:
                    self.job()
                    self._emit("done", {"finished_at": datetime.now().isoformat()})
                except Exception as e:
                    logger.error("Job planifié échoué : %s", e)
                    self._emit("error", {"error": str(e)})
                self._next_run = self._compute_next()
                self._emit("scheduled", {"next_run": self._next_run.isoformat()})
            self._stop.wait(_TICK)

    def _emit(self, event: str, data: dict):
        if self.on_event:
            try:
                self.on_event(event, data)
            except Exception as e:
                logger.warning("on_event %s a échoué : %s", event, e)
