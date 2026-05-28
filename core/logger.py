"""Configuration du logging de My_OS.

Logs lisibles et horodatés, écrits sur ``stderr``. Aucun secret ni contenu
sensible ne doit être journalisé (cf. docs/SECURITY.md menace 4).
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_configured = False


def get_logger(name: str) -> logging.Logger:
    """Renvoie un logger configuré pour My_OS.

    Le handler racine n'est installé qu'une seule fois, au premier appel.
    """
    global _configured
    if not _configured:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format=_LOG_FORMAT)
        _configured = True
    return logging.getLogger(name)
