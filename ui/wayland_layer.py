"""Présentation Wayland du popup : surface ``layer-shell`` + repli X11 (jalon 5).

Sous X11 (nominal), le popup se centre via ``move()`` et reste au-dessus grâce à
``WindowStaysOnTopHint`` (cf. ui/popup.py). Ces mécanismes **n'ont pas d'effet sous
Wayland** : le positionnement global et l'always-on-top côté client n'existent pas.

La voie pragmatique retenue (cf. docs/INTERFACES.md §9) : demander l'intégration
shell ``layer-shell`` de Qt (plugin système ``layer-shell-qt``) en posant
``QT_WAYLAND_SHELL_INTEGRATION=layer-shell`` **avant** la ``QApplication``. Le popup
devient alors une surface overlay centrée au-dessus de tout. Si le plugin/compositeur
ne supporte pas ``layer-shell``, Qt retombe sur ``xdg-shell`` (fenêtre normale,
centrage approximatif) — repli sûr, signalé par un avertissement.

Ce module ne dépend **pas** de PySide6 (juste de l'environnement) : il est donc
testable sans Qt ni session graphique réelle.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

from core.logger import get_logger
from core.session import SESSION_WAYLAND

_log = get_logger("popup.wayland")

_SHELL_ENV = "QT_WAYLAND_SHELL_INTEGRATION"
_LAYER_SHELL = "layer-shell"


def is_wayland(session_type: str) -> bool:
    """Vrai si la session est Wayland (présentation overlay au lieu du centrage X11)."""
    return session_type == SESSION_WAYLAND


def prepare_layer_shell(
    session_type: str, environ: MutableMapping[str, str] | None = None
) -> bool:
    """Prépare l'affichage du popup selon la session, **avant** la ``QApplication``.

    Sous Wayland : pose ``QT_WAYLAND_SHELL_INTEGRATION=layer-shell`` (sauf si la
    variable est déjà définie — on respecte alors le choix explicite de
    l'utilisateur). Renvoie ``True`` si ``layer-shell`` est (déjà ou nouvellement)
    l'intégration demandée. Sous X11 : ne touche à rien et renvoie ``False``.

    ``environ`` est injectable (défaut : ``os.environ``) pour les tests.
    """
    if not is_wayland(session_type):
        return False

    env = os.environ if environ is None else environ
    existing = env.get(_SHELL_ENV)
    if existing:
        _log.info("Wayland : %s déjà défini (%r), respecté.", _SHELL_ENV, existing)
        return existing == _LAYER_SHELL

    env[_SHELL_ENV] = _LAYER_SHELL
    _log.info(
        "Wayland détecté (expérimental) : popup en surface layer-shell (overlay, "
        "centré au-dessus de tout). Nécessite le plugin système 'layer-shell-qt' ; "
        "à défaut, repli xdg-shell (fenêtre normale, centrage par le compositeur)."
    )
    return True
