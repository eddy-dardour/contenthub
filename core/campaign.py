"""Orchestration d'un type de contenu : génération + circulation par compte.

Un « campaign run » prend un ContentType du catalogue et, pour CHAQUE plateforme
épinglée (séparément) :

  1. compte les comptes liés+actifs de la plateforme  → N comptes,
  2. génère N vidéos uniques via l'outil local,
  3. distribue UNE vidéo distincte par compte (vraie circulation, zéro doublon).

Le cœur ne connaît que les plugins (NetworkPlugin) ; aucune logique spécifique à
une plateforme ici. Le générateur écrit dans output/videos/ (cf. core.generator).
"""

from __future__ import annotations

import logging
from typing import Callable

from . import generator
from . import content as content_mod
from .catalog import ContentType
from .registry import get_plugins
from .publisher import Publisher

logger = logging.getLogger(__name__)


def _emit(cb: Callable | None, event: str, data: dict):
    if cb:
        try:
            cb(event, data)
        except Exception as e:
            logger.warning("callback %s a échoué : %s", event, e)


def eligible_accounts(content_type: ContentType) -> dict[str, list]:
    """Retourne {network_id: [comptes liés+actifs]} pour les plateformes épinglées."""
    plugins = get_plugins()
    result: dict[str, list] = {}
    for net_id in content_type.networks:
        plugin = plugins.get(net_id)
        if not plugin:
            continue
        accounts = [a for a in plugin.list_accounts(active_only=True)
                    if plugin.is_account_linked(a)]
        if accounts:
            result[net_id] = accounts
    return result


def plan(content_type: ContentType) -> dict[str, int]:
    """Aperçu : combien de vidéos seront générées par plateforme."""
    return {net_id: len(accs) for net_id, accs in eligible_accounts(content_type).items()}


def run(content_type: ContentType,
        progress: Callable | None = None,
        stop_check: Callable | None = None) -> dict:
    """Exécute la campagne pour un type de contenu.

    Pour chaque plateforme épinglée : génère 1 vidéo par compte lié+actif, puis
    distribue une vidéo unique par compte. Retourne un récap agrégé.

    Événements émis (en plus des événements publisher) :
      step     → {label, index, total}   étape nommée
      progress → {value, maximum}        avancement global (0..maximum)
    """
    summary = {"published": 0, "failed": 0, "skipped": 0,
               "generated": 0, "per_network": {}, "no_accounts": False}

    by_network = eligible_accounts(content_type)
    if not by_network:
        summary["no_accounts"] = True
        _emit(progress, "info", {
            "message": f"Aucun compte lié+actif sur les plateformes de "
                       f"« {content_type.label} » ({', '.join(content_type.networks)})."})
        _emit(progress, "done", summary)
        return summary

    # Calcul du nombre total d'étapes : génération + upload par compte par réseau
    total_accounts = sum(len(accs) for accs in by_network.values())
    # steps = 1 génération par réseau + 1 upload par compte
    total_steps = len(by_network) + total_accounts
    current_step = 0

    def _step(label: str):
        nonlocal current_step
        current_step += 1
        _emit(progress, "step", {"label": label, "index": current_step, "total": total_steps})
        _emit(progress, "progress", {"value": current_step, "maximum": total_steps})

    publisher = Publisher()

    for net_id, accounts in by_network.items():
        if stop_check and stop_check():
            break
        n = len(accounts)

        _step(f"Génération {net_id} — {n} vidéo(s)")
        _emit(progress, "info", {
            "message": f"[{net_id}] {n} compte(s) → génération de {n} vidéo(s) unique(s)…"})

        # 1) Vérifie le contenu déjà disponible dans output/.
        # Si des vidéos sont présentes (run précédent interrompu, génération manuelle…),
        # on les réutilise et on ne génère que le complément nécessaire.
        ok = True  # défaut : contenu disponible sans génération
        existing = content_mod.list_content()
        need = max(n - len(existing), 0)
        if need == 0:
            _emit(progress, "info", {
                "message": f"[{net_id}] {len(existing)} vidéo(s) déjà disponible(s) — génération ignorée."})
            items = existing[:n]
        else:
            if existing:
                _emit(progress, "info", {
                    "message": f"[{net_id}] {len(existing)} vidéo(s) existante(s), "
                               f"génération de {need} vidéo(s) supplémentaire(s)…"})
            before_keys = {i.key for i in existing}
            ok = generator.generate(
                need, content_type.gen_type,
                on_log=lambda m: _emit(progress, "log", {"message": m}),
                stop_check=stop_check)
            all_items = content_mod.list_content()
            new_items = [i for i in all_items if i.key not in before_keys]
            items = (existing + new_items)[:n]
        summary["generated"] += len(items)
        if not ok or not items:
            _emit(progress, "info", {
                "message": f"[{net_id}] Génération incomplète ({len(items)} vidéo(s)). "
                           "Distribution de ce qui est disponible."})

        # 2) Circulation : une vidéo distincte par compte, avec step par upload.
        def _progress_with_step(ev: str, data: dict):
            if ev == "uploading":
                _step(f"Upload {data.get('account','')} ({net_id})")
            _emit(progress, ev, data)

        net_summary = publisher.circulate(
            net_id, accounts, items,
            progress=_progress_with_step, stop_check=stop_check)
        summary["per_network"][net_id] = net_summary
        summary["published"] += net_summary["published"]
        summary["failed"] += net_summary["failed"]
        summary["skipped"] += net_summary.get("skipped", 0)
        summary["skipped_cooldown"] = summary.get("skipped_cooldown", 0) + net_summary.get("skipped_cooldown", 0)

    _emit(progress, "progress", {"value": total_steps, "maximum": total_steps})
    _emit(progress, "done", summary)
    return summary
