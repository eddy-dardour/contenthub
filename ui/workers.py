"""Workers QThread : exécutent les tâches longues hors du thread UI.

Chaque worker émet des signaux Qt (log/progress/done) consommés par les vues,
de sorte que la boucle d'évènements ne se bloque jamais.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from core.publisher import Publisher
from core import generator, campaign, stats
from core.catalog import ContentType
from core.registry import get_plugin
from core.accounts import AccountRepository


class LinkWorker(QThread):
    """Lie un compte (OAuth) sans bloquer l'UI."""
    log = Signal(str)
    finished_result = Signal(bool, str)

    def __init__(self, network_id: str, account_id: int):
        super().__init__()
        self.network_id = network_id
        self.account_id = account_id

    def run(self):
        plugin = get_plugin(self.network_id)
        acc = AccountRepository().get(self.account_id)
        if not plugin or not acc:
            self.finished_result.emit(False, "Compte ou réseau introuvable.")
            return
        result = plugin.link_account(acc, on_log=self.log.emit)
        self.finished_result.emit(result.success, result.detail)


class PublishWorker(QThread):
    """Lance la distribution. Relaie les évènements du Publisher en signaux."""
    event = Signal(str, dict)
    finished_result = Signal(dict)

    def __init__(self, network_ids: list[str] | None = None):
        super().__init__()
        self.network_ids = network_ids
        self.publisher = Publisher()

    def run(self):
        summary = self.publisher.run(
            network_ids=self.network_ids,
            progress=lambda ev, data: self.event.emit(ev, data))
        self.finished_result.emit(summary)

    def stop(self):
        self.publisher.stop()

    def pause(self):
        self.publisher.pause()

    def resume(self):
        self.publisher.resume()


class StatsWorker(QThread):
    """Collecte les stats par compte (local + distant) hors du thread UI."""
    finished_result = Signal(list)

    def __init__(self, with_remote: bool = True):
        super().__init__()
        self.with_remote = with_remote

    def run(self):
        try:
            data = stats.collect(with_remote=self.with_remote)
        except Exception:
            data = []
        self.finished_result.emit(data)


class CampaignWorker(QThread):
    """Exécute une campagne (génération + circulation) pour un type de contenu.

    Génère, par plateforme épinglée, 1 vidéo unique par compte lié+actif puis la
    distribue. Relaie les évènements du Publisher/campaign en signaux.
    """
    event = Signal(str, dict)
    finished_result = Signal(dict)

    def __init__(self, content_type: ContentType):
        super().__init__()
        self.content_type = content_type
        self._stop = False

    def run(self):
        try:
            summary = campaign.run(
                self.content_type,
                progress=lambda ev, data: self.event.emit(ev, data),
                stop_check=lambda: self._stop)
        except Exception as e:
            self.event.emit("info", {"message": f"Erreur : {e}"})
            summary = {"published": 0, "failed": 0, "skipped": 0, "error": str(e)}
        self.finished_result.emit(summary)

    def stop(self):
        self._stop = True


class DistributeWorker(QThread):
    """Distribue le contenu existant (output/videos/) sans régénérer.

    Respecte le content_type_id assigné à chaque compte :
    si un compte a un type assigné, seuls les contenus dont la clé correspond
    à ce type sont distribués. Sans assignation : tout le contenu disponible.
    """
    event = Signal(str, dict)
    finished_result = Signal(dict)

    def __init__(self, content_type_id: str | None = None,
                 network_ids: list[str] | None = None):
        super().__init__()
        self.content_type_id = content_type_id
        self.network_ids = network_ids
        self.publisher = Publisher()

    def run(self):
        summary = self.publisher.run(
            network_ids=self.network_ids,
            content_type_id=self.content_type_id,
            progress=lambda ev, data: self.event.emit(ev, data))
        self.finished_result.emit(summary)

    def stop(self):
        self.publisher.stop()

    def pause(self):
        self.publisher.pause()

    def resume(self):
        self.publisher.resume()


class GenerateWorker(QThread):
    """Génère N vidéos via l'outil local (sous-processus)."""
    log = Signal(str)
    finished_result = Signal(bool)

    def __init__(self, count: int, content_type: str | None = None):
        super().__init__()
        self.count = count
        self.content_type = content_type
        self._stop = False

    def run(self):
        try:
            ok = generator.generate(
                self.count, self.content_type,
                on_log=self.log.emit, stop_check=lambda: self._stop)
        except Exception as e:
            self.log.emit(f"Erreur : {e}")
            ok = False
        self.finished_result.emit(ok)

    def stop(self):
        self._stop = True
