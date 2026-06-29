"""Tests de la détection de session (X11 vs Wayland) — jalon 5.

Aucune session graphique réelle : on injecte un faux ``environ`` pour couvrir
chaque branche de :func:`core.session.detect_session_type`.
"""

# pylint: disable=missing-function-docstring
from __future__ import annotations

from core.session import SESSION_WAYLAND, SESSION_X11, detect_session_type


def test_xdg_session_type_wayland_explicite() -> None:
    assert detect_session_type({"XDG_SESSION_TYPE": "wayland"}) == SESSION_WAYLAND


def test_xdg_session_type_x11_explicite() -> None:
    assert detect_session_type({"XDG_SESSION_TYPE": "x11"}) == SESSION_X11


def test_xdg_session_type_insensible_a_la_casse_et_aux_espaces() -> None:
    assert detect_session_type({"XDG_SESSION_TYPE": "  Wayland "}) == SESSION_WAYLAND


def test_xdg_prioritaire_sur_les_variables_display() -> None:
    # XDG explicite l'emporte même si DISPLAY (X11) est aussi présent.
    env = {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}
    assert detect_session_type(env) == SESSION_WAYLAND


def test_heuristique_wayland_display() -> None:
    assert detect_session_type({"WAYLAND_DISPLAY": "wayland-0"}) == SESSION_WAYLAND


def test_heuristique_display_x11() -> None:
    assert detect_session_type({"DISPLAY": ":0"}) == SESSION_X11


def test_wayland_display_prioritaire_sur_display() -> None:
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert detect_session_type(env) == SESSION_WAYLAND


def test_xdg_non_concluant_retombe_sur_heuristique() -> None:
    # "tty" n'est ni x11 ni wayland : on regarde les variables d'affichage.
    env = {"XDG_SESSION_TYPE": "tty", "WAYLAND_DISPLAY": "wayland-0"}
    assert detect_session_type(env) == SESSION_WAYLAND


def test_environnement_vide_defaut_x11() -> None:
    assert detect_session_type({}) == SESSION_X11
