"""Fichiers de session de l'ISO, lus comme du texte (tourne partout, Windows compris) :
l'unité de myosd attend bien le marqueur que pose ``myos-user-firstrun``, et
``~/.xprofile`` les enchaîne dans le bon ordre. Le comportement du script lui-même est
testé dans ``test_firstrun.py``.
"""

# pylint: disable=missing-function-docstring
from __future__ import annotations

import re
from pathlib import Path

_AIROOTFS = Path(__file__).resolve().parents[2] / "packaging" / "archiso" / "airootfs"
_SCRIPT = _AIROOTFS / "usr" / "local" / "bin" / "myos-user-firstrun"
_UNIT = _AIROOTFS / "etc" / "skel" / ".config" / "systemd" / "user" / "myosd.service"
_XPROFILE = _AIROOTFS / "etc" / "skel" / ".xprofile"

# Affectation de premier niveau du script : nom="valeur".
_ASSIGNMENT = re.compile(r'^([a-z_]+)="([^"]*)"$')


def _script_path(name: str) -> str:
    """Valeur de la variable ``name`` du script, ``$base`` & co remplacés (``$HOME``
    reste tel quel)."""
    values: dict[str, str] = {}
    for line in _SCRIPT.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            value = match.group(2)
            for known, resolved in values.items():
                value = value.replace(f"${known}", resolved)
            values[match.group(1)] = value
    return values[name]


def _commands(path: Path) -> list[str]:
    """Lignes de commande d'un script shell (sans commentaires ni lignes vides)."""
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def test_myosd_attend_le_marqueur_que_pose_le_script() -> None:
    mark = _script_path("mark")
    assert mark == "$HOME/.local/share/my_os/.firstrun-done"
    conditions = [
        line.split("=", 1)[1]
        for line in _UNIT.read_text(encoding="utf-8").splitlines()
        if line.startswith("ConditionPathExists=")
    ]
    # %h : le home de l'utilisateur dont le gestionnaire systemd lance l'unité.
    assert conditions == [mark.replace("$HOME", "%h")]


def test_xprofile_environnement_puis_script_puis_myosd() -> None:
    commands = _commands(_XPROFILE)
    import_env = commands.index(
        "systemctl --user import-environment DISPLAY XAUTHORITY"
    )
    firstrun = commands.index("/usr/local/bin/myos-user-firstrun")
    start = commands.index("systemctl --user start myosd.service")
    # DISPLAY et XAUTHORITY avant le script : sa tâche de fond démarre myosd et montre
    # des avis du bureau ; myosd après : sans effet tant que le marqueur manque.
    assert import_env < firstrun < start
