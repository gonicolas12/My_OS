"""Tests du StubRuleModel (parser de commandes simples avant Ollama)."""

# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison
from __future__ import annotations

import pytest

from models.stub_model import StubRuleModel


def _history(user_message: str) -> list[dict]:
    return [{"role": "user", "content": user_message}]


@pytest.mark.parametrize(
    ("message", "expected_tool", "expected_args"),
    [
        ("lis /tmp/x.txt", "read_file", {"path": "/tmp/x.txt"}),
        ("read /etc/hosts", "read_file", {"path": "/etc/hosts"}),
        ("liste /tmp", "list_dir", {"path": "/tmp"}),
        ("ls /home/alice", "list_dir", {"path": "/home/alice"}),
        ("crée /tmp/new.txt", "create_file", {"path": "/tmp/new.txt"}),
        ("create /tmp/new.txt", "create_file", {"path": "/tmp/new.txt"}),
        ("supprime /tmp/x.txt", "delete_file", {"path": "/tmp/x.txt"}),
        ("rm /tmp/x.txt", "delete_file", {"path": "/tmp/x.txt"}),
        (
            "déplace /tmp/a vers /tmp/b",
            "move_file",
            {"src": "/tmp/a", "dst": "/tmp/b"},
        ),
        (
            "move /tmp/a to /tmp/b",
            "move_file",
            {"src": "/tmp/a", "dst": "/tmp/b"},
        ),
    ],
)
def test_patterns_simples_donnent_un_seul_tool_call(
    message: str, expected_tool: str, expected_args: dict
) -> None:
    plan = StubRuleModel().respond(_history(message))
    assert len(plan.tool_calls) == 1
    assert plan.tool_calls[0].tool == expected_tool
    assert plan.tool_calls[0].args == expected_args
    assert plan.narration  # narration non vide


def test_message_non_reconnu_renvoie_un_plan_vide_avec_aide() -> None:
    plan = StubRuleModel().respond(_history("salut comment ça va"))
    assert plan.tool_calls == []
    assert "stub" in plan.narration.lower()


def test_ecris_dans() -> None:
    plan = StubRuleModel().respond(_history("écris hello dans /tmp/out.txt"))
    assert len(plan.tool_calls) == 1
    call = plan.tool_calls[0]
    assert call.tool == "write_file"
    assert call.args == {"content": "hello", "path": "/tmp/out.txt"}


def test_premier_match_gagne() -> None:
    # « déplace … vers … » doit matcher AVANT que « lis » match ailleurs.
    plan = StubRuleModel().respond(
        _history("déplace /tmp/a vers /tmp/b puis lis /tmp/c")
    )
    assert plan.tool_calls[0].tool == "move_file"


def test_dernier_message_tool_donne_une_reponse_finale() -> None:
    # Après l'exécution d'un outil (message tool réinjecté), le stub conclut
    # sans demander de nouvel outil → la boucle de l'orchestrator s'arrête.
    history = [
        {"role": "user", "content": "lis /tmp/x"},
        {"role": "assistant", "content": "OK"},
        {"role": "tool", "tool": "read_file", "content": "contenu"},
    ]
    plan = StubRuleModel().respond(history)
    assert plan.tool_calls == []
    assert plan.narration


def test_on_token_recoit_la_narration() -> None:
    received: list[str] = []
    StubRuleModel().respond(_history("lis /tmp/x"), received.append)
    assert received and received[0]
