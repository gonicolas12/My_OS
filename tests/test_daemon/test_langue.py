"""Langue d'une requête côté daemon : le modèle, les outils, les confirmations et les
notices répondent dans la langue du ``user_message`` ; elle ne change aucune
décision de permission et ne déborde pas sur la requête suivante."""

# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from core.i18n import current_language
from daemon.orchestrator import Orchestrator, Plan, ToolCall
from permissions.audit_log import AuditLog
from permissions.confirmation import ConfirmationResponse
from permissions.session_grants import SessionGrants
from tools.files import FILE_TOOLS


class _RecordingModel:
    """Demande un outil puis conclut ; note la langue vue à chaque tour."""

    def __init__(self, calls: list[ToolCall]) -> None:
        self._calls = list(calls)
        self.languages: list[str] = []

    def respond(self, messages, on_token=None) -> Plan:
        self.languages.append(current_language())
        if self._calls:
            return Plan(tool_calls=[self._calls.pop(0)])
        return Plan()


class _Confirmation:
    def __init__(self, decision: str = "deny") -> None:
        self.decision = decision
        self.asked: list[dict] = []

    def ask(self, payload: dict) -> ConfirmationResponse:
        self.asked.append(payload)
        return ConfirmationResponse(
            request_id=payload["request_id"], decision=self.decision
        )


@pytest.fixture
def audit(tmp_path: Path) -> Iterator[AuditLog]:
    log = AuditLog(tmp_path / "audit.db")
    yield log
    log.close()


def _run(
    audit: AuditLog, calls: list[ToolCall], language: str | None, **message: object
) -> tuple[_RecordingModel, _Confirmation, list[dict]]:
    model = _RecordingModel(calls)
    confirmation = _Confirmation()
    orch = Orchestrator(
        model=model,
        tools=FILE_TOOLS,
        grants=SessionGrants(),
        audit=audit,
        confirmation_provider=confirmation,
    )
    replies: list[dict] = []
    payload = {"id": "m1", "content": "demande", **message}
    if language is not None:
        payload["language"] = language
    orch.handle(payload, replies.append)
    return model, confirmation, replies


def _results(replies: list[dict]) -> list[str]:
    return [r["text"] for r in replies if r["type"] == "tool_result"]


def test_requete_en_anglais_de_bout_en_bout(audit: AuditLog, tmp_path: Path) -> None:
    target = str(tmp_path / "notes.txt")
    model, confirmation, replies = _run(
        audit,
        [
            ToolCall("write_file", {"path": target, "content": "x"}),
            ToolCall("read_file", {"path": str(tmp_path / "absent.txt")}),
            ToolCall("delete_file", {"path": "/"}),
        ],
        "en",
        use_cloud=True,  # pas de routeur : repli local, signalé
    )
    assert model.languages == ["en"] * 4
    notices = [r["text"] for r in replies if r["type"] == "notice"]
    assert notices == [
        "⚠ Cloud mode requested but no API key is set up: answer generated locally."
    ]
    summary = confirmation.asked[0]["summary"]
    assert summary.startswith("[level 1] write_file(")
    denied, missing, blocked = _results(replies)
    assert denied.startswith("Denied by the user: [level 1]")
    assert missing.startswith("read_file: not found: ")
    assert ". The folder " in missing and " contains: " in missing
    assert blocked.startswith("Blocked: [level 3]")
    assert replies[-1]["type"] == "done"


def test_requete_sans_langue_dans_la_langue_du_daemon(audit: AuditLog) -> None:
    model, _, replies = _run(audit, [ToolCall("delete_file", {"path": "/"})], None)
    assert model.languages == ["fr", "fr"]
    assert _results(replies)[0].startswith("Bloqué : [niveau 3]")


def test_langue_inconnue_dans_la_langue_du_daemon(audit: AuditLog) -> None:
    model, _, _ = _run(audit, [], "klingon")
    assert model.languages == ["fr"]


def test_langue_limitee_a_sa_requete(audit: AuditLog) -> None:
    _run(audit, [], "en")
    assert current_language() == "fr"


def test_langue_de_chaque_demande_notee_dans_l_historique(audit: AuditLog) -> None:
    seen: list[list[dict]] = []

    class _Model:
        def respond(self, messages, on_token=None) -> Plan:
            seen.append([dict(m) for m in messages])
            return Plan()

    orch = Orchestrator(
        model=_Model(),
        tools={},
        grants=SessionGrants(),
        audit=audit,
        confirmation_provider=_Confirmation(),
    )
    orch.handle({"id": "m1", "content": "bonjour", "language": "fr"}, lambda _m: None)
    orch.handle({"id": "m2", "content": "hello", "language": "en"}, lambda _m: None)
    users = [m for m in seen[-1] if m["role"] == "user"]
    assert [(m["content"], m["language"]) for m in users] == [
        ("bonjour", "fr"),
        ("hello", "en"),
    ]


@pytest.mark.parametrize(
    "call",
    [
        ToolCall("write_file", {"path": "/tmp/x", "content": "x"}),
        ToolCall("write_file", {"path": "~/.ssh/authorized_keys", "content": "x"}),
        ToolCall("delete_file", {"path": "/tmp/x"}),
        ToolCall("move_file", {"src": "/etc", "dst": "/tmp/etc"}),
    ],
)
def test_la_langue_ne_change_aucune_decision(audit: AuditLog, call: ToolCall) -> None:
    def decision(language: str) -> tuple:
        _, confirmation, replies = _run(audit, [call], language)
        asked = [
            (c["risk_level"], tuple(c["allowed_scopes"])) for c in confirmation.asked
        ]
        statuses = [r["status"] for r in replies if r["type"] == "tool_result"]
        return asked, statuses

    assert decision("fr") == decision("en")
