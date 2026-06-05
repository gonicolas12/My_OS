"""Outils processus du jalon 3 (psutil).

Deux outils, sur le patron de :mod:`tools.files` :

* ``list_processes`` — **niveau 0** (lecture pure) : répond à « qu'est-ce qui
  mange ma RAM / mon CPU ? » en listant les processus triés par mémoire (défaut)
  ou CPU ;
* ``kill_process`` — **niveau 2** (action destructrice) : termine un processus
  par PID. La blocklist (cf. :mod:`permissions.blocklist`) interdit en amont de
  viser PID ≤ 1 (init/kernel) ou le daemon lui-même.

Aucune vérification de permission ici (rôle exclusif du ``policy_engine``).
``kill_process`` n'auto-élève **pas** : tuer un processus d'un autre
utilisateur échoue avec un message clair (root requis) plutôt que de déclencher
silencieusement polkit — l'élévation polkit est réservée à pacman (jalon 3,
``tools/packages.py``).

Le provider psutil est **injectable** (import paresseux) : les tests passent un
faux provider déterministe, sans dépendre des processus réels de la machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from tools.base_tool import BaseTool, ToolResult

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50
_VALID_SORT = ("memory", "cpu")


@dataclass
class ProcessInfo:
    """Instantané minimal d'un processus (donnée affichée à l'utilisateur)."""

    pid: int
    name: str
    username: str
    memory_percent: float
    cpu_percent: float


class ProcessProvider(Protocol):
    """Source de données processus, abstraite pour rester testable."""

    def list_processes(self) -> list[ProcessInfo]:
        """Liste les processus visibles (les inaccessibles sont ignorés)."""

    def pid_exists(self, pid: int) -> bool:
        """``True`` si un processus de ce PID existe."""

    def kill(self, pid: int) -> None:
        """Termine le processus (SIGTERM).

        Lève :class:`PermissionError` si l'appelant n'a pas le droit (processus
        d'un autre utilisateur / root), :class:`ProcessLookupError` si le PID a
        disparu entre-temps.
        """


class _PsutilProvider:
    """Provider de production basé sur ``psutil`` (import paresseux)."""

    def list_processes(self) -> list[ProcessInfo]:
        import psutil  # pylint: disable=import-outside-toplevel

        infos: list[ProcessInfo] = []
        for proc in psutil.process_iter(
            ["pid", "name", "username", "memory_percent", "cpu_percent"]
        ):
            try:
                info = proc.info
                infos.append(
                    ProcessInfo(
                        pid=int(info["pid"]),
                        name=str(info.get("name") or "?"),
                        username=str(info.get("username") or "?"),
                        memory_percent=float(info.get("memory_percent") or 0.0),
                        cpu_percent=float(info.get("cpu_percent") or 0.0),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, TypeError):
                continue  # le processus a disparu / est inaccessible : on l'ignore
        return infos

    def pid_exists(self, pid: int) -> bool:
        import psutil  # pylint: disable=import-outside-toplevel

        return psutil.pid_exists(pid)

    def kill(self, pid: int) -> None:
        import psutil  # pylint: disable=import-outside-toplevel

        try:
            psutil.Process(pid).terminate()
        except psutil.NoSuchProcess as exc:
            raise ProcessLookupError(str(exc)) from exc
        except psutil.AccessDenied as exc:
            raise PermissionError(str(exc)) from exc


def _err(message: str) -> ToolResult:
    """Construit un ToolResult d'échec lisible."""
    return ToolResult(success=False, output=message, reversible=False)


def _coerce_pid(value: object) -> int | None:
    """Convertit un PID (int ou chaîne décimale) en entier ; ``None`` si invalide."""
    if isinstance(value, bool):  # bool est un int en Python : on l'exclut
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


class ListProcesses(BaseTool):
    """Liste les processus triés par consommation (mémoire par défaut)."""

    name = "list_processes"
    description = (
        "Liste les processus en cours, triés par mémoire (défaut) ou CPU. "
        "Utile pour voir ce qui consomme les ressources."
    )
    risk_level = 0
    parameters = {
        "sort_by": "str — 'memory' (défaut) ou 'cpu'",
        "limit": "int — nombre de processus à afficher (défaut 10, max 50)",
    }

    def __init__(self, provider: ProcessProvider | None = None) -> None:
        self._provider: ProcessProvider = provider or _PsutilProvider()

    def run(self, args: dict) -> ToolResult:
        sort_by = args.get("sort_by", "memory")
        if sort_by not in _VALID_SORT:
            sort_by = "memory"
        limit = _coerce_pid(args.get("limit"))
        if limit is None or limit <= 0:
            limit = _DEFAULT_LIMIT
        limit = min(limit, _MAX_LIMIT)

        try:
            processes = self._provider.list_processes()
        except OSError as exc:
            return _err(f"list_processes : {exc}")

        key = (
            (lambda p: p.cpu_percent)
            if sort_by == "cpu"
            else (lambda p: p.memory_percent)
        )
        processes.sort(key=key, reverse=True)
        top = processes[:limit]

        header = f"Top {len(top)} processus par {sort_by} :"
        lines = [
            f"{p.pid:>7}  {p.memory_percent:5.1f}% RAM  "
            f"{p.cpu_percent:5.1f}% CPU  {p.name} ({p.username})"
            for p in top
        ]
        return ToolResult(
            success=True, output="\n".join([header, *lines]), reversible=False
        )


class KillProcess(BaseTool):
    """Termine un processus par son PID (SIGTERM)."""

    name = "kill_process"
    description = "Termine un processus identifié par son PID."
    risk_level = 2
    parameters = {"pid": "int — identifiant du processus à terminer"}

    def __init__(self, provider: ProcessProvider | None = None) -> None:
        self._provider: ProcessProvider = provider or _PsutilProvider()

    def run(self, args: dict) -> ToolResult:
        pid = _coerce_pid(args.get("pid"))
        if pid is None:
            return _err("kill_process : argument 'pid' manquant ou invalide")
        if pid <= 1 or pid == os.getpid():
            # Défense en profondeur : la blocklist a déjà dû refuser, mais on ne
            # s'auto-tue pas et on ne touche pas à init même si elle est contournée.
            return _err(f"kill_process : PID protégé, refusé : {pid}")
        if not self._provider.pid_exists(pid):
            return _err(f"kill_process : aucun processus avec le PID {pid}")
        try:
            self._provider.kill(pid)
        except PermissionError:
            return _err(
                f"kill_process : permission refusée pour le PID {pid} "
                "(processus d'un autre utilisateur ou root — nécessiterait des "
                "privilèges administrateur)"
            )
        except ProcessLookupError:
            return _err(
                f"kill_process : le PID {pid} a disparu avant l'envoi du signal"
            )
        except OSError as exc:
            return _err(f"kill_process : {exc}")
        return ToolResult(
            success=True, output=f"Signal d'arrêt envoyé au PID {pid}", reversible=False
        )


# Registre des outils processus — fusionné avec les autres registres dans le
# daemon (cf. daemon/myosd.py).
PROCESS_TOOLS: dict[str, BaseTool] = {
    tool.name: tool
    for tool in (
        ListProcesses(),
        KillProcess(),
    )
}
