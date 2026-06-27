"""Dépôt de comptes, agnostique de la plateforme.

Les identifiants de publication (tokens OAuth, secrets) sont sérialisés en JSON
puis chiffrés dans la colonne credentials_enc. Le cœur ne connaît jamais la
forme exacte des credentials : chaque plugin décide de ce qu'il y met.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from .db import get_db
from .crypto import encrypt, decrypt
from .models import Account

logger = logging.getLogger(__name__)


def _row_to_account(row: dict) -> Account:
    creds = {}
    if row.get("credentials_enc"):
        raw = decrypt(row["credentials_enc"])
        if raw:
            try:
                creds = json.loads(raw)
            except json.JSONDecodeError:
                creds = {}
    return Account(
        id=row["id"],
        network_id=row["network_id"],
        name=row["name"],
        handle=row.get("handle"),
        is_active=bool(row["is_active"]),
        cooldown_hours=float(row["cooldown_hours"]),
        last_posted=row.get("last_posted"),
        credentials=creds,
        content_type_id=row.get("content_type_id"),
    )


class AccountRepository:
    def __init__(self):
        self.db = get_db()

    # ── CRUD ────────────────────────────────────────────────────────────

    def add(self, network_id: str, name: str, cooldown_hours: float = 8.0,
            handle: str | None = None) -> int | None:
        try:
            return self.db.execute(
                "INSERT INTO accounts (network_id, name, handle, cooldown_hours) "
                "VALUES (?, ?, ?, ?)",
                (network_id, name, handle, cooldown_hours),
            )
        except Exception as e:
            logger.error("Ajout du compte « %s » échoué : %s", name, e)
            return None

    def delete(self, account_id: int) -> None:
        self.db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    def set_active(self, account_id: int, active: bool) -> None:
        self.db.execute(
            "UPDATE accounts SET is_active = ? WHERE id = ?",
            (1 if active else 0, account_id),
        )

    def set_content_type(self, account_id: int, content_type_id: str | None) -> None:
        self.db.execute(
            "UPDATE accounts SET content_type_id = ? WHERE id = ?",
            (content_type_id, account_id),
        )

    def get(self, account_id: int) -> Account | None:
        row = self.db.query_one("SELECT * FROM accounts WHERE id = ?", (account_id,))
        return _row_to_account(row) if row else None

    def list(self, network_id: str | None = None, active_only: bool = False) -> list[Account]:
        sql = "SELECT * FROM accounts"
        clauses, params = [], []
        if network_id:
            clauses.append("network_id = ?")
            params.append(network_id)
        if active_only:
            clauses.append("is_active = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY network_id, id"
        return [_row_to_account(r) for r in self.db.query(sql, tuple(params))]

    def count(self, network_id: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS c FROM accounts WHERE network_id = ?", (network_id,))
        return row["c"] if row else 0

    # ── Credentials (chiffrés) ─────────────────────────────────────────

    def set_credentials(self, account_id: int, credentials: dict,
                        handle: str | None = None) -> None:
        enc = encrypt(json.dumps(credentials)) if credentials else None
        if handle is not None:
            self.db.execute(
                "UPDATE accounts SET credentials_enc = ?, handle = ? WHERE id = ?",
                (enc, handle, account_id))
        else:
            self.db.execute(
                "UPDATE accounts SET credentials_enc = ? WHERE id = ?",
                (enc, account_id))

    def update_credentials(self, account_id: int, patch: dict) -> None:
        """Fusionne `patch` dans les credentials existants (ex: refresh token)."""
        acc = self.get(account_id)
        if not acc:
            return
        creds = {**acc.credentials, **patch}
        self.set_credentials(account_id, creds)

    # ── Drapeau « ré-authentification requise » ─────────────────────────
    # Stocké DANS les credentials (clé `needs_reauth`) pour éviter une migration
    # de schéma. Posé quand un refresh échoue (token révoqué / app re-review),
    # levé automatiquement après une nouvelle liaison réussie.

    def flag_reauth(self, account_id: int, needed: bool = True) -> None:
        acc = self.get(account_id)
        if not acc:
            return
        creds = dict(acc.credentials)
        if needed:
            creds["needs_reauth"] = True
        else:
            creds.pop("needs_reauth", None)
        self.set_credentials(account_id, creds)

    def needs_reauth(self, account_id: int) -> bool:
        acc = self.get(account_id)
        return bool(acc and acc.credentials.get("needs_reauth"))

    # ── Cooldown ────────────────────────────────────────────────────────

    def can_post(self, account_id: int) -> bool:
        acc = self.get(account_id)
        if not acc or not acc.last_posted:
            return True
        try:
            last = datetime.fromisoformat(acc.last_posted)
        except (ValueError, TypeError):
            return True
        return datetime.now() >= last + timedelta(hours=acc.cooldown_hours)

    def remaining_cooldown(self, account_id: int) -> int:
        acc = self.get(account_id)
        if not acc or not acc.last_posted:
            return 0
        try:
            last = datetime.fromisoformat(acc.last_posted)
        except (ValueError, TypeError):
            return 0
        remaining = (last + timedelta(hours=acc.cooldown_hours) - datetime.now()).total_seconds()
        return max(0, int(remaining))

    def mark_posted(self, account_id: int) -> None:
        self.db.execute(
            "UPDATE accounts SET last_posted = ? WHERE id = ?",
            (datetime.now().isoformat(), account_id))
