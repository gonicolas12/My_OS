"""Modèle stub à base de règles — utilisé tant qu'Ollama n'est pas branché.

Reconnaît une poignée de verbes français et anglais pour les outils fichiers
(``read_file``, ``list_dir``, ``write_file``, ``create_file``, ``move_file``,
``delete_file``) et renvoie un :class:`daemon.orchestrator.Plan` correspondant.
Ce stub ne « comprend » rien : il match des patterns simples pour permettre
de tester la chaîne complète sans modèle LLM.

Sera remplacé (ou complété) par :mod:`models.local_llm` au jalon 7-8.
"""

from __future__ import annotations

import re

from daemon.orchestrator import Plan, TokenCallback, ToolCall

_PATH = r"(?P<path>[\w./~\-]+)"
_SRC = r"(?P<src>[\w./~\-]+)"
_DST = r"(?P<dst>[\w./~\-]+)"

# Ordre : on essaie les motifs les plus spécifiques en premier.
_PATTERNS: list[tuple[re.Pattern[str], str, list[str]]] = [
    # déplace /a vers /b — move_file
    (
        re.compile(
            rf"\b(?:déplace|deplace|move|mv)\s+{_SRC}\s+(?:vers|to|→|->)\s+{_DST}\b"
        ),
        "move_file",
        ["src", "dst"],
    ),
    # écris X dans /chemin — write_file
    (
        re.compile(
            rf"\b(?:écris|ecris|écrit|ecrit|write)\s+(?P<content>.+?)\s+dans\s+{_PATH}\b"
        ),
        "write_file",
        ["content", "path"],
    ),
    # supprime /chemin — delete_file
    (
        re.compile(rf"\b(?:supprime|delete|rm|efface)\s+{_PATH}\b"),
        "delete_file",
        ["path"],
    ),
    # crée /chemin — create_file
    (
        re.compile(rf"\b(?:crée|cree|create|touch)\s+{_PATH}\b"),
        "create_file",
        ["path"],
    ),
    # liste /chemin — list_dir
    (
        re.compile(rf"\b(?:liste|list|ls)\s+{_PATH}\b"),
        "list_dir",
        ["path"],
    ),
    # lis /chemin — read_file
    (
        re.compile(rf"\b(?:lis|lit|read|cat)\s+{_PATH}\b"),
        "read_file",
        ["path"],
    ),
]


class StubRuleModel:
    """Implémentation de :class:`daemon.orchestrator.Model` à base de regex."""

    name = "stub-rules"

    def plan(self, user_message: str, on_token: TokenCallback | None = None) -> Plan:
        """Retourne un Plan basé sur un match regex, sinon un Plan vide narré.

        Le stub génère instantanément : ``on_token`` (s'il est fourni) reçoit
        la narration en une fois.
        """
        for pattern, tool_name, keys in _PATTERNS:
            match = pattern.search(user_message)
            if match is None:
                continue
            args = {key: match.group(key) for key in keys}
            narration = f"OK — je vais exécuter {tool_name}({args})."
            if on_token is not None:
                on_token(narration)
            return Plan(
                narration=narration, tool_calls=[ToolCall(tool=tool_name, args=args)]
            )

        narration = (
            "Je n'ai pas compris (modèle stub : essayez « lis /chemin », "
            "« écris hello dans /tmp/x », « supprime /tmp/x », "
            "« déplace /a vers /b »)."
        )
        if on_token is not None:
            on_token(narration)
        return Plan(
            narration=narration,
            tool_calls=[],
        )
