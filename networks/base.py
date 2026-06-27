"""Interface commune des plugins réseaux (TikTok, YouTube, X, …).

Un plugin encapsule TOUT ce qui est spécifique à une plateforme :
- son identité (id, nom affiché, icône),
- sa configuration (clés API, options) et son état (standby/configured),
- la liaison d'un compte (OAuth, login…),
- la publication d'un contenu sur un compte.

Le cœur (publisher, scheduler, UI) ne dépend que de cette interface : ajouter
une plateforme = déposer un nouveau plugin, sans toucher au reste.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from core.db import get_db
from core.crypto import encrypt, decrypt
from core.models import (
    Account, ContentItem, PublishResult, NetworkState, NetworkInfo,
)
from core.accounts import AccountRepository

logger = logging.getLogger(__name__)


class NetworkPlugin(ABC):
    """Classe de base d'un plugin réseau.

    Sous-classer et définir au minimum : id, display_name, _evaluate_state,
    link_account, publish. Les plugins « Standby » peuvent laisser link_account
    et publish lever NotImplementedError tant qu'ils ne sont pas configurés.
    """

    #: Identifiant stable du plugin (clé en base). Ex: "tiktok".
    id: str = ""
    #: Nom affiché dans l'UI. Ex: "TikTok".
    display_name: str = ""
    #: Glyphe/emoji affiché dans l'UI.
    icon: str = "●"
    #: Description courte de la plateforme.
    description: str = ""
    #: Champs de configuration attendus (clé -> libellé). Ex API keys.
    config_fields: dict[str, str] = {}
    #: Types de contenu supportés par ce réseau (ids du catalogue). Vide = tous.
    supported_content_types: tuple[str, ...] = ()

    def __init__(self):
        self.db = get_db()
        self.accounts = AccountRepository()
        self._ensure_registered()

    # ── Enregistrement / état ───────────────────────────────────────────

    def _ensure_registered(self) -> None:
        row = self.db.query_one("SELECT id FROM networks WHERE id = ?", (self.id,))
        if not row:
            self.db.execute(
                "INSERT INTO networks (id, display_name, state) VALUES (?, ?, ?)",
                (self.id, self.display_name, NetworkState.STANDBY.value),
            )

    def load_config(self) -> dict:
        row = self.db.query_one(
            "SELECT config_enc FROM networks WHERE id = ?", (self.id,))
        if not row or not row.get("config_enc"):
            return {}
        raw = decrypt(row["config_enc"])
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def save_config(self, config: dict) -> None:
        enc = encrypt(json.dumps(config)) if config else None
        self.db.execute(
            "UPDATE networks SET config_enc = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?", (enc, self.id))
        self.refresh_state()

    def refresh_state(self) -> NetworkState:
        state = self._evaluate_state(self.load_config())
        self.db.execute("UPDATE networks SET state = ? WHERE id = ?",
                        (state.value, self.id))
        return state

    @abstractmethod
    def _evaluate_state(self, config: dict) -> NetworkState:
        """Détermine l'état courant du réseau à partir de sa config."""

    def info(self) -> NetworkInfo:
        # Lecture seule : évalue l'état sans réécrire en DB à chaque appel
        # (le UPDATE en boucle depuis le dashboard ralentissait l'UI). L'état
        # persisté n'est mis à jour que via save_config()/refresh_state().
        state = self._evaluate_state(self.load_config())
        return NetworkInfo(
            id=self.id,
            display_name=self.display_name,
            state=state,
            accounts_count=self.accounts.count(self.id),
            note=self.status_note(state),
        )

    def status_note(self, state: NetworkState) -> str:
        return {
            NetworkState.STANDBY: "En attente de configuration (clé API requise).",
            NetworkState.CONFIGURED: "Prêt à publier.",
            NetworkState.ERROR: "Configuration invalide.",
        }.get(state, "")

    # ── Comptes ─────────────────────────────────────────────────────────

    def list_accounts(self, active_only: bool = False) -> list[Account]:
        return self.accounts.list(self.id, active_only=active_only)

    @abstractmethod
    def link_account(self, account: Account, on_log=None) -> PublishResult:
        """Lie un compte (OAuth/login). Stocke les credentials via le repo.

        Retourne PublishResult(success, detail). `on_log` : callback(str).
        """

    def is_account_linked(self, account: Account) -> bool:
        return account.linked

    # ── Publication ─────────────────────────────────────────────────────

    @abstractmethod
    def publish(self, account: Account, content: ContentItem,
                on_log=None) -> PublishResult:
        """Publie `content` sur `account`. Retourne PublishResult."""

    # ── Statistiques distantes (optionnel) ──────────────────────────────

    def fetch_stats(self, account: Account) -> dict | None:
        """Récupère les stats côté plateforme pour `account`.

        Retourne {'videos': int, 'views': int, 'likes': int} (clés optionnelles)
        ou None si la plateforme ne l'expose pas. Implémentation facultative :
        un plugin qui ne la surcharge pas n'affiche que les stats locales.
        Ne doit jamais lever : préférer retourner None en cas d'erreur réseau.
        """
        return None
