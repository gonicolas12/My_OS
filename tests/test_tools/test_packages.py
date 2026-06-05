"""Tests des outils paquets (:mod:`tools.packages`).

Runner **injecté** qui capture l'argv → on vérifie la commande pacman exacte,
la présence (ou non) de ``pkexec``, et surtout la **validation des noms** qui
ferme l'injection d'arguments. Aucun pacman réel n'est lancé.
"""

# Le runner stub respecte la signature (argv, timeout) attendue par run_command ;
# comparer explicitement avec [] (aucun lancement) est plus parlant en test.
# pylint: disable=missing-function-docstring,unused-argument,use-implicit-booleaness-not-comparison
from __future__ import annotations

import subprocess
from typing import Any

import pytest

from tools.packages import (
    PACKAGE_TOOLS,
    InstallPackage,
    RemovePackage,
    SearchPackage,
    UpdateSystem,
)


class _RecordingRunner:
    """Runner injectable : mémorise l'argv et renvoie un résultat scriptable."""

    def __init__(
        self, returncode: int = 0, stdout: str = "ok", stderr: str = ""
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float) -> Any:
        self.calls.append(argv)
        return subprocess.CompletedProcess(
            args=argv,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


# --- search_package (niveau 0, sans root) ---


def test_search_package_est_niveau_0_sans_elevation() -> None:
    tool = SearchPackage()
    assert tool.risk_level == 0
    assert tool.requires_elevation({"query": "vlc"}) is False


def test_search_package_construit_la_bonne_commande_sans_pkexec() -> None:
    runner = _RecordingRunner(stdout="extra/vlc 3.0 ...")
    result = SearchPackage(runner).run({"query": "vlc"})
    assert result.success is True
    assert runner.calls == [["pacman", "-Ss", "vlc"]]  # pas de pkexec


def test_search_package_sans_resultat_reste_un_succes() -> None:
    runner = _RecordingRunner(returncode=1, stdout="", stderr="")
    result = SearchPackage(runner).run({"query": "paquetinexistant"})
    assert result.success is True
    assert "Aucun paquet" in result.output


@pytest.mark.parametrize("bad", ["-rf", "vlc; rm -rf /", "vlc foo", "", "../x", None])
def test_search_package_requete_invalide_ne_lance_rien(bad: object) -> None:
    runner = _RecordingRunner()
    result = SearchPackage(runner).run({"query": bad})
    assert result.success is False
    assert runner.calls == []  # validation AVANT tout lancement


# --- install_package (niveau 1 + élévation) ---


def test_install_package_est_niveau_1_avec_elevation() -> None:
    tool = InstallPackage()
    assert tool.risk_level == 1
    assert tool.requires_elevation({"name": "vlc"}) is True


def test_install_package_construit_pkexec_pacman_s() -> None:
    runner = _RecordingRunner()
    result = InstallPackage(runner).run({"name": "vlc"})
    assert result.success is True
    assert runner.calls == [["pkexec", "pacman", "-S", "--noconfirm", "vlc"]]


@pytest.mark.parametrize(
    "bad",
    ["-rf", "vlc; rm", "vlc foo", "VLC", "", "/etc/passwd", "--root=/", None, 42],
)
def test_install_package_nom_invalide_ne_lance_rien(bad: object) -> None:
    runner = _RecordingRunner()
    result = InstallPackage(runner).run({"name": bad})
    assert result.success is False
    assert runner.calls == []


def test_install_package_echec_pacman_remonte_stderr() -> None:
    runner = _RecordingRunner(returncode=1, stdout="", stderr="target not found: vlc")
    result = InstallPackage(runner).run({"name": "vlc"})
    assert result.success is False
    assert "target not found" in result.output


def test_install_package_accepte_les_noms_valides_courants() -> None:
    runner = _RecordingRunner()
    for name in ("vlc", "python-pip", "gtk3", "lib32-glibc", "g++", "p7zip"):
        assert InstallPackage(runner).run({"name": name}).success is True


# --- remove_package (niveau 2 + élévation) ---


def test_remove_package_est_niveau_2_avec_elevation() -> None:
    tool = RemovePackage()
    assert tool.risk_level == 2
    assert tool.requires_elevation({"name": "vlc"}) is True


def test_remove_package_construit_pkexec_pacman_rns() -> None:
    runner = _RecordingRunner()
    RemovePackage(runner).run({"name": "vlc"})
    assert runner.calls == [["pkexec", "pacman", "-Rns", "--noconfirm", "vlc"]]


@pytest.mark.parametrize("bad", ["-rf", "vlc; rm", "VLC", "", None])
def test_remove_package_nom_invalide_ne_lance_rien(bad: object) -> None:
    runner = _RecordingRunner()
    result = RemovePackage(runner).run({"name": bad})
    assert result.success is False
    assert runner.calls == []


# --- update_system (niveau 2 + élévation) ---


def test_update_system_est_niveau_2_avec_elevation() -> None:
    tool = UpdateSystem()
    assert tool.risk_level == 2
    assert tool.requires_elevation({}) is True


def test_update_system_construit_pkexec_pacman_syu() -> None:
    runner = _RecordingRunner()
    UpdateSystem(runner).run({})
    assert runner.calls == [["pkexec", "pacman", "-Syu", "--noconfirm"]]


# --- registre ---


def test_registre_package_tools_complet() -> None:
    assert set(PACKAGE_TOOLS) == {
        "search_package",
        "install_package",
        "remove_package",
        "update_system",
    }
