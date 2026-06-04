"""Tests du client Ollama avec un client mocké (pas de daemon Ollama requis)."""

# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from models.local_llm import FILE_TOOLS_SCHEMA, OllamaClient


def _history(user_message: str) -> list[dict]:
    return [{"role": "user", "content": user_message}]


def _chunk(
    *,
    content: str = "",
    tool_calls: list[tuple[str, dict]] | None = None,
) -> SimpleNamespace:
    """Construit un fragment de stream compatible avec ollama.ChatResponse."""
    calls = [
        SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))
        for name, args in (tool_calls or [])
    ]
    return SimpleNamespace(
        message=SimpleNamespace(content=content, tool_calls=calls or None)
    )


class _FakeClient:
    """Client Ollama factice : ``chat`` renvoie une suite de fragments (stream)."""

    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks
        self.last_kwargs: dict[str, Any] | None = None

    def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.last_kwargs = kwargs
        return self._chunks


def test_plan_assemble_narration_et_tool_calls_depuis_les_fragments() -> None:
    fake = _FakeClient(
        [
            _chunk(content="Je vais "),
            _chunk(content="lire /tmp/x."),
            _chunk(tool_calls=[("read_file", {"path": "/tmp/x"})]),
        ]
    )
    client = OllamaClient(client=fake)

    plan = client.respond(_history("lis /tmp/x"))

    assert plan.narration == "Je vais lire /tmp/x."
    assert len(plan.tool_calls) == 1
    assert plan.tool_calls[0].tool == "read_file"
    assert plan.tool_calls[0].args == {"path": "/tmp/x"}


def test_plan_streame_chaque_fragment_via_on_token() -> None:
    fake = _FakeClient(
        [_chunk(content="Bon"), _chunk(content="soir"), _chunk(content=" !")]
    )
    received: list[str] = []
    OllamaClient(client=fake).respond(_history("salut"), received.append)
    assert received == ["Bon", "soir", " !"]


def test_plan_active_le_streaming() -> None:
    fake = _FakeClient([_chunk(content="ok")])
    OllamaClient(client=fake).respond(_history("ping"))
    assert fake.last_kwargs is not None
    assert fake.last_kwargs["stream"] is True


def test_plan_passe_le_systeme_et_le_user_au_modele() -> None:
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake).respond(_history("salut"))

    assert fake.last_kwargs is not None
    messages = fake.last_kwargs["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user"]
    assert messages[1]["content"] == "salut"


def test_plan_envoie_le_schema_des_outils() -> None:
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake).respond(_history("ping"))

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
    fake = _FakeClient([_chunk(content="Je n'ai pas besoin d'outil.")])
    plan = OllamaClient(client=fake).respond(_history("dis-moi bonjour"))
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
        [_chunk(tool_calls=[("read_file", None)])]  # type: ignore[list-item]
    )
    plan = OllamaClient(client=fake).respond(_history("?"))
    assert plan.tool_calls[0].args == {}


def test_historique_est_traduit_au_format_ollama() -> None:
    # L'historique générique (user/assistant/tool) est passé après le system.
    fake = _FakeClient([_chunk(content="ok")])
    history = [
        {"role": "user", "content": "liste /tmp"},
        {"role": "assistant", "content": "je liste", "tool_calls": []},
        {"role": "tool", "tool": "list_dir", "content": "a.txt b.txt"},
    ]
    OllamaClient(client=fake).respond(history)

    assert fake.last_kwargs is not None
    sent = fake.last_kwargs["messages"]
    assert sent[0]["role"] == "system"
    assert [m["role"] for m in sent[1:]] == ["user", "assistant", "tool"]
    # Le résultat d'outil est transmis comme message role=tool.
    assert sent[-1] == {"role": "tool", "content": "a.txt b.txt"}
