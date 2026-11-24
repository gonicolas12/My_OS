"""Tests du socle d'exécution système & d'élévation (:mod:`core.elevation`).

Sécurité prioritaire (cf. CLAUDE.md, SECURITY checklist §7) :

* l'argv est toujours une **liste** validée — une chaîne unique est refusée
  (porte d'injection sous un shell) ;
* ``elevate=True`` préfixe bien ``pkexec`` (élévation ponctuelle, par action) ;
* ``elevate=False`` ne préfixe **jamais** ``pkexec`` ;
* jamais de ``shell=True`` (vérifié sur le lanceur de production) ;
* timeout et binaire absent renvoient un résultat d'échec lisible, sans lever ;
* un refus de polkit (codes 126 et 127 de ``pkexec``, texte anglais en dur) est
  rendu dans la langue de My_OS, sans rien changer d'autre (code, échec).
"""

# Les runners stub respectent la signature (argv, timeout) attendue par
# run_command même quand le test n'inspecte pas le timeout.
# pylint: disable=missing-function-docstring,unused-argument
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from core.elevation import (
    CANCELLED_RETURNCODE,
    PKEXEC,
    CommandResult,
    build_argv,
    run_command,
)
from core.i18n import use_language


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


def test_lancement_sans_tube_pour_une_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Une application lancée hériterait des tubes de capture : subprocess.run
    # attendrait leur fermeture jusqu'au délai (vu en VM avec gio launch).
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return _fake_completed(0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(["gio", "launch", "/x.desktop"], detach_output=True)
    kwargs = captured["kwargs"]
    assert kwargs["shell"] is False
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert "capture_output" not in kwargs
    assert result.ok and result.stdout == ""


# --- refus de polkit : pkexec écrit en anglais, en dur (polkit 128, pkexec.c) ---

# Sorties exactes de pkexec : fenêtre d'authentification fermée (code 126), puis
# autorisation refusée (code 127, laissé par défaut, suivi d'une ligne vide et d'une
# phrase de plus).
_DISMISSED = "Error executing command as another user: Request dismissed\n"
_NOT_AUTHORIZED = (
    "Error executing command as another user: Not authorized\n"
    "\n"
    "This incident has been reported.\n"
)
_REFUSALS = [
    (
        126,
        _DISMISSED,
        "demande d'autorisation fermée sans réponse : la commande n'a pas été lancée",
        "authorization request closed without an answer: the command was not run",
    ),
    (
        127,
        _NOT_AUTHORIZED,
        "autorisation refusée : la commande n'a pas été lancée",
        "authorization denied: the command was not run",
    ),
]


@pytest.mark.parametrize(("code", "stderr", "french", "english"), _REFUSALS)
def test_refus_de_polkit_dans_la_langue_de_my_os(
    code: int, stderr: str, french: str, english: str
) -> None:
    def runner(argv: list[str], timeout: float) -> Any:
        return _fake_completed(code, "", stderr)

    argv = ["pacman", "-S", "--noconfirm", "vlc"]
    result = run_command(argv, elevate=True, runner=runner)
    # Seul le texte change : même code, toujours un échec.
    assert (result.returncode, result.ok, result.stderr) == (code, False, french)
    with use_language("en"):
        result = run_command(argv, elevate=True, runner=runner)
    assert (result.returncode, result.ok, result.stderr) == (code, False, english)


@pytest.mark.parametrize(
    ("code", "stderr", "elevate"),
    [
        # pkexec ne trouve pas le programme : 127 aussi, mais pas un refus.
        (127, "Cannot run program pacman: No such file or directory\n", True),
        # Autre échec de pkexec, hors des deux refus traduits.
        (
            127,
            (
                "Error executing command as another user: "
                "No authentication agent found.\n"
            ),
            True,
        ),
        # Le texte d'un refus sans son code (sortie de la commande lancée) : inchangé.
        (1, _DISMISSED, True),
        (127, _DISMISSED, True),
        (126, _NOT_AUTHORIZED, True),
        # Sans élévation, aucune sortie ne vient de pkexec.
        (126, _DISMISSED, False),
        (127, _NOT_AUTHORIZED, False),
        # Échec ordinaire de pacman.
        (1, "error: target not found: vlc\n", True),
    ],
)
def test_autres_sorties_laissees_telles_quelles(
    code: int, stderr: str, elevate: bool
) -> None:
    result = run_command(
        ["pacman", "-S", "vlc"],
        elevate=elevate,
        runner=lambda argv, timeout: _fake_completed(code, "", stderr),
    )
    assert (result.returncode, result.stderr) == (code, stderr)


def test_pkexec_absent_reste_commande_introuvable() -> None:
    # Code 127 de run_command lui-même : pas un refus de polkit.
    def runner(argv: list[str], timeout: float) -> Any:
        raise FileNotFoundError(argv[0])

    result = run_command(["pacman", "-S", "vlc"], elevate=True, runner=runner)
    assert result.returncode == 127
    assert "introuvable" in result.stderr
    assert "autorisation" not in result.stderr


def _faux_pkexec(directory: Path, code: int, stderr: str) -> Path:
    """Exécutable ``pkexec`` qui note ses arguments, écrit ``stderr`` et sort en
    ``code`` : le vrai lanceur (``subprocess.run``) le trouve dans le PATH."""
    script = directory / "pkexec"
    script.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$(dirname "$0")/argv"\n'
        f"cat >&2 <<'FIN'\n{stderr}FIN\n"
        f"exit {code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


@pytest.mark.skipif(sys.platform == "win32", reason="faux pkexec : script POSIX")
@pytest.mark.parametrize(
    ("code", "stderr", "french", "english"),
    [
        *_REFUSALS,
        (127, "Cannot run program pacman: No such file or directory\n", None, None),
    ],
)
def test_faux_pkexec_par_le_vrai_lanceur(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    stderr: str,
    french: str | None,
    english: str | None,
) -> None:
    _faux_pkexec(tmp_path, code, stderr)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    argv = ["pacman", "-S", "--noconfirm", "vlc"]
    result = run_command(argv, elevate=True)
    assert (tmp_path / "argv").read_text(encoding="utf-8").split() == argv
    assert (result.returncode, result.ok) == (code, False)
    assert result.stderr == (french or stderr)
    with use_language("en"):
        result = run_command(argv, elevate=True)
    assert result.stderr == (english or stderr)


# ---- annulation (contrôle de l'ordinateur arrêté pendant une frappe) -------------

_SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


def test_annulation_tue_la_commande_en_cours() -> None:
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    result = run_command(_SLEEPER, timeout=20, cancel=cancel)
    elapsed = time.monotonic() - started
    assert result.cancelled and not result.ok
    assert elapsed < 5  # et non les 30 s de la commande
    assert result.returncode == CANCELLED_RETURNCODE
    assert "annulée" in result.stderr


def test_annulation_avant_le_lancement_ne_lance_rien() -> None:
    cancel = threading.Event()
    cancel.set()
    calls: list = []

    def runner(argv: list[str], timeout: float) -> Any:
        calls.append(argv)
        return _fake_completed(0)

    result = run_command(["xdotool", "type", "x"], runner=runner, cancel=cancel)
    assert result.cancelled and calls == []
    with use_language("en"):
        assert "cancelled" in run_command(["true"], runner=runner, cancel=cancel).stderr


def test_commande_annulable_qui_finit_normalement() -> None:
    result = run_command(
        [sys.executable, "-c", "print('fini')"], timeout=20, cancel=threading.Event()
    )
    assert result.ok and not result.cancelled
    assert result.stdout.strip() == "fini"


def test_commande_annulable_garde_son_delai() -> None:
    result = run_command(_SLEEPER, timeout=0.5, cancel=threading.Event())
    assert (result.returncode, result.cancelled) == (124, False)


def test_runner_injecte_annulation_pendant_la_commande() -> None:
    cancel = threading.Event()

    def killed(argv: list[str], timeout: float) -> Any:
        cancel.set()  # annulée pendant la commande, qui finit en échec (tuée)
        return _fake_completed(-9)

    def finished(argv: list[str], timeout: float) -> Any:
        cancel.set()  # annulée juste après une fin normale : rien n'est perdu
        return _fake_completed(0, "ok")

    assert run_command(["xdotool", "key", "a"], runner=killed, cancel=cancel).cancelled
    cancel.clear()
    result = run_command(["xdotool", "key", "a"], runner=finished, cancel=cancel)
    assert result.ok and not result.cancelled


def test_sans_annulation_le_lanceur_reste_subprocess_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Les commandes système (pacman…) ne passent pas par le lanceur annulable.
    seen: dict = {}

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        seen["kwargs"] = kwargs
        return _fake_completed(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_command(["pacman", "-Q"]).ok
    assert seen["kwargs"]["shell"] is False
