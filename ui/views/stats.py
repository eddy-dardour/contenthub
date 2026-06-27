"""Vue Statistiques : suivi des vidéos par compte (local + distant).

Tableau par compte : publications locales (depuis la table jobs), dernière
publication, et — si la plateforme l'expose — vidéos / vues / likes côté API.
La collecte distante tourne dans un thread (StatsWorker) pour ne pas geler l'UI.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox,
)

from ..workers import StatsWorker
from .. import theme, widgets, brand
from core.registry import get_plugins
from core import stats as stats_mod


COLUMNS = ["Réseau", "Compte", "Handle", "Lié", "Publiées", "Échecs",
           "Dernière publi", "Vidéos (API)", "Vues/Abonnés", "Likes"]


class StatsView(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: StatsWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(widgets.title("Statistiques par compte"))
        head.addStretch(1)
        self.remote_chk = QCheckBox("Inclure les stats distantes (API)")
        self.remote_chk.setChecked(True)
        head.addWidget(self.remote_chk)
        self.refresh_btn = QPushButton("Actualiser")
        self.refresh_btn.setObjectName("Primary")
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn)
        root.addLayout(head)

        # Métriques globales
        self.m_accounts = widgets.metric("0", "Comptes")
        self.m_linked = widgets.metric("0", "Comptes liés")
        self.m_published = widgets.metric("0", "Vidéos publiées")
        self.m_views = widgets.metric("0", "Vues/Abonnés (API)")
        grid = QGridLayout()
        grid.setSpacing(14)
        for i, c in enumerate((self.m_accounts, self.m_linked,
                               self.m_published, self.m_views)):
            grid.addWidget(c, 0, i)
        root.addLayout(grid)

        # Tableau
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        self.status = widgets.dim("")
        root.addWidget(self.status)

    def refresh(self):
        if self._worker and self._worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText("Collecte en cours…")
        self._worker = StatsWorker(with_remote=self.remote_chk.isChecked())
        self._worker.finished_result.connect(self._on_data)
        self._worker.start()

    @staticmethod
    def _fmt(n) -> str:
        if n is None:
            return "—"
        try:
            return f"{int(n):,}".replace(",", " ")
        except (ValueError, TypeError):
            return str(n)

    def _on_data(self, rows: list):
        self.refresh_btn.setEnabled(True)
        t = stats_mod.totals(rows)
        self.m_accounts._value_label.setText(str(t["accounts"]))
        self.m_linked._value_label.setText(str(t["linked"]))
        self.m_published._value_label.setText(str(t["published"]))
        self.m_views._value_label.setText(self._fmt(t["remote_views"]))

        self.table.setRowCount(len(rows))
        for r, s in enumerate(rows):
            last = (s.last_posted or "")[:16].replace("T", " ")
            values = [
                s.network_name,
                s.account_name,
                s.handle or "—",
                "✔" if s.linked else "—",
                str(s.published),
                str(s.failed),
                last or "—",
                self._fmt(s.remote_videos),
                self._fmt(s.remote_views),
                self._fmt(s.remote_likes),
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                if c >= 4:
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, item)

        any_remote = any(s.remote_videos is not None or s.remote_views is not None
                         for s in rows)
        if not rows:
            self.status.setText("Aucun compte. Ajoutez et liez des comptes.")
        elif not any_remote and self.remote_chk.isChecked():
            self.status.setText(
                "Stats distantes indisponibles (API non autorisée ou sandbox) — "
                "stats locales affichées.")
        else:
            self.status.setText("")
