#!/usr/bin/env python3
"""Routine matinale ContentHub.

Tourne en tâche de fond Windows. Chaque matin à l'heure configurée :
  1. Affiche une fenêtre d'input utilisateur (override params, type de contenu…)
  2. Attend MAX_WAIT_MINUTES. Si l'utilisateur valide → lance avec ses params.
  3. Si timeout → lance avec les paramètres par défaut.

Lancement automatique au démarrage Windows :
  Raccourci dans %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
  pointant vers :  pythonw morning_routine.py  (ou l'exe empaqueté)

Usage :
    python morning_routine.py              # démarre le daemon
    python morning_routine.py --now        # lance la routine immédiatement (test)
    python morning_routine.py --config     # ouvre juste la fenêtre de config
"""

from __future__ import annotations

import sys
import json
import logging
import argparse
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Permet les imports core/ui depuis ce dossier
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent / "data" / "morning_routine.log",
            encoding="utf-8", errors="replace"),
    ]
)
logger = logging.getLogger(__name__)

# ── Constantes par défaut ────────────────────────────────────────────────────
DEFAULT_HOUR = 7          # heure de déclenchement (heure locale)
DEFAULT_MINUTE = 0
MAX_WAIT_MINUTES = 60     # délai avant lancement auto si pas de réponse
CONFIG_FILE = Path(__file__).parent / "data" / "routine_config.json"


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "hour": DEFAULT_HOUR,
        "minute": DEFAULT_MINUTE,
        "wait_minutes": MAX_WAIT_MINUTES,
        "default_content_type": "tts_minecraft",
        "enabled": True,
    }


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                           encoding="utf-8")


# ── Fenêtre d'input matinal (PySide6) ───────────────────────────────────────

def show_morning_prompt(cfg: dict, timeout_seconds: int) -> dict | None:
    """Affiche la fenêtre d'override. Retourne les params choisis ou None si timeout."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
        QPushButton, QComboBox, QCheckBox, QProgressBar,
    )
    from ui import theme
    from ui import widgets as w
    from core.catalog import list_types

    result = {"action": "default"}
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(theme.qss())

    dlg = QDialog()
    dlg.setWindowTitle("ContentHub — Routine matinale")
    dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
    dlg.setMinimumWidth(480)
    dlg.setStyleSheet(theme.qss())

    root = QVBoxLayout(dlg)
    root.setContentsMargins(24, 20, 24, 20)
    root.setSpacing(14)

    root.addWidget(w.title("Routine matinale"))
    root.addWidget(w.dim(
        f"La campagne démarrera automatiquement dans {timeout_seconds // 60} minute(s) "
        "avec les paramètres par défaut si vous ne répondez pas."))

    # Sélecteur de type de contenu
    type_row = QHBoxLayout()
    type_row.addWidget(QLabel("Type de contenu :"))
    type_combo = QComboBox()
    type_combo.setMinimumWidth(220)
    types = list(list_types())
    default_idx = 0
    for i, ct in enumerate(types):
        type_combo.addItem(f"{ct.icon}  {ct.label}", ct.id)
        if ct.id == cfg.get("default_content_type"):
            default_idx = i
    type_combo.setCurrentIndex(default_idx)
    type_row.addWidget(type_combo, 1)
    root.addLayout(type_row)

    # Option : générer seulement / distribuer seulement / les deux
    mode_row = QHBoxLayout()
    mode_row.addWidget(QLabel("Mode :"))
    mode_combo = QComboBox()
    mode_combo.addItem("⚡  Générer & Distribuer", "campaign")
    mode_combo.addItem("⬇  Générer seulement", "generate")
    mode_combo.addItem("↑  Distribuer seulement", "distribute")
    mode_row.addWidget(mode_combo, 1)
    root.addLayout(mode_row)

    # Option : passer (ne rien faire aujourd'hui)
    skip_chk = QCheckBox("Passer aujourd'hui (ne rien lancer)")
    root.addWidget(skip_chk)

    # Barre de progression du timeout
    bar_lbl = QLabel(f"Démarrage auto dans {timeout_seconds}s")
    bar_lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px;")
    root.addWidget(bar_lbl)
    bar = QProgressBar()
    bar.setRange(0, timeout_seconds)
    bar.setValue(timeout_seconds)
    root.addWidget(bar)

    # Boutons
    btn_row = QHBoxLayout()
    launch_btn = QPushButton("▶  Lancer maintenant")
    launch_btn.setObjectName("Primary")
    skip_now_btn = QPushButton("Passer")
    btn_row.addWidget(launch_btn)
    btn_row.addStretch(1)
    btn_row.addWidget(skip_now_btn)
    root.addLayout(btn_row)

    # Timer countdown
    remaining = [timeout_seconds]

    def tick():
        remaining[0] -= 1
        bar.setValue(remaining[0])
        bar_lbl.setText(f"Démarrage auto dans {remaining[0]}s")
        if remaining[0] <= 0:
            result["action"] = "default"
            result["content_type"] = type_combo.currentData()
            result["mode"] = mode_combo.currentData()
            dlg.accept()

    timer = QTimer(dlg)
    timer.timeout.connect(tick)
    timer.start(1000)

    def on_launch():
        timer.stop()
        result["action"] = "user"
        result["content_type"] = type_combo.currentData()
        result["mode"] = mode_combo.currentData()
        result["skip"] = skip_chk.isChecked()
        dlg.accept()

    def on_skip():
        timer.stop()
        result["action"] = "skip"
        dlg.accept()

    launch_btn.clicked.connect(on_launch)
    skip_now_btn.clicked.connect(on_skip)

    dlg.exec()
    return result


# ── Exécution de la campagne ─────────────────────────────────────────────────

def run_campaign(params: dict) -> None:
    if params.get("action") == "skip" or params.get("skip"):
        logger.info("Routine : skippée par l'utilisateur.")
        return

    content_type_id = params.get("content_type", "tts_minecraft")
    mode = params.get("mode", "campaign")

    logger.info("Routine : démarrage — type=%s mode=%s", content_type_id, mode)

    from core.catalog import get_type
    from core.campaign import run as campaign_run
    from core.publisher import Publisher
    from core import generator as gen_mod, content as content_mod
    from core.campaign import eligible_accounts

    ct = get_type(content_type_id)
    if not ct:
        logger.error("Type de contenu introuvable : %s", content_type_id)
        return

    def log(ev, data):
        msg = data.get("message") or data.get("error") or str(data)
        logger.info("[%s] %s", ev, msg)

    if mode == "campaign":
        campaign_run(ct, progress=log)
    elif mode == "generate":
        by_net = eligible_accounts(ct)
        n = sum(len(v) for v in by_net.values()) or 1
        gen_mod.generate(n, ct.gen_type, on_log=lambda m: logger.info(m))
    elif mode == "distribute":
        publisher = Publisher()
        publisher.run(
            network_ids=list(ct.networks) or None,
            content_type_id=ct.id,
            progress=log)

    logger.info("Routine : terminée.")


# ── Daemon : attend l'heure configurée chaque jour ──────────────────────────

def daemon_loop(cfg: dict) -> None:
    logger.info("Daemon ContentHub démarré. Heure de déclenchement : %02d:%02d",
                cfg["hour"], cfg["minute"])
    while True:
        now = datetime.now()
        target = now.replace(hour=cfg["hour"], minute=cfg["minute"],
                             second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        logger.info("Prochain déclenchement : %s (dans %.0fs)", target, wait)

        # Attend jusqu'à l'heure cible (interruptible par 30s tranches)
        import time
        deadline = target.timestamp()
        while time.time() < deadline:
            time.sleep(min(30, deadline - time.time()))

        if not load_config().get("enabled", True):
            logger.info("Routine désactivée dans la config — skippée.")
            continue

        cfg = load_config()
        wait_s = cfg.get("wait_minutes", MAX_WAIT_MINUTES) * 60
        params = show_morning_prompt(cfg, timeout_seconds=wait_s)
        run_campaign(params or {"action": "default",
                                "content_type": cfg["default_content_type"],
                                "mode": "campaign"})


# ── Config UI ────────────────────────────────────────────────────────────────

def show_config_ui(cfg: dict) -> None:
    from PySide6.QtWidgets import (
        QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
        QPushButton, QSpinBox, QComboBox, QCheckBox, QDialogButtonBox,
    )
    from ui import theme
    from ui import widgets as w
    from core.catalog import list_types

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(theme.qss())

    dlg = QDialog()
    dlg.setWindowTitle("Configuration de la routine matinale")
    dlg.setMinimumWidth(420)
    dlg.setStyleSheet(theme.qss())

    root = QVBoxLayout(dlg)
    root.setContentsMargins(24, 20, 24, 20)
    root.setSpacing(14)
    root.addWidget(w.title("Routine matinale"))

    # Heure
    time_row = QHBoxLayout()
    time_row.addWidget(QLabel("Heure de déclenchement :"))
    hour_spin = QSpinBox(); hour_spin.setRange(0, 23); hour_spin.setValue(cfg["hour"])
    hour_spin.setSuffix("h")
    min_spin = QSpinBox(); min_spin.setRange(0, 59); min_spin.setValue(cfg["minute"])
    min_spin.setSuffix("min")
    time_row.addWidget(hour_spin)
    time_row.addWidget(min_spin)
    time_row.addStretch(1)
    root.addLayout(time_row)

    # Délai d'attente
    wait_row = QHBoxLayout()
    wait_row.addWidget(QLabel("Délai avant lancement auto :"))
    wait_spin = QSpinBox(); wait_spin.setRange(1, 120)
    wait_spin.setValue(cfg.get("wait_minutes", 60)); wait_spin.setSuffix(" min")
    wait_row.addWidget(wait_spin)
    wait_row.addStretch(1)
    root.addLayout(wait_row)

    # Type par défaut
    type_row = QHBoxLayout()
    type_row.addWidget(QLabel("Type par défaut :"))
    type_combo = QComboBox(); type_combo.setMinimumWidth(200)
    types = list(list_types())
    for i, ct in enumerate(types):
        type_combo.addItem(f"{ct.icon}  {ct.label}", ct.id)
        if ct.id == cfg.get("default_content_type"):
            type_combo.setCurrentIndex(i)
    type_row.addWidget(type_combo, 1)
    root.addLayout(type_row)

    # Activé
    enabled_chk = QCheckBox("Routine activée")
    enabled_chk.setChecked(cfg.get("enabled", True))
    root.addWidget(enabled_chk)

    btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    root.addWidget(btns)

    if dlg.exec():
        cfg["hour"] = hour_spin.value()
        cfg["minute"] = min_spin.value()
        cfg["wait_minutes"] = wait_spin.value()
        cfg["default_content_type"] = type_combo.currentData()
        cfg["enabled"] = enabled_chk.isChecked()
        save_config(cfg)
        logger.info("Config sauvegardée : %s", cfg)


# ── Entrée ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Routine matinale ContentHub")
    parser.add_argument("--now", action="store_true",
                        help="Lance la routine immédiatement (test)")
    parser.add_argument("--config", action="store_true",
                        help="Ouvre la fenêtre de configuration")
    args = parser.parse_args()

    cfg = load_config()
    Path("data").mkdir(exist_ok=True)

    if args.config:
        show_config_ui(cfg)
        return

    if args.now:
        wait_s = cfg.get("wait_minutes", MAX_WAIT_MINUTES) * 60
        params = show_morning_prompt(cfg, timeout_seconds=wait_s)
        run_campaign(params or {"action": "default",
                                "content_type": cfg["default_content_type"],
                                "mode": "campaign"})
        return

    # Daemon normal
    daemon_loop(cfg)


if __name__ == "__main__":
    main()
