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

**Langue.** Toute la requête est traitée dans la langue de son ``user_message``
(champ ``language``, cf. :mod:`core.i18n`) : prompt du modèle, sorties d'outils,
résumés des confirmations et notices. Elle ne change aucune décision de permission.

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

from core.i18n import N_, current_language, tr, use_language
from core.logger import get_logger
from permissions.audit_log import AuditLog
from permissions.confirmation import (
    CONTROL_SUMMARY,
    CONTROL_TOOL,
    ConfirmationResponse,
    build_confirmation_needed,
    build_control_confirmation,
    new_request_id,
)
from permissions.policy_engine import evaluate
from permissions.risk_levels import COMPUTER_TOOLS
from permissions.session_grants import RequestGrants, SessionGrants
from tools.base_tool import BaseTool, ToolResult


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
#   {"role": "user", "content": str, "language": str}  # langue de la requête
#   {"role": "assistant", "content": str, "tool_calls": list[ToolCall]}
#   {"role": "tool", "tool": str, "content": str}   # résultat réinjecté
Message = dict

# Plafond d'itérations de la boucle agentique (anti-boucle infinie).
# Assez haut pour des tâches multi-fichiers (lister + N actions + vérif + conclusion),
# assez bas pour borner un modèle qui s'emballerait.
MAX_STEPS = 12
# Plafond pendant un contrôle de l'ordinateur : une tâche à l'écran enchaîne
# observer, cliquer, taper… L'utilisateur voit tout et l'arrête d'un Échap.
MAX_CONTROL_STEPS = 40

# Plafond de messages conservés dans l'historique persistant. Borne la mémoire,
# le contexte envoyé au modèle, et (jalon 4) le volume de données partant au
# cloud. L'élagage ne coupe qu'à une frontière de tour (message ``user``) pour
# ne jamais laisser un résultat d'outil orphelin de son appel (cf. _to_ollama).
MAX_HISTORY_MESSAGES = 40

# Logger dédié aux envois cloud (cf. docs/INTERFACES.md §5.4 + SECURITY menace 2).
# Trace QUOI part et QUAND (modèle, nombre de messages/caractères) — JAMAIS la clé,
# JAMAIS le contenu. Canal distinct du journal d'audit (réservé aux outils).
_cloud_log = get_logger("myosd.cloud")

# Notices in-transcript (messages ``notice``, cf. INTERFACES §1) : rendent le routage
# cloud VISIBLE dans la conversation, en plus de l'indicateur du popup. Traduites à
# l'envoi, dans la langue de la requête (comme tous les textes ``N_`` ci-dessous).
_CLOUD_ACTIVE_NOTICE = N_("☁ Requête envoyée au cloud (Claude).")
_CLOUD_FALLBACK_NOTICE = N_(
    "⚠ Mode cloud demandé mais aucune clé API n'est configurée : "
    "réponse générée en local."
)

# Statuts d'un message ``tool_result`` (cf. INTERFACES §1).
RESULT_OK = "ok"
RESULT_ERROR = "error"
RESULT_DENIED = "denied"
RESULT_BLOCKED = "blocked"

# Contrôle de l'ordinateur (cf. daemon.control).
_CONTROL_STOPPED_NOTICE = N_(
    "⏹ Contrôle de l'ordinateur arrêté : plus aucune action à l'écran."
)
_CONTROL_STOPPED_RESULT = N_(
    "Arrêté par l'utilisateur : le contrôle de l'ordinateur a pris fin."
)
_DENIED = N_("Refusé par l'utilisateur : {summary}")


class ControlProvider(Protocol):
    """Session de contrôle de l'ordinateur vue par l'orchestrator (cf. daemon.control)."""

    @property
    def active(self) -> bool:
        """``True`` pendant une session (indicateur affiché, pas d'arrêt)."""

    @property
    def cancelled(self) -> bool:
        """``True`` si l'utilisateur a arrêté la session en cours."""

    def unavailable_reason(self) -> str | None:
        """Pourquoi une session ne peut pas commencer (``None`` si elle le peut)."""

    def begin(self, request_id: str) -> str | None:
        """Ouvre une session (indicateur affiché) ; message d'échec ou ``None``."""

    def end(self, reason: str = "finished") -> None:
        """Ferme la session (idempotent)."""


@dataclass
class _Request:
    """État propre à une requête : grants vus, contrôle de l'ordinateur."""

    id: str
    text: str
    grants: RequestGrants
    control_denied: bool = False
    control_started: bool = False


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


class ModelRouter(Protocol):
    """Sélecteur d'un :class:`Model` cloud, par requête (cf. INTERFACES §5.3).

    Implémenté par :class:`models.cloud_router.CloudRouter` ; injecté dans
    l'orchestrator pour qu'il choisisse le backend selon ``use_cloud`` SANS importer
    de backend concret (évite tout couplage/import circulaire).
    """

    def is_available(self) -> bool:
        """``True`` si le cloud est utilisable (une clé API est configurée)."""

    def get_cloud_model(self) -> Model:
        """Renvoie le :class:`Model` cloud (le construit/cache au besoin)."""


class ConfirmationProvider(Protocol):
    """Fournisseur de confirmation injectable (transport-agnostique)."""

    def ask(self, payload: dict) -> ConfirmationResponse:
        """Soumet une demande de confirmation et bloque jusqu'à la réponse."""


Reply = Callable[[dict], None]


def _emit_result(reply: Reply, msg_id: str, tool: str, status: str, text: str) -> str:
    """Envoie le résultat d'un outil au popup (``tool_result``) et le renvoie.

    Le popup l'affiche à part de la narration, en texte brut (c'est une DONNÉE).
    La valeur **renvoyée** est le même texte, réinjecté tel quel au modèle.
    """
    reply(
        {
            "type": "tool_result",
            "id": msg_id,
            "tool": tool,
            "status": status,
            "text": text,
        }
    )
    return text


def _notice(reply: Reply, msg_id: str, level: str, text: str) -> None:
    """Envoie une notice du daemon (``info`` / ``warning``), affichée hors narration."""
    reply({"type": "notice", "id": msg_id, "level": level, "text": text})


class Orchestrator:  # pylint: disable=too-many-instance-attributes
    """Pilote une requête depuis le ``user_message`` jusqu'au ``done`` final."""

    # pylint: disable-next=too-many-arguments,too-many-positional-arguments
    def __init__(
        self,
        model: Model,
        tools: Mapping[str, BaseTool],
        grants: SessionGrants,
        audit: AuditLog,
        confirmation_provider: ConfirmationProvider,
        cloud_router: ModelRouter | None = None,
        control: ControlProvider | None = None,
    ) -> None:
        self._model = model
        self._tools = tools
        self._grants = grants
        self._audit = audit
        self._confirmation = confirmation_provider
        # Routeur cloud optionnel (jalon 4). None → toujours local (rétro-compatible).
        # Le local reste le défaut ; le cloud est choisi PAR REQUÊTE selon use_cloud.
        self._cloud_router = cloud_router
        # Session de contrôle de l'ordinateur (indicateur, arrêt). None → les outils
        # de l'écran répondent « indisponible ».
        self._control = control
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

        Toute la requête est traitée dans sa langue (``language``, sinon celle du
        daemon) : chaque requête a son thread, la langue est posée pour lui seul.
        """
        with use_language(message.get("language")):
            self._handle(message, reply)

    def _handle(self, message: dict, reply: Reply) -> None:
        user_message_id = str(message.get("id") or "")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            reply(
                {
                    "type": "error",
                    "id": user_message_id,
                    "message": tr("message vide ou invalide"),
                }
            )
            return

        # Ajoute le tour utilisateur à l'historique persistant et capture la
        # référence de la conversation courante (un reset concurrent la
        # remplacerait sans affecter ce tour en cours). La langue de la requête y
        # est notée : une conversation qui change de langue le rappelle au modèle.
        with self._history_lock:
            self._history.append(
                {"role": "user", "content": content, "language": current_language()}
            )
            history = self._history

        # Sélection du backend pour CETTE requête (une seule fois — jamais changé en
        # plein milieu de la boucle agentique). Le local est le défaut ; le cloud est
        # opt-in via use_cloud (cf. INTERFACES §5.3). En mode cloud, on journalise
        # l'envoi (tout l'historique part — cf. SECURITY menace 2).
        model, cloud_active = self._select_model(message, user_message_id, reply)
        if cloud_active:
            self._log_cloud_send(list(history), model)

        request = _Request(
            id=user_message_id, text=content, grants=RequestGrants(self._grants)
        )
        try:
            finished = self._loop(model, history, request, reply)
        finally:
            # Le contrôle de l'ordinateur ne survit jamais à sa requête.
            self._end_control(request, reply)
        if not finished:
            return  # erreur du modèle, déjà signalée au popup

        # Borne l'historique persistant après ce tour (sans casser les paires
        # assistant↔tool ; cf. _trim_history_locked).
        with self._history_lock:
            self._trim_history_locked(history)

        reply({"type": "done", "id": user_message_id})

    def _loop(
        self, model: Model, history: list[Message], request: _Request, reply: Reply
    ) -> bool:
        """Boucle agentique d'une requête ; ``False`` si le modèle a échoué."""
        step = 0
        while True:
            limit = MAX_CONTROL_STEPS if request.control_started else MAX_STEPS
            if step >= limit:
                _notice(
                    reply,
                    request.id,
                    "warning",
                    tr(
                        "J'ai atteint la limite de {limit} étapes. Si la tâche n'est "
                        "pas terminée, relancez-moi pour continuer.",
                        limit=limit,
                    ),
                )
                return True
            step += 1
            plan = self._run_model(model, list(history), request.id, reply)
            if plan is None:
                return False
            with self._history_lock:
                history.append(
                    {
                        "role": "assistant",
                        "content": plan.narration,
                        "tool_calls": plan.tool_calls,
                    }
                )
            if not plan.tool_calls:
                return True  # réponse finale : fin de la boucle
            for tool_call in plan.tool_calls:
                if self._control_stopped(request):
                    # Arrêté par l'utilisateur : plus rien ne s'exécute ; chaque appel
                    # reçoit quand même son résultat (historique et popup cohérents).
                    output = _emit_result(
                        reply,
                        request.id,
                        tool_call.tool,
                        RESULT_DENIED,
                        tr(_CONTROL_STOPPED_RESULT),
                    )
                    image = None
                else:
                    output, image = self._handle_tool_call(request, tool_call, reply)
                self._append_tool_result(history, tool_call.tool, output, image)
            if self._control_stopped(request):
                return True

    def _append_tool_result(
        self, history: list[Message], tool: str, output: str, image: bytes | None
    ) -> None:
        """Réinjecte un résultat d'outil ; seule la dernière capture d'écran est gardée."""
        entry: Message = {"role": "tool", "tool": tool, "content": output}
        with self._history_lock:
            if image is not None:
                for message in history:
                    message.pop("image", None)
                entry["image"] = image
            history.append(entry)

    # ---- contrôle de l'ordinateur ------------------------------------------------
    def _control_stopped(self, request: _Request) -> bool:
        return (
            request.control_started
            and self._control is not None
            and self._control.cancelled
        )

    def _ensure_control(
        self, request: _Request, reply: Reply
    ) -> tuple[str, str] | None:
        """Accord et ouverture du contrôle de l'ordinateur pour cette requête.

        Une seule confirmation par requête (niveau 2, la demande tapée par
        l'utilisateur affichée) ; l'indicateur doit s'afficher avant toute action.
        Renvoie ``None`` si le contrôle est actif, sinon ``(statut, texte)`` du refus.
        """
        if request.grants.control_allowed:
            if self._control_stopped(request):
                return RESULT_DENIED, tr(_CONTROL_STOPPED_RESULT)
            return None
        if self._control is None:
            return RESULT_ERROR, tr(
                "Contrôle de l'ordinateur indisponible dans cette installation."
            )
        if request.control_denied:
            return (
                RESULT_DENIED,
                tr(
                    "Refusé par l'utilisateur : {summary} Ne le redemandez pas dans "
                    "cette requête.",
                    summary=tr(CONTROL_SUMMARY),
                ),
            )
        reason = self._control.unavailable_reason()
        if reason:
            return RESULT_ERROR, tr(
                "Contrôle de l'ordinateur indisponible : {reason}.", reason=reason
            )
        payload = build_control_confirmation(
            request_id=new_request_id(),
            user_message_id=request.id,
            request_text=request.text,
        )
        approved = self._confirmation.ask(payload).is_approval
        failure = self._control.begin(request.id) if approved else None
        self._audit.log(
            tool=CONTROL_TOOL,
            args=payload["args"],
            risk_level=2,
            decision="approved" if approved else "denied",
            success=approved and failure is None,
            reversible=False,
        )
        if not approved:
            request.control_denied = True
            return RESULT_DENIED, tr(_DENIED, summary=tr(CONTROL_SUMMARY))
        if failure:
            return RESULT_ERROR, tr(
                "Contrôle de l'ordinateur indisponible : {reason}.", reason=failure
            )
        request.control_started = True
        request.grants.allow_control()
        _notice(
            reply,
            request.id,
            "info",
            tr(
                "🖱 Contrôle de l'ordinateur accordé pour cette demande "
                "(Échap pour arrêter)."
            ),
        )
        return None

    def _end_control(self, request: _Request, reply: Reply) -> None:
        if not request.control_started or self._control is None:
            return
        stopped = self._control.cancelled
        request.grants.revoke_control()
        self._control.end("user" if stopped else "finished")
        if stopped:
            _notice(reply, request.id, "warning", tr(_CONTROL_STOPPED_NOTICE))

    def _run_model(
        self, model: Model, messages: list[Message], msg_id: str, reply: Reply
    ) -> Plan | None:
        """Appelle ``model`` pour un tour, streame sa narration, renvoie le Plan.

        ``model`` est le backend résolu pour la requête courante (local ou cloud,
        cf. :meth:`_select_model`). Renvoie ``None`` si le modèle a échoué (l'erreur
        est déjà envoyée au popup), pour que :meth:`handle` interrompe la boucle.
        """
        emitted = False

        def emit_token(fragment: str) -> None:
            nonlocal emitted
            if fragment:
                emitted = True
                reply({"type": "token", "id": msg_id, "text": fragment})

        try:
            plan = model.respond(messages, emit_token)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            reply(
                {
                    "type": "error",
                    "id": msg_id,
                    "message": tr("échec du modèle : {error}", error=exc),
                }
            )
            return None

        # Repli : si le modèle n'a pas streamé mais a une narration, on l'envoie.
        if not emitted and plan.narration:
            reply({"type": "token", "id": msg_id, "text": plan.narration})
        return plan

    def _select_model(
        self, message: dict, msg_id: str, reply: Reply
    ) -> tuple[Model, bool]:
        """Choisit le backend pour cette requête. Renvoie ``(modèle, cloud_actif)``.

        Le cloud est **opt-in** (``use_cloud``) et ne s'active QUE si une clé est
        configurée ; sinon **repli local + notice claire** au popup (jamais de cloud
        silencieux, cf. SECURITY menace 2). Le LLM ne choisit jamais : c'est
        l'utilisateur, via le toggle du popup, qui porte ``use_cloud`` dans le
        message IPC. Le routeur étant absent (None) → toujours local.
        """
        if not message.get("use_cloud"):
            return self._model, False
        router = self._cloud_router
        if router is None or not router.is_available():
            _notice(reply, msg_id, "warning", tr(_CLOUD_FALLBACK_NOTICE))
            return self._model, False
        try:
            cloud_model = router.get_cloud_model()
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # Repli défensif (ex. clé retirée entre le check et la construction).
            _cloud_log.warning("Cloud indisponible (%s) ; repli local", exc)
            _notice(reply, msg_id, "warning", tr(_CLOUD_FALLBACK_NOTICE))
            return self._model, False
        _notice(reply, msg_id, "info", tr(_CLOUD_ACTIVE_NOTICE))
        return cloud_model, True

    @staticmethod
    def _log_cloud_send(history: list[Message], model: Model) -> None:
        """Journalise un envoi cloud : QUOI part (volume) et QUAND.

        Jamais de secret ni de contenu (cf. INTERFACES §5.4) : on ne trace que le
        modèle, le nombre de messages et le nombre de caractères transmis — pas le
        texte lui-même. Canal = logger ``myosd.cloud`` (distinct du journal d'audit).
        """
        chars = sum(len(str(m.get("content", "") or "")) for m in history)
        _cloud_log.info(
            "Envoi CLOUD (%s) : %d message(s), ~%d caractère(s) transmis à "
            "l'API Anthropic",
            getattr(model, "name", "cloud"),
            len(history),
            chars,
        )

    def _handle_tool_call(
        self, request: _Request, call: ToolCall, reply: Reply
    ) -> tuple[str, bytes | None]:
        """Évalue + exécute un appel d'outil. Renvoie ``(texte, capture)`` pour le modèle.

        Le même texte est aussi envoyé au popup (``tool_result``) pour la
        transparence ; la capture d'écran éventuelle (outils de l'écran) ne va
        qu'au modèle. Couvre tous les cas (inconnu / bloqué / refusé / erreur /
        succès) afin que le modèle « voie » toujours ce qui s'est passé et puisse
        s'adapter.
        """
        msg_id = request.id
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
            text = tr("Outil inconnu : {tool}", tool=call.tool)
            return _emit_result(reply, msg_id, call.tool, RESULT_BLOCKED, text), None

        if tool.name in COMPUTER_TOOLS:
            # Le contrôle s'ouvre AVANT de résoudre la cible : tant que le popup est
            # affiché, c'est lui qui a le focus (la cible serait My_OS lui-même).
            refusal = self._ensure_control(request, reply)
            if refusal is not None:
                status, text = refusal
                return _emit_result(reply, msg_id, tool.name, status, text), None

        # Normalise les arguments (ex. expansion de ~) AVANT toute évaluation :
        # blocklist, escalade, grants et run voient ainsi le chemin réel.
        args = tool.normalize_args(call.args)
        decision = evaluate(tool, args, request.grants)

        if decision.action == "blocked":
            self._audit.log(
                tool=tool.name,
                args=args,
                risk_level=decision.risk_level,
                decision="blocked",
                success=False,
                reversible=False,
            )
            text = tr("Bloqué : {summary}", summary=decision.summary)
            return _emit_result(reply, msg_id, tool.name, RESULT_BLOCKED, text), None

        audit_decision = "auto"
        if decision.action == "confirm":
            payload = build_confirmation_needed(
                request_id=new_request_id(),
                user_message_id=msg_id,
                tool=tool,
                args=args,
                decision=decision,
            )
            response = self._confirmation.ask(payload)
            if not response.is_approval:
                self._audit.log(
                    tool=tool.name,
                    args=args,
                    risk_level=decision.risk_level,
                    decision="denied",
                    success=False,
                    reversible=False,
                )
                text = tr(_DENIED, summary=decision.summary)
                return _emit_result(reply, msg_id, tool.name, RESULT_DENIED, text), None
            # Un grant n'est enregistré que pour une portée proposée par le daemon :
            # jamais au niveau 2, même si le client renvoie approve_scope (l'action
            # est alors exécutée une seule fois).
            if response.creates_grant and response.scope in payload["allowed_scopes"]:
                self._record_grant(request.grants, tool, args, response.scope)
            audit_decision = "approved"

        # auto ou confirmé : exécute.
        row_id = self._audit.log_pending(
            tool=tool.name,
            args=args,
            risk_level=decision.risk_level,
            decision=audit_decision,
        )
        try:
            result: ToolResult = tool.run(args)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            self._audit.update_result(row_id, success=False, reversible=False)
            text = tr("Erreur d'exécution : {error}", error=exc)
            return _emit_result(reply, msg_id, tool.name, RESULT_ERROR, text), None
        self._audit.update_result(
            row_id, success=result.success, reversible=result.reversible
        )
        status = RESULT_OK if result.success else RESULT_ERROR
        text = _emit_result(reply, msg_id, tool.name, status, result.output)
        return text, result.image

    @staticmethod
    def _record_grant(
        grants: RequestGrants, tool: BaseTool, args: dict, scope: str
    ) -> None:
        if scope == "session":
            grants.grant(tool.name, "session")
            return
        paths = tool.affected_paths(args)
        if not paths:
            return  # rien à autoriser
        if scope == "this_file":
            grants.grant(tool.name, "this_file", target=paths[0])
        else:  # this_folder
            parent = posixpath.dirname(paths[0].replace("\\", "/"))
            grants.grant(tool.name, "this_folder", target=parent)
