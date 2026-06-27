"""Modèles de données partagés entre le cœur, les plugins et l'UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NetworkState(str, Enum):
    STANDBY = "standby"        # plugin présent, pas encore configuré
    CONFIGURED = "configured"  # clés/sandbox prêtes, publication possible
    ERROR = "error"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Account:
    id: int
    network_id: str
    name: str
    handle: str | None = None
    is_active: bool = True
    cooldown_hours: float = 8.0
    last_posted: str | None = None
    credentials: dict = field(default_factory=dict)  # déchiffré en mémoire seulement
    content_type_id: str | None = None  # type de contenu assigné (None = tous)

    @property
    def linked(self) -> bool:
        """True si le compte possède des identifiants de publication."""
        return bool(self.credentials)


@dataclass
class ContentItem:
    """Une vidéo prête à publier, produite par l'outil de génération local.

    Les vidéos sont nommées `<story>_<part>.mp4` (ex: 002_01.mp4). `story_key`
    regroupe les parties d'une même histoire ; `part` donne leur ordre.
    """
    key: str          # nom de fichier (clé unique)
    path: str
    caption: str = ""
    size_bytes: int = 0
    story_key: str = ""   # préfixe d'histoire (ex: "002") — parties à publier ensemble
    part: int = 1         # numéro de partie (ordre de publication)


@dataclass
class PublishResult:
    success: bool
    detail: str = ""
    remote_id: str | None = None


@dataclass
class NetworkInfo:
    """Métadonnées affichées par l'UI pour un plugin réseau."""
    id: str
    display_name: str
    state: NetworkState
    accounts_count: int = 0
    note: str = ""
