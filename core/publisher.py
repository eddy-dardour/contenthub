"""Moteur de publication agnostique.

Deux modes de distribution :
  • run()       : publie tout le contenu prêt vers tous les comptes ciblés
                  (mode legacy/manuel « distribuer maintenant »).
  • circulate() : circulation — assigne UNE vidéo distincte par compte (utilisé
                  par les campagnes du catalogue : 10 vidéos → 10 comptes).

Gère dans les deux cas :
  • le cooldown par compte (anti-spam / anti-shadowban),
  • la déduplication (ne pas re-publier le même contenu sur le même compte),
  • les retries avec back-off,
  • un délai inter-publication jitteré (comportement humain),
  • un journal complet en base (table jobs) + des évènements live pour l'UI.

Tourne dans un thread ; réactif à pause/stop. Le cœur ne connaît que
NetworkPlugin — aucune logique spécifique à une plateforme ici.
"""

from __future__ import annotations

import time
import random
import logging
import threading
from datetime import datetime
from typing import Callable

from .db import get_db
from .accounts import AccountRepository
from . import content as content_mod
from .models import JobStatus, ContentItem, Account
from .registry import get_plugins, get_plugin

logger = logging.getLogger(__name__)

# Délais anti-shadowban entre deux publications réussies sur le même compte.
# TikTok détecte les rafales courtes — on simule un comportement humain (~3-8 min).
MIN_GAP_S = 180    # 3 min minimum
MAX_GAP_S = 480    # 8 min maximum
MAX_ATTEMPTS = 3

# Hashtags rotatifs injectés en fin de caption pour varier l'empreinte textuelle.
_HASHTAG_POOLS = [
    ["#storytime", "#reddit", "#drama", "#relatable"],
    ["#aita", "#redditstories", "#viralstory", "#fyp"],
    ["#storytelling", "#viral", "#foryou", "#trending"],
    ["#redditdrama", "#entertainme", "#storytime", "#fy"],
    ["#aita", "#drama", "#viral", "#tiktokviral"],
    ["#redditstories", "#fyp", "#relatable", "#storytelling"],
]


class Publisher:
    def __init__(self):
        self.db = get_db()
        self.accounts = AccountRepository()
        self._stop = threading.Event()
        self._pause = threading.Event()

    # ── Contrôle ────────────────────────────────────────────────────────

    def stop(self):
        self._stop.set()

    def pause(self):
        self._pause.set()

    def resume(self):
        self._pause.clear()

    def reset(self):
        self._stop.clear()
        self._pause.clear()

    @staticmethod
    def _emit(cb: Callable | None, event: str, data: dict):
        if cb:
            try:
                cb(event, data)
            except Exception as e:
                logger.warning("callback %s a échoué : %s", event, e)

    # ── Déduplication via la table jobs ────────────────────────────────

    def _already_published(self, content_key: str, account_id: int) -> bool:
        row = self.db.query_one(
            "SELECT 1 FROM jobs WHERE content_key = ? AND account_id = ? "
            "AND status = ? LIMIT 1",
            (content_key, account_id, JobStatus.SUCCESS.value))
        return row is not None

    def _log_job(self, content: ContentItem, account: Account, status: JobStatus,
                 attempts: int, error: str | None, remote_id: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO jobs (content_key, network_id, account_id, status, "
            "attempts, caption, error, remote_id, finished_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (content.key, account.network_id, account.id, status.value,
             attempts, content.caption, error, remote_id, datetime.now().isoformat()))

    # ── Boucle de distribution ─────────────────────────────────────────

    def run(self, network_ids: list[str] | None = None,
            content_type_id: str | None = None,
            progress: Callable | None = None) -> dict:
        """Distribue le contenu disponible vers les comptes des réseaux ciblés.

        network_ids=None → tous les réseaux.
        content_type_id=None → ignore le filtre par type de contenu de compte.
        Retourne un récap agrégé.
        """
        self.reset()
        all_items = content_mod.list_content()
        summary = {"published": 0, "failed": 0, "skipped": 0, "no_content": not all_items}

        if not all_items:
            self._emit(progress, "info", {"message": "Aucun contenu à publier."})
            return summary

        plugins = get_plugins()
        targets = network_ids or list(plugins.keys())
        logger.info("Distribution : %d réseau(x) ciblé(s), %d contenu(s)", len(targets), len(all_items))

        any_account = False
        for net_id in targets:
            plugin = plugins.get(net_id)
            if not plugin:
                logger.warning("Plugin introuvable pour net_id=%r", net_id)
                continue
            all_accounts = plugin.list_accounts(active_only=True)
            accounts = [a for a in all_accounts if plugin.is_account_linked(a)]
            logger.info(
                "  [%s] %d compte(s) actif(s) total, %d lié(s)",
                net_id, len(all_accounts), len(accounts))
            if not accounts:
                self._emit(progress, "info", {
                    "message": f"[{plugin.display_name}] Aucun compte actif lié — liez un compte dans l'onglet Comptes."})
                continue
            any_account = True

            # Pour chaque compte, filtre le contenu selon son content_type_id assigné.
            for account in accounts:
                effective_type = account.content_type_id or content_type_id
                items = self._filter_items_for_account(all_items, effective_type)
                if not items:
                    self._emit(progress, "info", {
                        "message": f"[{plugin.display_name}/{account.name}] Aucun contenu compatible."})
                    continue
                for item in items:
                    if self._stop.is_set():
                        self._emit(progress, "stopped", {})
                        return summary
                    if self._already_published(item.key, account.id):
                        summary["skipped"] += 1
                        continue
                    self._publish_one(plugin, account, item, summary, progress)

        if not any_account:
            self._emit(progress, "info", {
                "message": "Aucun compte actif lié sur les réseaux ciblés. Ajoutez et liez un compte dans l'onglet Comptes."})

        self._emit(progress, "done", summary)
        return summary

    @staticmethod
    def _filter_items_for_account(items, content_type_id: str | None):
        """Filtre les items selon le type assigné au compte.

        Les vidéos générées (001_01.mp4) ne portent pas de tag de type dans leur
        nom — le générateur produit un seul type à la fois. On retourne donc tout
        le contenu disponible ; le filtre par type de contenu est appliqué au niveau
        de la campagne (génération ciblée), pas ici.
        """
        return items

    # ── Circulation : 1 vidéo unique par compte ─────────────────────────

    def circulate(self, network_id: str, accounts: list[Account],
                  items: list[ContentItem], progress: Callable | None = None,
                  stop_check: Callable | None = None) -> dict:
        """Circulation : 1 vidéo unique par compte par cycle.

        S'arrête dans deux cas :
          1. Tous les comptes sont en cooldown → rien à faire ce slot.
          2. Chaque compte éligible a reçu exactement 1 vidéo → cycle terminé.
        """
        summary = {"published": 0, "failed": 0, "skipped": 0, "skipped_cooldown": 0}
        plugin = get_plugin(network_id)
        if not plugin:
            return summary

        # Arrêt condition 1 : tous les comptes en cooldown → inutile de continuer
        cooldowns = [(a, self.accounts.remaining_cooldown(a.id)) for a in accounts]
        all_in_cooldown = all(r > 0 for _, r in cooldowns)
        if all_in_cooldown:
            soonest = min(r for _, r in cooldowns)
            h, s = divmod(soonest, 3600)
            m = s // 60
            human = f"{h}h{m:02d}" if h else f"{m}min"
            self._emit(progress, "info", {
                "message": f"[{network_id}] Tous les comptes en cooldown — "
                           f"prochain slot disponible dans {human}. Routine arrêtée."})
            summary["skipped_cooldown"] = len(accounts)
            return summary

        # Comptes disponibles d'abord (cascade)
        sorted_accounts = sorted(accounts, key=lambda a: self.accounts.remaining_cooldown(a.id))
        all_groups = content_mod.group_by_story(items)

        for account in sorted_accounts:
            if self._stop.is_set() or (stop_check and stop_check()):
                break

            remaining = self.accounts.remaining_cooldown(account.id)
            if remaining > 0:
                h, s = divmod(remaining, 3600)
                m = s // 60
                human = f"{h}h{m:02d}" if h else f"{m}min"
                self._emit(progress, "info", {
                    "message": f"[{network_id}] {account.name} en cooldown — disponible dans {human}"})
                summary["skipped_cooldown"] += 1
                continue

            # Première histoire que ce compte n'a pas encore publiée
            assigned = None
            for group in all_groups:
                already_done = all(self._already_published(part.key, account.id) for part in group)
                if not already_done:
                    assigned = group
                    break

            if assigned is None:
                self._emit(progress, "info", {
                    "message": f"[{network_id}] {account.name} — toutes les vidéos déjà publiées."})
                summary["skipped"] += 1
                continue

            # Arrêt condition 2 : 1 seule vidéo par compte par cycle (anti-spam)
            # On publie uniquement la 1ère partie de l'histoire assignée
            self._publish_story(plugin, account, assigned[:1], summary, progress, stop_check)

        return summary

    def _publish_story(self, plugin, account: Account, parts: list[ContentItem],
                       summary: dict, progress, stop_check=None) -> None:
        """Publie toutes les parties d'une histoire sur un compte, dans l'ordre.

        Si une partie échoue, on continue avec les suivantes (chaque partie est
        une vidéo autonome) mais on log l'échec. L'ordre des parties est garanti.
        """
        if len(parts) > 1:
            self._emit(progress, "info", {
                "message": f"[{account.name}] histoire « {parts[0].story_key} » : "
                           f"{len(parts)} parties à publier dans l'ordre."})
        for item in parts:
            if self._stop.is_set() or (stop_check and stop_check()):
                return
            self._wait_if_paused()
            if self._already_published(item.key, account.id):
                summary["skipped"] += 1
                continue
            self._publish_one(plugin, account, item, summary, progress)

    # ── Publication d'une paire (compte, vidéo) ─────────────────────────

    def _publish_one(self, plugin, account: Account, item: ContentItem,
                     summary: dict, progress) -> None:
        self._wait_if_paused()
        self._wait_cooldown(account, item, progress)
        if self._stop.is_set():
            return

        varied = self._vary_caption(item)
        self._emit(progress, "uploading", {
            "network": plugin.display_name, "account": account.name,
            "content": item.key})

        ok, attempts, error, remote_id = self._publish_with_retry(plugin, account, varied, progress)

        if ok:
            summary["published"] += 1
            self.accounts.mark_posted(account.id)
            self.accounts.flag_reauth(account.id, needed=False)  # auth OK → lève le drapeau
            self._log_job(item, account, JobStatus.SUCCESS, attempts, None, remote_id=remote_id)
            self._emit(progress, "success", {
                "network": plugin.display_name, "account": account.name,
                "content": item.key})
            content_mod.remove_content(item.key)
            self._human_gap()
        else:
            summary["failed"] += 1
            self._log_job(item, account, JobStatus.FAILED, attempts, error)
            # Détecte un échec d'authentification → marque le compte à ré-authentifier.
            if self._is_auth_error(error):
                self.accounts.flag_reauth(account.id, needed=True)
                summary.setdefault("needs_reauth", []).append(account.name)
                self._emit(progress, "needs_reauth", {
                    "network": plugin.display_name, "account": account.name})
                try:
                    from . import progress_state
                    progress_state.add_reauth(account.name)
                except Exception:
                    pass
            self._emit(progress, "failed", {
                "network": plugin.display_name, "account": account.name,
                "content": item.key, "error": error})

    @staticmethod
    def _is_auth_error(error: str | None) -> bool:
        """Détecte un échec lié à l'authentification (token expiré/révoqué)."""
        if not error:
            return False
        e = error.lower()
        return (
            "401" in e or "invalid authentication" in e
            or "invalid_grant" in e or "expiré" in e or "expired" in e
            or "reauth" in e or "ré-auth" in e or "unauthorized" in e
            or "access token" in e
        )

    @staticmethod
    def _vary_caption(item: ContentItem) -> ContentItem:
        """Ajoute un bloc de hashtags rotatif unique à chaque publication."""
        pool = random.choice(_HASHTAG_POOLS)
        tags = " ".join(random.sample(pool, k=min(3, len(pool))))
        caption = (item.caption or "").rstrip()
        if caption:
            caption = f"{caption}\n\n{tags}"
        else:
            caption = tags
        from dataclasses import replace
        return replace(item, caption=caption)

    def _publish_with_retry(self, plugin, account: Account, item: ContentItem,
                            progress):
        attempts, error = 0, None
        # Recharge le compte (credentials frais) avant publication.
        fresh = self.accounts.get(account.id) or account
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self._stop.is_set():
                break
            attempts = attempt
            try:
                result = plugin.publish(fresh, item, on_log=lambda m: self._emit(
                    progress, "log", {"message": m}))
                if result.success:
                    return True, attempts, None, result.remote_id
                error = result.detail
            except Exception as e:
                error = str(e)
                logger.error("Publication exception (%s/%s) : %s",
                             plugin.id, account.name, e)
            if attempt < MAX_ATTEMPTS:
                backoff = 2 ** attempt
                self._emit(progress, "retry", {
                    "account": account.name, "attempt": attempt,
                    "backoff": backoff, "error": error})
                self._interruptible_sleep(backoff)
        return False, attempts, error, None

    # ── Temporisations ──────────────────────────────────────────────────

    def _wait_cooldown(self, account: Account, item: ContentItem, progress):
        """En mode manuel (run), attend le cooldown. En mode circulate, on cascade."""
        while not self.accounts.can_post(account.id):
            if self._stop.is_set():
                return
            remaining = self.accounts.remaining_cooldown(account.id)
            h, s = divmod(remaining, 3600)
            m = s // 60
            human = f"{h}h{m:02d}" if h else f"{m}min"
            self._emit(progress, "cooldown", {
                "account": account.name, "content": item.key,
                "remaining": remaining, "human": human})
            self._interruptible_sleep(min(remaining, 30) or 1)

    def _human_gap(self):
        self._interruptible_sleep(random.uniform(MIN_GAP_S, MAX_GAP_S))

    def _wait_if_paused(self):
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.5)

    def _interruptible_sleep(self, seconds: float):
        deadline = time.time() + seconds
        while time.time() < deadline and not self._stop.is_set():
            time.sleep(min(0.5, deadline - time.time()))


# ── Statistiques pour le tableau de bord ───────────────────────────────

def stats() -> dict:
    db = get_db()
    total = db.query_one("SELECT COUNT(*) c FROM jobs")["c"]
    ok = db.query_one("SELECT COUNT(*) c FROM jobs WHERE status='success'")["c"]
    failed = db.query_one("SELECT COUNT(*) c FROM jobs WHERE status='failed'")["c"]
    return {"jobs_total": total, "jobs_success": ok, "jobs_failed": failed}


def recent_jobs(limit: int = 50) -> list[dict]:
    return get_db().query(
        "SELECT j.*, a.name AS account_name FROM jobs j "
        "LEFT JOIN accounts a ON a.id = j.account_id "
        "ORDER BY j.id DESC LIMIT ?", (limit,))
