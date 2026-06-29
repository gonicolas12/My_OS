"""Détection du type de session graphique (X11 vs Wayland) — jalon 5.

Le raccourci global et la présentation du popup dépendent du serveur d'affichage.
**X11 reste le chemin nominal** ; Wayland est ajouté en *best-effort* (expérimental)
et sélectionné au démarrage par :func:`detect_session_type`.

Fonction **pure** : elle ne lit que l'environnement (injectable) et ne dépend
d'aucune bibliothèque graphique, donc testable sans session réelle
(cf. docs/INTERFACES.md §9).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

SESSION_X11 = "x11"
SESSION_WAYLAND = "wayland"


def detect_session_type(environ: Mapping[str, str] | None = None) -> str:
    """Renvoie ``"wayland"`` ou ``"x11"`` (défaut).

    Ordre de décision :

    1. ``XDG_SESSION_TYPE`` explicite (``"wayland"`` ou ``"x11"``) — la source la
       plus fiable, posée par le gestionnaire de session ;
    2. sinon heuristique : ``WAYLAND_DISPLAY`` défini → Wayland ; ``DISPLAY``
       défini → X11 ;
    3. défaut : ``"x11"`` (chemin nominal — la détection ne lève jamais).

    ``environ`` est injectable (défaut : ``os.environ``) pour des tests
    déterministes sans session graphique.
    """
    env = os.environ if environ is None else environ

    declared = env.get("XDG_SESSION_TYPE", "").strip().lower()
    if declared == SESSION_WAYLAND:
        return SESSION_WAYLAND
    if declared == SESSION_X11:
        return SESSION_X11

    # XDG_SESSION_TYPE absent ou non concluant (ex. "tty", "") : on se rabat sur
    # la présence des variables d'affichage.
    if env.get("WAYLAND_DISPLAY"):
        return SESSION_WAYLAND
    if env.get("DISPLAY"):
        return SESSION_X11

    return SESSION_X11
