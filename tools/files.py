"""Outils fichiers du jalon 2.

Chacun hérite de :class:`tools.base_tool.BaseTool`, déclare son ``risk_level``
de base et surcharge :meth:`escalate` pour passer en niveau 2 quand un chemin
sensible est en jeu (cf. :func:`permissions.risk_levels.is_sensitive_path`).

Aucune vérification de permission n'est faite ici — c'est le rôle exclusif du
:mod:`permissions.policy_engine` (cf. CLAUDE.md invariant 4). Les outils
supposent qu'ils ont déjà été autorisés quand :meth:`run` est appelé.

Pas de ``subprocess(shell=True)`` : on utilise exclusivement ``pathlib`` et
``shutil`` (cf. SECURITY checklist §7).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from permissions.risk_levels import is_sensitive_path
from tools.base_tool import BaseTool, ToolResult

_MAX_READ_BYTES = 1_000_000  # 1 Mio — au-delà l'outil renvoie un message tronqué


def _err(message: str) -> ToolResult:
    """Construit un ToolResult d'échec lisible."""
    return ToolResult(success=False, output=message, reversible=False)


class ReadFile(BaseTool):
    """Lit le contenu d'un fichier texte."""

    name = "read_file"
    description = "Lit le contenu d'un fichier texte."
    risk_level = 0
    parameters = {"path": "str — chemin absolu du fichier à lire"}

    def affected_paths(self, args: dict) -> list[str]:
        path = args.get("path")
        return [str(path)] if isinstance(path, str) else []

    def run(self, args: dict) -> ToolResult:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return _err("read_file : argument 'path' manquant ou invalide")
        target = Path(path)
        if not target.exists():
            return _err(f"read_file : fichier introuvable : {path}")
        if not target.is_file():
            return _err(f"read_file : pas un fichier : {path}")
        try:
            data = target.read_bytes()
        except OSError as exc:
            return _err(f"read_file : {exc}")
        truncated = ""
        if len(data) > _MAX_READ_BYTES:
            data = data[:_MAX_READ_BYTES]
            truncated = f"\n[... contenu tronqué à {_MAX_READ_BYTES} octets ...]"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _err(f"read_file : contenu non-UTF8 : {path}")
        return ToolResult(success=True, output=text + truncated, reversible=False)


class ListDir(BaseTool):
    """Liste le contenu d'un répertoire (un nom par ligne, tri lexical)."""

    name = "list_dir"
    description = "Liste le contenu d'un répertoire."
    risk_level = 0
    parameters = {"path": "str — chemin absolu du répertoire à lister"}

    def affected_paths(self, args: dict) -> list[str]:
        path = args.get("path")
        return [str(path)] if isinstance(path, str) else []

    def run(self, args: dict) -> ToolResult:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return _err("list_dir : argument 'path' manquant ou invalide")
        target = Path(path)
        if not target.exists():
            return _err(f"list_dir : répertoire introuvable : {path}")
        if not target.is_dir():
            return _err(f"list_dir : pas un répertoire : {path}")
        try:
            entries = sorted(p.name for p in target.iterdir())
        except OSError as exc:
            return _err(f"list_dir : {exc}")
        return ToolResult(success=True, output="\n".join(entries), reversible=False)


class WriteFile(BaseTool):
    """Écrit (ou écrase) un fichier texte avec un contenu donné."""

    name = "write_file"
    description = "Écrit (ou écrase) un fichier avec le contenu fourni."
    risk_level = 1
    parameters = {
        "path": "str — chemin absolu du fichier à écrire",
        "content": "str — contenu textuel UTF-8",
    }

    def escalate(self, args: dict) -> int:
        path = args.get("path")
        if isinstance(path, str) and is_sensitive_path(path):
            return 2
        return self.risk_level

    def affected_paths(self, args: dict) -> list[str]:
        path = args.get("path")
        return [str(path)] if isinstance(path, str) else []

    def run(self, args: dict) -> ToolResult:
        path = args.get("path")
        content = args.get("content", "")
        if not isinstance(path, str) or not path:
            return _err("write_file : argument 'path' manquant ou invalide")
        if not isinstance(content, str):
            return _err("write_file : argument 'content' doit être une chaîne")
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return _err(f"write_file : {exc}")
        return ToolResult(
            success=True,
            output=f"Écrit {len(content)} caractères dans {path}",
            reversible=False,
        )


class CreateFile(BaseTool):
    """Crée un fichier vide. Échoue si le fichier existe déjà."""

    name = "create_file"
    description = "Crée un fichier vide. Refuse de remplacer un fichier existant."
    risk_level = 1
    parameters = {"path": "str — chemin absolu du fichier à créer"}

    def escalate(self, args: dict) -> int:
        path = args.get("path")
        if isinstance(path, str) and is_sensitive_path(path):
            return 2
        return self.risk_level

    def affected_paths(self, args: dict) -> list[str]:
        path = args.get("path")
        return [str(path)] if isinstance(path, str) else []

    def run(self, args: dict) -> ToolResult:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return _err("create_file : argument 'path' manquant ou invalide")
        target = Path(path)
        if target.exists():
            return _err(f"create_file : existe déjà : {path}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        except OSError as exc:
            return _err(f"create_file : {exc}")
        return ToolResult(success=True, output=f"Créé : {path}", reversible=False)


class MoveFile(BaseTool):
    """Déplace un fichier ou un dossier de ``src`` vers ``dst``."""

    name = "move_file"
    description = "Déplace un fichier ou un dossier."
    risk_level = 1
    parameters = {
        "src": "str — chemin absolu source",
        "dst": "str — chemin absolu destination",
    }

    def escalate(self, args: dict) -> int:
        src = args.get("src")
        dst = args.get("dst")
        if isinstance(src, str) and is_sensitive_path(src):
            return 2
        if isinstance(dst, str) and is_sensitive_path(dst):
            return 2
        return self.risk_level

    def affected_paths(self, args: dict) -> list[str]:
        paths: list[str] = []
        for key in ("src", "dst"):
            value = args.get(key)
            if isinstance(value, str):
                paths.append(value)
        return paths

    def run(self, args: dict) -> ToolResult:
        src = args.get("src")
        dst = args.get("dst")
        if not isinstance(src, str) or not src:
            return _err("move_file : argument 'src' manquant ou invalide")
        if not isinstance(dst, str) or not dst:
            return _err("move_file : argument 'dst' manquant ou invalide")
        source = Path(src)
        if not source.exists():
            return _err(f"move_file : source introuvable : {src}")
        destination = Path(dst)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        except (OSError, shutil.Error) as exc:
            return _err(f"move_file : {exc}")
        return ToolResult(
            success=True,
            output=f"Déplacé : {src} → {dst}",
            reversible=False,
        )


class DeleteFile(BaseTool):
    """Supprime un fichier. Ne supprime pas un répertoire (sécurité)."""

    name = "delete_file"
    description = "Supprime un fichier (refuse les répertoires)."
    risk_level = 2
    parameters = {"path": "str — chemin absolu du fichier à supprimer"}

    def affected_paths(self, args: dict) -> list[str]:
        path = args.get("path")
        return [str(path)] if isinstance(path, str) else []

    def run(self, args: dict) -> ToolResult:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return _err("delete_file : argument 'path' manquant ou invalide")
        target = Path(path)
        if not target.exists():
            return _err(f"delete_file : introuvable : {path}")
        if target.is_dir():
            return _err(
                f"delete_file : refuse de supprimer un répertoire : {path} "
                "(utilisez un outil dédié au jalon 3)"
            )
        try:
            target.unlink()
        except OSError as exc:
            return _err(f"delete_file : {exc}")
        return ToolResult(success=True, output=f"Supprimé : {path}", reversible=False)


# Registre des outils fichiers — utilisé par l'orchestrator pour résoudre
# un nom d'outil reçu du LLM vers son instance.
FILE_TOOLS: dict[str, BaseTool] = {
    tool.name: tool
    for tool in (
        ReadFile(),
        ListDir(),
        WriteFile(),
        CreateFile(),
        MoveFile(),
        DeleteFile(),
    )
}
