"""Paramètres globaux persistés en base (table settings, clé/valeur)."""

from __future__ import annotations

from .db import get_db

KEY_DEFAULT_CONTENT_TYPE = "default_content_type_id"


def get(key: str, default: str | None = None) -> str | None:
    row = get_db().query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set(key: str, value: str | None) -> None:
    if value is None:
        get_db().execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        get_db().execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value))


def get_default_content_type() -> str | None:
    return get(KEY_DEFAULT_CONTENT_TYPE)


def set_default_content_type(type_id: str | None) -> None:
    set(KEY_DEFAULT_CONTENT_TYPE, type_id)
