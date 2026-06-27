#!/usr/bin/env python3
"""Serveur FastAPI pour ContentHub — orchestration via HTTP.

Routes:
  GET  /health    → ping
  GET  /config    → lire routine_config.json
  POST /config    → écrire routine_config.json
  POST /run       → lancer morning_routine.py --now
  GET  /progress  → état subprocess en cours
  GET  /status    → derniers jobs DB
  GET  /logs      → historique runs cloud
  POST /log_run   → logguer un run
  POST /reauth    → relancer OAuth d'un compte
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Setup paths
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Point to the same data dir as the frozen exe so DB/config are shared.
if not os.environ.get("CONTENTHUB_DATA_DIR") and not getattr(sys, "frozen", False):
    _appdata = os.environ.get("APPDATA") or str(Path.home())
    os.environ["CONTENTHUB_DATA_DIR"] = str(Path(_appdata) / "ContentHub")

from core.paths import app_data_dir, data_dir

_DATA_DIR = data_dir()
CONFIG_FILE = app_data_dir() / "routine_config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_DATA_DIR / "api_server.log", encoding="utf-8", errors="replace"),
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="ContentHub API", version="1.0.0")

# ── Config Management ────────────────────────────────────────────────────────


def load_config() -> dict:
    """Load routine_config.json, creating and saving defaults on first run."""
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_bytes().decode("utf-8-sig"))
            # Ensure api_key exists (migration for pre-API installs)
            if not cfg.get("api_key"):
                cfg["api_key"] = secrets.token_urlsafe(32)
                save_config(cfg)
            return cfg
        except Exception as e:
            logger.error(f"Error loading config: {e}")

    # First run: create default config and save it immediately
    cfg = {
        "wait_minutes": 0,
        "default_content_type": "tts_drama",
        "enabled": True,
        "api_key": secrets.token_urlsafe(32),
        "auto_start_api": False,
        "ngrok_url": "",
    }
    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    """Save config to routine_config.json."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Auth Middleware ─────────────────────────────────────────────────────────


def verify_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    """Verify X-API-Key header against config."""
    expected_key = load_config().get("api_key", "")
    if not x_api_key or x_api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key


# ── Pydantic Models ──────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    mode: str = "campaign"  # campaign | generate | distribute
    content_type: Optional[str] = None


class ConfigUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    default_content_type: Optional[str] = None
    wait_minutes: Optional[int] = None
    ngrok_url: Optional[str] = None


# ── Routes ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    """Simple health check."""
    return {"ok": True, "timestamp": datetime.now().isoformat()}


@app.get("/config")
async def get_config(x_api_key: str = Header(None)):
    """Get current routine_config.json."""
    verify_api_key(x_api_key)
    cfg = load_config()
    # Don't expose API key in response
    cfg_safe = {k: v for k, v in cfg.items() if k != "api_key"}
    return cfg_safe


@app.post("/config")
async def update_config(
    update: ConfigUpdateRequest,
    x_api_key: str = Header(None),
):
    """Update routine_config.json."""
    verify_api_key(x_api_key)
    cfg = load_config()

    if update.enabled is not None:
        cfg["enabled"] = update.enabled
    if update.default_content_type is not None:
        cfg["default_content_type"] = update.default_content_type
    if update.wait_minutes is not None:
        cfg["wait_minutes"] = update.wait_minutes
    if update.ngrok_url is not None:
        cfg["ngrok_url"] = update.ngrok_url

    save_config(cfg)
    logger.info(f"Config updated: {update}")

    cfg_safe = {k: v for k, v in cfg.items() if k != "api_key"}
    return {"status": "updated", "config": cfg_safe}



@app.post("/reauth")
async def reauth_account(request: Request, x_api_key: str = Header(None)):
    """Relance le flux OAuth (ouvre le navigateur localement) pour un compte.

    Body: {"account": "<nom>"} ou {"account_id": <id>}. Quand un token est
    expiré/revoqué (401), appeler cet endpoint rouvre la page de consentement
    sur la machine locale et persiste les nouveaux tokens.
    """
    verify_api_key(x_api_key)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    from core.accounts import AccountRepository
    from core.registry import get_plugin

    accounts = AccountRepository()
    acc = None
    if body.get("account_id") is not None:
        acc = accounts.get(int(body["account_id"]))
    elif body.get("account"):
        name = body["account"].strip().lower()
        acc = next((a for a in accounts.list() if a.name.lower() == name), None)

    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")

    plugin = get_plugin(acc.network_id)
    if not plugin:
        raise HTTPException(status_code=400, detail=f"No plugin for {acc.network_id}")

    logger.info(f"Re-auth requested for {acc.name} ({acc.network_id}) — opening browser")

    logs: list[str] = []
    try:
        result = plugin.link_account(acc, on_log=lambda m: logs.append(str(m)))
    except Exception as e:
        logger.error(f"Re-auth failed: {e}")
        raise HTTPException(status_code=500, detail=f"Re-auth error: {e}")

    ok = bool(getattr(result, "success", False))
    msg = getattr(result, "message", "") or ""
    logger.info(f"Re-auth {acc.name}: {'OK' if ok else 'FAILED'} — {msg}")

    return {"status": "ok" if ok else "failed", "account": acc.name,
            "message": msg, "logs": logs[-10:]}


@app.post("/run")
async def run_campaign(
    request: RunRequest,
    x_api_key: str = Header(None),
):
    """Launch morning_routine.py --now in non-blocking subprocess."""
    verify_api_key(x_api_key)

    cfg = load_config()

    if not cfg.get("enabled", True):
        return {"status": "skipped", "reason": "Routine disabled in config"}

    # Build command
    contenthub_dir = Path(__file__).resolve().parent
    routine_script = contenthub_dir / "morning_routine.py"

    if not routine_script.exists():
        raise HTTPException(status_code=500, detail="morning_routine.py not found")

    # Launch subprocess (non-blocking)
    try:
        # On Windows, use pythonw to avoid console window, otherwise python
        python_exe = "pythonw" if sys.platform == "win32" else "python"

        # Pass content_type + mode via env vars so the routine respects them
        # (sinon mode=distribute demandé par Claude retombe sur 'campaign').
        env = os.environ.copy()
        if request.content_type:
            env["CONTENT_TYPE"] = request.content_type
        else:
            env["CONTENT_TYPE"] = cfg.get("default_content_type", "tts_drama")
        env["MODE"] = request.mode or "campaign"

        # Subprocess runs detached so API returns immediately
        if sys.platform == "win32":
            subprocess.Popen(
                [python_exe, str(routine_script), "--now"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            )
        else:
            subprocess.Popen(
                [python_exe, str(routine_script), "--now"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

        logger.info(
            f"Campaign launched: mode={request.mode}, content_type={request.content_type or cfg.get('default_content_type')}"
        )
        return {
            "status": "launched",
            "mode": request.mode,
            "content_type": request.content_type or cfg.get("default_content_type"),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to launch campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to launch: {str(e)}")


@app.get("/progress")
async def get_progress(x_api_key: str = Header(None)):
    """Retourne l'état de progression en cours (progress_state.json).

    Permet à la routine cloud Claude de savoir si une campagne est encore en cours
    (status="running") ou terminée (status="done"/"error"/"idle"), sans avoir à
    attendre un délai fixe.
    """
    verify_api_key(x_api_key)
    from core import progress_state
    state = progress_state.read()
    return {
        "active": state.get("active", False),
        "status": state.get("status", "idle"),
        "label": state.get("label", ""),
        "value": state.get("value", 0),
        "maximum": state.get("maximum", 1),
        "summary": state.get("summary", ""),
        "needs_reauth": state.get("needs_reauth", []),
        "run_id": state.get("run_id", ""),
        "updated_at": state.get("updated_at", 0),
    }


@app.get("/status")
async def get_status(x_api_key: str = Header(None)):
    """Get last 20 jobs from database with aggregated stats."""
    verify_api_key(x_api_key)

    try:
        from core.db import get_db

        db = get_db()
        rows = db.query(
            """
            SELECT j.id, j.content_key, j.network_id, j.account_id, j.status,
                   j.attempts, j.error, j.created_at, j.finished_at,
                   a.name as account_name, a.handle
            FROM jobs j
            LEFT JOIN accounts a ON a.id = j.account_id
            ORDER BY j.created_at DESC
            LIMIT 40
            """
        )

        jobs = [
            {
                "id": row["id"],
                "content_key": row["content_key"],
                "network_id": row["network_id"],
                "account_name": row["account_name"],
                "handle": row["handle"],
                "status": row["status"],
                "attempts": row["attempts"],
                "error": row["error"],
                "created_at": row["created_at"],
                "finished_at": row["finished_at"],
            }
            for row in rows
        ]

        # Aggregate by status
        from collections import Counter
        counts = Counter(j["status"] for j in jobs)

        return {
            "jobs": jobs,
            "summary": {
                "total": len(jobs),
                "success": counts.get("success", 0),
                "failed": counts.get("failed", 0),
                "pending": counts.get("pending", 0),
                "running": counts.get("running", 0),
                "skipped": counts.get("skipped", 0),
            }
        }
    except Exception as e:
        logger.error(f"Error fetching status: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching status: {str(e)}")


@app.post("/log_run")
async def log_run(request: Request, x_api_key: str = Header(None)):
    """Called by Claude Routine to log a run result (success/failure per upload)."""
    verify_api_key(x_api_key)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Write to a dedicated runs log file
    runs_log = _DATA_DIR / "routine_runs.jsonl"
    entry = {
        "timestamp": datetime.now().isoformat(),
        "slot": body.get("slot", "unknown"),
        "status": body.get("status", "unknown"),
        "summary": body.get("summary", ""),
        "uploads": body.get("uploads", []),  # [{network, account, status, error}]
    }
    with open(runs_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(f"Run logged: slot={entry['slot']} status={entry['status']}")
    return {"status": "logged"}


@app.get("/logs")
async def get_logs(x_api_key: str = Header(None), limit: int = 20):
    """Get last N routine run logs."""
    verify_api_key(x_api_key)

    runs_log = _DATA_DIR / "routine_runs.jsonl"
    if not runs_log.exists():
        return {"runs": [], "total": 0}

    try:
        lines = runs_log.read_text(encoding="utf-8").strip().splitlines()
        runs = [json.loads(l) for l in lines if l.strip()]
        runs.reverse()  # newest first
        return {"runs": runs[:limit], "total": len(runs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading logs: {str(e)}")


# ── Error Handlers ───────────────────────────────────────────────────────────


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
    )


# ── Main ─────────────────────────────────────────────────────────────────────


def ensure_config_exists():
    """Create default config if it doesn't exist."""
    cfg = load_config()
    if not cfg.get("api_key"):
        cfg["api_key"] = secrets.token_urlsafe(32)
        save_config(cfg)
    logger.info(f"API Key (first 16 chars): {cfg['api_key'][:16]}...")
    logger.info(f"Config file: {CONFIG_FILE}")


if __name__ == "__main__":
    import uvicorn

    ensure_config_exists()

    logger.info("Starting ContentHub API Server on http://0.0.0.0:5050")
    uvicorn.run(app, host="0.0.0.0", port=5050, log_level="info")
