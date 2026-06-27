"""Accès SQLite thread-safe, agnostique de la plateforme.

Schéma pensé pour un réseau multi-comptes / multi-plateformes :

  networks  : une ligne par plugin réseau installé (tiktok, youtube, x…).
              Stocke l'état (standby/configured) et la config chiffrée (clés API).
  accounts  : comptes rattachés à un réseau. Tokens/credentials chiffrés.
  jobs      : file + historique de publication (statut, retries, erreurs).

Toutes les écritures passent par un verrou de process. WAL activé pour la
concurrence lecture/écriture.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .paths import db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS networks (
    id            TEXT PRIMARY KEY,          -- identifiant du plugin (ex: 'tiktok')
    display_name  TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'standby',  -- standby | configured | error
    config_enc    TEXT,                      -- JSON chiffré (clés API, options)
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    network_id       TEXT NOT NULL,
    name             TEXT NOT NULL,
    handle           TEXT,                   -- @pseudo ou open_id affiché
    is_active        INTEGER NOT NULL DEFAULT 1,
    cooldown_hours   REAL NOT NULL DEFAULT 2,
    last_posted      TIMESTAMP,
    credentials_enc  TEXT,                   -- JSON chiffré (tokens, secrets)
    content_type_id  TEXT,                   -- type de contenu préféré (catalog id)
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(network_id, name)
);

CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    content_key   TEXT NOT NULL,             -- nom de fichier vidéo
    network_id    TEXT NOT NULL,
    account_id    INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|running|success|failed|skipped
    attempts      INTEGER NOT NULL DEFAULT 0,
    caption       TEXT,
    error         TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at   TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_account ON jobs(account_id);
CREATE INDEX IF NOT EXISTS idx_jobs_content ON jobs(content_key);
CREATE INDEX IF NOT EXISTS idx_accounts_network ON accounts(network_id);
"""


class Database:
    """Singleton léger : un fichier SQLite, un verrou d'écriture."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else db_path()
        self._lock = threading.Lock()
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self.connect()
            try:
                conn.executescript(_SCHEMA)
                # Migrations — safe to run on existing DBs (ALTER TABLE IF NOT EXISTS not available in old SQLite)
                try:
                    conn.execute("ALTER TABLE accounts ADD COLUMN content_type_id TEXT")
                    conn.commit()
                except Exception:
                    pass  # column already exists
            finally:
                conn.close()

    # ── Helpers génériques (write protégé par le verrou) ────────────────

    def execute(self, sql: str, params: tuple = ()) -> int:
        """INSERT/UPDATE/DELETE. Retourne lastrowid."""
        with self._lock:
            conn = self.connect()
            try:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self.connect()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


_instance: Database | None = None


def get_db() -> Database:
    global _instance
    if _instance is None:
        _instance = Database()
    return _instance
