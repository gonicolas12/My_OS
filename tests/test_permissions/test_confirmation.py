"""Tests de la construction et du parsing des payloads de confirmation IPC."""

# pylint: disable=missing-function-docstring
from __future__ import annotations

import pytest

from permissions.confirmation import (
    ConfirmationResponse,
    build_confirmation_needed,
    new_request_id,
    parse_confirmation_response,
)
from permissions.policy_engine import Decision
from tools.base_tool import BaseTool, ToolResult


class _StubTool(BaseTool):
    name = "delete_file"
    description = "stub"
    risk_level = 2

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="")


def test_new_request_id_renvoie_une_chaine_non_vide() -> None:
    rid = new_request_id()
    assert isinstance(rid, str) and rid
    assert new_request_id() != rid  # unicité raisonnable


def test_build_confirmation_needed_contient_tous_les_champs() -> None:
    decision = Decision(action="confirm", risk_level=2, summary="suppr de /tmp/x")
    payload = build_confirmation_needed(
        request_id="rid-1",
        user_message_id="uid-1",
        tool=_StubTool(),
        args={"path": "/tmp/x"},
        decision=decision,
    )
    assert payload == {
        "type": "confirmation_needed",
        "id": "uid-1",
        "request_id": "rid-1",
        "tool": "delete_file",
        "args": {"path": "/tmp/x"},
        "risk_level": 2,
        "summary": "suppr de /tmp/x",
    }


def test_parse_approve_once() -> None:
    resp = parse_confirmation_response(
        {
            "type": "confirmation_response",
            "request_id": "rid-1",
            "decision": "approve_once",
        }
    )
    assert resp == ConfirmationResponse(request_id="rid-1", decision="approve_once")
    assert resp.is_approval is True
    assert resp.creates_grant is False


def test_parse_approve_scope_this_folder() -> None:
    resp = parse_confirmation_response(
        {
            "type": "confirmation_response",
            "request_id": "rid-1",
            "decision": "approve_scope",
            "scope": "this_folder",
        }
    )
    assert resp.is_approval is True
    assert resp.creates_grant is True
    assert resp.scope == "this_folder"


def test_parse_deny() -> None:
    resp = parse_confirmation_response(
        {"type": "confirmation_response", "request_id": "rid-1", "decision": "deny"}
    )
    assert resp.is_approval is False
    assert resp.creates_grant is False


def test_parse_approve_once_ignore_le_champ_scope() -> None:
    resp = parse_confirmation_response(
        {
            "type": "confirmation_response",
            "request_id": "rid-1",
            "decision": "approve_once",
            "scope": "session",  # ignoré
        }
    )
    assert resp.scope is None


@pytest.mark.parametrize(
    ("message", "match"),
    [
        ({"type": "wrong"}, "type"),
        ({"type": "confirmation_response", "decision": "deny"}, "request_id"),
        (
            {"type": "confirmation_response", "request_id": "", "decision": "deny"},
            "request_id",
        ),
        (
            {"type": "confirmation_response", "request_id": "rid", "decision": "huh"},
            "decision",
        ),
        (
            {
                "type": "confirmation_response",
                "request_id": "rid",
                "decision": "approve_scope",
                # scope manquant
            },
            "scope",
        ),
        (
            {
                "type": "confirmation_response",
                "request_id": "rid",
                "decision": "approve_scope",
                "scope": "forever",
            },
            "scope",
        ),
    ],
)
def test_parse_messages_invalides_levent_value_error(message: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_confirmation_response(message)
