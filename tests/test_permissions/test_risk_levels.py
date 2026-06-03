"""Tests de la table statique des risques et de l'identification des chemins sensibles."""

# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison
from __future__ import annotations

import pytest

from permissions.risk_levels import (
    HOME_SENSITIVE_SUBPATHS,
    SYSTEM_SENSITIVE_ROOTS,
    TOOL_RISK_LEVELS,
    is_sensitive_path,
    sensitive_roots,
)


def test_chaque_outil_declare_un_niveau_valide() -> None:
    assert TOOL_RISK_LEVELS, "la table ne doit pas être vide"
    for tool_name, level in TOOL_RISK_LEVELS.items():
        assert isinstance(tool_name, str) and tool_name
        assert level in (0, 1, 2, 3), f"{tool_name} a un niveau invalide : {level}"


def test_outils_fichiers_jalon_2_declares() -> None:
    # Les outils du jalon 2 doivent figurer dans la table.
    attendus = {"read_file", "list_dir", "write_file", "move_file", "create_file", "delete_file"}
    assert attendus.issubset(TOOL_RISK_LEVELS.keys())


def test_lectures_sont_niveau_0() -> None:
    assert TOOL_RISK_LEVELS["read_file"] == 0
    assert TOOL_RISK_LEVELS["list_dir"] == 0


def test_suppression_est_au_moins_niveau_2() -> None:
    assert TOOL_RISK_LEVELS["delete_file"] >= 2


@pytest.mark.parametrize(
    "path",
    [
        "/etc",
        "/etc/passwd",
        "/boot/grub/grub.cfg",
        "/usr/bin/ls",
        "/var/lib/dbus/machine-id",
        "/sys/class/net",
        "/proc/1/status",
        "/root/.bashrc",
        "/opt/some-app/config",
    ],
)
def test_chemins_systeme_sont_sensibles(path: str) -> None:
    assert is_sensitive_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/x",
        "/home/alice/Documents/note.md",
        "/var/log/syslog",
        "/data/backup",
        "/srv/site/index.html",
    ],
)
def test_chemins_quelconques_ne_sont_pas_sensibles(path: str) -> None:
    assert is_sensitive_path(path) is False


def test_prefixe_partiel_ne_match_pas() -> None:
    # "/etcfoo" ne doit PAS matcher "/etc" (faux préfixe).
    assert is_sensitive_path("/etcfoo/bar") is False
    assert is_sensitive_path("/boots") is False


def test_normalisation_traversee_de_repertoire() -> None:
    # "/etc/../tmp/x" se normalise en "/tmp/x" — non sensible.
    assert is_sensitive_path("/etc/../tmp/x") is False
    # Inversement, "/tmp/../etc/passwd" devient "/etc/passwd" — sensible.
    assert is_sensitive_path("/tmp/../etc/passwd") is True


def test_chemins_sensibles_dans_le_home() -> None:
    home = "/home/alice"
    assert is_sensitive_path(f"{home}/.ssh/id_rsa", home=home) is True
    assert is_sensitive_path(f"{home}/.config/systemd/user/myosd.service", home=home) is True


def test_chemins_quelconques_du_home_ne_sont_pas_sensibles() -> None:
    home = "/home/alice"
    assert is_sensitive_path(f"{home}/Documents/note.md", home=home) is False
    assert is_sensitive_path(f"{home}/Downloads/x.zip", home=home) is False


def test_sensitive_roots_contient_tous_les_prefixes() -> None:
    roots = sensitive_roots(home="/home/alice")
    for prefix in SYSTEM_SENSITIVE_ROOTS:
        assert prefix in roots
    for sub in HOME_SENSITIVE_SUBPATHS:
        assert f"/home/alice/{sub}" in roots
