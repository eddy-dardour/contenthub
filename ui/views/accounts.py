"""Vue Comptes : gérer les comptes par réseau, les lier (OAuth), assigner un type de contenu."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QDoubleSpinBox, QAbstractItemView,
)
from PySide6.QtGui import QColor

from core.registry import get_plugins, get_plugin
from core.accounts import AccountRepository
from core.catalog import list_types
from ..workers import LinkWorker
from .. import theme, widgets, brand
from .content_assignment import ContentAssignmentDialog

_BTN_STYLE = (
    f"QPushButton {{ background:{theme.SURFACE_2}; border:1px solid {theme.BORDER}; "
    f"border-radius:6px; padding:4px 10px; color:{theme.TEXT}; font-size:12px; }}"
    f"QPushButton:hover {{ border:1px solid {theme.ACCENT}; color:{theme.ACCENT}; }}"
    f"QPushButton#Danger {{ border:1px solid {theme.ERR}; color:{theme.ERR}; }}"
    f"QPushButton#Danger:hover {{ background:{theme.ERR}; color:#0b0d12; }}"
)

_COMBO_STYLE = (
    f"QComboBox {{ background:{theme.SURFACE_2}; border:1px solid {theme.BORDER}; "
    f"border-radius:6px; padding:3px 8px; color:{theme.TEXT}; font-size:12px; }}"
    f"QComboBox:hover {{ border:1px solid {theme.ACCENT}; }}"
    f"QComboBox QAbstractItemView {{ background:{theme.SURFACE_2}; border:1px solid {theme.BORDER}; "
    f"selection-background-color:{theme.ACCENT}; selection-color:#0b0d12; }}"
)


def _compatible_types(network_id: str) -> list:
    """Retourne les ContentType compatibles avec ce réseau."""
    result = []
    for ct in list_types():
        if not ct.networks or network_id in ct.networks:
            result.append(ct)
    return result


class AccountsView(QWidget):
    def __init__(self, on_changed=lambda: None):
        super().__init__()
        self.repo = AccountRepository()
        self.on_changed = on_changed
        self._link_worker = None

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        hdr = QHBoxLayout()
        hdr.addWidget(widgets.title("Comptes"))
        hdr.addStretch(1)
        assign_btn = QPushButton("⊞  Assigner les types")
        assign_btn.setObjectName("Primary")
        assign_btn.clicked.connect(self._open_assignment)
        hdr.addWidget(assign_btn)
        root.addLayout(hdr)
        root.addWidget(widgets.dim(
            "Ajoutez des comptes par réseau, liez-les (OAuth) puis assignez le type "
            "de contenu à publier pour chacun."))

        # ── Barre d'ajout ────────────────────────────────────────────────
        add = widgets.Card()
        bar = QHBoxLayout()
        bar.setSpacing(10)

        self.net_select = QComboBox()
        self.net_select.setMinimumWidth(140)
        for p in get_plugins().values():
            self.net_select.addItem(brand.icon(p.icon, 18), f"  {p.display_name}", p.id)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Nom du compte (ex: compte_us_01)")

        self.cooldown = QDoubleSpinBox()
        self.cooldown.setRange(0, 72)
        self.cooldown.setValue(2)
        self.cooldown.setSuffix(" h cooldown")
        self.cooldown.setFixedWidth(130)

        add_btn = QPushButton("Ajouter le compte")
        add_btn.setObjectName("Primary")
        add_btn.clicked.connect(self._add)

        bar.addWidget(self.net_select)
        bar.addWidget(self.name_edit, 1)
        bar.addWidget(self.cooldown)
        bar.addWidget(add_btn)
        add.body.addLayout(bar)
        root.addWidget(add)

        # ── Tableau ──────────────────────────────────────────────────────
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Réseau", "Compte", "Handle", "Lié", "Actif", "Type de contenu", "Actions"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(5, 200)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(
            f"QTableWidget::item:alternate {{ background: {theme.SURFACE_2}; }}")
        root.addWidget(self.table, 1)

        self.refresh()

    # ── Actions ─────────────────────────────────────────────────────────

    def _add(self):
        net_id = self.net_select.currentData()
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Nom requis", "Saisissez un nom de compte.")
            return
        acc_id = self.repo.add(net_id, name, cooldown_hours=self.cooldown.value())
        if acc_id is None:
            QMessageBox.warning(self, "Doublon",
                                f"Le compte « {name} » existe déjà sur ce réseau.")
            return
        self.name_edit.clear()
        self.refresh()
        self.on_changed()

    def _link(self, network_id: str, account_id: int):
        plugin = get_plugin(network_id)
        info = plugin.info()
        if info.state.value == "standby":
            QMessageBox.information(
                self, "Réseau en Standby",
                f"{plugin.display_name} doit d'abord être configuré (clés API) "
                "dans l'onglet Réseaux.")
            return
        self._link_worker = LinkWorker(network_id, account_id)
        self._link_worker.finished_result.connect(self._on_linked)
        self._link_worker.start()
        QMessageBox.information(
            self, "Liaison en cours",
            "Une fenêtre d'autorisation va s'ouvrir dans votre navigateur.\n"
            "Autorisez le compte puis revenez ici.")

    def _on_linked(self, success: bool, detail: str):
        if success:
            QMessageBox.information(self, "Compte lié", detail)
        else:
            QMessageBox.warning(self, "Liaison échouée", detail)
        self.refresh()
        self.on_changed()

    def _toggle_active(self, account_id: int, active: bool):
        self.repo.set_active(account_id, active)
        self.refresh()

    def _set_content_type(self, account_id: int, content_type_id: str | None):
        self.repo.set_content_type(account_id, content_type_id)
        self.on_changed()

    def _open_assignment(self):
        dlg = ContentAssignmentDialog(self)
        dlg.assigned.connect(self.refresh)
        dlg.assigned.connect(self.on_changed)
        dlg.exec()

    def _delete(self, account_id: int, name: str):
        rep = QMessageBox.question(self, "Supprimer",
                                   f"Supprimer le compte « {name} » ?")
        if rep == QMessageBox.StandardButton.Yes:
            self.repo.delete(account_id)
            self.refresh()
            self.on_changed()

    # ── Rendu ───────────────────────────────────────────────────────────

    def refresh(self):
        plugins = get_plugins()
        rows = []
        for p in plugins.values():
            for a in p.list_accounts():
                rows.append((p, a))
        self.table.setRowCount(len(rows))
        for r, (p, a) in enumerate(rows):
            linked = p.is_account_linked(a)
            self.table.setRowHeight(r, 48)

            net_item = QTableWidgetItem(f" {p.display_name}")
            net_item.setIcon(brand.icon(p.icon, 18))
            self.table.setItem(r, 0, net_item)
            self.table.setItem(r, 1, QTableWidgetItem(a.name))
            self.table.setItem(r, 2, QTableWidgetItem(a.handle or "—"))

            lk = QTableWidgetItem("✔ Oui" if linked else "✗ Non")
            lk.setForeground(QColor(theme.OK) if linked else QColor(theme.TEXT_FAINT))
            self.table.setItem(r, 3, lk)

            act = QTableWidgetItem("Actif" if a.is_active else "Inactif")
            act.setForeground(QColor(theme.OK) if a.is_active else QColor(theme.TEXT_FAINT))
            self.table.setItem(r, 4, act)

            self.table.setCellWidget(r, 5, self._content_type_cell(p, a))
            self.table.setCellWidget(r, 6, self._actions_cell(p, a, linked))

    def _content_type_cell(self, plugin, account) -> QWidget:
        cell = QWidget()
        cell.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 4, 6, 4)

        combo = QComboBox()
        combo.setStyleSheet(_COMBO_STYLE)
        compatible = _compatible_types(plugin.id)

        combo.addItem("— Tous les types —", None)
        for ct in compatible:
            combo.addItem(f"{ct.icon} {ct.label}", ct.id)

        # Sélectionne le type actuellement assigné
        current = account.content_type_id
        if current:
            for i in range(combo.count()):
                if combo.itemData(i) == current:
                    combo.setCurrentIndex(i)
                    break

        combo.currentIndexChanged.connect(
            lambda _, cb=combo, aid=account.id:
            self._set_content_type(aid, cb.currentData()))

        lay.addWidget(combo)
        return cell

    def _actions_cell(self, plugin, account, linked: bool) -> QWidget:
        cell = QWidget()
        cell.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        link_btn = QPushButton("Re-lier" if linked else "Lier")
        link_btn.setStyleSheet(_BTN_STYLE)
        link_btn.clicked.connect(
            lambda _, n=plugin.id, i=account.id: self._link(n, i))
        lay.addWidget(link_btn)

        label = "Désactiver" if account.is_active else "Activer"
        toggle = QPushButton(label)
        toggle.setStyleSheet(_BTN_STYLE)
        toggle.clicked.connect(
            lambda _, i=account.id, s=not account.is_active: self._toggle_active(i, s))
        lay.addWidget(toggle)

        del_btn = QPushButton("Suppr.")
        del_btn.setObjectName("Danger")
        del_btn.setStyleSheet(_BTN_STYLE)
        del_btn.clicked.connect(
            lambda _, i=account.id, nm=account.name: self._delete(i, nm))
        lay.addWidget(del_btn)
        lay.addStretch(1)
        return cell
