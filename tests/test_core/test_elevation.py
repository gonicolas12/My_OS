"""Tests du socle d'exécution système & d'élévation (:mod:`core.elevation`).

Sécurité prioritaire (cf. CLAUDE.md, SECURITY checklist §7) :

* l'argv est toujours une **liste** validée — une chaîne unique est refusée
  (porte d'injection sous un shell) ;
* ``elevate=True`` préfixe bien ``pkexec`` (élévation ponctuelle, par action) ;
* ``elevate=False`` ne préfixe **jamais** ``pkexec`` ;
* jamais de ``shell=True`` (vérifié sur le lanceur de production) ;
* timeout et binaire absent renvoient un résultat d'échec lisible, sans lever.
"""

# Les runners stub respectent la signature (argv, timeout) attendue par
# run_command même quand le test n'inspecte pas le timeout.
# pylint: disable=missing-function-docstring,unused-argument
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from core.elevation import PKEXEC, CommandResult, build_argv, run_command


def _fake_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_build_argv_sans_elevation_ne_prefixe_pas_pkexec() -> None:
    assert build_argv(["pacman", "-Ss", "vlc"], elevate=False) == [
        "pacman",
        "-Ss",
        "vlc",
    ]


def test_build_argv_avec_elevation_prefixe_pkexec() -> None:
    assert build_argv(["pacman", "-S", "vlc"], elevate=True) == [
        PKEXEC,
        "pacman",
        "-S",
        "vlc",
    ]


@pytest.mark.parametrize("bad", ["pacman -S vlc", "", None, 42, [], ["ok", ""]])
def test_build_argv_refuse_argv_invalide(bad: object) -> None:
    with pytest.raises(ValueError):
        build_argv(bad, elevate=False)  # type: ignore[arg-type]


def test_run_command_passe_argv_brut_sans_elevation() -> None:
    captured: dict[str, Any] = {}

    def runner(argv: list[str], timeout: float) -> Any:
        captured["argv"] = argv
        captured["timeout"] = timeout
        return _fake_completed(0, "ok", "")

    result = run_command(["pacman", "-Ss", "vlc"], runner=runner)
    assert captured["argv"] == ["pacman", "-Ss", "vlc"]
    assert result.ok is True
    assert result.stdout == "ok"


def test_run_command_avec_elevation_injecte_pkexec() -> None:
    captured: dict[str, Any] = {}

    def runner(argv: list[str], timeout: float) -> Any:
        captured["argv"] = argv
        return _fake_completed(0)

    run_command(["pacman", "-S", "vlc"], elevate=True, runner=runner)
    assert captured["argv"][0] == PKEXEC
    assert captured["argv"][1:] == ["pacman", "-S", "vlc"]


def test_run_command_remonte_le_code_retour() -> None:
    result = run_command(
        ["false"], runner=lambda argv, timeout: _fake_completed(1, "", "boom")
    )
    assert result.returncode == 1
    assert result.ok is False
    assert result.stderr == "boom"


def test_run_command_binaire_absent_renvoie_echec_lisible() -> None:
    def runner(argv: list[str], timeout: float) -> Any:
        raise FileNotFoundError(argv[0])

    result = run_command(["pkexec"], elevate=False, runner=runner)
    assert result.returncode == 127
    assert result.ok is False
    assert "introuvable" in result.stderr


def test_run_command_timeout_renvoie_echec_lisible() -> None:
    def runner(argv: list[str], timeout: float) -> Any:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    result = run_command(["pacman", "-Syu"], elevate=True, timeout=5, runner=runner)
    assert result.returncode == 124
    assert "délai dépassé" in result.stderr


def test_run_command_argv_invalide_leve_avant_tout_lancement() -> None:
    # La validation est faite par build_argv, donc même sans runner appelé.
    def runner(argv: list[str], timeout: float) -> Any:
        return _fake_completed()

    with pytest.raises(ValueError):
        run_command("rm -rf /", runner=runner)  # type: ignore[arg-type]


def test_command_result_ok_property() -> None:
    assert CommandResult(0, "", "").ok is True
    assert CommandResult(1, "", "").ok is False


def test_default_runner_n_utilise_jamais_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    # On intercepte subprocess.run pour vérifier shell=False (invariant SECURITY §7).
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _fake_completed(0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_command(["echo", "hello"])
    assert captured["kwargs"]["shell"] is False
    assert captured["argv"] == ["echo", "hello"]
