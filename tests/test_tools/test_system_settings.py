"""Tests des outils de réglages système (:mod:`tools.system_settings`).

Backend **factice** injecté → on teste la logique d'outil (validation, bornage,
messages) sans toucher à D-Bus, pactl ni au matériel. Couvre aussi la
propagation d'erreur backend et l'absence d'élévation.
"""

# Comparer explicitement avec [] (aucun appel backend) est plus parlant en test.
# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison
from __future__ import annotations

import pytest

from tools.system_settings import (
    SETTINGS_TOOLS,
    SetBrightness,
    SetMute,
    SetVolume,
    SetWifi,
)


class _FakeBackend:
    """Backend scriptable : mémorise les appels, peut lever sur demande."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.brightness: list[int] = []
        self.volume: list[int] = []
        self.mute: list[bool] = []
        self.wifi: list[bool] = []

    def set_brightness(self, percent: int) -> None:
        if self.error:
            raise self.error
        self.brightness.append(percent)

    def set_volume(self, percent: int) -> None:
        if self.error:
            raise self.error
        self.volume.append(percent)

    def set_mute(self, muted: bool) -> None:
        if self.error:
            raise self.error
        self.mute.append(muted)

    def set_wifi(self, enabled: bool) -> None:
        if self.error:
            raise self.error
        self.wifi.append(enabled)


# --- niveaux & élévation ---


@pytest.mark.parametrize("tool", [SetBrightness(), SetVolume(), SetMute(), SetWifi()])
def test_reglages_sont_niveau_1_sans_elevation(tool: object) -> None:
    assert tool.risk_level == 1  # type: ignore[attr-defined]
    assert tool.requires_elevation({}) is False  # type: ignore[attr-defined]


# --- brightness ---


def test_set_brightness_applique_la_valeur() -> None:
    backend = _FakeBackend()
    result = SetBrightness(backend).run({"percent": 40})
    assert result.success is True
    assert backend.brightness == [40]
    assert "40" in result.output


def test_set_brightness_borne_les_valeurs_hors_plage() -> None:
    backend = _FakeBackend()
    SetBrightness(backend).run({"percent": 250})
    SetBrightness(backend).run({"percent": -30})
    assert backend.brightness == [100, 0]


def test_set_brightness_accepte_une_chaine_numerique() -> None:
    backend = _FakeBackend()
    assert SetBrightness(backend).run({"percent": "55"}).success is True
    assert backend.brightness == [55]


@pytest.mark.parametrize("bad", [None, "beaucoup", "", True, [50]])
def test_set_brightness_valeur_invalide_echoue(bad: object) -> None:
    backend = _FakeBackend()
    result = SetBrightness(backend).run({"percent": bad})
    assert result.success is False
    assert backend.brightness == []


def test_set_brightness_erreur_backend_est_remontee() -> None:
    backend = _FakeBackend(error=RuntimeError("aucun rétroéclairage"))
    result = SetBrightness(backend).run({"percent": 50})
    assert result.success is False
    assert "aucun rétroéclairage" in result.output


# --- volume / mute ---


def test_set_volume_applique_la_valeur() -> None:
    backend = _FakeBackend()
    assert SetVolume(backend).run({"percent": 30}).success is True
    assert backend.volume == [30]


def test_set_mute_true_et_false() -> None:
    backend = _FakeBackend()
    assert SetMute(backend).run({"muted": True}).success is True
    assert SetMute(backend).run({"muted": "off"}).success is True
    assert backend.mute == [True, False]


@pytest.mark.parametrize("bad", [None, "peut-être", 1.5, [True]])
def test_set_mute_valeur_invalide_echoue(bad: object) -> None:
    backend = _FakeBackend()
    result = SetMute(backend).run({"muted": bad})
    assert result.success is False
    assert backend.mute == []


# --- wifi ---


def test_set_wifi_active_et_desactive() -> None:
    backend = _FakeBackend()
    assert SetWifi(backend).run({"enabled": "on"}).success is True
    assert SetWifi(backend).run({"enabled": False}).success is True
    assert backend.wifi == [True, False]


def test_set_wifi_valeur_invalide_echoue() -> None:
    backend = _FakeBackend()
    result = SetWifi(backend).run({"enabled": "bof"})
    assert result.success is False
    assert backend.wifi == []


def test_set_wifi_erreur_backend_est_remontee() -> None:
    backend = _FakeBackend(error=RuntimeError("NetworkManager indisponible"))
    result = SetWifi(backend).run({"enabled": True})
    assert result.success is False
    assert "NetworkManager" in result.output


# --- registre ---


def test_registre_settings_tools_complet() -> None:
    assert set(SETTINGS_TOOLS) == {
        "set_brightness",
        "set_volume",
        "set_mute",
        "set_wifi",
    }
