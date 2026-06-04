"""Tests bout-en-bout de l'orchestrator (boucle agentique) sans Ollama.

Le modèle est scripté (séquence de Plans) ou inspecte l'historique reçu, ce qui
permet de tester la chaîne respond → policy → audit → exécution → réinjection
sans dépendre d'un LLM réel (cf. CLAUDE.md « mock-first »).
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,use-implicit-booleaness-not-comparison,unused-argument
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from daemon.orchestrator import MAX_STEPS, Orchestrator, Plan, ToolCall
from permissions.audit_log import AuditLog
from permissions.confirmation import ConfirmationResponse
from permissions.session_grants import SessionGrants
from tools.base_tool import BaseTool, ToolResult


# ---------- doubles de test : modèles ----------


class _ScriptedModel:
    """Renvoie un Plan par appel ; une fois la liste épuisée, réponse finale vide.

    La réponse finale par défaut a une narration vide (aucun token superflu) et
    aucun outil → la boucle de l'orchestrator s'arrête proprement.
    """

    def __init__(self, plans: list[Plan]) -> None:
        self._plans = list(plans)
        self._calls = 0

    def respond(self, messages, on_token=None) -> Plan:
        plan = self._plans[self._calls] if self._calls < len(self._plans) else Plan()
        self._calls += 1
        if on_token is not None and plan.narration:
            on_token(plan.narration)
        return plan


class _FailingModel:
    def respond(self, messages, on_token=None) -> Plan:
        raise RuntimeError("modèle indisponible")


class _StreamingModel:
    """Émet la narration fragment par fragment, sans outil (réponse finale)."""

    def __init__(self, fragments: list[str]) -> None:
        self._fragments = fragments

    def respond(self, messages, on_token=None) -> Plan:
        for fragment in self._fragments:
            if on_token is not None:
                on_token(fragment)
        return Plan(narration="".join(self._fragments), tool_calls=[])


# ---------- doubles de test : confirmation + outils ----------


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


class _Lister(BaseTool):
    name = "list_dir"
    description = "stub niveau 0"
    risk_level = 0

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="a.txt b.txt")


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


class _NormalizingReader(BaseTool):
    """Outil niveau 0 qui normalise ~ en /home/test, pour vérifier le câblage."""

    name = "read_file"
    description = "lecture avec normalisation"
    risk_level = 0

    def normalize_args(self, args: dict) -> dict:
        return {**args, "path": args.get("path", "").replace("~", "/home/test")}

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output=f"lu {args['path']}")


# ---------- fixtures / helpers ----------


@pytest.fixture
def audit(tmp_path: Path) -> Iterator[AuditLog]:
    log = AuditLog(tmp_path / "audit.db")
    yield log
    log.close()


def _build(
    *,
    plans: list[Plan],
    confirmation_responses: list[ConfirmationResponse] | None = None,
    tools: dict[str, BaseTool] | None = None,
    audit: AuditLog,
) -> tuple[Orchestrator, list[dict], _FakeConfirmation, SessionGrants]:
    grants = SessionGrants()
    confirmation = _FakeConfirmation(confirmation_responses or [])
    tools = (
        tools
        if tools is not None
        else {
            "read_file": _SafeReader(),
            "list_dir": _Lister(),
            "write_file": _Writer(),
            "delete_file": _Deleter(),
            "boom": _RaisingTool(),
        }
    )
    orch = Orchestrator(
        model=_ScriptedModel(plans),
        tools=tools,
        grants=grants,
        audit=audit,
        confirmation_provider=confirmation,
    )
    return orch, [], confirmation, grants


def _types(replies: list[dict]) -> list[str]:
    return [r["type"] for r in replies]


def _tokens(replies: list[dict]) -> list[str]:
    return [r["text"] for r in replies if r["type"] == "token"]


# ---------- réponses sans outil ----------


def test_reponse_finale_sans_outil_envoie_narration_et_done(audit: AuditLog) -> None:
    orch, replies, _, _ = _build(
        plans=[Plan(narration="rien à faire", tool_calls=[])], audit=audit
    )
    orch.handle({"id": "m1", "content": "ping"}, replies.append)

    assert _types(replies) == ["token", "done"]
    assert replies[0]["text"] == "rien à faire"
    assert audit.fetch_all() == []


def test_message_vide_donne_une_erreur(audit: AuditLog) -> None:
    orch, replies, _, _ = _build(plans=[Plan()], audit=audit)
    orch.handle({"id": "m1", "content": "   "}, replies.append)
    assert _types(replies) == ["error"]


def test_modele_qui_plante_donne_une_erreur(audit: AuditLog) -> None:
    orch = Orchestrator(
        model=_FailingModel(),
        tools={},
        grants=SessionGrants(),
        audit=audit,
        confirmation_provider=_FakeConfirmation([]),
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "boum"}, replies.append)
    assert _types(replies) == ["error"]
    assert "modèle" in replies[0]["message"] or "indisponible" in replies[0]["message"]


# ---------- un outil par niveau ----------


def test_outil_niveau_0_execute_directement_et_audit_auto(audit: AuditLog) -> None:
    orch, replies, conf, _ = _build(
        plans=[Plan(tool_calls=[ToolCall("read_file", {"path": "/tmp/x"})])],
        audit=audit,
    )
    orch.handle({"id": "m1", "content": "lis"}, replies.append)

    assert conf.asked == []  # niveau 0 : aucune confirmation
    assert any("contenu de /tmp/x" in t for t in _tokens(replies))
    assert replies[-1]["type"] == "done"
    entries = audit.fetch_all()
    assert len(entries) == 1
    assert entries[0]["decision"] == "auto"
    assert entries[0]["success"] is True


def test_outil_niveau_1_demande_confirmation_et_execute_si_approuve(
    audit: AuditLog,
) -> None:
    orch, replies, conf, _ = _build(
        plans=[Plan(tool_calls=[ToolCall("write_file", {"path": "/tmp/x"})])],
        confirmation_responses=[
            ConfirmationResponse(request_id="x", decision="approve_once")
        ],
        audit=audit,
    )
    orch.handle({"id": "m1", "content": "écris"}, replies.append)

    assert len(conf.asked) == 1
    payload = conf.asked[0]
    assert payload["type"] == "confirmation_needed"
    assert payload["tool"] == "write_file"
    assert payload["risk_level"] == 1
    assert any("écrit /tmp/x" in t for t in _tokens(replies))
    entries = audit.fetch_all()
    assert entries[0]["decision"] == "approved"
    assert entries[0]["success"] is True


def test_outil_confirme_mais_refuse_n_est_pas_execute(audit: AuditLog) -> None:
    orch, replies, _, _ = _build(
        plans=[Plan(tool_calls=[ToolCall("write_file", {"path": "/tmp/x"})])],
        confirmation_responses=[
            ConfirmationResponse(request_id="rid", decision="deny")
        ],
        audit=audit,
    )
    orch.handle({"id": "m1", "content": "écris"}, replies.append)

    assert any("Refusé" in t for t in _tokens(replies))
    entries = audit.fetch_all()
    assert entries[0]["decision"] == "denied"
    assert entries[0]["success"] is False


def test_outil_niveau_2_demande_confirmation_renforcee(audit: AuditLog) -> None:
    orch, replies, conf, _ = _build(
        plans=[Plan(tool_calls=[ToolCall("delete_file", {"path": "/tmp/x"})])],
        confirmation_responses=[
            ConfirmationResponse(request_id="x", decision="approve_once")
        ],
        audit=audit,
    )
    orch.handle({"id": "m1", "content": "supprime"}, replies.append)

    assert conf.asked[0]["risk_level"] == 2
    entries = audit.fetch_all()
    assert entries[0]["decision"] == "approved"


# ---------- blocages ----------


def test_outil_inconnu_est_bloque_et_audite(audit: AuditLog) -> None:
    orch, replies, _, _ = _build(
        plans=[Plan(tool_calls=[ToolCall("hypothetical_tool", {})])],
        audit=audit,
        tools={},
    )
    orch.handle({"id": "m1", "content": "?"}, replies.append)

    assert any("Outil inconnu" in t for t in _tokens(replies))
    entries = audit.fetch_all()
    assert entries[0]["decision"] == "blocked"
    assert entries[0]["risk_level"] == 3
    assert entries[0]["success"] is False


def test_outil_bloque_par_blocklist_n_est_pas_execute(audit: AuditLog) -> None:
    orch, replies, _, _ = _build(
        plans=[Plan(tool_calls=[ToolCall("delete_file", {"path": "/etc"})])],
        audit=audit,
    )
    orch.handle({"id": "m1", "content": "supprime"}, replies.append)

    assert any("Bloqué" in t for t in _tokens(replies))
    entries = audit.fetch_all()
    assert entries[0]["decision"] == "blocked"


# ---------- grants (plusieurs outils dans un tour) ----------


def test_approve_scope_this_folder_cree_un_grant(audit: AuditLog) -> None:
    orch, replies, conf, grants = _build(
        plans=[
            Plan(
                tool_calls=[
                    ToolCall("write_file", {"path": "/home/alice/Downloads/a.txt"}),
                    ToolCall("write_file", {"path": "/home/alice/Downloads/b.txt"}),
                ]
            )
        ],
        confirmation_responses=[
            ConfirmationResponse(
                request_id="rid", decision="approve_scope", scope="this_folder"
            )
        ],
        audit=audit,
    )
    orch.handle({"id": "m1", "content": "écris deux fichiers"}, replies.append)

    assert len(conf.asked) == 1  # une seule confirmation pour deux écritures
    assert grants.is_granted("write_file", ["/home/alice/Downloads/b.txt"]) is True
    entries = audit.fetch_all()
    assert [e["decision"] for e in entries] == ["approved", "auto"]


def test_approve_scope_session_couvre_tous_les_appels(audit: AuditLog) -> None:
    orch, replies, conf, _ = _build(
        plans=[
            Plan(
                tool_calls=[
                    ToolCall("delete_file", {"path": "/tmp/a"}),
                    ToolCall("delete_file", {"path": "/tmp/b"}),
                    ToolCall("delete_file", {"path": "/tmp/c"}),
                ]
            )
        ],
        confirmation_responses=[
            ConfirmationResponse(
                request_id="rid", decision="approve_scope", scope="session"
            )
        ],
        audit=audit,
    )
    orch.handle({"id": "m1", "content": "tri massif"}, replies.append)

    assert len(conf.asked) == 1
    entries = audit.fetch_all()
    assert [e["decision"] for e in entries] == ["approved", "auto", "auto"]


# ---------- exécution / normalisation ----------


def test_outil_qui_plante_dans_run_audite_un_echec(audit: AuditLog) -> None:
    orch, replies, _, _ = _build(
        plans=[Plan(tool_calls=[ToolCall("boom", {})])], audit=audit
    )
    orch.handle({"id": "m1", "content": "fais boum"}, replies.append)

    assert any("Erreur" in t for t in _tokens(replies))
    entries = audit.fetch_all()
    assert entries[0]["success"] is False


def test_normalize_args_est_applique_avant_run_et_audit(audit: AuditLog) -> None:
    orch, replies, _, _ = _build(
        plans=[Plan(tool_calls=[ToolCall("read_file", {"path": "~/notes.txt"})])],
        audit=audit,
        tools={"read_file": _NormalizingReader()},
    )
    orch.handle({"id": "m1", "content": "lis"}, replies.append)

    assert any("lu /home/test/notes.txt" in t for t in _tokens(replies))
    entries = audit.fetch_all()
    assert entries[0]["args"]["path"] == "/home/test/notes.txt"


# ---------- streaming ----------


def test_streaming_emet_un_token_par_fragment(audit: AuditLog) -> None:
    orch = Orchestrator(
        model=_StreamingModel(["Bon", "soir", " !"]),
        tools={},
        grants=SessionGrants(),
        audit=audit,
        confirmation_provider=_FakeConfirmation([]),
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "salut"}, replies.append)

    assert _tokens(replies) == ["Bon", "soir", " !"]
    assert replies[-1]["type"] == "done"


def test_fallback_si_modele_ne_streame_pas(audit: AuditLog) -> None:
    class _NonStreaming:
        def respond(self, messages, on_token=None) -> Plan:
            return Plan(narration="réponse complète", tool_calls=[])

    orch = Orchestrator(
        model=_NonStreaming(),
        tools={},
        grants=SessionGrants(),
        audit=audit,
        confirmation_provider=_FakeConfirmation([]),
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "?"}, replies.append)
    assert _tokens(replies) == ["réponse complète"]


def test_done_est_toujours_envoye_a_la_fin(audit: AuditLog) -> None:
    orch, replies, _, _ = _build(
        plans=[
            Plan(
                tool_calls=[
                    ToolCall("read_file", {"path": "/tmp/x"}),
                    ToolCall("delete_file", {"path": "/etc"}),  # bloqué
                ]
            )
        ],
        audit=audit,
    )
    orch.handle({"id": "m1", "content": "..."}, replies.append)
    assert replies[-1]["type"] == "done"


# ---------- boucle agentique ----------


class _TwoStepModel:
    """Tour 1 : liste un dossier. Tour 2 (après avoir vu le résultat) : conclut."""

    def __init__(self) -> None:
        self.turns_seen: list[int] = []

    def respond(self, messages, on_token=None) -> Plan:
        tool_results = [m for m in messages if m.get("role") == "tool"]
        self.turns_seen.append(len(tool_results))
        if not tool_results:
            return Plan(
                narration="Je liste d'abord.",
                tool_calls=[ToolCall("list_dir", {"path": "/tmp"})],
            )
        # Le résultat du list_dir a bien été réinjecté dans l'historique.
        return Plan(narration=f"Le dossier contient : {tool_results[0]['content']}")


def test_boucle_reinjecte_le_resultat_au_modele(audit: AuditLog) -> None:
    model = _TwoStepModel()
    orch = Orchestrator(
        model=model,
        tools={"list_dir": _Lister()},
        grants=SessionGrants(),
        audit=audit,
        confirmation_provider=_FakeConfirmation([]),
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "qu'y a-t-il dans /tmp ?"}, replies.append)

    # Deux tours : le 1er sans résultat, le 2e avec le résultat du list_dir.
    assert model.turns_seen == [0, 1]
    # La conclusion du modèle cite le résultat de l'outil (preuve de réinjection).
    assert any("a.txt b.txt" in t for t in _tokens(replies))
    assert replies[-1]["type"] == "done"
    # list_dir a bien été exécuté et audité.
    assert [e["tool"] for e in audit.fetch_all()] == ["list_dir"]


class _NeverStopsModel:
    """Renvoie toujours un appel d'outil → ne conclut jamais (test du plafond)."""

    def respond(self, messages, on_token=None) -> Plan:
        return Plan(tool_calls=[ToolCall("read_file", {"path": "/tmp/x"})])


def test_max_steps_interrompt_la_boucle(audit: AuditLog) -> None:
    orch = Orchestrator(
        model=_NeverStopsModel(),
        tools={"read_file": _SafeReader()},
        grants=SessionGrants(),
        audit=audit,
        confirmation_provider=_FakeConfirmation([]),
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "boucle"}, replies.append)

    # La boucle s'arrête : un seul outil exécuté par tour, plafonné à MAX_STEPS.
    assert len(audit.fetch_all()) == MAX_STEPS
    assert any("Limite d'itérations" in t for t in _tokens(replies))
    assert replies[-1]["type"] == "done"
