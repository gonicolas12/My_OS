"""Point de passage **unique** : décide auto / confirm / bloqué.

Aucune action ne doit atteindre :meth:`tools.base_tool.BaseTool.run` sans une
:class:`Decision` produite par :func:`evaluate` (cf. CLAUDE.md, ARCHITECTURE
§3.2, SECURITY §4).

Ordre impératif (cf. docs/INTERFACES.md §3) :

1. ``blocklist.is_blocked(tool, args)`` — vérifié **en premier**, jamais
   franchissable, même avec grant.
2. Calcul du risque effectif via ``tool.escalate(args)`` ; le résultat est
   immédiatement clipé par ``max(..., tool.risk_level)`` pour rendre
   l'invariant « l'escalade peut UNIQUEMENT augmenter » indépendant du
   comportement des sous-classes (défense en profondeur).
3. Si les grants déjà accordés couvrent les chemins affectés → ``auto``,
   **uniquement jusqu'au niveau** :data:`GRANTABLE_MAX_LEVEL` (1). Le niveau 2
   (confirmation renforcée) est redemandé à chaque fois : sinon un grant obtenu
   sur une action anodine couvrirait une action escaladée (ex. ``~/.ssh``).
4. Décision par niveau : 0 → auto ; 1 → confirm ; 2 → confirm ; 3 → bloqué.

``requires_elevation`` (polkit) est **orthogonal au niveau** : il reflète
uniquement ``tool.requires_elevation(args)``, c'est-à-dire un outil qui passe
réellement par ``pkexec`` — typiquement ``pacman -S`` en niveau 1 (cf.
docs/INTERFACES.md §3). Une suppression de fichier de niveau 2, exécutée en
utilisateur, n'en demande pas : l'annoncer ferait attendre un mot de passe qui ne
viendra jamais. L'élévation réelle se fait à l'exécution dans
:func:`core.elevation.run_command`, pas ici.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from permissions.blocklist import is_blocked
from permissions.risk_levels import COMPUTER_TOOLS
from tools.base_tool import BaseTool

# Niveau maximal qu'un grant (dossier / session) peut couvrir. Au-delà, chaque
# action est reconfirmée et aucune portée de grant n'est proposée ni enregistrée.
GRANTABLE_MAX_LEVEL = 1


class Grants(Protocol):
    """Grants consultés par :func:`evaluate` (``SessionGrants`` ou ``RequestGrants``)."""

    def is_granted(self, tool_name: str, paths: list[str]) -> bool:
        """``True`` si un grant couvre l'outil pour ces chemins."""


@dataclass
class Decision:
    """Sort d'une action après évaluation par le policy_engine.

    ``action`` vaut ``"auto"``, ``"confirm"`` ou ``"blocked"`` ; ``risk_level``
    est le niveau effectif après escalade ; ``summary`` est un résumé lisible
    par l'utilisateur ; ``requires_elevation`` indique si l'outil passera par
    une élévation ``polkit`` (``pkexec``) à l'exécution.
    """

    action: str
    risk_level: int
    summary: str
    requires_elevation: bool = False


def _effective_risk(tool: BaseTool, args: dict) -> int:
    """Risque effectif, clipé pour ne jamais descendre sous ``tool.risk_level``."""
    return max(tool.escalate(args), tool.risk_level)


def _summary(tool: BaseTool, args: dict, level: int) -> str:
    return f"[niveau {level}] {tool.name}({args})"


def evaluate(tool: BaseTool, args: dict, grants: Grants) -> Decision:
    """Évalue une demande d'exécution d'outil et renvoie la décision à appliquer."""
    # 1. Blocklist : jamais franchissable.
    if is_blocked(tool.name, args):
        return Decision(
            action="blocked",
            risk_level=3,
            summary=_summary(tool, args, 3),
        )

    # 2. Risque effectif (l'escalade ne peut qu'augmenter).
    level = _effective_risk(tool, args)

    # Élévation : orthogonale au niveau, déclarée par l'outil lui-même (pkexec
    # réellement utilisé, ex. pacman -S en niveau 1) — jamais déduite du niveau.
    elevation = tool.requires_elevation(args)

    # 3. Grant existant → auto, seulement jusqu'à GRANTABLE_MAX_LEVEL : le niveau 2
    #    n'est jamais délégué à un grant (blocklist déjà éliminée plus haut).
    if level <= GRANTABLE_MAX_LEVEL and grants.is_granted(
        tool.name, tool.affected_paths(args)
    ):
        return Decision(
            action="auto",
            risk_level=level,
            summary=_summary(tool, args, level),
            requires_elevation=elevation,
        )

    # 4. Décision par niveau.
    summary = _summary(tool, args, level)
    if level == 0:
        return Decision(action="auto", risk_level=0, summary=summary)
    if level in (1, 2):
        return Decision(
            action="confirm",
            risk_level=level,
            summary=summary,
            requires_elevation=elevation,
        )
    # Niveau 3 hors blocklist : ne devrait pas survenir mais on bloque par sécurité.
    return Decision(action="blocked", risk_level=3, summary=summary)


def grantable_scopes(tool: BaseTool, args: dict, risk_level: int) -> list[str]:
    """Portées de grant que l'utilisateur peut choisir pour cette action.

    Vide au-delà de :data:`GRANTABLE_MAX_LEVEL` (confirmation renforcée : « une
    fois » seulement), et pour le contrôle de l'écran (accordé à une requête, jamais
    au-delà). ``this_folder`` n'a de sens que si l'outil touche des chemins (un
    ``pacman -S`` n'en a pas). Le popup n'affiche que ces portées et l'orchestrator
    n'enregistre jamais un grant hors de cette liste.
    """
    if risk_level > GRANTABLE_MAX_LEVEL or tool.name in COMPUTER_TOOLS:
        return []
    return ["this_folder", "session"] if tool.affected_paths(args) else ["session"]
