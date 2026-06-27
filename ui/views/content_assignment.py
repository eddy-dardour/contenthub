"""Wizard d'assignation de type de contenu par compte.

Présente chaque compte séquentiellement : l'utilisateur choisit quel type
de contenu sera généré/distribué pour ce compte. Un type par défaut global
peut être défini et appliqué à tous les comptes en un clic.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QWidget, QFrame, QButtonGroup, QScrollArea,
    QSizePolicy,
)

from core.registry import get_plugins
from core.accounts import AccountRepository
from core.catalog import list_types, ContentType
from core import settings as app_settings
from .. import theme, widgets, brand


class ContentAssignmentDialog(QDialog):
    """Wizard séquentiel : pour chaque compte, choisir un type de contenu."""

    assigned = Signal()  # émis quand les assignations ont changé

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assignation du type de contenu par compte")
        self.setMinimumSize(580, 480)
        self.setStyleSheet(theme.qss())
        self.repo = AccountRepository()
        self._types = list(list_types())
        self._rows: list[_AccountRow] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # En-tête
        root.addWidget(widgets.title("Assignation des types de contenu"))
        root.addWidget(widgets.dim(
            "Choisissez un type de contenu pour chaque compte. "
            "Le type par défaut est utilisé pour l'automatisation."))

        # Type par défaut global
        default_card = widgets.Card()
        default_row = QHBoxLayout()
        default_row.setSpacing(12)
        lbl = QLabel("Type par défaut (automatisation) :")
        lbl.setStyleSheet(f"font-weight:600; color:{theme.TEXT};")
        default_row.addWidget(lbl)

        self.default_combo = QComboBox()
        self.default_combo.setMinimumWidth(220)
        self.default_combo.addItem("— Aucun —", None)
        current_default = app_settings.get_default_content_type()
        for ct in self._types:
            self.default_combo.addItem(f"{ct.icon}  {ct.label}", ct.id)
            if ct.id == current_default:
                self.default_combo.setCurrentIndex(self.default_combo.count() - 1)

        self.default_combo.currentIndexChanged.connect(self._save_default)
        default_row.addWidget(self.default_combo, 1)

        apply_btn = QPushButton("Appliquer à tous")
        apply_btn.setObjectName("Primary")
        apply_btn.setFixedWidth(130)
        apply_btn.clicked.connect(self._apply_default_to_all)
        default_row.addWidget(apply_btn)
        default_card.body.addLayout(default_row)
        root.addWidget(default_card)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{theme.BORDER};")
        root.addWidget(sep)

        root.addWidget(widgets.title("Comptes", "H2"))

        # Zone scrollable des comptes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content_widget = QWidget()
        self._accounts_layout = QVBoxLayout(content_widget)
        self._accounts_layout.setContentsMargins(0, 0, 0, 0)
        self._accounts_layout.setSpacing(8)
        self._accounts_layout.addStretch(1)
        scroll.setWidget(content_widget)
        root.addWidget(scroll, 1)

        self._build_account_rows()

        # Boutons bas
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Fermer")
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Construction ────────────────────────────────────────────────────

    def _build_account_rows(self):
        # Vide l'existant (hors stretch final)
        while self._accounts_layout.count() > 1:
            item = self._accounts_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._rows.clear()

        plugins = get_plugins()
        any_account = False
        for p in plugins.values():
            accounts = p.list_accounts()
            if not accounts:
                continue
            # En-tête réseau
            net_hdr = QWidget()
            hdr_lay = QHBoxLayout(net_hdr)
            hdr_lay.setContentsMargins(0, 4, 0, 2)
            logo = QLabel()
            logo.setPixmap(brand.pixmap(p.icon, 16))
            logo.setFixedSize(16, 16)
            logo.setScaledContents(True)
            hdr_lay.addWidget(logo)
            hdr_lay.addWidget(widgets.title(p.display_name, "H2"))
            hdr_lay.addStretch(1)
            self._accounts_layout.insertWidget(
                self._accounts_layout.count() - 1, net_hdr)

            for acc in accounts:
                compatible = self._compatible_types(p.id)
                row = _AccountRow(acc, compatible, on_changed=self._on_row_changed)
                self._rows.append(row)
                self._accounts_layout.insertWidget(
                    self._accounts_layout.count() - 1, row)
            any_account = True

        if not any_account:
            empty = widgets.dim("Aucun compte configuré. Ajoutez des comptes dans l'onglet Comptes.")
            self._accounts_layout.insertWidget(0, empty)

    def _compatible_types(self, network_id: str) -> list[ContentType]:
        return [ct for ct in self._types if not ct.networks or network_id in ct.networks]

    # ── Actions ─────────────────────────────────────────────────────────

    def _save_default(self):
        type_id = self.default_combo.currentData()
        app_settings.set_default_content_type(type_id)

    def _apply_default_to_all(self):
        type_id = self.default_combo.currentData()
        for row in self._rows:
            row.set_type(type_id)
            self.repo.set_content_type(row.account_id, type_id)
        self.assigned.emit()

    def _on_row_changed(self, account_id: int, type_id: str | None):
        self.repo.set_content_type(account_id, type_id)
        self.assigned.emit()


class _AccountRow(QFrame):
    """Une ligne compte + sélecteur de type."""

    def __init__(self, account, compatible_types: list[ContentType], on_changed):
        super().__init__()
        self.account_id = account.id
        self._on_changed = on_changed
        self._types = compatible_types
        self._suppress = False

        self.setObjectName("Card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(14)

        # Info compte
        info = QVBoxLayout()
        info.setSpacing(2)
        name_lbl = QLabel(account.name)
        name_lbl.setStyleSheet(f"font-weight:600; color:{theme.TEXT}; font-size:13px;")
        info.addWidget(name_lbl)
        if account.handle:
            handle_lbl = QLabel(account.handle)
            handle_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:11px;")
            info.addWidget(handle_lbl)
        lay.addLayout(info, 1)

        # Badge lié / non lié
        linked = bool(account.credentials)
        badge = widgets.StatusBadge(
            "configured" if linked else "standby",
            "Lié" if linked else "Non lié")
        lay.addWidget(badge)

        # Sélecteur de type
        self.combo = QComboBox()
        self.combo.setMinimumWidth(200)
        self.combo.addItem("— Tous les types —", None)
        for ct in compatible_types:
            self.combo.addItem(f"{ct.icon}  {ct.label}", ct.id)

        # Présélectionne le type actuel
        current = account.content_type_id
        if current:
            for i in range(self.combo.count()):
                if self.combo.itemData(i) == current:
                    self._suppress = True
                    self.combo.setCurrentIndex(i)
                    self._suppress = False
                    break

        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        lay.addWidget(self.combo)

    def set_type(self, type_id: str | None):
        self._suppress = True
        for i in range(self.combo.count()):
            if self.combo.itemData(i) == type_id:
                self.combo.setCurrentIndex(i)
                break
        else:
            self.combo.setCurrentIndex(0)
        self._suppress = False

    def _on_combo_changed(self):
        if self._suppress:
            return
        self._on_changed(self.account_id, self.combo.currentData())
