"""Tests des outils de réglages système (:mod:`tools.system_settings`).

Backend **factice** injecté → on teste la logique d'outil (validation, bornage,
messages) sans toucher à D-Bus, pactl ni au matériel. Couvre aussi la
propagation d'erreur backend et l'absence d'élévation, et le rejeu de ``pactl``
pendant le démarrage du serveur son, puis sa repose après la restauration de
WirePlumber (commande et horloge simulées).
"""

# Comparer explicitement avec [] (aucun appel backend) est plus parlant en test.
# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison
from __future__ import annotations

import pytest

from core.elevation import CommandResult
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


# --- pactl : serveur son qui démarre (backend de production, commande simulée) ---


class _Clock:
    """Horloge factice : ``sleep`` avance ``monotonic`` sans attendre."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _pactl_double(
    monkeypatch: pytest.MonkeyPatch,
    failures: int,
    code: int = 1,
    error: str = "Failed to get sink information: No such entity",
) -> tuple[list[list[str]], _Clock]:
    """``run_command`` simulé : ``failures`` échecs puis succès. Renvoie (appels, horloge)."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> CommandResult:
        calls.append(argv)
        if len(calls) <= failures:
            return CommandResult(returncode=code, stdout="", stderr=error)
        return CommandResult(returncode=0, stdout="", stderr="")

    clock = _Clock()
    monkeypatch.setattr("core.elevation.run_command", fake_run)
    monkeypatch.setattr("tools.system_settings.time", clock)
    return calls, clock


def test_pactl_rejoue_tant_que_la_sortie_audio_n_existe_pas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Vu en VM (ISO v1.3) : PipeWire démarre à la demande ; la 1re commande de la
    # session échouait (« No such entity ») avant que la sortie audio n'existe.
    # Réussie, elle est reposée deux fois (après 0,5 puis 1 s) : cf. le test suivant.
    calls, clock = _pactl_double(monkeypatch, failures=3)
    assert SetVolume().run({"percent": 30}).success is True
    assert calls == [["pactl", "set-sink-volume", "@DEFAULT_SINK@", "30%"]] * 6
    assert clock.sleeps == [0.1, 0.1, 0.1, 0.5, 1.0]


def test_pactl_repose_le_reglage_que_la_restauration_ecrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Vu en VM (ISO v1.8, PipeWire 1.6.11 et WirePlumber 0.5.19) : la sortie apparaît,
    # « rétablir le son » réussit, puis WirePlumber restaure l'état mémorisé de la
    # sortie (son coupé) et l'écrase ; l'outil avait pourtant répondu « Son rétabli ».
    clock = _Clock()
    sink = {"exists": False, "muted": True, "restored": False}
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> CommandResult:
        calls.append(argv)
        sink["exists"] = clock.now >= 0.1  # sortie créée 0,1 s après le démarrage
        if not sink["exists"]:
            return CommandResult(returncode=1, stdout="", stderr="No such entity")
        sink["muted"] = argv[-1] == "1"
        return CommandResult(returncode=0, stdout="", stderr="")

    def sleep(seconds: float) -> None:
        clock.sleeps.append(seconds)
        clock.now += seconds
        if sink["exists"] and not sink["restored"] and clock.now >= 0.25:
            sink["muted"] = True  # état mémorisé, restauré une fois après la création
            sink["restored"] = True

    monkeypatch.setattr(clock, "sleep", sleep)
    monkeypatch.setattr("core.elevation.run_command", fake_run)
    monkeypatch.setattr("tools.system_settings.time", clock)
    assert SetMute().run({"muted": False}).success is True
    clock.sleep(2.0)  # le temps passe : la restauration a eu lieu au plus tard ici
    assert sink["restored"] is True
    assert sink["muted"] is False
    assert len(calls) == 4  # 1 échec, la réussite, puis deux fois reposée


def test_pactl_serveur_deja_pret_aucun_rejeu(monkeypatch: pytest.MonkeyPatch) -> None:
    # Serveur son déjà en marche : sa sortie et son état restauré existent, la
    # commande ne passe qu'une fois et l'outil répond aussitôt.
    calls, clock = _pactl_double(monkeypatch, failures=0)
    assert SetMute().run({"muted": False}).success is True
    assert calls == [["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"]]
    assert clock.sleeps == []


def test_set_mute_rejoue_aussi(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, _clock = _pactl_double(monkeypatch, failures=1)
    assert SetMute().run({"muted": True}).success is True
    assert calls == [["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"]] * 4


def test_pactl_echec_d_une_repose_remonte(monkeypatch: pytest.MonkeyPatch) -> None:
    # La sortie disparaît entre la réussite et la repose : l'échec est rapporté.
    codes = iter([1, 0, 1])
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> CommandResult:
        calls.append(argv)
        code = next(codes)
        return CommandResult(returncode=code, stdout="", stderr="No such entity" * code)

    monkeypatch.setattr("core.elevation.run_command", fake_run)
    monkeypatch.setattr("tools.system_settings.time", _Clock())
    result = SetVolume().run({"percent": 30})
    assert result.success is False
    assert "No such entity" in result.output
    assert len(calls) == 3


def test_pactl_echec_persistant_remonte_apres_le_delai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, clock = _pactl_double(monkeypatch, failures=10_000)
    result = SetVolume().run({"percent": 30})
    assert result.success is False
    assert "No such entity" in result.output
    assert 5.0 <= clock.now <= 5.1 + 1e-9
    assert len(calls) == len(clock.sleeps) + 1


@pytest.mark.parametrize("code", [124, 127])
def test_pactl_introuvable_ou_bloque_echoue_aussitot(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    calls, clock = _pactl_double(monkeypatch, failures=1, code=code, error="pactl ?")
    assert SetVolume().run({"percent": 30}).success is False
    assert len(calls) == 1
    assert clock.sleeps == []


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
