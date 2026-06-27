"""Statistiques par compte : local (table jobs) + distant (API plateformes).

Deux sources :
  • LOCAL — toujours disponible : agrège la table `jobs` (publié / échec / dernière
    publication) par compte. Aucun appel réseau.
  • DISTANT — best-effort : si le plugin réseau implémente `fetch_stats(account)`,
    on récupère le nombre de vidéos et de vues/likes côté plateforme. En cas
    d'échec ou de non-support, on retombe proprement sur les stats locales.

Le cœur n'appelle jamais une API directement : tout passe par le plugin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .db import get_db
from .registry import get_plugins
from .models import Account

logger = logging.getLogger(__name__)


@dataclass
class AccountStats:
    account_id: int
    network_id: str
    network_name: str
    account_name: str
    handle: str | None = None
    linked: bool = False
    # Local (table jobs)
    published: int = 0
    failed: int = 0
    last_posted: str | None = None
    # Distant (API plateforme) — None si non disponible
    remote_videos: int | None = None
    remote_views: int | None = None
    remote_likes: int | None = None
    remote_error: str | None = None


def _local_by_account() -> dict[int, dict]:
    """Agrège la table jobs : {account_id: {published, failed, last_posted}}."""
    rows = get_db().query(
        "SELECT account_id, "
        "  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS published, "
        "  SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END) AS failed, "
        "  MAX(CASE WHEN status='success' THEN finished_at END) AS last_posted "
        "FROM jobs GROUP BY account_id")
    return {r["account_id"]: r for r in rows}


def collect(with_remote: bool = True) -> list[AccountStats]:
    """Construit les stats de tous les comptes de toutes les plateformes.

    with_remote=True tente un appel API par compte lié (peut être lent) ; en cas
    d'échec, on garde les stats locales et on note l'erreur.
    """
    local = _local_by_account()
    out: list[AccountStats] = []

    for plugin in get_plugins().values():
        for acc in plugin.list_accounts():
            linked = plugin.is_account_linked(acc)
            loc = local.get(acc.id, {})
            st = AccountStats(
                account_id=acc.id,
                network_id=plugin.id,
                network_name=plugin.display_name,
                account_name=acc.name,
                handle=acc.handle,
                linked=linked,
                published=int(loc.get("published") or 0),
                failed=int(loc.get("failed") or 0),
                last_posted=loc.get("last_posted"),
            )

            if with_remote and linked and hasattr(plugin, "fetch_stats"):
                try:
                    remote = plugin.fetch_stats(acc)
                    if remote:
                        st.remote_videos = remote.get("videos")
                        st.remote_views = remote.get("views")
                        st.remote_likes = remote.get("likes")
                except Exception as e:  # API instable → on n'échoue jamais l'UI
                    logger.warning("fetch_stats(%s/%s) échoué : %s",
                                   plugin.id, acc.name, e)
                    st.remote_error = str(e)
            out.append(st)
    return out


def totals(stats: list[AccountStats]) -> dict:
    """Agrégats globaux pour les métriques du dashboard."""
    return {
        "accounts": len(stats),
        "linked": sum(1 for s in stats if s.linked),
        "published": sum(s.published for s in stats),
        "failed": sum(s.failed for s in stats),
        "remote_views": sum(s.remote_views or 0 for s in stats),
        "remote_videos": sum(s.remote_videos or 0 for s in stats),
    }
