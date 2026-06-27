"""Fenêtre principale : sidebar de navigation + pile de vues."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget,
    QPushButton, QLabel, QButtonGroup,
)

from .views.dashboard import DashboardView
from .views.networks import NetworksView
from .views.accounts import AccountsView
from .views.content import ContentView
from .views.stats import StatsView
from .views.logs import LogsView
from . import brand as _brand_mod
from . import theme


def _app_icon() -> QIcon:
    """Icône de l'application — cherche icon.svg ou icon.png dans ui/assets/."""
    here = Path(__file__).resolve().parent / "assets"
    for name in ("icon.svg", "icon.png"):
        f = here / name
        if f.exists():
            if name.endswith(".svg"):
                pm = _brand_mod._render_svg(f, 64)
                if pm and not pm.isNull():
                    return QIcon(pm)
            else:
                ico = QIcon(str(f))
                if not ico.isNull():
                    return ico
    return QIcon()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ContentHub")
        self.resize(1180, 760)
        self.setMinimumSize(960, 620)

        ico = _app_icon()
        if not ico.isNull():
            self.setWindowIcon(ico)

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
        self.content = ContentView()
        self.stats = StatsView()
        self.logs = LogsView()
        for v in (self.dashboard, self.networks, self.accounts,
                  self.content, self.stats, self.logs):
            self.stack.addWidget(v)

        layout.addWidget(self._sidebar())
        layout.addWidget(self.stack, 1)

        # Rafraîchit la vue active toutes les 30s pour garder les données à jour.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_active)
        self._refresh_timer.start(30_000)

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
            ("▶  Contenu", 3),
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
        footer = QLabel("100 % local · open-source")
        footer.setObjectName("BrandSub")
        lay.addWidget(footer)
        return side

    def _refresh_active(self):
        widget = self.stack.currentWidget()
        if widget and hasattr(widget, "refresh"):
            widget.refresh()

    def _navigate(self, index: int):
        self.stack.setCurrentIndex(index)
        widget = self.stack.widget(index)
        if hasattr(widget, "refresh"):
            widget.refresh()

    def _on_data_changed(self):
        self.dashboard.refresh()
        self.networks.refresh()
        self.accounts.refresh()
