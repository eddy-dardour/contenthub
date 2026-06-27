"""Vue Catalogue & Publication.

Une carte par type de contenu (actuellement : TTS Drama uniquement).
En dessous : liste des vidéos générées disponibles dans output/videos/.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTimeEdit,
    QTextEdit, QProgressBar, QFrame, QScrollArea, QSizePolicy,
    QListWidget, QListWidgetItem, QAbstractItemView,
)

from core import catalog, content as content_mod
from core.catalog import ContentType
from core.campaign import plan
from core.registry import get_plugins
from core.scheduler import DailyScheduler
from ..workers import CampaignWorker, GenerateWorker, DistributeWorker
from .. import theme, widgets, brand


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


class ContentTypeCard(widgets.Card):
    """Carte d'un type de contenu — pleine largeur."""

    def __init__(self, content_type: ContentType,
                 on_generate, on_distribute, on_campaign, on_toggle_schedule):
        super().__init__()
        self.ct = content_type
        self._on_generate = on_generate
        self._on_distribute = on_distribute
        self._on_campaign = on_campaign
        self._on_toggle_schedule = on_toggle_schedule
        self._scheduled = False

        self.body.setSpacing(14)
        self.body.setContentsMargins(22, 20, 22, 20)

        # ── En-tête ─────────────────────────────────────────────────────
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

        # Logos plateformes
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

        # ── Séparateur ───────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{theme.BORDER};")
        self.body.addWidget(sep)

        # ── Aperçu du plan ───────────────────────────────────────────────
        self.plan_lbl = QLabel()
        self.plan_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:13px;")
        self.plan_lbl.setWordWrap(True)
        self.body.addWidget(self.plan_lbl)

        # ── Boutons d'action ─────────────────────────────────────────────
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

        # ── Planification ─────────────────────────────────────────────────
        sched_row = QHBoxLayout()
        sched_row.setSpacing(10)
        sched_lbl = QLabel("Planifier chaque jour à")
        sched_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px;")
        sched_row.addWidget(sched_lbl)
        self.sched_time = QTimeEdit(QTime(8, 0))
        self.sched_time.setDisplayFormat("HH:mm")
        self.sched_time.setFixedWidth(72)
        sched_row.addWidget(self.sched_time)
        self.sched_btn = QPushButton("Planifier")
        self.sched_btn.setFixedWidth(110)
        self.sched_btn.clicked.connect(self._toggle_schedule)
        sched_row.addWidget(self.sched_btn)
        sched_row.addStretch(1)
        self.body.addLayout(sched_row)

        self.refresh_plan()

    def refresh_plan(self):
        preview = plan(self.ct)
        if not preview:
            self.plan_lbl.setText(
                "⚠  Aucun compte lié+actif sur les plateformes épinglées.")
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

    def _toggle_schedule(self):
        self._scheduled = not self._scheduled
        t = self.sched_time.time()
        self._on_toggle_schedule(self.ct, self._scheduled, t.hour(), t.minute())
        self.sched_btn.setText("Annuler" if self._scheduled else "Planifier")
        self.sched_btn.setStyleSheet(
            f"QPushButton {{ color:{theme.ERR}; }}" if self._scheduled else "")


class VideoListCard(widgets.Card):
    """Carte listant les vidéos disponibles dans output/videos/."""

    def __init__(self):
        super().__init__()
        self.body.setSpacing(10)
        self.body.setContentsMargins(22, 18, 22, 18)

        # En-tête
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
            f"QListWidget {{ background:{theme.SURFACE}; border:none; "
            f"border-radius:8px; padding:4px; }}"
            f"QListWidget::item {{ padding:8px 12px; border-bottom:1px solid {theme.BORDER}; "
            f"color:{theme.TEXT}; }}"
            f"QListWidget::item:last {{ border-bottom:none; }}")
        self._list.setMinimumHeight(140)
        self._list.setMaximumHeight(320)
        self.body.addWidget(self._list)

        self._empty_lbl = QLabel(
            "Aucune vidéo disponible.\nCliquez sur « Générer » pour en créer.")
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setStyleSheet(
            f"color:{theme.TEXT_FAINT}; font-size:13px; padding:24px;")
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
        self._count_lbl.setText(
            f"{len(items)} vidéo{'s' if len(items) > 1 else ''}")

        for item in items:
            size_mb = item.size_bytes / 1_048_576
            caption_short = (item.caption or "")[:60] + (
                "…" if len(item.caption or "") > 60 else "")
            line = QListWidgetItem(
                f"🎬  {item.key}   •   {size_mb:.1f} Mo   •   {caption_short}")
            line.setToolTip(item.caption or "")
            line.setForeground(QColor(theme.TEXT))
            self._list.addItem(line)


class ScheduleView(QWidget):
    def __init__(self):
        super().__init__()
        self._campaign_worker: CampaignWorker | None = None
        self._generate_worker: GenerateWorker | None = None
        self._distribute_worker: DistributeWorker | None = None
        self._schedulers: dict[str, DailyScheduler] = {}
        self._cards: list[ContentTypeCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header fixe ──────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(f"background:{theme.BG};")
        hlay = QVBoxLayout(header)
        hlay.setContentsMargins(28, 24, 28, 10)
        hlay.setSpacing(4)
        hlay.addWidget(widgets.title("Catalogue de contenu"))
        hlay.addWidget(widgets.dim(
            "Générez du contenu, distribuez l'existant, ou lancez les deux en une fois."))

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

        # ── Zone scrollable ───────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(f"QScrollArea {{ background:{theme.BG}; border:none; }}")

        cards_host = QWidget()
        cards_host.setStyleSheet(f"background:{theme.BG};")
        cards_lay = QVBoxLayout(cards_host)
        cards_lay.setContentsMargins(28, 8, 28, 20)
        cards_lay.setSpacing(16)

        for ct in catalog.list_types():
            card = ContentTypeCard(
                ct,
                on_generate=self._run_generate,
                on_distribute=self._run_distribute,
                on_campaign=self._run_campaign,
                on_toggle_schedule=self._toggle_schedule,
            )
            self._cards.append(card)
            cards_lay.addWidget(card)

        # Carte vidéos disponibles
        self._video_list_card = VideoListCard()
        cards_lay.addWidget(self._video_list_card)
        cards_lay.addStretch(1)

        scroll.setWidget(cards_host)
        root.addWidget(scroll, 1)

        # ── Console fixe en bas ───────────────────────────────────────────
        log_wrap = QWidget()
        log_wrap.setStyleSheet(f"background:{theme.BG};")
        lw_lay = QVBoxLayout(log_wrap)
        lw_lay.setContentsMargins(28, 0, 28, 20)

        log_card = widgets.Card()
        log_card.setMaximumHeight(200)
        log_card.body.setContentsMargins(16, 12, 16, 12)
        log_card.body.setSpacing(8)

        log_head = QHBoxLayout()
        log_head.addWidget(widgets.title("Activité", "H2"))
        log_head.addStretch(1)
        clear_btn = QPushButton("Effacer")
        clear_btn.setFixedWidth(70)
        clear_btn.clicked.connect(lambda: self.console.clear())
        log_head.addWidget(clear_btn)
        log_card.body.addLayout(log_head)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(
            f"background:{theme.SURFACE}; border:none; "
            f"font-family:monospace; font-size:12px;")
        log_card.body.addWidget(self.console)
        lw_lay.addWidget(log_card)
        root.addWidget(log_wrap)

    def refresh(self):
        for card in self._cards:
            card.refresh_plan()
        self._video_list_card.refresh()

    # ── Guards & helpers ─────────────────────────────────────────────────

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

    def _stop_all(self):
        if self._campaign_worker:
            self._campaign_worker.stop()
        if self._generate_worker:
            self._generate_worker.stop()
        if self._distribute_worker:
            self._distribute_worker.stop()
        self._log("⏹ Arrêt demandé…")

    # ── Générer ──────────────────────────────────────────────────────────

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
        self._generate_worker.log.connect(lambda m: self._log(f"    {m}"))
        self._generate_worker.finished_result.connect(self._on_generate_done)
        self._generate_worker.start()

    def _on_generate_done(self, ok: bool):
        self._set_running(False)
        self._log("✔ Génération terminée." if ok else "✖ Génération échouée ou interrompue.")
        self.refresh()

    # ── Distribuer ───────────────────────────────────────────────────────

    def _run_distribute(self, content_type: ContentType):
        if self._is_busy():
            self._log("⚠ Une opération est déjà en cours.")
            return
        self._log(f"↑ Distribution « {content_type.label} »…")
        self._set_running(True)
        self._distribute_worker = DistributeWorker(
            content_type_id=content_type.id,
            network_ids=list(content_type.networks) if content_type.networks else None,
        )
        self._distribute_worker.event.connect(self._on_event)
        self._distribute_worker.finished_result.connect(self._on_distribute_done)
        self._distribute_worker.start()

    def _on_distribute_done(self, summary: dict):
        self._set_running(False)
        self.refresh()
        self._log(
            f"✔ Distribution — {summary.get('published', 0)} publiée(s), "
            f"{summary.get('failed', 0)} échec(s), "
            f"{summary.get('skipped', 0)} ignorée(s).")

    # ── Générer & Distribuer ─────────────────────────────────────────────

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
        else:
            self._log(
                f"✔ Campagne — {summary.get('generated', 0)} générée(s), "
                f"{summary.get('published', 0)} publiée(s), "
                f"{summary.get('failed', 0)} échec(s).")

    # ── Événements publisher ──────────────────────────────────────────────

    def _on_event(self, event: str, data: dict):
        msgs = {
            "uploading": lambda d: f"  ↑ {d.get('content','')} → {d.get('account','')} ({d.get('network','')})",
            "success":   lambda d: f"  ✔ {d.get('account','')} : publié.",
            "failed":    lambda d: f"  ✖ {d.get('account','')} : {d.get('error','échec')}",
            "cooldown":  lambda d: f"  ⏳ {d.get('account','')} cooldown ({d.get('remaining',0)}s)…",
            "retry":     lambda d: f"  ↻ {d.get('account','')} retry {d.get('attempt','')} dans {d.get('backoff','')}s",
            "info":      lambda d: f"  ℹ {d.get('message','')}",
            "log":       lambda d: f"    {d.get('message','')}",
        }
        fn = msgs.get(event)
        if fn:
            self._log(fn(data))

    # ── Planification ────────────────────────────────────────────────────

    def _toggle_schedule(self, content_type: ContentType, on: bool, hour: int, minute: int):
        existing = self._schedulers.pop(content_type.id, None)
        if existing:
            existing.stop()
        if not on:
            self._log(f"⏹ Planification « {content_type.label} » désactivée.")
            return

        def job():
            from core import campaign
            campaign.run(content_type, progress=lambda e, d: self._on_event(e, d))

        sched = DailyScheduler(hour, minute, job, on_event=self._on_sched_event)
        sched.start()
        self._schedulers[content_type.id] = sched
        self._log(f"  « {content_type.label} » planifié à {hour:02d}:{minute:02d} chaque jour.")

    def _on_sched_event(self, event: str, data: dict):
        if event == "running":
            self._log("▶ Campagne planifiée démarrée.")
        elif event == "done":
            self._log("✔ Campagne planifiée terminée.")
            self.refresh()
        elif event == "error":
            self._log(f"✖ Campagne planifiée : {data.get('error','')}")
