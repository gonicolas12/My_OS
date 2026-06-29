"""Tests du backend cloud et du routeur, sans réseau ni clé réelle.

Le client ``anthropic`` est un faux (streaming simulé) et ``keyring`` est mocké via
``models.secrets``. On vérifie : la traduction de l'historique au format Anthropic
(appariement tool_use ↔ tool_result), l'assemblage du Plan, le routage par
disponibilité de clé, et surtout que **la clé ne fuit jamais** dans ce qui est
envoyé au modèle.
"""

# pylint: disable=missing-function-docstring
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from daemon.orchestrator import ToolCall
from models import secrets
from models.cloud_router import (
    ClaudeClient,
    CloudRouter,
    CloudUnavailable,
    _to_anthropic,
    _to_anthropic_tools,
)
from models.local_llm import ALL_TOOLS_SCHEMA


# ---------- faux client anthropic (context manager de streaming) ----------


class _FakeStream:
    """Imite ``client.messages.stream(...)`` : itérable de texte + message final."""

    def __init__(self, text_parts: list[str], final_message: object) -> None:
        self.text_stream = list(text_parts)
        self._final = final_message

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def get_final_message(self) -> object:
        return self._final


class _FakeMessages:
    def __init__(self, stream: _FakeStream) -> None:
        self._stream = stream
        self.last_kwargs: dict | None = None

    def stream(self, **kwargs: object) -> _FakeStream:
        self.last_kwargs = kwargs
        return self._stream


class _FakeAnthropic:
    def __init__(self, stream: _FakeStream) -> None:
        self.messages = _FakeMessages(stream)


def _final(blocks: list[object]) -> SimpleNamespace:
    return SimpleNamespace(content=blocks)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=args, id="srv-id")


# ---------- ClaudeClient.respond ----------


def test_respond_assemble_narration_et_tool_calls() -> None:
    final = _final(
        [_text_block("Je lis."), _tool_use_block("read_file", {"path": "/tmp/x"})]
    )
    anthropic = _FakeAnthropic(_FakeStream(["Je ", "lis."], final))
    received: list[str] = []

    plan = ClaudeClient(client=anthropic).respond(
        [{"role": "user", "content": "lis /tmp/x"}], received.append
    )

    assert received == ["Je ", "lis."]  # streaming fragment par fragment
    assert plan.narration == "Je lis."
    assert len(plan.tool_calls) == 1
    assert plan.tool_calls[0].tool == "read_file"
    assert plan.tool_calls[0].args == {"path": "/tmp/x"}


def test_respond_envoie_systeme_outils_et_messages_traduits() -> None:
    anthropic = _FakeAnthropic(_FakeStream([], _final([_text_block("ok")])))
    ClaudeClient(client=anthropic, model="claude-sonnet-4-6").respond(
        [{"role": "user", "content": "salut"}]
    )

    kwargs = anthropic.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["messages"] == [{"role": "user", "content": "salut"}]
    names = {tool["name"] for tool in kwargs["tools"]}
    assert "read_file" in names and "install_package" in names
    system = kwargs["system"]
    assert "CLOUD" in system  # note de backend honnête
    assert os.path.expanduser("~") in system  # contexte machine partagé


def test_la_cle_n_apparait_jamais_dans_ce_qui_part_au_modele() -> None:
    anthropic = _FakeAnthropic(_FakeStream([], _final([_text_block("ok")])))
    ClaudeClient(client=anthropic, api_key="sk-ant-supersecret").respond(
        [{"role": "user", "content": "coucou"}]
    )

    kwargs = anthropic.messages.last_kwargs
    assert kwargs is not None
    assert "sk-ant-supersecret" not in kwargs["system"]
    assert "sk-ant-supersecret" not in str(kwargs["messages"])
    assert "sk-ant-supersecret" not in str(kwargs["tools"])


# ---------- traduction des outils et de l'historique ----------


def test_to_anthropic_tools_format() -> None:
    tools = _to_anthropic_tools(ALL_TOOLS_SCHEMA)
    sample = next(tool for tool in tools if tool["name"] == "read_file")
    assert sample["description"]
    assert sample["input_schema"]["type"] == "object"
    # Plus de wrapper « function » : format Anthropic (name/description/input_schema).
    assert all("function" not in tool for tool in tools)


def test_to_anthropic_apparie_tool_use_et_tool_result() -> None:
    out = _to_anthropic(
        [
            {"role": "user", "content": "liste /tmp"},
            {
                "role": "assistant",
                "content": "je liste",
                "tool_calls": [ToolCall("list_dir", {"path": "/tmp"})],
            },
            {"role": "tool", "tool": "list_dir", "content": "a.txt b.txt"},
        ]
    )

    assert out[0] == {"role": "user", "content": "liste /tmp"}
    assert out[1]["role"] == "assistant"
    text_block, tool_use = out[1]["content"]
    assert text_block == {"type": "text", "text": "je liste"}
    assert tool_use["type"] == "tool_use"
    assert tool_use["name"] == "list_dir"
    assert tool_use["input"] == {"path": "/tmp"}
    assert out[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": tool_use["id"],  # même id que le tool_use
                "content": "a.txt b.txt",
            }
        ],
    }


def test_to_anthropic_assistant_sans_texte_n_emet_que_le_tool_use() -> None:
    out = _to_anthropic(
        [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [ToolCall("list_dir", {})],
            },
            {"role": "tool", "tool": "list_dir", "content": "r"},
        ]
    )
    blocks = out[1]["content"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "tool_use"


def test_to_anthropic_apparie_plusieurs_outils_dans_l_ordre() -> None:
    out = _to_anthropic(
        [
            {"role": "user", "content": "deux"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [ToolCall("a", {}), ToolCall("b", {})],
            },
            {"role": "tool", "tool": "a", "content": "ra"},
            {"role": "tool", "tool": "b", "content": "rb"},
        ]
    )
    ids = [block["id"] for block in out[1]["content"]]
    assert len(ids) == 2 and ids[0] != ids[1]  # ids uniques
    assert out[2]["content"][0]["tool_use_id"] == ids[0]
    assert out[2]["content"][0]["content"] == "ra"
    assert out[3]["content"][0]["tool_use_id"] == ids[1]
    assert out[3]["content"][0]["content"] == "rb"


# ---------- CloudRouter : disponibilité et cache ----------


def test_router_indisponible_sans_cle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets, "has_api_key", lambda: False)
    monkeypatch.setattr(secrets, "get_api_key", lambda: None)

    router = CloudRouter()
    assert router.is_available() is False
    with pytest.raises(CloudUnavailable):
        router.get_cloud_model()


def test_router_construit_via_la_fabrique_et_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets, "has_api_key", lambda: True)
    monkeypatch.setattr(secrets, "get_api_key", lambda: "sk-test")
    built: list[object] = []
    sentinel = object()

    def factory() -> object:
        built.append(sentinel)
        return sentinel

    router = CloudRouter(client_factory=factory)
    assert router.is_available() is True
    assert router.get_cloud_model() is sentinel
    assert router.get_cloud_model() is sentinel  # caché
    assert len(built) == 1  # construit une seule fois


def test_router_reconstruit_si_la_cle_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets, "has_api_key", lambda: True)
    current = {"key": "sk-1"}
    monkeypatch.setattr(secrets, "get_api_key", lambda: current["key"])

    built: list[object] = []

    def factory() -> object:
        obj = object()
        built.append(obj)
        return obj

    router = CloudRouter(client_factory=factory)
    first = router.get_cloud_model()
    current["key"] = "sk-2"  # l'utilisateur corrige sa clé
    second = router.get_cloud_model()

    assert first is not second
    assert len(built) == 2  # reconstruit car l'empreinte de clé a changé
