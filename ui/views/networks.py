"""Vue Réseaux : cartes par plateforme, état, et configuration des clés API.

Un réseau sans clé reste en Standby. La fenêtre de configuration expose les
champs déclarés par le plugin (config_fields) et fait basculer l'état dès que
les clés requises sont fournies.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
)

from core.registry import get_plugins
from .. import theme, widgets, brand


class ConfigDialog(QDialog):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle(f"Configurer {plugin.display_name}")
        self.setMinimumWidth(440)
        self.setStyleSheet(theme.qss())

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(brand.pixmap(plugin.icon, 26))
        logo.setFixedSize(26, 26)
        logo.setScaledContents(True)
        head.addWidget(logo)
        head.addWidget(widgets.title(plugin.display_name, "H2"))
        head.addStretch(1)
        lay.addLayout(head)
        lay.addWidget(widgets.dim(plugin.description))

        form = QFormLayout()
        form.setSpacing(10)
        self.fields: dict[str, QLineEdit] = {}
        config = plugin.load_config()
        for key, label in plugin.config_fields.items():
            edit = QLineEdit(str(config.get(key, "")))
            if "secret" in key:
                edit.setEchoMode(QLineEdit.Password)
            edit.setPlaceholderText(label)
            self.fields[key] = edit
            form.addRow(label, edit)
        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _save(self):
        config = {}
        for key, edit in self.fields.items():
            val = edit.text().strip()
            if val:
                config[key] = val
        self.plugin.save_config(config)
        self.accept()


class NetworkCard(widgets.Card):
    def __init__(self, plugin, on_changed):
        super().__init__()
        self.plugin = plugin
        self.on_changed = on_changed
        info = plugin.info()

        header = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(brand.pixmap(plugin.icon, 30))
        icon.setFixedSize(30, 30)
        icon.setScaledContents(True)
        header.addWidget(icon)
        name_box = QVBoxLayout()
        name_box.setSpacing(2)
        t = QLabel(info.display_name)
        t.setObjectName("CardTitle")
        name_box.addWidget(t)
        self.badge = widgets.StatusBadge(info.state.value)
        name_box.addWidget(self.badge, alignment=Qt.AlignLeft)
        header.addLayout(name_box)
        header.addStretch(1)
        self.body.addLayout(header)

        self.note = QLabel(info.note)
        self.note.setObjectName("CardNote")
        self.note.setWordWrap(True)
        self.body.addWidget(self.note)

        self.body.addWidget(widgets.dim(f"{info.accounts_count} compte(s) rattaché(s)"))

        actions = QHBoxLayout()
        cfg_btn = QPushButton("Configurer")
        cfg_btn.clicked.connect(self._configure)
        actions.addWidget(cfg_btn)
        actions.addStretch(1)
        self.body.addLayout(actions)

    def _configure(self):
        dlg = ConfigDialog(self.plugin, self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            self.on_changed()

    def refresh(self):
        info = self.plugin.info()
        self.badge.set_state(info.state.value)
        self.note.setText(info.note)


class NetworksView(QWidget):
    def __init__(self, on_changed=lambda: None):
        super().__init__()
        self.on_changed = on_changed
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        root.addWidget(widgets.title("Réseaux"))
        root.addWidget(widgets.dim(
            "Ajoutez des plateformes sous forme de modules. Un réseau sans clé "
            "reste en Standby jusqu'à configuration."))

        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        root.addLayout(self.grid)
        root.addStretch(1)

        self.cards = []
        for i, plugin in enumerate(get_plugins().values()):
            card = NetworkCard(plugin, on_changed)
            self.cards.append(card)
            self.grid.addWidget(card, i // 2, i % 2)

    def refresh(self):
        for c in self.cards:
            c.refresh()
