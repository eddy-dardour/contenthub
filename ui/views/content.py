"""Vue Contenu — Catalogue + Automatisation fusionnés.

Sections :
  • Catalogue  : cartes ContentType, génération / distribution, liste vidéos
  • Automatisation : options routine, statut API/tunnel, historique runs Claude
Panneau fixe en bas : barre de progression + console d'activité.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import threading
import time as _time
from pathlib import Path

import requests as _requests

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QProgressBar, QFrame, QScrollArea,
    QListWidget, QListWidgetItem, QAbstractItemView,
    QComboBox, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
)

from core import catalog, content as content_mod, progress_state
from core.catalog import ContentType
from core.campaign import plan
from core.registry import get_plugins
from ..workers import CampaignWorker, GenerateWorker, DistributeWorker
from .. import theme, widgets, brand


# ── Styles boutons ───────────────────────────────────────────────────────────

_GENERATE_STYLE = (
    f"QPushButton {{ background:{theme.SURFACE_2}; border:1px solid {theme.ACCENT}; "
    f"border-radius:8px; padding:10px 20px; color:{theme.ACCENT}; font-weight:600; font-size:13px; }}"
    f"QPushButton:hover {{ background:{theme.ACCENT}; color:#0b0d12; }}"
    f"QPushButton:disabled {{ border-color:{theme.BORDER}; color:{theme.TEXT_FAINT}; background:{theme.SURFACE_2}; }}"
)
_DISTRIBUTE_STYLE = (
    f"QPushButton {{ background:{theme.SURFACE_2}; border:1px solid {theme.OK}; "
    f"border-radius:8px; padding:10px 20px; color:{theme.OK}; font-weight:600; font-size:13px; }}"
    f"QPushButton:hover {{ background:{theme.OK}; color:#0b0d12; }}"
    f"QPushButton:disabled {{ border-color:{theme.BORDER}; color:{theme.TEXT_FAINT}; background:{theme.SURFACE_2}; }}"
)
_CAMPAIGN_STYLE = (
    f"QPushButton {{ background:{theme.ACCENT}; border:none; "
    f"border-radius:8px; padding:10px 20px; color:#0b0d12; font-weight:700; font-size:13px; }}"
    f"QPushButton:hover {{ background:{theme.ACCENT_HOVER}; }}"
    f"QPushButton:disabled {{ background:{theme.SURFACE_2}; color:{theme.TEXT_FAINT}; }}"
)


# ── Helpers config automation ────────────────────────────────────────────────

def _config_path():
    from core.paths import app_data_dir
    return app_data_dir() / "routine_config.json"


def _load_config() -> dict:
    p = _config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"wait_minutes": 0, "default_content_type": "tts_drama", "enabled": True}


def _save_config(cfg: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


_ROUTINE = Path(__file__).resolve().parent.parent.parent / "morning_routine.py"


# ── Carte : type de contenu ──────────────────────────────────────────────────

class ContentTypeCard(widgets.Card):
    def __init__(self, content_type: ContentType,
                 on_generate, on_distribute, on_campaign):
        super().__init__()
        self.ct = content_type
        self._on_generate = on_generate
        self._on_distribute = on_distribute
        self._on_campaign = on_campaign

        self.body.setSpacing(14)
        self.body.setContentsMargins(22, 20, 22, 20)

        head = QHBoxLayout()
        head.setSpacing(16)
        icon_lbl = QLabel(content_type.icon)
        icon_lbl.setStyleSheet("font-size:36px;")
        icon_lbl.setFixedWidth(52)
        head.addWidget(icon_lbl)

        info_box = QVBoxLayout()
        info_box.setSpacing(4)
        title_lbl = QLabel(content_type.label)
        title_lbl.setStyleSheet(f"font-size:18px; font-weight:700; color:{theme.TEXT};")
        info_box.addWidget(title_lbl)
        desc_lbl = QLabel(content_type.description)
        desc_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:13px;")
        desc_lbl.setWordWrap(True)
        info_box.addWidget(desc_lbl)
        head.addLayout(info_box, 1)

        logo_box = QHBoxLayout()
        logo_box.setSpacing(8)
        plugins = get_plugins()
        for net_id in content_type.networks:
            p = plugins.get(net_id)
            if not p:
                continue
            logo = QLabel()
            logo.setPixmap(brand.pixmap(p.icon, 28))
            logo.setFixedSize(28, 28)
            logo.setScaledContents(True)
            logo.setToolTip(p.display_name)
            logo_box.addWidget(logo)
        head.addLayout(logo_box)
        self.body.addLayout(head)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{theme.BORDER};")
        self.body.addWidget(sep)

        self.plan_lbl = QLabel()
        self.plan_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:13px;")
        self.plan_lbl.setWordWrap(True)
        self.body.addWidget(self.plan_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.gen_btn = QPushButton("⬇  Générer")
        self.gen_btn.setStyleSheet(_GENERATE_STYLE)
        self.gen_btn.setMinimumWidth(140)
        self.gen_btn.setToolTip("Génère les vidéos (sans les publier).")
        self.gen_btn.clicked.connect(lambda: self._on_generate(self.ct))
        btn_row.addWidget(self.gen_btn)

        self.dist_btn = QPushButton("↑  Distribuer")
        self.dist_btn.setStyleSheet(_DISTRIBUTE_STYLE)
        self.dist_btn.setMinimumWidth(140)
        self.dist_btn.setToolTip("Publie les vidéos déjà générées.")
        self.dist_btn.clicked.connect(lambda: self._on_distribute(self.ct))
        btn_row.addWidget(self.dist_btn)

        self.campaign_btn = QPushButton("⚡  Générer & Distribuer")
        self.campaign_btn.setStyleSheet(_CAMPAIGN_STYLE)
        self.campaign_btn.setMinimumWidth(200)
        self.campaign_btn.setToolTip("Pipeline complet : génère puis publie immédiatement.")
        self.campaign_btn.clicked.connect(lambda: self._on_campaign(self.ct))
        btn_row.addWidget(self.campaign_btn)

        btn_row.addStretch(1)
        self.body.addLayout(btn_row)
        self.refresh_plan()

    def refresh_plan(self):
        preview = plan(self.ct)
        if not preview:
            self.plan_lbl.setText("⚠  Aucun compte lié+actif sur les plateformes épinglées.")
            self.dist_btn.setEnabled(False)
            self.campaign_btn.setEnabled(False)
            return
        self.dist_btn.setEnabled(True)
        self.campaign_btn.setEnabled(True)
        plugins = get_plugins()
        parts = []
        total = 0
        for net_id, n in preview.items():
            name = plugins[net_id].display_name if net_id in plugins else net_id
            parts.append(f"{name} : {n} compte(s)")
            total += n
        self.plan_lbl.setText(
            "  ·  ".join(parts) + f"   →   {total} vidéo(s) à générer")

    def set_running(self, running: bool):
        self.gen_btn.setEnabled(not running)
        self.dist_btn.setEnabled(not running)
        self.campaign_btn.setEnabled(not running)
        self.campaign_btn.setText("En cours…" if running else "⚡  Générer & Distribuer")
        if not running:
            self.gen_btn.setText("⬇  Générer")
            self.dist_btn.setText("↑  Distribuer")


# ── Carte : liste vidéos ─────────────────────────────────────────────────────

class VideoListCard(widgets.Card):
    def __init__(self):
        super().__init__()
        self.body.setSpacing(10)
        self.body.setContentsMargins(22, 18, 22, 18)

        head = QHBoxLayout()
        head.addWidget(widgets.title("Vidéos disponibles", "H2"))
        head.addStretch(1)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(
            f"color:{theme.TEXT_DIM}; font-size:12px; background:{theme.SURFACE_2}; "
            f"border:1px solid {theme.BORDER}; border-radius:8px; padding:3px 10px;")
        head.addWidget(self._count_lbl)
        self.refresh_btn = QPushButton("↻ Rafraîchir")
        self.refresh_btn.setFixedWidth(100)
        self.refresh_btn.setStyleSheet(
            f"QPushButton {{ background:{theme.SURFACE_2}; border:1px solid {theme.BORDER}; "
            f"border-radius:6px; padding:4px 10px; color:{theme.TEXT_DIM}; font-size:12px; }}"
            f"QPushButton:hover {{ border-color:{theme.ACCENT}; color:{theme.ACCENT}; }}")
        head.addWidget(self.refresh_btn)
        self.body.addLayout(head)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{theme.BORDER};")
        self.body.addWidget(sep)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.NoSelection)
        self._list.setStyleSheet(
            f"QListWidget {{ background:{theme.SURFACE}; border:none; border-radius:8px; padding:4px; }}"
            f"QListWidget::item {{ padding:8px 12px; border-bottom:1px solid {theme.BORDER}; color:{theme.TEXT}; }}"
            f"QListWidget::item:last {{ border-bottom:none; }}")
        self._list.setMinimumHeight(100)
        self._list.setMaximumHeight(260)
        self.body.addWidget(self._list)

        self._empty_lbl = QLabel("Aucune vidéo disponible.\nCliquez sur « Générer » pour en créer.")
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size:13px; padding:24px;")
        self._empty_lbl.setVisible(False)
        self.body.addWidget(self._empty_lbl)

        self.refresh_btn.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self):
        self._list.clear()
        items = content_mod.list_content()
        if not items:
            self._list.setVisible(False)
            self._empty_lbl.setVisible(True)
            self._count_lbl.setText("0 vidéo")
            return
        self._list.setVisible(True)
        self._empty_lbl.setVisible(False)
        self._count_lbl.setText(f"{len(items)} vidéo{'s' if len(items) > 1 else ''}")
        for item in items:
            size_mb = item.size_bytes / 1_048_576
            caption_short = (item.caption or "")[:60] + ("…" if len(item.caption or "") > 60 else "")
            line = QListWidgetItem(f"🎬  {item.key}   •   {size_mb:.1f} Mo   •   {caption_short}")
            line.setToolTip(item.caption or "")
            line.setForeground(QColor(theme.TEXT))
            self._list.addItem(line)


# ── Vue principale ───────────────────────────────────────────────────────────

class ContentView(QWidget):
    def __init__(self):
        super().__init__()
        self._campaign_worker: CampaignWorker | None = None
        self._generate_worker: GenerateWorker | None = None
        self._distribute_worker: DistributeWorker | None = None
        self._cards: list[ContentTypeCard] = []
        self._cfg = _load_config()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header fixe ──────────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(f"background:{theme.BG};")
        hlay = QVBoxLayout(header)
        hlay.setContentsMargins(28, 24, 28, 10)
        hlay.setSpacing(4)
        hlay.addWidget(widgets.title("Contenu"))
        hlay.addWidget(widgets.dim(
            "Générez et distribuez votre contenu. Configurez et pilotez la routine automatique."))

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setFixedHeight(5)
        ctrl_row.addWidget(self.progress, 1)
        self.stop_btn = QPushButton("⏹  Arrêter")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setVisible(False)
        self.stop_btn.setFixedWidth(110)
        self.stop_btn.clicked.connect(self._stop_all)
        ctrl_row.addWidget(self.stop_btn)
        hlay.addLayout(ctrl_row)
        root.addWidget(header)

        # ── Zone scrollable ───────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"QScrollArea {{ background:{theme.BG}; border:none; }}")

        cards_host = QWidget()
        cards_host.setStyleSheet(f"background:{theme.BG};")
        cards_lay = QVBoxLayout(cards_host)
        cards_lay.setContentsMargins(28, 8, 28, 20)
        cards_lay.setSpacing(16)

        # — Section Catalogue —
        cards_lay.addWidget(widgets.title("Catalogue", "H2"))

        for ct in catalog.list_types():
            card = ContentTypeCard(
                ct,
                on_generate=self._run_generate,
                on_distribute=self._run_distribute,
                on_campaign=self._run_campaign,
            )
            self._cards.append(card)
            cards_lay.addWidget(card)

        self._video_list_card = VideoListCard()
        cards_lay.addWidget(self._video_list_card)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{theme.BORDER}; margin-top:8px; margin-bottom:4px;")
        cards_lay.addWidget(sep)

        # — Section Automatisation —
        cards_lay.addWidget(widgets.title("Automatisation", "H2"))

        cards_lay.addWidget(self._build_options_card())
        cards_lay.addWidget(self._build_api_card())
        cards_lay.addWidget(self._build_history_card())
        cards_lay.addStretch(1)

        scroll.setWidget(cards_host)
        root.addWidget(scroll, 1)

        # ── Panneau Progress + Console fixe en bas ────────────────────────────
        bottom = QWidget()
        bottom.setStyleSheet(f"background:{theme.BG};")
        bot_lay = QVBoxLayout(bottom)
        bot_lay.setContentsMargins(28, 0, 28, 20)
        bot_lay.setSpacing(8)

        prog_card = widgets.Card()
        prog_card.body.setContentsMargins(16, 12, 16, 12)
        prog_card.body.setSpacing(6)

        prog_head = QHBoxLayout()
        prog_head.addWidget(widgets.title("Progression", "H2"))
        prog_head.addStretch(1)
        self._step_lbl = QLabel("En attente…")
        self._step_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px; font-style:italic;")
        prog_head.addWidget(self._step_lbl)
        prog_card.body.addLayout(prog_head)

        self._det_bar = QProgressBar()
        self._det_bar.setRange(0, 1)
        self._det_bar.setValue(0)
        self._det_bar.setFixedHeight(8)
        self._det_bar.setTextVisible(False)
        self._det_bar.setStyleSheet(
            f"QProgressBar {{ background:{theme.SURFACE_2}; border-radius:4px; border:none; }}"
            f"QProgressBar::chunk {{ background:{theme.ACCENT}; border-radius:4px; }}")
        prog_card.body.addWidget(self._det_bar)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px; padding-top:2px;")
        self._summary_lbl.setWordWrap(True)
        prog_card.body.addWidget(self._summary_lbl)
        bot_lay.addWidget(prog_card)

        log_card = widgets.Card()
        log_card.body.setContentsMargins(16, 12, 16, 12)
        log_card.body.setSpacing(6)

        log_head = QHBoxLayout()
        log_head.addWidget(widgets.title("Activité & logs vidéo", "H2"))
        log_head.addStretch(1)
        clear_btn = QPushButton("Effacer")
        clear_btn.setFixedWidth(70)
        clear_btn.clicked.connect(lambda: self.console.clear())
        log_head.addWidget(clear_btn)
        log_card.body.addLayout(log_head)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(160)
        self.console.setMaximumHeight(260)
        self.console.setStyleSheet(
            f"background:{theme.SURFACE}; border:none; "
            f"font-family:monospace; font-size:12px;")
        log_card.body.addWidget(self.console)
        bot_lay.addWidget(log_card)
        root.addWidget(bottom)

        # ── Synchro routine externe (Claude cloud) ────────────────────────────
        self._startup_ts: float = _time.time()
        self._ext_run_id: str | None = None
        self._ext_active = False
        self._ext_timer = QTimer(self)
        self._ext_timer.setInterval(1000)
        self._ext_timer.timeout.connect(self._poll_external)
        self._ext_timer.start()

        # Polling API + tunnel toutes les 10s
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_services)
        self._poll_timer.start(10_000)
        self._poll_services()
        self._refresh_runs_table()

    # ── Construction cartes automation ────────────────────────────────────────

    def _build_options_card(self) -> widgets.Card:
        card = widgets.Card()
        card.body.setContentsMargins(22, 20, 22, 20)
        card.body.setSpacing(14)
        card.body.addWidget(widgets.title("Options routine", "H3"))

        self.enabled_chk = QCheckBox("Routine activée")
        self.enabled_chk.setStyleSheet(f"color:{theme.TEXT}; font-size:13px;")
        self.enabled_chk.setChecked(self._cfg.get("enabled", True))
        self.enabled_chk.toggled.connect(self._save_cfg)
        card.body.addWidget(self.enabled_chk)

        type_row = QHBoxLayout()
        type_row.setSpacing(10)
        type_row.addWidget(QLabel("Type de contenu :"))
        self.type_combo = QComboBox()
        self.type_combo.setMinimumWidth(220)
        current = self._cfg.get("default_content_type", "")
        for ct in catalog.list_types():
            self.type_combo.addItem(f"{ct.icon}  {ct.label}", ct.id)
            if ct.id == current:
                self.type_combo.setCurrentIndex(self.type_combo.count() - 1)
        self.type_combo.currentIndexChanged.connect(self._save_cfg)
        type_row.addWidget(self.type_combo)
        type_row.addStretch(1)
        card.body.addLayout(type_row)

        act_row = QHBoxLayout()
        run_btn = QPushButton("▶  Lancer la routine maintenant")
        run_btn.setObjectName("Primary")
        run_btn.clicked.connect(self._run_routine_now)
        act_row.addWidget(run_btn)
        act_row.addStretch(1)
        card.body.addLayout(act_row)
        return card


    def _build_api_card(self) -> widgets.Card:
        card = widgets.Card()
        card.body.setContentsMargins(22, 20, 22, 20)
        card.body.setSpacing(14)
        card.body.addWidget(widgets.title("Serveur API + Tunnel", "H3"))
        card.body.addWidget(widgets.dim(
            "Démarre automatiquement. Expose l'API via tunnel pour la routine cloud Claude."))

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{theme.BORDER};")
        card.body.addWidget(sep)

        status_row = QHBoxLayout()
        status_row.setSpacing(24)
        self.api_status_label = QLabel("● API : vérification...")
        self.api_status_label.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px;")
        status_row.addWidget(self.api_status_label)
        self.tunnel_status_label = QLabel("● Tunnel : vérification...")
        self.tunnel_status_label.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px;")
        status_row.addWidget(self.tunnel_status_label)
        status_row.addStretch(1)
        card.body.addLayout(status_row)

        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        url_row.addWidget(QLabel("URL publique :"))
        self.tunnel_url_label = QLabel("(détection en cours...)")
        self.tunnel_url_label.setStyleSheet(
            f"color:{theme.ACCENT}; font-family:monospace; font-size:11px;")
        self.tunnel_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        url_row.addWidget(self.tunnel_url_label)
        copy_url_btn = QPushButton("Copier")
        copy_url_btn.setFixedWidth(60)
        copy_url_btn.clicked.connect(self._copy_tunnel_url)
        url_row.addWidget(copy_url_btn)
        url_row.addStretch(1)
        card.body.addLayout(url_row)

        key_row = QHBoxLayout()
        key_row.setSpacing(10)
        key_row.addWidget(QLabel("Clé API :"))
        self.api_key_label = QLabel()
        self.api_key_label.setStyleSheet(
            f"color:{theme.TEXT_DIM}; font-family:monospace; font-size:11px;")
        self.api_key_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        key_row.addWidget(self.api_key_label)
        copy_key_btn = QPushButton("Copier")
        copy_key_btn.setFixedWidth(60)
        copy_key_btn.clicked.connect(self._copy_api_key)
        key_row.addWidget(copy_key_btn)
        regen_btn = QPushButton("Regénérer")
        regen_btn.clicked.connect(self._regenerate_api_key)
        key_row.addWidget(regen_btn)
        key_row.addStretch(1)
        card.body.addLayout(key_row)
        self._load_api_config()
        return card

    def _build_history_card(self) -> widgets.Card:
        card = widgets.Card()
        card.body.setContentsMargins(22, 20, 22, 20)
        card.body.setSpacing(14)
        card.body.addWidget(widgets.title("Historique Claude Routine", "H3"))
        card.body.addWidget(widgets.dim(
            "Chaque déclenchement cloud est logué ici avec le statut des uploads."))

        self.runs_table = QTableWidget(0, 5)
        self.runs_table.setHorizontalHeaderLabels(["Date", "Slot", "Statut", "Résumé", "Uploads"])
        self.runs_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.runs_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.runs_table.setAlternatingRowColors(True)
        self.runs_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.runs_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.runs_table.setMaximumHeight(220)
        self.runs_table.setStyleSheet(
            f"QTableWidget {{ background:{theme.SURFACE}; border:none; font-size:12px; }}"
            f"QHeaderView::section {{ background:{theme.SURFACE_2}; color:{theme.TEXT_DIM}; "
            f"font-size:11px; border:none; padding:4px; }}"
            f"QTableWidget::item {{ color:{theme.TEXT}; padding:4px; }}")
        card.body.addWidget(self.runs_table)

        ref_row = QHBoxLayout()
        ref_btn = QPushButton("Rafraîchir")
        ref_btn.clicked.connect(self._refresh_runs_table)
        ref_row.addWidget(ref_btn)
        ref_row.addStretch(1)
        card.body.addLayout(ref_row)
        return card

    # ── Automation helpers ────────────────────────────────────────────────────

    def _save_cfg(self):
        self._cfg["enabled"] = self.enabled_chk.isChecked()
        self._cfg["default_content_type"] = self.type_combo.currentData()
        _save_config(self._cfg)

    def _run_routine_now(self):
        self._save_cfg()
        try:
            subprocess.Popen(
                [sys.executable, str(_ROUTINE), "--now"],
                cwd=str(_ROUTINE.parent))
            self._log("▶ Routine lancée.")
        except Exception as e:
            self._log(f"✖ Erreur lancement routine : {e}")

    def _load_api_config(self):
        cfg = _load_config()
        api_key = cfg.get("api_key", "")
        self.api_key_label.setText(
            (api_key[:16] + "...") if len(api_key) > 16 else api_key or "(pas encore générée)")
        url = cfg.get("cloudflare_url") or cfg.get("ngrok_url") or ""
        self.tunnel_url_label.setText(url or "(pas encore disponible)")

    def _poll_services(self):
        threading.Thread(target=self._poll_services_worker, daemon=True).start()

    def _poll_services_worker(self):
        from PySide6.QtCore import QMetaObject, Q_ARG, Qt as _Qt
        try:
            r = _requests.get("http://localhost:5050/health", timeout=3)
            api_ok = r.status_code == 200
        except Exception:
            api_ok = False
        cfg = _load_config()
        url = cfg.get("cloudflare_url") or cfg.get("ngrok_url") or ""
        tunnel_ok = False
        if url:
            try:
                r = _requests.get(f"{url}/health", timeout=12)
                tunnel_ok = r.status_code == 200
            except Exception:
                pass
        QMetaObject.invokeMethod(
            self, "_apply_poll_result",
            _Qt.ConnectionType.QueuedConnection,
            Q_ARG(bool, api_ok), Q_ARG(str, url), Q_ARG(bool, tunnel_ok))

    @Slot(bool, str, bool)
    def _apply_poll_result(self, api_ok: bool, url: str, tunnel_ok: bool):
        if api_ok:
            self.api_status_label.setText("● API : active (port 5050)")
            self.api_status_label.setStyleSheet(f"color:{theme.OK}; font-size:12px;")
        else:
            self.api_status_label.setText("● API : arrêtée")
            self.api_status_label.setStyleSheet(f"color:{theme.ERR}; font-size:12px;")
        if not url:
            self.tunnel_status_label.setText("● Tunnel : non configuré")
            self.tunnel_status_label.setStyleSheet(f"color:{theme.WARN}; font-size:12px;")
        else:
            self.tunnel_url_label.setText(url)
            if tunnel_ok:
                self.tunnel_status_label.setText("● Tunnel : actif")
                self.tunnel_status_label.setStyleSheet(f"color:{theme.OK}; font-size:12px;")
            else:
                self.tunnel_status_label.setText("● Tunnel : injoignable")
                self.tunnel_status_label.setStyleSheet(f"color:{theme.ERR}; font-size:12px;")

    def _refresh_runs_table(self):
        cfg = _load_config()
        api_key = cfg.get("api_key", "")
        try:
            r = _requests.get("http://localhost:5050/logs",
                              headers={"X-API-Key": api_key},
                              params={"limit": 15}, timeout=3)
            if r.status_code != 200:
                return
            runs = r.json().get("runs", [])
        except Exception:
            return
        self.runs_table.setRowCount(0)
        for run in runs:
            row = self.runs_table.rowCount()
            self.runs_table.insertRow(row)
            ts = run.get("timestamp", "")[:16].replace("T", " ")
            slot = str(run.get("slot", ""))
            status = run.get("status", "")
            summary = run.get("summary", "")
            uploads = run.get("uploads", [])
            upload_txt = ", ".join(
                f"{u.get('network_id','?')}:{u.get('status','?')}" for u in uploads
            ) if uploads else "—"
            color = {"ok": theme.OK, "success": theme.OK,
                     "error": theme.ERR, "failed": theme.ERR,
                     "skipped": theme.TEXT_FAINT}.get(status, theme.TEXT_DIM)
            for col, text in enumerate([ts, slot, status, summary, upload_txt]):
                item = QTableWidgetItem(text)
                if col == 2:
                    item.setForeground(QColor(color))
                self.runs_table.setItem(row, col, item)

    def _copy_tunnel_url(self):
        from PySide6.QtWidgets import QApplication
        url = self.tunnel_url_label.text()
        if url and url not in ("(pas encore disponible)", "(détection en cours...)"):
            QApplication.clipboard().setText(url)
            self._log("✔ URL tunnel copiée")

    def _copy_api_key(self):
        from PySide6.QtWidgets import QApplication
        api_key = _load_config().get("api_key", "")
        if api_key:
            QApplication.clipboard().setText(api_key)
            self._log("✔ Clé API copiée")

    def _regenerate_api_key(self):
        cfg = _load_config()
        cfg["api_key"] = secrets.token_urlsafe(32)
        _save_config(cfg)
        self._load_api_config()
        self._log("✔ Nouvelle clé API générée")

    # ── Synchro routine externe ───────────────────────────────────────────────

    def _poll_external(self):
        if self._is_busy():
            return
        try:
            state = progress_state.read()
        except Exception:
            return
        if state.get("updated_at", 0) < self._startup_ts:
            return
        if state.get("active") and state.get("status") == "running":
            run_id = state.get("run_id")
            if not self._ext_active or run_id != self._ext_run_id:
                self._ext_active = True
                self._ext_run_id = run_id
                self.progress.setVisible(True)
                self.stop_btn.setVisible(False)
                self._summary_lbl.setText("")
                self._log("▶ Routine Claude détectée — suivi en direct.")
                for c in self._cards:
                    c.set_running(True)
            v, m = state.get("value", 0), max(state.get("maximum", 1), 1)
            self._det_bar.setRange(0, m)
            self._det_bar.setValue(v)
            lbl = state.get("label", "")
            if lbl:
                self._step_lbl.setText(lbl)
        elif self._ext_active:
            self._ext_active = False
            self.progress.setVisible(False)
            self._det_bar.setRange(0, 1)
            self._det_bar.setValue(1)
            for c in self._cards:
                c.set_running(False)
            summary = state.get("summary", "")
            self._step_lbl.setText("Terminé")
            if summary:
                self._summary_lbl.setText(f"✔ {summary}")
            self._log(f"✔ Routine Claude terminée — {summary or 'OK'}.")
            reauth = state.get("needs_reauth", [])
            if reauth:
                self._log(f"⚠ Comptes à ré-authentifier : {', '.join(reauth)}")
            self.refresh()

    # ── Guards & helpers ──────────────────────────────────────────────────────

    def _is_busy(self) -> bool:
        return (
            (self._campaign_worker and self._campaign_worker.isRunning()) or
            (self._generate_worker and self._generate_worker.isRunning()) or
            (self._distribute_worker and self._distribute_worker.isRunning())
        )

    def _log(self, msg: str):
        self.console.append(msg)
        self.console.verticalScrollBar().setValue(
            self.console.verticalScrollBar().maximum())

    def _set_running(self, running: bool):
        for c in self._cards:
            c.set_running(running)
        self.progress.setVisible(running)
        self.stop_btn.setVisible(running)
        if running:
            self._det_bar.setRange(0, 0)
            self._det_bar.setValue(0)
            self._step_lbl.setText("Démarrage…")
            self._summary_lbl.setText("")
        else:
            self._det_bar.setRange(0, 1)
            self._det_bar.setValue(1)

    def _stop_all(self):
        if self._campaign_worker:
            self._campaign_worker.stop()
        if self._generate_worker:
            self._generate_worker.stop()
        if self._distribute_worker:
            self._distribute_worker.stop()
        self._log("⏹ Arrêt demandé…")

    def refresh(self):
        for card in self._cards:
            card.refresh_plan()
        self._video_list_card.refresh()
        self._cfg = _load_config()
        self.enabled_chk.setChecked(self._cfg.get("enabled", True))
        self._load_api_config()
        self._refresh_runs_table()

    # ── Générer ───────────────────────────────────────────────────────────────

    def _run_generate(self, content_type: ContentType):
        if self._is_busy():
            self._log("⚠ Une opération est déjà en cours.")
            return
        from core.campaign import plan as make_plan
        preview = make_plan(content_type)
        n = max(sum(preview.values()), 1) if preview else 1
        self._log(f"⬇ Génération de {n} vidéo(s) « {content_type.label} »…")
        self._set_running(True)
        self._generate_worker = GenerateWorker(n, content_type.gen_type)
        self._generate_worker.log.connect(lambda m: self._log(f"  {m}"))
        self._generate_worker.finished_result.connect(self._on_generate_done)
        self._generate_worker.start()

    def _on_generate_done(self, ok: bool):
        self._set_running(False)
        self._log("✔ Génération terminée." if ok else "✖ Génération échouée ou interrompue.")
        self.refresh()

    # ── Distribuer ────────────────────────────────────────────────────────────

    def _run_distribute(self, content_type: ContentType):
        if self._is_busy():
            self._log("⚠ Une opération est déjà en cours.")
            return
        self._log(f"↑ Distribution « {content_type.label} »…")
        self._set_running(True)
        self._distribute_worker = DistributeWorker(
            content_type_id=content_type.id,
            network_ids=list(content_type.networks) if content_type.networks else None)
        self._distribute_worker.event.connect(self._on_event)
        self._distribute_worker.finished_result.connect(self._on_distribute_done)
        self._distribute_worker.start()

    def _on_distribute_done(self, summary: dict):
        self._set_running(False)
        self.refresh()
        pub = summary.get("published", 0)
        fail = summary.get("failed", 0)
        skip = summary.get("skipped", 0)
        self._log(f"✔ Distribution — {pub} publiée(s), {fail} échec(s), {skip} ignorée(s).")
        self._step_lbl.setText("Terminé")
        self._summary_lbl.setText(
            f"✔ {pub} publiée(s)" + (f" · {fail} échec(s)" if fail else "")
            + (f" · {skip} ignorée(s)" if skip else ""))

    # ── Générer & Distribuer ──────────────────────────────────────────────────

    def _run_campaign(self, content_type: ContentType):
        if self._is_busy():
            self._log("⚠ Une opération est déjà en cours.")
            return
        self._log(f"⚡ Campagne « {content_type.label} »…")
        self._set_running(True)
        self._campaign_worker = CampaignWorker(content_type)
        self._campaign_worker.event.connect(self._on_event)
        self._campaign_worker.finished_result.connect(self._on_campaign_done)
        self._campaign_worker.start()

    def _on_campaign_done(self, summary: dict):
        self._set_running(False)
        self.refresh()
        if summary.get("no_accounts"):
            self._log("⚠ Aucun compte lié+actif sur les plateformes ciblées.")
            self._step_lbl.setText("Aucun compte éligible")
            self._summary_lbl.setText("⚠ Aucun compte lié+actif.")
        else:
            pub = summary.get("published", 0)
            fail = summary.get("failed", 0)
            gen = summary.get("generated", 0)
            cd = summary.get("skipped_cooldown", 0)
            parts = [f"{gen} générée(s)", f"{pub} publiée(s)"]
            if fail:
                parts.append(f"{fail} échec(s)")
            if cd:
                parts.append(f"{cd} en cooldown (cascade)")
            self._log("✔ Campagne — " + " · ".join(parts))
            self._step_lbl.setText("Terminé")
            self._summary_lbl.setText("✔ " + " · ".join(parts))

    # ── Événements publisher ──────────────────────────────────────────────────

    def _on_event(self, event: str, data: dict):
        if event == "step":
            idx, total = data.get("index", 0), data.get("total", 1)
            self._det_bar.setRange(0, total)
            self._det_bar.setValue(idx)
            self._step_lbl.setText(data.get("label", ""))
        elif event == "progress":
            v, m = data.get("value", 0), data.get("maximum", 1)
            if m > 0:
                self._det_bar.setRange(0, m)
                self._det_bar.setValue(v)

        msgs = {
            "uploading": lambda d: f"  ↑ {d.get('content','')} → {d.get('account','')} ({d.get('network','')})",
            "success":   lambda d: f"  ✔ {d.get('account','')} : publié.",
            "failed":    lambda d: f"  ✖ {d.get('account','')} : {d.get('error','échec')}",
            "cooldown":  lambda d: f"  ⏳ {d.get('account','')} cooldown — disponible dans {d.get('human', str(d.get('remaining',0))+'s')}",
            "retry":     lambda d: f"  ↻ {d.get('account','')} retry {d.get('attempt','')} dans {d.get('backoff','')}s",
            "info":      lambda d: f"  ℹ {d.get('message','')}",
            "log":       lambda d: f"  {d.get('message','')}",
        }
        fn = msgs.get(event)
        if fn:
            self._log(fn(data))
