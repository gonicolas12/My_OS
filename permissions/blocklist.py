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

Cette liste évoluera avec les jalons suivants (pacman, D-Bus, processus).
"""

from __future__ import annotations

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


_BLOCKERS: dict[str, tuple[Callable[[dict], bool], ...]] = {
    "delete_file": (_block_delete,),
    "move_file": (_block_move,),
    "write_file": (_block_write,),
    "create_file": (_block_write,),
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
