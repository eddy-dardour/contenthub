"""Vue Catalogue & Publication.

Catalogue modulaire de types de contenu (cf. core.catalog).
Pour chaque type :
  - ses plateformes épinglées et un aperçu (combien de comptes → combien de vidéos)
  - bouton « Générer » : génère les vidéos sans publier
  - bouton « Distribuer » : publie le contenu déjà généré vers les comptes compatibles
  - bouton « Générer & Distribuer » : pipeline complet (campagne)
  - planification quotidienne par type
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTimeEdit,
    QTextEdit, QProgressBar, QGridLayout, QFrame,
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
    f"border-radius:8px; padding:7px 14px; color:{theme.ACCENT}; font-weight:600; }}"
    f"QPushButton:hover {{ background:{theme.ACCENT}; color:#0b0d12; }}"
    f"QPushButton:disabled {{ border-color:{theme.BORDER}; color:{theme.TEXT_FAINT}; background:{theme.SURFACE_2}; }}"
)

_DISTRIBUTE_STYLE = (
    f"QPushButton {{ background:{theme.SURFACE_2}; border:1px solid {theme.OK}; "
    f"border-radius:8px; padding:7px 14px; color:{theme.OK}; font-weight:600; }}"
    f"QPushButton:hover {{ background:{theme.OK}; color:#0b0d12; }}"
    f"QPushButton:disabled {{ border-color:{theme.BORDER}; color:{theme.TEXT_FAINT}; background:{theme.SURFACE_2}; }}"
)

_CAMPAIGN_STYLE = (
    f"QPushButton {{ background:{theme.ACCENT}; border:none; "
    f"border-radius:8px; padding:7px 14px; color:#0b0d12; font-weight:600; }}"
    f"QPushButton:hover {{ background:{theme.ACCENT_HOVER}; }}"
    f"QPushButton:disabled {{ background:{theme.SURFACE_2}; color:{theme.TEXT_FAINT}; }}"
)


class ContentTypeCard(widgets.Card):
    """Carte d'un type de contenu du catalogue."""

    def __init__(self, content_type: ContentType, on_generate, on_distribute, on_campaign,
                 on_toggle_schedule):
        super().__init__()
        self.ct = content_type
        self._on_generate = on_generate
        self._on_distribute = on_distribute
        self._on_campaign = on_campaign
        self._on_toggle_schedule = on_toggle_schedule
        self._scheduled = False

        # En-tête : icône + nom + logos plateformes
        head = QHBoxLayout()
        icon = QLabel(content_type.icon)
        icon.setStyleSheet("font-size:24px;")
        head.addWidget(icon)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(widgets.title(content_type.label, "H2"))
        title_box.addWidget(widgets.dim(content_type.description))
        head.addLayout(title_box, 1)

        # Logos des plateformes épinglées
        logo_box = QHBoxLayout()
        logo_box.setSpacing(6)
        plugins = get_plugins()
        for net_id in content_type.networks:
            p = plugins.get(net_id)
            if not p:
                continue
            logo = QLabel()
            logo.setPixmap(brand.pixmap(p.icon, 22))
            logo.setFixedSize(22, 22)
            logo.setScaledContents(True)
            logo.setToolTip(p.display_name)
            logo_box.addWidget(logo)
        head.addLayout(logo_box)
        self.body.addLayout(head)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{theme.BORDER};")
        self.body.addWidget(sep)

        # Aperçu du plan (comptes → vidéos)
        self.plan_lbl = QLabel()
        self.plan_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px;")
        self.body.addWidget(self.plan_lbl)

        # ── Boutons d'action ─────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.gen_btn = QPushButton("⬇  Générer")
        self.gen_btn.setStyleSheet(_GENERATE_STYLE)
        self.gen_btn.setToolTip("Génère les vidéos dans output/videos/ sans publier.")
        self.gen_btn.clicked.connect(lambda: self._on_generate(self.ct))
        btn_row.addWidget(self.gen_btn)

        self.dist_btn = QPushButton("↑  Distribuer")
        self.dist_btn.setStyleSheet(_DISTRIBUTE_STYLE)
        self.dist_btn.setToolTip("Publie le contenu déjà généré vers les comptes compatibles.")
        self.dist_btn.clicked.connect(lambda: self._on_distribute(self.ct))
        btn_row.addWidget(self.dist_btn)

        self.campaign_btn = QPushButton("⚡  Générer & Distribuer")
        self.campaign_btn.setStyleSheet(_CAMPAIGN_STYLE)
        self.campaign_btn.setToolTip("Pipeline complet : génère puis publie immédiatement.")
        self.campaign_btn.clicked.connect(lambda: self._on_campaign(self.ct))
        btn_row.addWidget(self.campaign_btn)

        btn_row.addStretch(1)
        self.body.addLayout(btn_row)

        # ── Planification ─────────────────────────────────────────────────
        sched_row = QHBoxLayout()
        sched_row.setSpacing(8)
        sched_lbl = QLabel("Planifier chaque jour à")
        sched_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px;")
        sched_row.addWidget(sched_lbl)
        self.sched_time = QTimeEdit(QTime(8, 0))
        self.sched_time.setDisplayFormat("HH:mm")
        self.sched_time.setFixedWidth(70)
        sched_row.addWidget(self.sched_time)
        self.sched_btn = QPushButton("Planifier")
        self.sched_btn.setFixedWidth(100)
        self.sched_btn.clicked.connect(self._toggle_schedule)
        sched_row.addWidget(self.sched_btn)
        sched_row.addStretch(1)
        self.body.addLayout(sched_row)

        self.refresh()

    def refresh(self):
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
            "  ·  ".join(parts) + f"   →  {total} vidéo(s) à générer")

    def set_running(self, label: str | None):
        """label=None → idle. label=str → en cours."""
        running = label is not None
        self.gen_btn.setEnabled(not running)
        self.dist_btn.setEnabled(not running)
        self.campaign_btn.setEnabled(not running)
        if running:
            self.gen_btn.setText(label if "génér" in label.lower() else "⬇  Générer")
            self.dist_btn.setText(label if "distribu" in label.lower() else "↑  Distribuer")
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

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        root.addWidget(widgets.title("Catalogue de contenu"))
        root.addWidget(widgets.dim(
            "Générez du contenu, distribuez l'existant, ou lancez les deux à la suite. "
            "Le type de contenu assigné à chaque compte détermine ce qu'il reçoit."))

        # Grille des types de contenu
        grid_host = QWidget()
        self.grid = QGridLayout(grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(14)
        self._build_cards()
        root.addWidget(grid_host)

        # Progression
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # Bouton Stop
        stop_row = QHBoxLayout()
        self.stop_btn = QPushButton("⏹  Arrêter")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._stop_all)
        stop_row.addWidget(self.stop_btn)
        stop_row.addStretch(1)
        root.addLayout(stop_row)

        # Console live
        log_card = widgets.Card()
        log_card.body.addWidget(widgets.title("Activité", "H2"))
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(160)
        log_card.body.addWidget(self.console)

        clear_btn = QPushButton("Effacer")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self.console.clear)
        log_card.body.addWidget(clear_btn, alignment=Qt.AlignRight)
        root.addWidget(log_card, 1)

    def _build_cards(self):
        for i, ct in enumerate(catalog.list_types()):
            card = ContentTypeCard(
                ct,
                on_generate=self._run_generate,
                on_distribute=self._run_distribute,
                on_campaign=self._run_campaign,
                on_toggle_schedule=self._toggle_schedule,
            )
            self._cards.append(card)
            self.grid.addWidget(card, i // 2, i % 2)

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

        # Calcule le nombre de vidéos à générer (1 par compte lié+actif)
        from core.campaign import plan as make_plan
        preview = make_plan(content_type)
        n = sum(preview.values()) if preview else 1

        self._log(f"⬇ Génération de {n} vidéo(s) « {content_type.label} »…")
        self._set_running("En cours…")

        self._generate_worker = GenerateWorker(n, content_type.gen_type)
        self._generate_worker.log.connect(
            lambda m: self._log(f"    {m}"))
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
        self._log(f"🕒 « {content_type.label} » planifié chaque jour à {hour:02d}:{minute:02d}.")

    def _on_sched_event(self, event: str, data: dict):
        if event == "running":
            self._log("▶ Campagne planifiée démarrée.")
        elif event == "done":
            self._log("✔ Campagne planifiée terminée.")
            self.refresh()
        elif event == "error":
            self._log(f"✖ Campagne planifiée : {data.get('error','')}")
