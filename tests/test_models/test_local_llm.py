"""Tests du client Ollama avec un client mocké (pas de daemon Ollama requis)."""

# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from models.local_llm import FILE_TOOLS_SCHEMA, OllamaClient


def _fake_response(
    *,
    content: str = "",
    tool_calls: list[tuple[str, dict]] | None = None,
) -> SimpleNamespace:
    """Construit une réponse minimale compatible avec ollama.ChatResponse."""
    calls = [
        SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))
        for name, args in (tool_calls or [])
    ]
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=calls or None)
    )


class _FakeClient:
    def __init__(self, response: SimpleNamespace) -> None:
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None

    def chat(self, **kwargs: Any) -> SimpleNamespace:
        self.last_kwargs = kwargs
        return self._response


def test_plan_renvoie_narration_et_tool_calls() -> None:
    fake = _FakeClient(
        _fake_response(
            content="Je vais lire /tmp/x.",
            tool_calls=[("read_file", {"path": "/tmp/x"})],
        )
    )
    client = OllamaClient(client=fake)

    plan = client.plan("lis /tmp/x")

    assert plan.narration == "Je vais lire /tmp/x."
    assert len(plan.tool_calls) == 1
    assert plan.tool_calls[0].tool == "read_file"
    assert plan.tool_calls[0].args == {"path": "/tmp/x"}


def test_plan_passe_le_systeme_et_le_user_au_modele() -> None:
    fake = _FakeClient(_fake_response())
    OllamaClient(client=fake).plan("salut")

    assert fake.last_kwargs is not None
    messages = fake.last_kwargs["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user"]
    assert messages[1]["content"] == "salut"


def test_plan_envoie_le_schema_des_outils() -> None:
    fake = _FakeClient(_fake_response())
    OllamaClient(client=fake).plan("ping")

    assert fake.last_kwargs is not None
    tools = fake.last_kwargs["tools"]
    names = {t["function"]["name"] for t in tools}
    assert names == {
        "read_file",
        "list_dir",
        "write_file",
        "create_file",
        "move_file",
        "delete_file",
    }


def test_plan_sans_tool_calls_donne_un_plan_de_narration_seule() -> None:
    fake = _FakeClient(_fake_response(content="Je n'ai pas besoin d'outil."))
    plan = OllamaClient(client=fake).plan("dis-moi bonjour")
    assert plan.tool_calls == []
    assert plan.narration == "Je n'ai pas besoin d'outil."


def test_schema_couvre_tous_les_outils_du_jalon_2() -> None:
    # Le schéma envoyé au LLM doit être le même que la liste connue.
    names = {entry["function"]["name"] for entry in FILE_TOOLS_SCHEMA}
    assert names == {
        "read_file",
        "list_dir",
        "write_file",
        "create_file",
        "move_file",
        "delete_file",
    }
    for entry in FILE_TOOLS_SCHEMA:
        assert entry["type"] == "function"
        fn = entry["function"]
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"
        assert fn["parameters"]["required"]


def test_arguments_none_devient_dict_vide() -> None:
    # Certains modèles renvoient arguments=None ; on ne doit pas paniquer.
    fake = _FakeClient(
        _fake_response(
            tool_calls=[("read_file", None)],  # type: ignore[arg-type]
        )
    )
    plan = OllamaClient(client=fake).plan("?")
    assert plan.tool_calls[0].args == {}
