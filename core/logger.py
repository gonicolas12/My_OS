"""Configuration du logging de My_OS.

Logs lisibles et horodatés, écrits sur ``stderr``. Aucun secret ni contenu
sensible ne doit être journalisé (cf. docs/SECURITY.md menace 4).
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    """Renvoie un logger configuré pour My_OS.

    ``logging.basicConfig`` est idempotent : le handler racine n'est installé
    qu'au premier appel, les appels suivants sont sans effet.
    """
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format=_LOG_FORMAT)
    return logging.getLogger(name)
