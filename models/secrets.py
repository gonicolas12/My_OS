"""Stockage de la clé API cloud via le trousseau du système (``keyring``).

Contrat figé (cf. docs/INTERFACES.md §5.1) : ``get_api_key`` / ``set_api_key`` /
``has_api_key`` / ``delete_api_key``.

Sécurité (cf. docs/SECURITY.md menace 4) : la clé API n'est **jamais** écrite en
clair — ni dans ``config.yaml``, ni dans les logs, ni dans le journal d'audit.
Elle vit uniquement dans le trousseau de l'OS (Secret Service / kwallet sous
Linux), chiffré par la session. Le **popup** y écrit la clé directement
(:func:`set_api_key`) et le **daemon** la lit (:func:`get_api_key`) : le secret ne
transite donc jamais par la socket IPC.

Import paresseux de ``keyring`` : ce module s'importe sans la dépendance (tests,
mode stub) ; ``keyring`` n'est requis qu'au premier accès réel au trousseau. Toute
erreur du trousseau (backend absent, verrouillé) est traitée comme « pas de clé »
afin que l'appelant replie proprement sur le local, sans jamais planter.
"""

from __future__ import annotations

from typing import Any

# Identifiants du trousseau. ``SERVICE_NAME`` regroupe les secrets de My_OS ;
# ``API_KEY_NAME`` est l'entrée de la clé API Anthropic.
SERVICE_NAME = "my_os"
API_KEY_NAME = "anthropic_api_key"


def _keyring() -> Any:
    """Renvoie le module ``keyring`` (import paresseux).

    Isolé dans une fonction pour rester mockable en test et n'exiger la
    dépendance qu'au premier accès réel au trousseau.
    """
    import keyring  # pylint: disable=import-outside-toplevel

    return keyring


def get_api_key() -> str | None:
    """Renvoie la clé API stockée, ou ``None`` si absente.

    Une erreur du trousseau (backend indisponible/verrouillé) est assimilée à
    « pas de clé » : on renvoie ``None`` plutôt que de propager — l'appelant
    repliera sur le backend local (cf. cloud_router).
    """
    try:
        key = _keyring().get_password(SERVICE_NAME, API_KEY_NAME)
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    return key or None


def set_api_key(key: str) -> None:
    """Stocke (ou remplace) la clé API dans le trousseau de l'OS.

    Lève :class:`ValueError` si la clé est vide, pour ne pas enregistrer un
    secret factice. La valeur est rognée (espaces de bord retirés).
    """
    if not key or not key.strip():
        raise ValueError("clé API vide")
    _keyring().set_password(SERVICE_NAME, API_KEY_NAME, key.strip())


def has_api_key() -> bool:
    """``True`` si une clé API est enregistrée dans le trousseau."""
    return get_api_key() is not None


def delete_api_key() -> None:
    """Supprime la clé du trousseau si présente. Idempotent (aucune erreur si absente)."""
    try:
        _keyring().delete_password(SERVICE_NAME, API_KEY_NAME)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
