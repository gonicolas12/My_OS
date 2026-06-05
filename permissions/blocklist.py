"""Blocklist absolue — niveau 3, jamais franchissable.

Vérifiée **en premier** par :mod:`permissions.policy_engine`. Aucun grant
utilisateur, aucune confirmation, ne peut faire passer une action listée ici
(cf. docs/SECURITY.md §4 invariant 3).

Au jalon 2 (outils fichiers), on bloque :

* la suppression / déplacement d'une **racine** ou d'un répertoire système
  *lui-même* (``/``, ``/etc``, ``/boot``, ``/home``, …) — la suppression d'un
  *fichier dans* ces répertoires reste possible en niveau 2 (confirmation
  renforcée), c'est seulement la racine qui est interdite ;
* l'écriture / création directe d'une cible critique du bootloader
  (``/boot/grub/grub.cfg``, ``/boot/vmlinuz*``, ``/boot/initramfs*``, ``/boot/efi*``).

Au jalon 3, on ajoute :

* ``kill_process`` sur PID ≤ 1 (init/systemd, kernel) ou sur le daemon
  lui-même (``os.getpid()``) — se tuer ou tuer init n'est jamais permis.

Cette liste évoluera encore (pacman ``-R`` sur paquets critiques, etc.).
"""

from __future__ import annotations

import os
import posixpath
from collections.abc import Callable

from permissions.risk_levels import SYSTEM_SENSITIVE_ROOTS

# Cibles supplémentaires (au-delà de SYSTEM_SENSITIVE_ROOTS) dont la racine
# ne doit jamais être supprimée ou déplacée.
_ROOT_LIKE_EXTRA: tuple[str, ...] = ("/", "/home")

# Fichiers bootloader à protéger en écriture/création.
_BOOTLOADER_FILES: tuple[str, ...] = ("/boot/grub/grub.cfg",)
_BOOTLOADER_PREFIXES: tuple[str, ...] = (
    "/boot/vmlinuz",
    "/boot/initramfs",
    "/boot/efi",
    "/boot/EFI",
)


def _norm(path: object) -> str:
    """Normalise en chemin POSIX (les ``..`` sont résolus textuellement)."""
    if not isinstance(path, str):
        return ""
    return posixpath.normpath(path.replace("\\", "/"))


def _is_critical_root(path: object) -> bool:
    """``True`` si ``path`` désigne exactement la racine ou un préfixe sensible.

    On ne bloque PAS un fichier *à l'intérieur* — seulement le répertoire racine
    lui-même (ex. ``/etc`` mais pas ``/etc/passwd``).
    """
    normalized = _norm(path)
    if not normalized:
        return False
    blocked = (*SYSTEM_SENSITIVE_ROOTS, *_ROOT_LIKE_EXTRA)
    return normalized in blocked


def _is_bootloader_target(path: object) -> bool:
    """``True`` si ``path`` est un fichier critique du bootloader."""
    normalized = _norm(path)
    if not normalized:
        return False
    if normalized in _BOOTLOADER_FILES:
        return True
    return any(normalized.startswith(prefix) for prefix in _BOOTLOADER_PREFIXES)


def _block_delete(args: dict) -> bool:
    return _is_critical_root(args.get("path"))


def _block_move(args: dict) -> bool:
    # On bloque si la source OU la destination est une racine critique.
    return _is_critical_root(args.get("src")) or _is_critical_root(args.get("dst"))


def _block_write(args: dict) -> bool:
    return _is_bootloader_target(args.get("path"))


def _coerce_pid(value: object) -> int | None:
    """Convertit un PID (int ou chaîne décimale) en entier ; ``None`` si invalide."""
    if isinstance(value, bool):  # bool est un int : on l'exclut explicitement
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _block_kill(args: dict) -> bool:
    """Bloque la terminaison d'init/kernel (PID ≤ 1) ou du daemon lui-même.

    Un PID invalide (non parsable) n'est *pas* bloqué ici : c'est l'outil qui
    le rejettera proprement. On bloque uniquement les cibles dont on est sûr
    qu'elles sont interdites.
    """
    pid = _coerce_pid(args.get("pid"))
    if pid is None:
        return False
    return pid <= 1 or pid == os.getpid()


_BLOCKERS: dict[str, tuple[Callable[[dict], bool], ...]] = {
    "delete_file": (_block_delete,),
    "move_file": (_block_move,),
    "write_file": (_block_write,),
    "create_file": (_block_write,),
    "kill_process": (_block_kill,),
}


def is_blocked(tool_name: str, args: dict) -> bool:
    """``True`` si la combinaison (outil, arguments) est absolument interdite.

    Pour les outils non listés ci-dessus, renvoie ``False`` — la politique
    standard du ``policy_engine`` s'applique alors normalement.
    """
    for predicate in _BLOCKERS.get(tool_name, ()):
        if predicate(args):
            return True
    return False
