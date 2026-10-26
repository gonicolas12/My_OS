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

from permissions.risk_levels import COMPUTER_TOOLS

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
        return bool(parent) and (tool_name, parent) in self._folder_grants

    def clear(self) -> None:
        """Vide tous les grants (utilisé en test ou réinit volontaire)."""
        self._file_grants.clear()
        self._folder_grants.clear()
        self._session_grants.clear()


class RequestGrants:
    """Grants vus par UNE requête : ceux de la session, plus le contrôle de l'écran.

    Le contrôle de l'ordinateur est accordé par l'utilisateur pour la requête en
    cours seulement (confirmation dédiée) ; il disparaît avec elle. Tant qu'il est
    accordé, les outils de :data:`permissions.risk_levels.COMPUTER_TOOLS` sont
    couverts — et le ``policy_engine`` ne laisse un grant couvrir que le niveau 1 :
    une action escaladée (terminal, bouton « Supprimer »…) reste redemandée.

    Même interface que :class:`SessionGrants` pour le ``policy_engine`` ; les grants
    « dossier » et « session » sont enregistrés dans la session partagée.
    """

    def __init__(self, session: SessionGrants) -> None:
        self._session = session
        self._control = False

    @property
    def control_allowed(self) -> bool:
        """``True`` si le contrôle de l'ordinateur est accordé pour cette requête."""
        return self._control

    def allow_control(self) -> None:
        """Accorde le contrôle de l'ordinateur pour cette requête."""
        self._control = True

    def revoke_control(self) -> None:
        """Retire le contrôle (fin de requête, arrêt par l'utilisateur)."""
        self._control = False

    def grant(self, tool_name: str, scope: str, target: str | None = None) -> None:
        """Enregistre un grant de session (délégué à :class:`SessionGrants`)."""
        if tool_name in COMPUTER_TOOLS:
            return  # jamais de grant durable sur le contrôle de l'écran
        self._session.grant(tool_name, scope, target)

    def is_granted(self, tool_name: str, paths: list[str]) -> bool:
        """Grants de la session, plus le contrôle accordé à la requête."""
        if tool_name in COMPUTER_TOOLS:
            return self._control
        return self._session.is_granted(tool_name, paths)
