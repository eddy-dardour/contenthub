"""Vue Tableau de bord : vue d'ensemble live de la plateforme."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel

from core.registry import get_plugins
from core import content, publisher
from core.accounts import AccountRepository
from .. import widgets, brand


class DashboardView(QWidget):
    def __init__(self):
        super().__init__()
        self.accounts = AccountRepository()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        root.addWidget(widgets.title("Tableau de bord"))
        root.addWidget(widgets.dim(
            "Vue d'ensemble de votre réseau de comptes et de la publication."))

        # Métriques
        self.m_networks = widgets.metric("0", "Réseaux actifs")
        self.m_accounts = widgets.metric("0", "Comptes liés")
        self.m_content = widgets.metric("0", "Contenus prêts")
        self.m_published = widgets.metric("0", "Publications réussies")
        grid = QGridLayout()
        grid.setSpacing(14)
        for i, card in enumerate(
                (self.m_networks, self.m_accounts, self.m_content, self.m_published)):
            grid.addWidget(card, 0, i)
        root.addLayout(grid)

        # Réseaux (résumé)
        net_card = widgets.Card()
        net_card.body.addWidget(widgets.title("Réseaux", "H2"))
        self.net_box = QVBoxLayout()
        self.net_box.setSpacing(8)
        net_card.body.addLayout(self.net_box)
        root.addWidget(net_card)
        root.addStretch(1)

        # Signature de l'état des réseaux : évite de reconstruire les widgets
        # quand rien n'a changé (le rebuild complet est coûteux et fait flicker).
        self._net_signature: tuple | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # 12 s : suffisant pour un tableau de bord, sans matraquer la DB/Fernet.
        self._timer.start(12000)
        self.refresh()

    def _tick(self):
        # Ne rafraîchit en boucle que si le dashboard est visible (économie CPU).
        if self.isVisible():
            self.refresh()

    def refresh(self):
        plugins = get_plugins()
        active = 0
        linked = 0
        # Un seul parcours des comptes par plugin (au lieu de 2 décryptages).
        net_states = {}
        for p in plugins.values():
            state = p.info().state.value
            net_states[p.id] = state
            if state == "configured":
                active += 1
            for a in p.list_accounts():
                if p.is_account_linked(a):
                    linked += 1
        items = len(content.list_content())
        stats = publisher.stats()

        self.m_networks._value_label.setText(str(active))
        self.m_accounts._value_label.setText(str(linked))
        self.m_content._value_label.setText(str(items))
        self.m_published._value_label.setText(str(stats["jobs_success"]))

        # Reconstruit la liste des réseaux UNIQUEMENT si elle a changé.
        signature = tuple(
            (p.id, net_states[p.id], p.info().accounts_count)
            for p in plugins.values())
        if signature == self._net_signature:
            return
        self._net_signature = signature

        while self.net_box.count():
            item = self.net_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for p in plugins.values():
            info = p.info()
            line = QWidget()
            lay = QHBoxLayout(line)
            lay.setContentsMargins(0, 0, 0, 0)
            logo = QLabel()
            logo.setPixmap(brand.pixmap(p.icon, 20))
            logo.setFixedSize(20, 20)
            logo.setScaledContents(True)
            lay.addWidget(logo)
            name = QLabel(info.display_name)
            name.setStyleSheet("font-size:14px; font-weight:600;")
            lay.addWidget(name)
            lay.addWidget(widgets.dim(f"· {info.accounts_count} compte(s)"))
            lay.addWidget(widgets.hspacer())
            lay.addWidget(widgets.StatusBadge(info.state.value))
            self.net_box.addWidget(line)
