# Control channel

Canal de communication entre la routine cloud Claude et la machine locale.

- `trigger.json` — écrit par la routine cloud pour déclencher une campagne
- `result-<id>.json` — écrit par la machine locale avec le résultat

Tout passe par api.github.com (whitelisté dans l'environnement cloud).
