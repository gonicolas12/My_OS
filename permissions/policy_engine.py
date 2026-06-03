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
3. Si les grants déjà accordés couvrent les chemins affectés → ``auto``
   au niveau effectif courant.
4. Décision par niveau : 0 → auto ; 1 → confirm ; 2 → confirm avec élévation
   (polkit attendu) ; 3 → bloqué.
"""

from __future__ import annotations

from dataclasses import dataclass

from permissions.blocklist import is_blocked
from permissions.session_grants import SessionGrants
from tools.base_tool import BaseTool


@dataclass
class Decision:
    """Sort d'une action après évaluation par le policy_engine.

    ``action`` vaut ``"auto"``, ``"confirm"`` ou ``"blocked"`` ; ``risk_level``
    est le niveau effectif après escalade ; ``summary`` est un résumé lisible
    par l'utilisateur ; ``requires_elevation`` indique si une élévation
    ``polkit`` est attendue (typiquement niveau 2).
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


def evaluate(tool: BaseTool, args: dict, grants: SessionGrants) -> Decision:
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

    # 3. Grant existant → auto, quel que soit le niveau (sauf blocklist déjà éliminée).
    if grants.is_granted(tool.name, tool.affected_paths(args)):
        return Decision(
            action="auto",
            risk_level=level,
            summary=_summary(tool, args, level),
        )

    # 4. Décision par niveau.
    summary = _summary(tool, args, level)
    if level == 0:
        return Decision(action="auto", risk_level=0, summary=summary)
    if level == 1:
        return Decision(action="confirm", risk_level=1, summary=summary)
    if level == 2:
        return Decision(
            action="confirm",
            risk_level=2,
            summary=summary,
            requires_elevation=True,
        )
    # Niveau 3 hors blocklist : ne devrait pas survenir mais on bloque par sécurité.
    return Decision(action="blocked", risk_level=3, summary=summary)
