"""Base de plugin « Standby » : réseau présent mais pas encore opérationnel.

Sert de socle aux plateformes dont l'intégration n'est pas codée (YouTube, X…).
Le réseau apparaît dans l'UI, accepte des comptes et de la configuration, mais
reste en STANDBY tant que les clés API requises ne sont pas fournies — et la
publication est refusée proprement (au lieu de planter).

Brancher la vraie intégration plus tard = sous-classer NetworkPlugin et
implémenter link_account/publish, sans rien changer au cœur.
"""

from __future__ import annotations

from networks.base import NetworkPlugin
from core.models import Account, ContentItem, PublishResult, NetworkState


class StandbyNetwork(NetworkPlugin):
    #: Clés de config qui, une fois toutes remplies, font passer en CONFIGURED.
    required_keys: tuple[str, ...] = ()

    def _evaluate_state(self, config: dict) -> NetworkState:
        if self.required_keys and all(config.get(k) for k in self.required_keys):
            return NetworkState.CONFIGURED
        return NetworkState.STANDBY

    def link_account(self, account: Account, on_log=None) -> PublishResult:
        if self.refresh_state() == NetworkState.STANDBY:
            return PublishResult(
                False,
                f"{self.display_name} est en Standby : renseignez les clés API "
                f"dans la configuration du réseau pour activer la liaison.")
        return PublishResult(
            False,
            f"L'intégration de liaison {self.display_name} n'est pas encore "
            f"disponible dans cette version.")

    def publish(self, account: Account, content: ContentItem,
                on_log=None) -> PublishResult:
        return PublishResult(
            False,
            f"{self.display_name} : publication indisponible (réseau en Standby).")
