"""État de progression partagé entre processus (app UI ⇄ routine cloud).

La routine (morning_routine.py) tourne dans un SOUS-PROCESSUS distinct de l'app :
son LogBus en mémoire est invisible pour l'UI. Pour synchroniser la barre de
progression, l'état est sérialisé dans un petit fichier JSON que l'app polle.

Écriture atomique (fichier temporaire + os.replace) pour éviter qu'un lecteur
ne tombe sur un JSON tronqué.

Schéma du fichier :
    {
      "active": bool,            # une opération est en cours
      "label": str,              # étape courante lisible
      "value": int,              # avancement (0..maximum)
      "maximum": int,            # nombre total d'étapes
      "status": "running"|"done"|"error"|"idle",
      "summary": str,            # récap final
      "needs_reauth": [str],     # noms de comptes à ré-authentifier
      "updated_at": iso8601,
      "run_id": str,             # identifiant de l'exécution courante
    }
"""

from __future__ import annotations

import os
import json
import time
import logging
from pathlib import Path

from .paths import app_data_dir

logger = logging.getLogger(__name__)

_FILE = app_data_dir() / "progress_state.json"

# Au-delà de ce délai sans mise à jour, un état "running" est considéré périmé
# (le process a probablement crashé) et l'UI cesse de le suivre.
# 900s (15 min) : la génération vidéo (Whisper + ffmpeg) peut bloquer longtemps
# entre deux callbacks ; 120s était trop court et causait un "terminé" fantôme.
STALE_AFTER_S = 900


def _path() -> Path:
    return app_data_dir() / "progress_state.json"


def _write(state: dict) -> None:
    state["updated_at"] = time.time()
    p = _path()
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        logger.warning("progress_state write échoué : %s", e)


def read() -> dict:
    """Lit l'état courant. Retourne un état idle si absent/illisible/périmé."""
    p = _path()
    if not p.exists():
        return _idle()
    try:
        state = json.loads(p.read_bytes().decode("utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return _idle()
    # Périmé : un "running" jamais finalisé (crash) ne doit pas bloquer l'UI.
    if state.get("status") == "running":
        ts = state.get("updated_at", 0)
        if time.time() - ts > STALE_AFTER_S:
            state["active"] = False
            state["status"] = "idle"
    return state


def _idle() -> dict:
    return {"active": False, "label": "", "value": 0, "maximum": 1,
            "status": "idle", "summary": "", "needs_reauth": [],
            "updated_at": time.time(), "run_id": ""}


def start(label: str = "Démarrage…", run_id: str | None = None) -> None:
    _write({"active": True, "label": label, "value": 0, "maximum": 1,
            "status": "running", "summary": "", "needs_reauth": [],
            "run_id": run_id or str(int(time.time()))})


def step(label: str, index: int, total: int) -> None:
    cur = read()
    cur.update({"active": True, "status": "running",
                "label": label, "value": index, "maximum": max(total, 1)})
    _write(cur)


def progress(value: int, maximum: int) -> None:
    cur = read()
    cur.update({"active": True, "status": "running",
                "value": value, "maximum": max(maximum, 1)})
    _write(cur)


def add_reauth(account_name: str) -> None:
    cur = read()
    lst = cur.get("needs_reauth", [])
    if account_name not in lst:
        lst.append(account_name)
    cur["needs_reauth"] = lst
    _write(cur)


def finish(summary: str = "", status: str = "done") -> None:
    cur = read()
    cur.update({"active": False, "status": status, "summary": summary,
                "value": cur.get("maximum", 1)})
    _write(cur)


def heartbeat() -> None:
    """Rafraîchit updated_at sans modifier l'état — empêche le timer stale."""
    cur = read()
    if cur.get("status") == "running":
        _write(cur)


def clear() -> None:
    _write(_idle())
