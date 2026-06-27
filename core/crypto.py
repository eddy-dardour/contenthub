"""Chiffrement symétrique (Fernet) des données sensibles (tokens, credentials).

La clé est lue depuis l'environnement (CONTENTHUB_KEY), sinon générée et
persistée dans le .env de la plateforme au premier appel. Tout reste local.
"""

from __future__ import annotations

import os
import logging

from cryptography.fernet import Fernet

from .paths import env_path

logger = logging.getLogger(__name__)

_KEY_VAR = "CONTENTHUB_KEY"
_cached_key: bytes | None = None


def _read_key_from_env_file() -> str | None:
    """Lit la clé directement depuis le .env (sans dépendre de load_dotenv).

    Rend le chiffrement auto-suffisant : peu importe l'ordre d'init, on
    réutilise toujours la même clé déjà persistée.
    """
    env = env_path()
    if not env.exists():
        return None
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(_KEY_VAR + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        return None
    return None


def _get_or_create_key() -> bytes:
    global _cached_key
    if _cached_key:
        return _cached_key

    key_b64 = os.getenv(_KEY_VAR) or _read_key_from_env_file()
    if key_b64:
        _cached_key = key_b64.encode("utf-8")
        os.environ[_KEY_VAR] = key_b64
        return _cached_key

    new_key = Fernet.generate_key()
    env = env_path()
    line = f"{_KEY_VAR}={new_key.decode('utf-8')}\n"
    try:
        if env.exists():
            content = env.read_text(encoding="utf-8")
            if _KEY_VAR + "=" not in content:
                env.write_text(content.rstrip("\n") + "\n" + line, encoding="utf-8")
        else:
            env.write_text(line, encoding="utf-8")
    except OSError as e:
        logger.warning("Impossible de persister la clé de chiffrement : %s", e)

    os.environ[_KEY_VAR] = new_key.decode("utf-8")
    _cached_key = new_key
    logger.info("Clé de chiffrement générée et stockée localement.")
    return _cached_key


def encrypt(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return Fernet(_get_or_create_key()).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return Fernet(_get_or_create_key()).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception as e:  # clé invalide / données corrompues
        logger.error("Déchiffrement échoué : %s", e)
        return None
