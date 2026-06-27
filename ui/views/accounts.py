"""Vue Comptes : gérer les comptes par réseau, les lier (OAuth), assigner un type de contenu."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QAbstractItemView,
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor

from core.registry import get_plugins, get_plugin
from core.accounts import AccountRepository
from core.catalog import list_types
from ..workers import LinkWorker
from .. import theme, widgets, brand

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

        self._cooldown_timer = QTimer(self)
        self._cooldown_timer.timeout.connect(self._refresh_cooldown_column)
        self._cooldown_timer.start(60_000)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        hdr = QHBoxLayout()
        hdr.addWidget(widgets.title("Comptes"))
        hdr.addStretch(1)
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

        add_btn = QPushButton("Ajouter le compte")
        add_btn.setObjectName("Primary")
        add_btn.clicked.connect(self._add)

        bar.addWidget(self.net_select)
        bar.addWidget(self.name_edit, 1)
        bar.addWidget(add_btn)
        add.body.addLayout(bar)
        root.addWidget(add)

        # ── Tableau ──────────────────────────────────────────────────────
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Réseau", "Compte", "Handle", "Lié", "Actif", "Type de contenu", "Disponible", "Actions"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.Fixed)
        self.table.setColumnWidth(5, 200)
        hh.setSectionResizeMode(6, QHeaderView.Fixed)
        self.table.setColumnWidth(6, 110)
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
        acc_id = self.repo.add(net_id, name, cooldown_hours=8.0)
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
        self._pending_link_id = account_id
        self._link_worker = LinkWorker(network_id, account_id)
        self._link_worker.finished_result.connect(self._on_linked)
        self._link_worker.start()
        QMessageBox.information(
            self, "Liaison en cours",
            "Une fenêtre d'autorisation va s'ouvrir dans votre navigateur.\n"
            "Autorisez le compte puis revenez ici.")

    def _on_linked(self, success: bool, detail: str):
        if success:
            # Liaison réussie → lève le drapeau de ré-authentification.
            pending = getattr(self, "_pending_link_id", None)
            if pending is not None:
                self.repo.flag_reauth(pending, needed=False)
            QMessageBox.information(self, "Compte lié", detail)
        else:
            QMessageBox.warning(self, "Liaison échouée", detail)
        self._pending_link_id = None
        self.refresh()
        self.on_changed()

    def _toggle_active(self, account_id: int, active: bool):
        self.repo.set_active(account_id, active)
        self.refresh()

    def _set_content_type(self, account_id: int, content_type_id: str | None):
        self.repo.set_content_type(account_id, content_type_id)
        self.on_changed()

    def _delete(self, account_id: int, name: str):
        rep = QMessageBox.question(self, "Supprimer",
                                   f"Supprimer le compte « {name} » ?")
        if rep == QMessageBox.StandardButton.Yes:
            self.repo.delete(account_id)
            self.refresh()
            self.on_changed()

    # ── Rendu ───────────────────────────────────────────────────────────

    def _refresh_cooldown_column(self):
        plugins = get_plugins()
        rows = []
        for p in plugins.values():
            for a in p.list_accounts():
                rows.append(a)
        for r, a in enumerate(rows):
            if r >= self.table.rowCount():
                break
            remaining = self.repo.remaining_cooldown(a.id)
            if remaining <= 0:
                text, color = "✔ Disponible", theme.OK
            else:
                h, s = divmod(remaining, 3600)
                m = s // 60
                text = f"⏳ {h}h{m:02d}" if h else f"⏳ {m}min"
                color = theme.WARN
            item = self.table.item(r, 6)
            if item:
                item.setText(text)
                item.setForeground(QColor(color))

    def refresh(self):
        plugins = get_plugins()
        rows = []
        reauth_pending = []
        for p in plugins.values():
            for a in p.list_accounts():
                rows.append((p, a))
                if a.credentials.get("needs_reauth"):
                    reauth_pending.append((p, a))
        self.table.setRowCount(len(rows))
        for r, (p, a) in enumerate(rows):
            linked = p.is_account_linked(a)
            needs_reauth = bool(a.credentials.get("needs_reauth"))
            self.table.setRowHeight(r, 48)

            net_item = QTableWidgetItem(f" {p.display_name}")
            net_item.setIcon(brand.icon(p.icon, 18))
            self.table.setItem(r, 0, net_item)
            self.table.setItem(r, 1, QTableWidgetItem(a.name))
            self.table.setItem(r, 2, QTableWidgetItem(a.handle or "—"))

            # Colonne « Lié » : signale aussi une ré-authentification requise.
            if needs_reauth:
                lk = QTableWidgetItem("⚠ Ré-auth")
                lk.setForeground(QColor(theme.ERR))
                lk.setToolTip("Token expiré/révoqué — cliquez « Re-lier » pour ré-authentifier.")
            elif linked:
                lk = QTableWidgetItem("✔ Oui")
                lk.setForeground(QColor(theme.OK))
            else:
                lk = QTableWidgetItem("✗ Non")
                lk.setForeground(QColor(theme.TEXT_FAINT))
            self.table.setItem(r, 3, lk)

            act = QTableWidgetItem("Actif" if a.is_active else "Inactif")
            act.setForeground(QColor(theme.OK) if a.is_active else QColor(theme.TEXT_FAINT))
            self.table.setItem(r, 4, act)

            self.table.setCellWidget(r, 5, self._content_type_cell(p, a))

            remaining = self.repo.remaining_cooldown(a.id)
            if remaining <= 0:
                avail_text, avail_color = "✔ Disponible", theme.OK
            else:
                h, s = divmod(remaining, 3600)
                m = s // 60
                avail_text = f"⏳ {h}h{m:02d}" if h else f"⏳ {m}min"
                avail_color = theme.WARN
            avail_item = QTableWidgetItem(avail_text)
            avail_item.setForeground(QColor(avail_color))
            self.table.setItem(r, 6, avail_item)

            self.table.setCellWidget(r, 7, self._actions_cell(p, a, linked, needs_reauth))

        # Ré-authentification automatique : si un compte a été marqué (token
        # révoqué pendant une routine), propose de relancer l'OAuth tout de suite.
        # Une seule invite à la fois et jamais en boucle (anti-spam navigateur).
        self._maybe_auto_reauth(reauth_pending)

    def _maybe_auto_reauth(self, pending: list):
        """Déclenche automatiquement la ré-auth des comptes flaggés.

        Pour éviter d'ouvrir plusieurs onglets navigateur en rafale, on traite
        UN compte à la fois et on n'auto-déclenche pas un compte déjà tenté dans
        cette session (l'utilisateur garde le bouton « Ré-authentifier » manuel).
        """
        if not pending:
            return
        if self._link_worker and self._link_worker.isRunning():
            return  # une liaison est déjà en cours
        attempted = getattr(self, "_auto_reauth_attempted", set())
        for plugin, account in pending:
            if account.id in attempted:
                continue
            # Réseau en standby (ex: TikTok en review) → pas d'auto-reauth possible.
            if plugin.info().state.value == "standby":
                continue
            attempted.add(account.id)
            self._auto_reauth_attempted = attempted
            rep = QMessageBox.question(
                self, "Ré-authentification requise",
                f"Le token du compte « {account.name} » ({plugin.display_name}) a expiré.\n\n"
                "Ouvrir la page d'autorisation maintenant pour le ré-authentifier ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if rep == QMessageBox.StandardButton.Yes:
                self._link(plugin.id, account.id)
            return  # un seul à la fois

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

    def _actions_cell(self, plugin, account, linked: bool, needs_reauth: bool = False) -> QWidget:
        cell = QWidget()
        cell.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        if needs_reauth:
            link_btn = QPushButton("⚠ Ré-authentifier")
            # Met le bouton en évidence quand une ré-auth est requise.
            link_btn.setStyleSheet(
                f"QPushButton {{ background:{theme.ERR}; border:none; border-radius:6px; "
                f"padding:4px 10px; color:#0b0d12; font-size:12px; font-weight:600; }}"
                f"QPushButton:hover {{ background:{theme.ACCENT}; }}")
        else:
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
