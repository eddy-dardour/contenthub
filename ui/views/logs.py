"""Vue Logs & Historique : flux de logs live + historique des publications."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QAbstractItemView,
)

from core.logbus import get_bus
from core.publisher import recent_jobs
from .. import theme, widgets

_LEVEL_COLOR = {"ERROR": theme.ERR, "WARNING": theme.WARN, "INFO": theme.TEXT_DIM}


class LogsView(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)

        head = QHBoxLayout()
        head.addWidget(widgets.title("Logs & Historique"))
        head.addStretch(1)
        refresh_btn = QPushButton("Rafraîchir l'historique")
        refresh_btn.clicked.connect(self.refresh_history)
        head.addWidget(refresh_btn)
        root.addLayout(head)

        # Historique des publications
        hist_card = widgets.Card()
        hist_card.body.addWidget(widgets.title("Historique des publications", "H2"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Contenu", "Réseau", "Compte", "Statut", "Détail"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(180)
        hist_card.body.addWidget(self.table)
        root.addWidget(hist_card)

        # Flux de logs
        log_card = widgets.Card()
        log_card.body.addWidget(widgets.title("Journal applicatif", "H2"))
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        log_card.body.addWidget(self.console)
        root.addWidget(log_card, 1)

        bus = get_bus()
        for entry in bus.history():
            self._append(entry)
        bus.subscribe(self._on_log)

        self.refresh_history()

    def _on_log(self, entry: dict):
        # Appelé depuis des threads variés : on planifie sur le thread UI.
        QTimer.singleShot(0, lambda: self._append(entry))

    def _append(self, entry: dict):
        color = _LEVEL_COLOR.get(entry["level"], theme.TEXT_DIM)
        self.console.append(
            f'<span style="color:{theme.TEXT_FAINT}">{entry["time"]}</span> '
            f'<span style="color:{color}">[{entry["level"]}]</span> '
            f'<span style="color:{theme.TEXT_FAINT}">{entry["name"]}</span> '
            f'{entry["message"]}')
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def refresh(self):
        self.refresh_history()

    def refresh_history(self):
        jobs = recent_jobs(100)
        self.table.setRowCount(len(jobs))
        for r, j in enumerate(jobs):
            self.table.setItem(r, 0, QTableWidgetItem(j["content_key"]))
            self.table.setItem(r, 1, QTableWidgetItem(j["network_id"]))
            self.table.setItem(r, 2, QTableWidgetItem(j.get("account_name") or "—"))
            status = QTableWidgetItem(j["status"])
            from PySide6.QtGui import QColor
            status.setForeground(QColor(theme.STATE_COLORS.get(j["status"], theme.TEXT_DIM)))
            self.table.setItem(r, 3, status)
            self.table.setItem(r, 4, QTableWidgetItem(j.get("error") or "—"))
