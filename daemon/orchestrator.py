"""Orchestrator : pilote une requête utilisateur via une boucle agentique.

L'orchestrator **conserve la conversation entre les requêtes** : l'historique
(user / assistant / tool) est gardé en mémoire côté daemon — le cœur de
confiance — d'une requête à l'autre, et non reconstruit par le popup à chaque
envoi (cf. docs/INTERFACES.md §6, §1 message ``reset``). Ainsi le modèle « se
souvient » du tour précédent. L'historique est **borné** (``MAX_HISTORY_MESSAGES``)
pour ne pas croître sans fin — et, au jalon 4, pour limiter ce qui part au cloud.
Un message ``reset`` (raccourci du popup) le vide pour repartir à zéro.

Cycle de vie (cf. docs/INTERFACES.md §6 + §6.5) :

1. Reçoit ``user_message`` ; **ajoute** le message à l'historique persistant.
2. **Boucle** (jusqu'à une réponse finale ou ``MAX_STEPS``) :
   a. appelle le modèle (:meth:`Model.respond`) avec l'historique, streame sa
      narration vers le popup (``token``) ;
   b. si le modèle ne demande aucun outil → réponse finale, on sort ;
   c. sinon, pour chaque :class:`ToolCall`, passe par le **choke point**
      :func:`permissions.policy_engine.evaluate` :
        * ``blocked``  → audit + message, aucune exécution ;
        * ``auto``     → ``log_pending`` + ``run`` + ``update_result`` ;
        * ``confirm``  → ``confirmation_provider.ask`` ; refus → audit
          ``denied`` ; approbation → exécution puis grant éventuel ;
   d. **réinjecte** chaque résultat dans l'historique (message ``tool``) et
      reboucle : le modèle observe les résultats et décide de la suite.
3. Envoie un ``done`` final.

Les résultats d'outils réinjectés sont des DONNÉES (le system prompt du modèle
le rappelle) : un contenu lu ne commande jamais une action (SECURITY §2.2).

Le LLM réel n'est jamais appelé directement ici : on dépend d'un protocole
:class:`Model` injecté, ce qui rend la chaîne testable avec un modèle scripté
sans Ollama (cf. CLAUDE.md « mock-first »).
"""

from __future__ import annotations

import posixpath
import threading
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


# Callback de streaming : reçoit chaque fragment de texte au fil de la génération.
TokenCallback = Callable[[str], None]

# Un message de l'historique conversationnel. Formes :
#   {"role": "user", "content": str}
#   {"role": "assistant", "content": str, "tool_calls": list[ToolCall]}
#   {"role": "tool", "tool": str, "content": str}   # résultat réinjecté
Message = dict

# Plafond d'itérations de la boucle agentique (anti-boucle infinie).
# Assez haut pour des tâches multi-fichiers (lister + N actions + vérif + conclusion),
# assez bas pour borner un modèle qui s'emballerait.
MAX_STEPS = 12

# Plafond de messages conservés dans l'historique persistant. Borne la mémoire,
# le contexte envoyé au modèle, et (jalon 4) le volume de données partant au
# cloud. L'élagage ne coupe qu'à une frontière de tour (message ``user``) pour
# ne jamais laisser un résultat d'outil orphelin de son appel (cf. _to_ollama).
MAX_HISTORY_MESSAGES = 40


class Model(Protocol):
    """Contrat minimal d'un modèle côté orchestrator (cf. INTERFACES §6.5)."""

    def respond(
        self, messages: list[Message], on_token: TokenCallback | None = None
    ) -> Plan:
        """Renvoie un :class:`Plan` (narration + ``ToolCall``) pour cet historique.

        ``messages`` est l'historique conversationnel complet (user/assistant/
        tool). Si ``on_token`` est fourni, le modèle l'appelle pour chaque
        fragment de texte au fil de la génération (streaming). ``Plan.narration``
        reste le texte complet du tour, pour l'audit/les tests.

        Un ``Plan`` sans ``tool_calls`` signale une réponse finale (fin de boucle).
        """


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
        # Historique conversationnel persistant entre les requêtes (cf. en-tête
        # du module). Protégé par un verrou car chaque user_message est traité
        # dans un thread dédié (cf. daemon.ipc_server._dispatch).
        self._history: list[Message] = []
        self._history_lock = threading.Lock()

    def reset_history(self) -> None:
        """Oublie la conversation courante : repart d'un historique vide.

        Déclenché par le message IPC ``reset`` (raccourci du popup, cf.
        docs/INTERFACES.md §1). On **remplace** la liste plutôt que la vider en
        place : un tour éventuellement en cours garde sa propre référence et se
        termine sans corrompre la nouvelle conversation.
        """
        with self._history_lock:
            self._history = []

    def _trim_history_locked(self, history: list[Message]) -> None:
        """Borne ``history`` à ``MAX_HISTORY_MESSAGES``, à appeler **sous verrou**.

        N'élague que l'historique actif (si un ``reset`` l'a détaché entre-temps,
        ne touche à rien). Coupe par l'avant puis retire les messages de tête qui
        ne sont pas ``user`` : l'historique commence donc toujours par un tour
        utilisateur complet — jamais un ``assistant``/``tool`` orphelin.
        """
        if history is not self._history:
            return
        while len(history) > MAX_HISTORY_MESSAGES:
            history.pop(0)
        while history and history[0].get("role") != "user":
            history.pop(0)

    def handle(self, message: dict, reply: Reply) -> None:
        """Traite un ``user_message`` via la boucle agentique, jusqu'au ``done``.

        Le message est **ajouté à l'historique persistant** (mémoire entre
        requêtes). Tant que le modèle demande des outils, on les exécute (via le
        choke point) et on réinjecte les résultats dans ce même historique, puis
        on rappelle le modèle — jusqu'à une réponse finale sans outil ou
        ``MAX_STEPS``. Le modèle reçoit une **copie** de l'historique à chaque
        tour (snapshot), pour ne pas être affecté par une mutation concurrente.
        """
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

        # Ajoute le tour utilisateur à l'historique persistant et capture la
        # référence de la conversation courante (un reset concurrent la
        # remplacerait sans affecter ce tour en cours).
        with self._history_lock:
            self._history.append({"role": "user", "content": content})
            history = self._history

        for _step in range(MAX_STEPS):
            plan = self._run_model(list(history), user_message_id, reply)
            if plan is None:
                return  # erreur déjà signalée au popup
            with self._history_lock:
                history.append(
                    {
                        "role": "assistant",
                        "content": plan.narration,
                        "tool_calls": plan.tool_calls,
                    }
                )
            if not plan.tool_calls:
                break  # réponse finale : fin de la boucle
            for tool_call in plan.tool_calls:
                output = self._handle_tool_call(user_message_id, tool_call, reply)
                with self._history_lock:
                    history.append(
                        {"role": "tool", "tool": tool_call.tool, "content": output}
                    )
        else:
            reply(
                {
                    "type": "token",
                    "id": user_message_id,
                    "text": (
                        f"\n\n_(J'ai atteint la limite de {MAX_STEPS} étapes. "
                        "Si la tâche n'est pas terminée, relancez-moi pour continuer.)_\n"
                    ),
                }
            )

        # Borne l'historique persistant après ce tour (sans casser les paires
        # assistant↔tool ; cf. _trim_history_locked).
        with self._history_lock:
            self._trim_history_locked(history)

        reply({"type": "done", "id": user_message_id})

    def _run_model(
        self, messages: list[Message], msg_id: str, reply: Reply
    ) -> Plan | None:
        """Appelle le modèle pour un tour, streame sa narration, renvoie le Plan.

        Renvoie ``None`` si le modèle a échoué (l'erreur est déjà envoyée au
        popup), pour que :meth:`handle` interrompe la boucle.
        """
        emitted = False

        def emit_token(fragment: str) -> None:
            nonlocal emitted
            if fragment:
                emitted = True
                reply({"type": "token", "id": msg_id, "text": fragment})

        try:
            plan = self._model.respond(messages, emit_token)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            reply(
                {
                    "type": "error",
                    "id": msg_id,
                    "message": f"échec du modèle : {exc}",
                }
            )
            return None

        # Repli : si le modèle n'a pas streamé mais a une narration, on l'envoie.
        if not emitted and plan.narration:
            reply({"type": "token", "id": msg_id, "text": plan.narration})
        return plan

    def _handle_tool_call(self, msg_id: str, call: ToolCall, reply: Reply) -> str:
        """Évalue + exécute un appel d'outil. Renvoie le texte à réinjecter au modèle.

        Le même texte est aussi envoyé au popup (token) pour la transparence.
        Couvre tous les cas (inconnu / bloqué / refusé / erreur / succès) afin que
        le modèle « voie » toujours ce qui s'est passé et puisse s'adapter.
        """
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
            return self._emit_result(msg_id, reply, f"Outil inconnu : {call.tool}")

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
            return self._emit_result(msg_id, reply, f"Bloqué : {decision.summary}")

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
                return self._emit_result(
                    msg_id, reply, f"Refusé par l'utilisateur : {decision.summary}"
                )
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
            return self._emit_result(msg_id, reply, f"Erreur d'exécution : {exc}")
        self._audit.update_result(
            row_id, success=result.success, reversible=result.reversible
        )
        return self._emit_result(msg_id, reply, result.output)

    @staticmethod
    def _emit_result(msg_id: str, reply: Reply, text: str) -> str:
        """Envoie le résultat au popup et le renvoie (brut) pour réinjection.

        Côté popup, le résultat est formaté comme un bloc citation markdown
        (sur ses propres lignes), pour le séparer visuellement de la narration
        du modèle. La valeur **renvoyée** (réinjectée au modèle) reste le texte
        brut, sans balisage.
        """
        quoted = "\n\n> " + text.replace("\n", "\n> ") + "\n\n"
        reply({"type": "token", "id": msg_id, "text": quoted})
        return text

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
