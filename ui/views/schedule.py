"""Vue Catalogue & Publication.

Catalogue modulaire de types de contenu (cf. core.catalog).
Pour chaque type :
  - ses plateformes épinglées et un aperçu (combien de comptes → combien de vidéos)
  - bouton « Générer » : génère les vidéos sans publier
  - bouton « Distribuer » : publie le contenu déjà généré vers les comptes compatibles
  - bouton « Générer & Distribuer » : pipeline complet (campagne)
  - planification quotidienne par type

La vue est entièrement scrollable : les cartes grandissent librement.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTimeEdit,
    QTextEdit, QProgressBar, QFrame, QScrollArea, QSizePolicy,
)

from core import catalog
from core.catalog import ContentType
from core.campaign import plan
from core.registry import get_plugins
from core.scheduler import DailyScheduler
from ..workers import CampaignWorker, GenerateWorker, DistributeWorker
from .. import theme, widgets, brand


_GENERATE_STYLE = (
    f"QPushButton {{ background:{theme.SURFACE_2}; border:1px solid {theme.ACCENT}; "
    f"border-radius:8px; padding:9px 18px; color:{theme.ACCENT}; font-weight:600; font-size:13px; }}"
    f"QPushButton:hover {{ background:{theme.ACCENT}; color:#0b0d12; }}"
    f"QPushButton:disabled {{ border-color:{theme.BORDER}; color:{theme.TEXT_FAINT}; background:{theme.SURFACE_2}; }}"
)

_DISTRIBUTE_STYLE = (
    f"QPushButton {{ background:{theme.SURFACE_2}; border:1px solid {theme.OK}; "
    f"border-radius:8px; padding:9px 18px; color:{theme.OK}; font-weight:600; font-size:13px; }}"
    f"QPushButton:hover {{ background:{theme.OK}; color:#0b0d12; }}"
    f"QPushButton:disabled {{ border-color:{theme.BORDER}; color:{theme.TEXT_FAINT}; background:{theme.SURFACE_2}; }}"
)

_CAMPAIGN_STYLE = (
    f"QPushButton {{ background:{theme.ACCENT}; border:none; "
    f"border-radius:8px; padding:9px 18px; color:#0b0d12; font-weight:700; font-size:13px; }}"
    f"QPushButton:hover {{ background:{theme.ACCENT_HOVER}; }}"
    f"QPushButton:disabled {{ background:{theme.SURFACE_2}; color:{theme.TEXT_FAINT}; }}"
)

_COMING_STYLE = (
    f"QPushButton {{ background:{theme.SURFACE_2}; border:1px solid {theme.BORDER}; "
    f"border-radius:8px; padding:9px 18px; color:{theme.TEXT_FAINT}; font-size:13px; }}"
    f"QPushButton:disabled {{ color:{theme.TEXT_FAINT}; }}"
)


class ContentTypeCard(widgets.Card):
    """Carte d'un type de contenu du catalogue — pleine largeur, bien aérée."""

    def __init__(self, content_type: ContentType, on_generate, on_distribute, on_campaign,
                 on_toggle_schedule):
        super().__init__()
        self.ct = content_type
        self._on_generate = on_generate
        self._on_distribute = on_distribute
        self._on_campaign = on_campaign
        self._on_toggle_schedule = on_toggle_schedule
        self._scheduled = False

        # Indique si ce type est « bientôt disponible » (générateur non implémenté)
        self._coming_soon = content_type.generator_kind not in ("manual",)

        self.body.setSpacing(14)
        self.body.setContentsMargins(20, 18, 20, 18)

        # ── En-tête ─────────────────────────────────────────────────────
        head = QHBoxLayout()
        head.setSpacing(14)

        icon_lbl = QLabel(content_type.icon)
        icon_lbl.setStyleSheet("font-size:32px; padding:4px;")
        icon_lbl.setFixedWidth(48)
        head.addWidget(icon_lbl)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title_lbl = widgets.title(content_type.label, "H2")
        title_lbl.setStyleSheet(f"font-size:17px; font-weight:700;")
        title_box.addWidget(title_lbl)
        desc_lbl = widgets.dim(content_type.description)
        desc_lbl.setWordWrap(True)
        title_box.addWidget(desc_lbl)
        head.addLayout(title_box, 1)

        if self._coming_soon:
            badge = QLabel("Bientôt")
            badge.setStyleSheet(
                f"background:{theme.WARN}22; color:{theme.WARN}; border:1px solid {theme.WARN}; "
                f"border-radius:6px; padding:3px 10px; font-size:11px; font-weight:600;")
            head.addWidget(badge, alignment=Qt.AlignTop)

        # Logos des plateformes épinglées
        logo_box = QHBoxLayout()
        logo_box.setSpacing(8)
        plugins = get_plugins()
        for net_id in content_type.networks:
            p = plugins.get(net_id)
            if not p:
                continue
            logo = QLabel()
            logo.setPixmap(brand.pixmap(p.icon, 26))
            logo.setFixedSize(26, 26)
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

        if self._coming_soon:
            placeholder = QPushButton("Génération bientôt disponible")
            placeholder.setStyleSheet(_COMING_STYLE)
            placeholder.setEnabled(False)
            btn_row.addWidget(placeholder)
            btn_row.addStretch(1)
            self.gen_btn = self.dist_btn = self.campaign_btn = None
        else:
            self.gen_btn = QPushButton("⬇  Générer")
            self.gen_btn.setStyleSheet(_GENERATE_STYLE)
            self.gen_btn.setMinimumWidth(140)
            self.gen_btn.setToolTip("Génère les vidéos dans output/videos/ sans publier.")
            self.gen_btn.clicked.connect(lambda: self._on_generate(self.ct))
            btn_row.addWidget(self.gen_btn)

            self.dist_btn = QPushButton("↑  Distribuer")
            self.dist_btn.setStyleSheet(_DISTRIBUTE_STYLE)
            self.dist_btn.setMinimumWidth(140)
            self.dist_btn.setToolTip("Publie le contenu déjà généré vers les comptes compatibles.")
            self.dist_btn.clicked.connect(lambda: self._on_distribute(self.ct))
            btn_row.addWidget(self.dist_btn)

            self.campaign_btn = QPushButton("⚡  Générer & Distribuer")
            self.campaign_btn.setStyleSheet(_CAMPAIGN_STYLE)
            self.campaign_btn.setMinimumWidth(190)
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
        if self._coming_soon:
            self.sched_btn.setEnabled(False)
        else:
            self.sched_btn.clicked.connect(self._toggle_schedule)
        sched_row.addWidget(self.sched_btn)
        sched_row.addStretch(1)
        self.body.addLayout(sched_row)

        self.refresh()

    def refresh(self):
        if self._coming_soon:
            self.plan_lbl.setText("Ce type de contenu sera disponible dans une prochaine mise à jour.")
            return
        preview = plan(self.ct)
        if not preview:
            self.plan_lbl.setText("⚠  Aucun compte lié+actif sur les plateformes épinglées.")
            self.gen_btn.setEnabled(True)
            self.dist_btn.setEnabled(False)
            self.campaign_btn.setEnabled(False)
            return
        self.gen_btn.setEnabled(True)
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

    def set_running(self, label: str | None):
        if self._coming_soon or not self.gen_btn:
            return
        running = label is not None
        self.gen_btn.setEnabled(not running)
        self.dist_btn.setEnabled(not running)
        self.campaign_btn.setEnabled(not running)
        if running:
            self.campaign_btn.setText("En cours…")
        else:
            self.gen_btn.setText("⬇  Générer")
            self.dist_btn.setText("↑  Distribuer")
            self.campaign_btn.setText("⚡  Générer & Distribuer")

    def _toggle_schedule(self):
        self._scheduled = not self._scheduled
        t = self.sched_time.time()
        self._on_toggle_schedule(self.ct, self._scheduled, t.hour(), t.minute())
        self.sched_btn.setText("Annuler" if self._scheduled else "Planifier")
        self.sched_btn.setStyleSheet(
            f"QPushButton {{ color:{theme.ERR}; }}" if self._scheduled else "")


class ScheduleView(QWidget):
    def __init__(self):
        super().__init__()
        self._campaign_worker: CampaignWorker | None = None
        self._generate_worker: GenerateWorker | None = None
        self._distribute_worker: DistributeWorker | None = None
        self._schedulers: dict[str, DailyScheduler] = {}
        self._cards: list[ContentTypeCard] = []

        # Layout racine : header fixe + zone scrollable + console fixe en bas
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(f"background:{theme.BG};")
        hlay = QVBoxLayout(header)
        hlay.setContentsMargins(28, 24, 28, 12)
        hlay.setSpacing(4)
        hlay.addWidget(widgets.title("Catalogue de contenu"))
        hlay.addWidget(widgets.dim(
            "Générez du contenu, distribuez l'existant, ou lancez les deux à la suite. "
            "Faites défiler pour voir tous les types disponibles."))

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setFixedHeight(6)
        ctrl_row.addWidget(self.progress, 1)
        self.stop_btn = QPushButton("⏹  Arrêter")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setVisible(False)
        self.stop_btn.setFixedWidth(110)
        self.stop_btn.clicked.connect(self._stop_all)
        ctrl_row.addWidget(self.stop_btn)
        hlay.addLayout(ctrl_row)
        root.addWidget(header)

        # ── Zone scrollable : cartes en colonne ──────────────────────────
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

        self._build_cards(cards_lay)
        cards_lay.addStretch(1)
        scroll.setWidget(cards_host)
        root.addWidget(scroll, 1)

        # ── Console live (fixe en bas) ───────────────────────────────────
        log_card = widgets.Card()
        log_card.setMaximumHeight(220)
        log_card.body.setContentsMargins(16, 12, 16, 12)
        log_card.body.setSpacing(8)

        log_head = QHBoxLayout()
        log_head.addWidget(widgets.title("Activité", "H2"))
        log_head.addStretch(1)
        clear_btn = QPushButton("Effacer")
        clear_btn.setFixedWidth(72)
        clear_btn.clicked.connect(lambda: self.console.clear())
        log_head.addWidget(clear_btn)
        log_card.body.addLayout(log_head)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet(
            f"background:{theme.SURFACE}; border:none; font-family:monospace; font-size:12px;")
        log_card.body.addWidget(self.console)

        console_wrapper = QWidget()
        console_wrapper.setStyleSheet(f"background:{theme.BG};")
        cw_lay = QVBoxLayout(console_wrapper)
        cw_lay.setContentsMargins(28, 0, 28, 20)
        cw_lay.addWidget(log_card)
        root.addWidget(console_wrapper)

    def _build_cards(self, layout: QVBoxLayout):
        for ct in catalog.list_types():
            card = ContentTypeCard(
                ct,
                on_generate=self._run_generate,
                on_distribute=self._run_distribute,
                on_campaign=self._run_campaign,
                on_toggle_schedule=self._toggle_schedule,
            )
            self._cards.append(card)
            layout.addWidget(card)

    def refresh(self):
        for card in self._cards:
            card.refresh()

    # ── Logique ─────────────────────────────────────────────────────────

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

    def _set_running(self, label: str | None):
        running = label is not None
        for c in self._cards:
            c.set_running(label)
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
        n = sum(preview.values()) if preview else 1
        self._log(f"⬇ Génération de {n} vidéo(s) « {content_type.label} »…")
        self._set_running("En cours…")
        self._generate_worker = GenerateWorker(n, content_type.gen_type)
        self._generate_worker.log.connect(lambda m: self._log(f"    {m}"))
        self._generate_worker.finished_result.connect(self._on_generate_done)
        self._generate_worker.start()

    def _on_generate_done(self, ok: bool):
        self._set_running(None)
        if ok:
            self._log("✔ Génération terminée. Contenu prêt dans output/videos/.")
        else:
            self._log("✖ Génération échouée ou interrompue.")
        for c in self._cards:
            c.refresh()

    # ── Distribuer ───────────────────────────────────────────────────────

    def _run_distribute(self, content_type: ContentType):
        if self._is_busy():
            self._log("⚠ Une opération est déjà en cours.")
            return
        self._log(f"↑ Distribution du contenu « {content_type.label} »…")
        self._set_running("En cours…")
        self._distribute_worker = DistributeWorker(
            content_type_id=content_type.id,
            network_ids=list(content_type.networks) if content_type.networks else None,
        )
        self._distribute_worker.event.connect(self._on_event)
        self._distribute_worker.finished_result.connect(self._on_distribute_done)
        self._distribute_worker.start()

    def _on_distribute_done(self, summary: dict):
        self._set_running(None)
        for c in self._cards:
            c.refresh()
        self._log(
            f"✔ Distribution terminée — "
            f"{summary.get('published', 0)} publiée(s), "
            f"{summary.get('failed', 0)} échec(s), "
            f"{summary.get('skipped', 0)} ignorée(s).")

    # ── Générer & Distribuer (campagne) ──────────────────────────────────

    def _run_campaign(self, content_type: ContentType):
        if self._is_busy():
            self._log("⚠ Une opération est déjà en cours.")
            return
        self._log(f"⚡ Campagne « {content_type.label} » : génération + distribution…")
        self._set_running("En cours…")
        self._campaign_worker = CampaignWorker(content_type)
        self._campaign_worker.event.connect(self._on_event)
        self._campaign_worker.finished_result.connect(self._on_campaign_done)
        self._campaign_worker.start()

    def _on_campaign_done(self, summary: dict):
        self._set_running(None)
        for c in self._cards:
            c.refresh()
        if summary.get("no_accounts"):
            self._log("⚠ Aucun compte lié+actif sur les plateformes ciblées.")
        else:
            self._log(
                f"✔ Campagne terminée — {summary.get('generated', 0)} générée(s), "
                f"{summary.get('published', 0)} publiée(s), "
                f"{summary.get('failed', 0)} échec(s), "
                f"{summary.get('skipped', 0)} ignorée(s).")

    # ── Événements publisher ──────────────────────────────────────────────

    def _on_event(self, event: str, data: dict):
        msgs = {
            "uploading": lambda d: f"  ↑ {d.get('content','')} → {d.get('account','')} ({d.get('network','')})",
            "success":   lambda d: f"  ✔ {d.get('account','')} : publié.",
            "failed":    lambda d: f"  ✖ {d.get('account','')} : {d.get('error','échec')}",
            "cooldown":  lambda d: f"  ⏳ {d.get('account','')} en cooldown ({d.get('remaining',0)}s)…",
            "retry":     lambda d: f"  ↻ {d.get('account','')} retry {d.get('attempt','')} dans {d.get('backoff','')}s",
            "info":      lambda d: f"  ℹ {d.get('message','')}",
            "log":       lambda d: f"    {d.get('message','')}",
        }
        fn = msgs.get(event)
        if fn:
            self._log(fn(data))

    # ── Planification par type ────────────────────────────────────────────

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
        self._log(f"  « {content_type.label} » planifie chaque jour a {hour:02d}:{minute:02d}.")

    def _on_sched_event(self, event: str, data: dict):
        if event == "running":
            self._log("▶ Campagne planifiée démarrée.")
        elif event == "done":
            self._log("✔ Campagne planifiée terminée.")
            self.refresh()
        elif event == "error":
            self._log(f"✖ Campagne planifiée : {data.get('error','')}")
