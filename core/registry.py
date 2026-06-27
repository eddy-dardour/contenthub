"""Découverte et instanciation des plugins réseaux.

Chaque sous-package de networks/ qui expose une sous-classe de NetworkPlugin
via `PLUGIN = MaClasse` est chargé automatiquement. Ajouter une plateforme ne
demande donc aucune modification du cœur : il suffit de déposer un dossier.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

import networks
from networks.base import NetworkPlugin

logger = logging.getLogger(__name__)

_plugins: dict[str, NetworkPlugin] | None = None


def _discover() -> dict[str, NetworkPlugin]:
    found: dict[str, NetworkPlugin] = {}
    for mod in pkgutil.iter_modules(networks.__path__):
        if mod.name in ("base",) or not mod.ispkg and mod.name == "base":
            continue
        if not mod.ispkg:
            continue
        try:
            module = importlib.import_module(f"networks.{mod.name}")
            plugin_cls = getattr(module, "PLUGIN", None)
            if plugin_cls is None or not issubclass(plugin_cls, NetworkPlugin):
                continue
            instance = plugin_cls()
            found[instance.id] = instance
            logger.info("Plugin réseau chargé : %s", instance.display_name)
        except Exception as e:
            logger.error("Échec du chargement du plugin « %s » : %s", mod.name, e)
    return found


def get_plugins() -> dict[str, NetworkPlugin]:
    global _plugins
    if _plugins is None:
        _plugins = _discover()
    return _plugins


def get_plugin(network_id: str) -> NetworkPlugin | None:
    return get_plugins().get(network_id)
