"""Source de vérité statique : niveaux de risque + chemins sensibles.

C'est *ici* que sont déclarés les niveaux de base par outil et la liste des
préfixes système considérés sensibles (déclenchant une escalade
``write/move/delete → niveau 2``). Le LLM n'a aucune voix au chapitre :
l'évaluation passe par :mod:`permissions.policy_engine` qui lit cette table
(cf. docs/SECURITY.md §4 invariants 1 et 2).

Les comparaisons utilisent la sémantique POSIX (le projet cible Linux) pour
rester déterministes quel que soit l'OS de développement.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

# Niveaux de base par outil (avant escalade par arguments).
TOOL_RISK_LEVELS: dict[str, int] = {
    # Fichiers (jalon 2)
    "read_file": 0,
    "list_dir": 0,
    "write_file": 1,
    "move_file": 1,
    "create_file": 1,
    "delete_file": 2,
    # Processus (jalon 3)
    "list_processes": 0,
    "kill_process": 2,
    # Paquets / pacman (jalon 3)
    "search_package": 0,
    "install_package": 1,  # niveau 1 (install) MAIS requires_elevation (root)
    "update_system": 2,
    "remove_package": 2,
    # Réglages système / D-Bus (jalon 3)
    "set_brightness": 1,
    "set_volume": 1,
    "set_mute": 1,
    "set_wifi": 1,
}

# Paquets dont la suppression rendrait le système inutilisable : la blocklist
# refuse ``remove_package`` sur ces noms (niveau 3, jamais exécuté). Liste
# volontairement conservatrice — on bloque le socle, pas chaque dépendance.
CRITICAL_PACKAGES: frozenset[str] = frozenset(
    {
        "base",
        "bash",
        "coreutils",
        "filesystem",
        "gcc-libs",
        "glibc",
        "linux",
        "linux-firmware",
        "pacman",
        "pacman-mirrorlist",
        "polkit",
        "systemd",
        "systemd-libs",
        "sudo",
        "util-linux",
    }
)

# Préfixes système toujours sensibles (liste « standard » choisie au jalon 2).
SYSTEM_SENSITIVE_ROOTS: tuple[str, ...] = (
    "/etc",
    "/boot",
    "/usr",
    "/var/lib",
    "/sys",
    "/proc",
    "/root",
    "/opt",
)

# Sous-chemins sensibles à l'intérieur du HOME utilisateur.
HOME_SENSITIVE_SUBPATHS: tuple[str, ...] = (
    ".ssh",
    ".config/systemd",
)


def _posix(path: str | Path) -> str:
    """Normalise une chaîne en chemin POSIX absolu non résolu."""
    return posixpath.normpath(str(path).replace("\\", "/"))


def sensitive_roots(home: str | Path | None = None) -> list[str]:
    """Renvoie la liste résolue des préfixes sensibles.

    ``home`` permet d'injecter un répertoire utilisateur (utile pour les
    tests) ; par défaut, ``~`` est expansé depuis l'environnement.
    """
    home_path = _posix(home) if home is not None else _posix(posixpath.expanduser("~"))
    home_path = home_path.rstrip("/")
    return [
        *SYSTEM_SENSITIVE_ROOTS,
        *[f"{home_path}/{sub}" for sub in HOME_SENSITIVE_SUBPATHS],
    ]


def is_sensitive_path(path: str | Path, home: str | Path | None = None) -> bool:
    """``True`` si ``path`` est exactement, ou est sous, un préfixe sensible.

    Les séquences ``..`` sont normalisées : ``"/etc/../tmp/x"`` devient
    ``"/tmp/x"`` et n'est donc pas considéré sensible.
    """
    normalized = _posix(path)
    for root in sensitive_roots(home):
        if normalized == root or normalized.startswith(root + "/"):
            return True
    return False
