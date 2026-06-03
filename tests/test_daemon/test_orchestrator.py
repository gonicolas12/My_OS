"""Tests bout-en-bout de l'orchestrator avec FakeLLM + FakeConfirmation.

Vérifient la chaîne plan → policy → audit → exécution **sans** Ollama :
l'orchestrator est piloté par un modèle mocké qui renvoie un ``Plan`` connu,
et par un fournisseur de confirmation qui débite des réponses pré-écrites.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,use-implicit-booleaness-not-comparison
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from daemon.orchestrator import Orchestrator, Plan, ToolCall
from permissions.audit_log import AuditLog
from permissions.confirmation import ConfirmationResponse
from permissions.session_grants import SessionGrants
from tools.base_tool import BaseTool, ToolResult


# ---------- doubles de test ----------


class _FakeLLM:
    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    def plan(self, _user_message: str) -> Plan:
        return self._plan


class _FailingLLM:
    def plan(self, _user_message: str) -> Plan:
        raise RuntimeError("modèle indisponible")


class _FakeConfirmation:
    def __init__(self, responses: list[ConfirmationResponse]) -> None:
        self._responses = list(responses)
        self.asked: list[dict] = []

    def ask(self, payload: dict) -> ConfirmationResponse:
        self.asked.append(payload)
        if not self._responses:
            raise RuntimeError("aucune réponse de confirmation préparée")
        return self._responses.pop(0)


class _SafeReader(BaseTool):
    name = "read_file"
    description = "stub niveau 0"
    risk_level = 0

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output=f"contenu de {args['path']}")


class _Writer(BaseTool):
    name = "write_file"
    description = "stub niveau 1"
    risk_level = 1

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output=f"écrit {args['path']}")


class _Deleter(BaseTool):
    name = "delete_file"
    description = "stub niveau 2"
    risk_level = 2

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output=f"supprimé {args['path']}")


class _RaisingTool(BaseTool):
    name = "boom"
    description = "outil qui plante"
    risk_level = 0

    def run(self, args: dict) -> ToolResult:
        raise RuntimeError("explosion volontaire")


# ---------- fixtures ----------


@pytest.fixture
def audit(tmp_path: Path) -> Iterator[AuditLog]:
    log = AuditLog(tmp_path / "audit.db")
    yield log
    log.close()


def _build(
    *,
    plan: Plan,
    confirmation_responses: list[ConfirmationResponse] | None = None,
    tools: dict[str, BaseTool] | None = None,
    audit: AuditLog,
) -> tuple[Orchestrator, list[dict], _FakeConfirmation, SessionGrants]:
    grants = SessionGrants()
    confirmation = _FakeConfirmation(confirmation_responses or [])
    tools = tools or {
        "read_file": _SafeReader(),
        "write_file": _Writer(),
        "delete_file": _Deleter(),
        "boom": _RaisingTool(),
    }
    orch = Orchestrator(
        model=_FakeLLM(plan),
        tools=tools,
        grants=grants,
        audit=audit,
        confirmation_provider=confirmation,
    )
    replies: list[dict] = []
    return orch, replies, confirmation, grants


def _types(replies: list[dict]) -> list[str]:
    return [r["type"] for r in replies]


# ---------- tests ----------


def test_plan_vide_envoie_juste_narration_et_done(audit: AuditLog) -> None:
    plan = Plan(narration="rien à faire", tool_calls=[])
    orch, replies, _, _ = _build(plan=plan, audit=audit)

    orch.handle({"id": "m1", "content": "ping"}, replies.append)

    assert _types(replies) == ["token", "done"]
    assert replies[0]["text"] == "rien à faire"
    assert audit.fetch_all() == []


def test_message_vide_donne_une_erreur(audit: AuditLog) -> None:
    orch, replies, _, _ = _build(plan=Plan(), audit=audit)
    orch.handle({"id": "m1", "content": "   "}, replies.append)
    assert _types(replies) == ["error"]


def test_modele_qui_plante_donne_une_erreur(audit: AuditLog) -> None:
    orch = Orchestrator(
        model=_FailingLLM(),
        tools={},
        grants=SessionGrants(),
        audit=audit,
        confirmation_provider=_FakeConfirmation([]),
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "boum"}, replies.append)
    assert _types(replies) == ["error"]
    assert "modèle" in replies[0]["message"] or "indisponible" in replies[0]["message"]


def test_outil_niveau_0_execute_directement_et_audit_auto(audit: AuditLog) -> None:
    plan = Plan(tool_calls=[ToolCall("read_file", {"path": "/tmp/x"})])
    orch, replies, conf, _ = _build(plan=plan, audit=audit)

    orch.handle({"id": "m1", "content": "lis"}, replies.append)

    # Pas de confirmation demandée pour niveau 0.
    assert conf.asked == []
    assert _types(replies) == ["token", "done"]
    assert "contenu de /tmp/x" in replies[0]["text"]
    entries = audit.fetch_all()
    assert len(entries) == 1
    assert entries[0]["decision"] == "auto"
    assert entries[0]["success"] is True


def test_outil_niveau_1_demande_confirmation_et_execute_si_approuve(
    audit: AuditLog,
) -> None:
    plan = Plan(tool_calls=[ToolCall("write_file", {"path": "/tmp/x"})])
    orch, replies, conf, _ = _build(
        plan=plan,
        confirmation_responses=[
            ConfirmationResponse(request_id="will-be-replaced", decision="approve_once")
        ],
        audit=audit,
    )

    orch.handle({"id": "m1", "content": "écris"}, replies.append)

    assert len(conf.asked) == 1
    payload = conf.asked[0]
    assert payload["type"] == "confirmation_needed"
    assert payload["tool"] == "write_file"
    assert payload["risk_level"] == 1
    assert "écrit /tmp/x" in replies[0]["text"]
    entries = audit.fetch_all()
    assert entries[0]["decision"] == "approved"
    assert entries[0]["success"] is True


def test_outil_confirme_mais_refuse_n_est_pas_execute(audit: AuditLog) -> None:
    plan = Plan(tool_calls=[ToolCall("write_file", {"path": "/tmp/x"})])
    orch, replies, _, _ = _build(
        plan=plan,
        confirmation_responses=[
            ConfirmationResponse(request_id="rid", decision="deny")
        ],
        audit=audit,
    )

    orch.handle({"id": "m1", "content": "écris"}, replies.append)

    assert any("Refusé" in r["text"] for r in replies if r["type"] == "token")
    entries = audit.fetch_all()
    assert entries[0]["decision"] == "denied"
    assert entries[0]["success"] is False


def test_outil_inconnu_est_bloque_et_audite(audit: AuditLog) -> None:
    plan = Plan(tool_calls=[ToolCall("hypothetical_tool", {})])
    orch, replies, _, _ = _build(plan=plan, audit=audit, tools={})

    orch.handle({"id": "m1", "content": "?"}, replies.append)

    assert any("Outil inconnu" in r["text"] for r in replies if r["type"] == "token")
    entries = audit.fetch_all()
    assert entries[0]["decision"] == "blocked"
    assert entries[0]["risk_level"] == 3
    assert entries[0]["success"] is False


def test_outil_bloque_par_blocklist_n_est_pas_execute(audit: AuditLog) -> None:
    # delete_file sur /etc → bloqué par la blocklist absolue.
    plan = Plan(tool_calls=[ToolCall("delete_file", {"path": "/etc"})])
    orch, replies, _, _ = _build(plan=plan, audit=audit)

    orch.handle({"id": "m1", "content": "supprime"}, replies.append)

    assert any("Bloqué" in r["text"] for r in replies if r["type"] == "token")
    entries = audit.fetch_all()
    assert entries[0]["decision"] == "blocked"


def test_approve_scope_this_folder_cree_un_grant(audit: AuditLog) -> None:
    plan = Plan(
        tool_calls=[
            ToolCall("write_file", {"path": "/home/alice/Downloads/a.txt"}),
            ToolCall("write_file", {"path": "/home/alice/Downloads/b.txt"}),
        ]
    )
    orch, replies, conf, grants = _build(
        plan=plan,
        confirmation_responses=[
            ConfirmationResponse(
                request_id="rid", decision="approve_scope", scope="this_folder"
            ),
            # PAS de deuxième réponse : le second appel doit passer en auto
            # grâce au grant créé par le premier.
        ],
        audit=audit,
    )

    orch.handle({"id": "m1", "content": "écris deux fichiers"}, replies.append)

    # Une seule demande de confirmation pour deux écritures.
    assert len(conf.asked) == 1
    assert grants.is_granted("write_file", ["/home/alice/Downloads/b.txt"]) is True
    entries = audit.fetch_all()
    assert len(entries) == 2
    assert entries[0]["decision"] == "approved"
    assert entries[1]["decision"] == "auto"  # 2ᵉ passe via le grant


def test_approve_scope_session_couvre_tous_les_appels(audit: AuditLog) -> None:
    plan = Plan(
        tool_calls=[
            ToolCall("delete_file", {"path": "/tmp/a"}),
            ToolCall("delete_file", {"path": "/tmp/b"}),
            ToolCall("delete_file", {"path": "/tmp/c"}),
        ]
    )
    orch, replies, conf, _ = _build(
        plan=plan,
        confirmation_responses=[
            ConfirmationResponse(
                request_id="rid", decision="approve_scope", scope="session"
            )
        ],
        audit=audit,
    )

    orch.handle({"id": "m1", "content": "tri massif"}, replies.append)

    # Confirmation demandée UNE fois pour trois suppressions.
    assert len(conf.asked) == 1
    entries = audit.fetch_all()
    assert [e["decision"] for e in entries] == ["approved", "auto", "auto"]


def test_outil_qui_plante_dans_run_audite_un_echec(audit: AuditLog) -> None:
    plan = Plan(tool_calls=[ToolCall("boom", {})])
    orch, replies, _, _ = _build(plan=plan, audit=audit)

    orch.handle({"id": "m1", "content": "fais boum"}, replies.append)

    assert any("Erreur" in r.get("text", "") for r in replies)
    entries = audit.fetch_all()
    assert entries[0]["success"] is False


def test_narration_est_streamee_avant_les_outils(audit: AuditLog) -> None:
    plan = Plan(
        narration="je vais lire le fichier",
        tool_calls=[ToolCall("read_file", {"path": "/tmp/x"})],
    )
    orch, replies, _, _ = _build(plan=plan, audit=audit)

    orch.handle({"id": "m1", "content": "lis"}, replies.append)

    tokens = [r for r in replies if r["type"] == "token"]
    assert tokens[0]["text"] == "je vais lire le fichier"
    assert "contenu de" in tokens[1]["text"]


def test_done_est_toujours_envoye_a_la_fin(audit: AuditLog) -> None:
    plan = Plan(
        tool_calls=[
            ToolCall("read_file", {"path": "/tmp/x"}),
            ToolCall("delete_file", {"path": "/etc"}),  # bloqué
        ]
    )
    orch, replies, _, _ = _build(plan=plan, audit=audit)
    orch.handle({"id": "m1", "content": "..."}, replies.append)
    assert replies[-1]["type"] == "done"
