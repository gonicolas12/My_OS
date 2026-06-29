"""Tests de la présentation Wayland du popup (jalon 5).

Sans Qt ni session réelle : on injecte un faux ``environ`` pour vérifier la pose
(ou non) de ``QT_WAYLAND_SHELL_INTEGRATION``.
"""

# pylint: disable=missing-function-docstring
from __future__ import annotations

from core.session import SESSION_WAYLAND, SESSION_X11
from ui.wayland_layer import is_wayland, prepare_layer_shell


def test_is_wayland() -> None:
    assert is_wayland(SESSION_WAYLAND) is True
    assert is_wayland(SESSION_X11) is False


def test_x11_ne_touche_pas_l_environnement() -> None:
    env: dict[str, str] = {}
    assert prepare_layer_shell(SESSION_X11, env) is False
    assert not env


def test_wayland_pose_layer_shell() -> None:
    env: dict[str, str] = {}
    assert prepare_layer_shell(SESSION_WAYLAND, env) is True
    assert env["QT_WAYLAND_SHELL_INTEGRATION"] == "layer-shell"


def test_wayland_respecte_un_choix_explicite_layer_shell() -> None:
    env = {"QT_WAYLAND_SHELL_INTEGRATION": "layer-shell"}
    assert prepare_layer_shell(SESSION_WAYLAND, env) is True
    # Inchangé : on n'écrase pas la valeur existante.
    assert env["QT_WAYLAND_SHELL_INTEGRATION"] == "layer-shell"


def test_wayland_respecte_une_autre_integration_choisie() -> None:
    # L'utilisateur a explicitement désactivé layer-shell : on ne le force pas.
    env = {"QT_WAYLAND_SHELL_INTEGRATION": "xdg-shell"}
    assert prepare_layer_shell(SESSION_WAYLAND, env) is False
    assert env["QT_WAYLAND_SHELL_INTEGRATION"] == "xdg-shell"
