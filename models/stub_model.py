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

    def respond(
        self, messages: list[dict], on_token: TokenCallback | None = None
    ) -> Plan:
        """Renvoie un Plan pour le dernier message de l'historique.

        Le stub ne gère pas de vrai raisonnement multi-tours : si le dernier
        message est un résultat d'outil (``role == "tool"``), il renvoie une
        réponse finale (pas de nouvel outil) pour clore proprement la boucle.
        Sinon, il applique ses regex sur le dernier message ``user``.

        Génère instantanément : ``on_token`` (s'il est fourni) reçoit la
        narration en une fois.
        """
        last = messages[-1] if messages else {}
        if last.get("role") == "tool":
            return self._final("C'est fait.", on_token)

        user_message = self._last_user_content(messages)
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

        return self._final(
            "Je n'ai pas compris (modèle stub : essayez « lis /chemin », "
            "« écris hello dans /tmp/x », « supprime /tmp/x », "
            "« déplace /a vers /b »).",
            on_token,
        )

    @staticmethod
    def _last_user_content(messages: list[dict]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                return str(message.get("content", ""))
        return ""

    @staticmethod
    def _final(narration: str, on_token: TokenCallback | None) -> Plan:
        if on_token is not None:
            on_token(narration)
        return Plan(narration=narration, tool_calls=[])
