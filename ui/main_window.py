"""Fenêtre principale : sidebar de navigation + pile de vues."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QPushButton, QLabel, QButtonGroup,
)

from . import theme
from .views.dashboard import DashboardView
from .views.networks import NetworksView
from .views.accounts import AccountsView
from .views.schedule import ScheduleView
from .views.stats import StatsView
from .views.logs import LogsView
from .views.content_assignment import ContentAssignmentDialog


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ContentHub — Plateforme d'automatisation de contenu")
        self.resize(1180, 760)
        self.setMinimumSize(960, 620)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Vues
        self.stack = QStackedWidget()
        self.dashboard = DashboardView()
        self.networks = NetworksView(on_changed=self._on_data_changed)
        self.accounts = AccountsView(on_changed=self._on_data_changed)
        self.schedule = ScheduleView()
        self.stats = StatsView()
        self.logs = LogsView()
        for v in (self.dashboard, self.networks, self.accounts,
                  self.schedule, self.stats, self.logs):
            self.stack.addWidget(v)

        layout.addWidget(self._sidebar())
        layout.addWidget(self.stack, 1)

    def _sidebar(self) -> QWidget:
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(228)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 12)
        lay.setSpacing(0)

        brand = QLabel("◆ ContentHub")
        brand.setObjectName("Brand")
        sub = QLabel("Automatisation locale")
        sub.setObjectName("BrandSub")
        lay.addWidget(brand)
        lay.addWidget(sub)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        items = [
            ("◈  Tableau de bord", 0),
            ("⬡  Réseaux", 1),
            ("◉  Comptes", 2),
            ("▶  Catalogue", 3),
            ("◧  Statistiques", 4),
            ("≡  Logs", 5),
        ]
        for label, idx in items:
            btn = QPushButton(label)
            btn.setObjectName("NavBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, i=idx: self._navigate(i))
            self.nav_group.addButton(btn, idx)
            lay.addWidget(btn)
        self.nav_group.button(0).setChecked(True)

        lay.addStretch(1)

        # Bouton d'assignation rapide des types de contenu
        assign_btn = QPushButton("⊞  Assigner les types")
        assign_btn.setObjectName("NavBtn")
        assign_btn.clicked.connect(self._open_assignment)
        lay.addWidget(assign_btn)

        footer = QLabel("100 % local · open-source")
        footer.setObjectName("BrandSub")
        lay.addWidget(footer)
        return side

    def _open_assignment(self):
        dlg = ContentAssignmentDialog(self)
        dlg.assigned.connect(self._on_data_changed)
        dlg.exec()

    def _navigate(self, index: int):
        self.stack.setCurrentIndex(index)
        # Rafraîchit les vues data-driven à l'ouverture.
        widget = self.stack.widget(index)
        if hasattr(widget, "refresh"):
            widget.refresh()

    def _on_data_changed(self):
        self.dashboard.refresh()
        self.networks.refresh()
        self.accounts.refresh()
