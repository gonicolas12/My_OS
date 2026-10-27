"""Construction et parsing des payloads IPC de confirmation.

Convertit une :class:`Decision` (sortie de :mod:`permissions.policy_engine`)
en message ``confirmation_needed`` à destination du popup (cf. INTERFACES §1)
et parse les ``confirmation_response`` reçus en retour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from core.i18n import N_, tr
from permissions.policy_engine import Decision, grantable_scopes
from tools.base_tool import BaseTool

_VALID_DECISIONS = ("approve_once", "approve_scope", "deny")
_VALID_SCOPES = ("this_file", "this_folder", "session")


def new_request_id() -> str:
    """Génère un identifiant unique pour une demande de confirmation."""
    return str(uuid4())


def build_confirmation_needed(
    *,
    request_id: str,
    user_message_id: str,
    tool: BaseTool,
    args: dict,
    decision: Decision,
) -> dict:
    """Construit le message IPC ``confirmation_needed`` à envoyer au popup.

    ``allowed_scopes`` liste les portées de grant proposables (vide au niveau 2) :
    le popup n'affiche que celles-ci, et l'orchestrator ignore toute autre portée
    renvoyée (le daemon ne fait pas confiance au client sur ce point).
    """
    return {
        "type": "confirmation_needed",
        "id": user_message_id,
        "request_id": request_id,
        "tool": tool.name,
        "args": args,
        "risk_level": decision.risk_level,
        "summary": decision.summary,
        "requires_elevation": decision.requires_elevation,
        "allowed_scopes": grantable_scopes(tool, args, decision.risk_level),
    }


# Pseudo-outil de la confirmation du contrôle de l'ordinateur (une par requête).
CONTROL_TOOL = "computer_control"
# Traduit au moment de la demande (langue de la requête) : ``tr(CONTROL_SUMMARY)``.
CONTROL_SUMMARY = N_(
    "Prendre le contrôle de l'ordinateur pour cette demande : voir l'écran, "
    "déplacer la souris, cliquer et taper au clavier."
)
_REQUEST_PREVIEW_MAX = 400


def build_control_confirmation(
    *, request_id: str, user_message_id: str, request_text: str
) -> dict:
    """Demande d'accord pour une session de contrôle de l'ordinateur.

    Toujours au niveau 2 (confirmation renforcée, « Refuser » par défaut, aucune
    portée) : l'accord vaut pour CETTE requête seulement. La demande montrée est
    celle que l'utilisateur a tapée, pas une description fournie par le modèle.
    """
    text = " ".join(request_text.split())
    if len(text) > _REQUEST_PREVIEW_MAX:
        text = text[: _REQUEST_PREVIEW_MAX - 1] + "…"
    return {
        "type": "confirmation_needed",
        "id": user_message_id,
        "request_id": request_id,
        "tool": CONTROL_TOOL,
        "args": {"demande": text},
        "risk_level": 2,
        "summary": tr(CONTROL_SUMMARY),
        "requires_elevation": False,
        "allowed_scopes": [],
        "control": True,
    }


@dataclass
class ConfirmationResponse:
    """Réponse utilisateur typée — produite par :func:`parse_confirmation_response`."""

    request_id: str
    decision: Literal["approve_once", "approve_scope", "deny"]
    scope: Literal["this_file", "this_folder", "session"] | None = None

    @property
    def is_approval(self) -> bool:
        """``True`` si l'utilisateur a approuvé (une fois ou pour un scope)."""
        return self.decision in ("approve_once", "approve_scope")

    @property
    def creates_grant(self) -> bool:
        """``True`` si la réponse doit produire un grant persistant pour la session."""
        return self.decision == "approve_scope" and self.scope is not None


def parse_confirmation_response(message: dict) -> ConfirmationResponse:
    """Parse un message ``confirmation_response`` venu du popup.

    Lève :class:`ValueError` si le format est invalide (champ manquant,
    valeur hors énum, ``approve_scope`` sans ``scope`` valide).
    """
    if message.get("type") != "confirmation_response":
        raise ValueError(
            f"type attendu 'confirmation_response' ; reçu {message.get('type')!r}"
        )
    request_id = message.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id manquant ou invalide")
    decision = message.get("decision")
    if decision not in _VALID_DECISIONS:
        raise ValueError(f"decision invalide : {decision!r}")
    scope = message.get("scope")
    if decision == "approve_scope" and scope not in _VALID_SCOPES:
        raise ValueError(f"approve_scope requiert un scope valide ; reçu {scope!r}")
    return ConfirmationResponse(
        request_id=request_id,
        decision=decision,  # type: ignore[arg-type]
        scope=scope if decision == "approve_scope" else None,  # type: ignore[arg-type]
    )
