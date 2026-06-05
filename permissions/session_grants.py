"""Mémoire en RAM des autorisations utilisateur de la session courante.

Trois portées documentées dans :doc:`docs/INTERFACES.md` §1 :

* ``this_file`` — autorisation pour *ce fichier* uniquement.
* ``this_folder`` — autorisation pour toute action sur *ce dossier précis*
  (les sous-dossiers ne sont **pas** couverts, pour rester conservateur).
* ``session`` — autorisation pour cet outil, quels que soient les arguments,
  tant que le daemon tourne.

``approve_once`` ne crée pas de grant : c'est une approbation pour la requête
courante uniquement, gérée par :mod:`permissions.policy_engine` sans
persistance.

Pas de stockage disque (cf. docs/ARCHITECTURE.md §7) : tout est en mémoire et
réinitialisé au démarrage du daemon.
"""

from __future__ import annotations

import posixpath

VALID_SCOPES: tuple[str, ...] = ("this_file", "this_folder", "session")


def _norm(path: str) -> str:
    """Normalise un chemin en POSIX (résout les ``..`` textuellement)."""
    return posixpath.normpath(str(path).replace("\\", "/"))


class SessionGrants:
    """État en mémoire des grants accordés au cours de la session."""

    def __init__(self) -> None:
        self._file_grants: set[tuple[str, str]] = set()
        self._folder_grants: set[tuple[str, str]] = set()
        self._session_grants: set[str] = set()

    def grant(self, tool_name: str, scope: str, target: str | None = None) -> None:
        """Enregistre un grant.

        * ``scope="session"`` : ``target`` ignoré.
        * ``scope="this_file" | "this_folder"`` : ``target`` requis (chemin).
        """
        if scope == "session":
            self._session_grants.add(tool_name)
            return
        if scope not in VALID_SCOPES:
            raise ValueError(f"scope inconnu : {scope!r}")
        if target is None:
            raise ValueError(f"scope={scope!r} requiert un target")
        normalized = _norm(target)
        if scope == "this_file":
            self._file_grants.add((tool_name, normalized))
        else:  # this_folder
            self._folder_grants.add((tool_name, normalized))

    def is_granted(self, tool_name: str, paths: list[str]) -> bool:
        """``True`` si **tous** les ``paths`` sont couverts par un grant existant.

        Si un grant de portée ``session`` couvre l'outil, renvoie ``True``
        directement (les chemins n'importent pas). Sinon vérifie chaque chemin :
        couverture par ``this_file`` exact ou par ``this_folder`` égal au
        parent direct (pas de récursion).

        Sans ``paths`` et sans grant de session : renvoie ``False`` (rien à
        couvrir = rien à autoriser).
        """
        if tool_name in self._session_grants:
            return True
        if not paths:
            return False
        return all(self._path_granted(tool_name, p) for p in paths)

    def _path_granted(self, tool_name: str, path: str) -> bool:
        normalized = _norm(path)
        if (tool_name, normalized) in self._file_grants:
            return True
        parent = posixpath.dirname(normalized)
        if parent and (tool_name, parent) in self._folder_grants:
            return True
        return False

    def clear(self) -> None:
        """Vide tous les grants (utilisé en test ou réinit volontaire)."""
        self._file_grants.clear()
        self._folder_grants.clear()
        self._session_grants.clear()
