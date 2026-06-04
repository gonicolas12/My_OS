"""Orchestrator : pilote une requête utilisateur de bout en bout.

Cycle de vie (cf. docs/INTERFACES.md §6 + §6.5) :

1. Reçoit ``user_message`` du popup.
2. Demande un :class:`Plan` au modèle (:meth:`Model.plan`).
3. Stream la narration vers le popup (un ``token``).
4. Pour chaque :class:`ToolCall`, passe par le **choke point** :
   :func:`permissions.policy_engine.evaluate`.

   * ``blocked``  → audit + token d'info, aucune exécution.
   * ``auto``     → ``log_pending`` + ``run`` + ``update_result`` + token sortie.
   * ``confirm``  → ``confirmation_provider.ask`` ; selon la réponse,
     refus → audit ``denied`` ; approbation → exécution puis grant éventuel.
5. Envoie un ``done`` final.

Le LLM réel n'est jamais appelé directement ici : on dépend d'un protocole
:class:`Model` injecté, ce qui rend la chaîne testable avec un ``FakeLLM``
sans Ollama (cf. CLAUDE.md « mock-first »).
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from permissions.audit_log import AuditLog
from permissions.confirmation import (
    ConfirmationResponse,
    build_confirmation_needed,
    new_request_id,
)
from permissions.policy_engine import evaluate
from permissions.session_grants import SessionGrants
from tools.base_tool import BaseTool


@dataclass
class ToolCall:
    """Demande d'exécution d'un outil par le modèle."""

    tool: str
    args: dict


@dataclass
class Plan:
    """Plan retourné par le modèle pour une requête utilisateur."""

    narration: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Model(Protocol):
    """Contrat minimal d'un modèle côté orchestrator (cf. INTERFACES §6.5)."""

    def plan(self, user_message: str) -> Plan:
        """Renvoie un :class:`Plan` (narration + ``ToolCall``) pour ce message."""


class ConfirmationProvider(Protocol):
    """Fournisseur de confirmation injectable (transport-agnostique)."""

    def ask(self, payload: dict) -> ConfirmationResponse:
        """Soumet une demande de confirmation et bloque jusqu'à la réponse."""


Reply = Callable[[dict], None]


class Orchestrator:
    """Pilote une requête depuis le ``user_message`` jusqu'au ``done`` final."""

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        model: Model,
        tools: Mapping[str, BaseTool],
        grants: SessionGrants,
        audit: AuditLog,
        confirmation_provider: ConfirmationProvider,
    ) -> None:
        self._model = model
        self._tools = tools
        self._grants = grants
        self._audit = audit
        self._confirmation = confirmation_provider

    def handle(self, message: dict, reply: Reply) -> None:
        """Traite un ``user_message`` complet (validation, plan, exécution, done)."""
        user_message_id = str(message.get("id") or "")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            reply(
                {
                    "type": "error",
                    "id": user_message_id,
                    "message": "message vide ou invalide",
                }
            )
            return

        try:
            plan = self._model.plan(content)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            reply(
                {
                    "type": "error",
                    "id": user_message_id,
                    "message": f"échec du planification : {exc}",
                }
            )
            return

        if plan.narration:
            reply({"type": "token", "id": user_message_id, "text": plan.narration})

        for tool_call in plan.tool_calls:
            self._handle_tool_call(user_message_id, tool_call, reply)

        reply({"type": "done", "id": user_message_id})

    def _handle_tool_call(self, msg_id: str, call: ToolCall, reply: Reply) -> None:
        tool = self._tools.get(call.tool)
        if tool is None:
            # Outil inconnu : tracé comme blocked au niveau 3 ; rien d'autre n'est sûr.
            self._audit.log(
                tool=call.tool,
                args=call.args,
                risk_level=3,
                decision="blocked",
                success=False,
                reversible=False,
            )
            reply(
                {
                    "type": "token",
                    "id": msg_id,
                    "text": f"Outil inconnu : {call.tool}\n",
                }
            )
            return

        # Normalise les arguments (ex. expansion de ~) AVANT toute évaluation :
        # blocklist, escalade, grants et run voient ainsi le chemin réel.
        args = tool.normalize_args(call.args)
        decision = evaluate(tool, args, self._grants)

        if decision.action == "blocked":
            self._audit.log(
                tool=tool.name,
                args=args,
                risk_level=decision.risk_level,
                decision="blocked",
                success=False,
                reversible=False,
            )
            reply(
                {
                    "type": "token",
                    "id": msg_id,
                    "text": f"Bloqué : {decision.summary}\n",
                }
            )
            return

        audit_decision = "auto"
        if decision.action == "confirm":
            response = self._confirmation.ask(
                build_confirmation_needed(
                    request_id=new_request_id(),
                    user_message_id=msg_id,
                    tool=tool,
                    args=args,
                    decision=decision,
                )
            )
            if not response.is_approval:
                self._audit.log(
                    tool=tool.name,
                    args=args,
                    risk_level=decision.risk_level,
                    decision="denied",
                    success=False,
                    reversible=False,
                )
                reply(
                    {
                        "type": "token",
                        "id": msg_id,
                        "text": f"Refusé : {decision.summary}\n",
                    }
                )
                return
            if response.creates_grant and response.scope is not None:
                self._record_grant(tool, args, response.scope)
            audit_decision = "approved"

        # auto ou confirmé : exécute.
        row_id = self._audit.log_pending(
            tool=tool.name,
            args=args,
            risk_level=decision.risk_level,
            decision=audit_decision,
        )
        try:
            result = tool.run(args)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._audit.update_result(row_id, success=False, reversible=False)
            reply(
                {
                    "type": "token",
                    "id": msg_id,
                    "text": f"Erreur d'exécution : {exc}\n",
                }
            )
            return
        self._audit.update_result(
            row_id, success=result.success, reversible=result.reversible
        )
        reply({"type": "token", "id": msg_id, "text": result.output + "\n"})

    def _record_grant(self, tool: BaseTool, args: dict, scope: str) -> None:
        if scope == "session":
            self._grants.grant(tool.name, "session")
            return
        paths = tool.affected_paths(args)
        if not paths:
            return  # rien à autoriser
        if scope == "this_file":
            self._grants.grant(tool.name, "this_file", target=paths[0])
        else:  # this_folder
            parent = posixpath.dirname(paths[0].replace("\\", "/"))
            self._grants.grant(tool.name, "this_folder", target=parent)
