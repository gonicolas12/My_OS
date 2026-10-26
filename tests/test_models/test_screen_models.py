"""Contrôle de l'ordinateur côté modèles : outils et consignes de l'écran, capture
d'écran jointe au résultat d'outil (local : selon la vitesse de lecture ; cloud :
toujours)."""

# pylint: disable=missing-function-docstring
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from daemon.orchestrator import ToolCall
from models.cloud_router import ClaudeClient, _to_anthropic
from models.local_llm import (
    BASE_TOOLS_SCHEMA,
    COMPUTER_TOOLS_SCHEMA,
    SCREEN_PROMPT,
    VISION_MIN_GENERATION_RATE,
    OllamaClient,
    UnreadableResponse,
)
from permissions.risk_levels import COMPUTER_TOOLS


def _final_chunk(
    *,
    generated: int = 0,
    seconds: float = 1.0,
    prompt_tokens: int = 0,
    prompt_seconds: float = 1.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        message=SimpleNamespace(content="ok", tool_calls=None),
        done=True,
        eval_count=generated,
        eval_duration=int(seconds * 1e9),
        prompt_eval_count=prompt_tokens,
        prompt_eval_duration=int(prompt_seconds * 1e9),
    )


class _FakeOllama:
    def __init__(self, chunk: SimpleNamespace | None = None) -> None:
        self.chunk = chunk or _final_chunk()
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(kwargs)
        return [self.chunk]


def _screen_history() -> list[dict]:
    return [
        {"role": "user", "content": "écris bonjour"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [ToolCall("observe_screen", {})],
        },
        {
            "role": "tool",
            "tool": "observe_screen",
            "content": "[1] bouton « OK »",
            "image": b"\xff\xd8jpeg",
        },
    ]


def _images(call: dict[str, Any]) -> list:
    return [m.get("images") for m in call["messages"] if m.get("images")]


def test_schemas_de_l_ecran_alignes_sur_les_permissions() -> None:
    names = {entry["function"]["name"] for entry in COMPUTER_TOOLS_SCHEMA}
    assert names == COMPUTER_TOOLS


def test_outils_et_consignes_de_l_ecran_retires_si_desactive() -> None:
    fake = _FakeOllama()
    OllamaClient(client=fake, screen_control=False).respond(_screen_history())
    call = fake.calls[0]
    assert call["tools"] == BASE_TOOLS_SCHEMA
    assert SCREEN_PROMPT not in call["messages"][0]["content"]
    with_screen = _FakeOllama()
    OllamaClient(client=with_screen).respond(_screen_history())
    assert SCREEN_PROMPT in with_screen.calls[0]["messages"][0]["content"]


def test_vision_off_et_on() -> None:
    off = _FakeOllama()
    OllamaClient(client=off, vision="off").respond(_screen_history())
    assert _images(off.calls[0]) == []
    on = _FakeOllama()
    OllamaClient(client=on, vision="on").respond(_screen_history())
    assert _images(on.calls[0]) == [[b"\xff\xd8jpeg"]]
    tool = next(m for m in on.calls[0]["messages"] if m["role"] == "tool")
    assert tool["images"] == [b"\xff\xd8jpeg"]  # sur le résultat d'outil lui-même


def test_vision_auto_attend_une_generation_rapide() -> None:
    fast = _FakeOllama(_final_chunk(generated=120, seconds=1.0))
    client = OllamaClient(client=fast, vision="auto")
    assert not client.wants_images()  # rien de mesuré encore
    client.respond(_screen_history())
    assert _images(fast.calls[0]) == []
    assert client.wants_images()  # 120 tokens/s : carte graphique
    client.respond(_screen_history())
    assert _images(fast.calls[1]) == [[b"\xff\xd8jpeg"]]


@pytest.mark.parametrize(
    ("tokens", "seconds", "expected"),
    [
        (100, 10.0, False),  # 10 tokens/s : processeur ou iGPU
        (8, 0.01, False),  # trop peu de tokens pour mesurer
        (int(VISION_MIN_GENERATION_RATE * 2), 1.0, True),
    ],
)
def test_vision_auto_selon_la_vitesse_mesuree(
    tokens: int, seconds: float, expected: bool
) -> None:
    client = OllamaClient(
        client=_FakeOllama(_final_chunk(generated=tokens, seconds=seconds)),
        vision="auto",
    )
    client.respond(_screen_history())
    assert client.wants_images() is expected


def test_vision_auto_ignore_la_lecture_gonflee_par_le_cache() -> None:
    # Vu en labo (Ollama 0.40, iGPU) : 3 528 tokens « lus » en 1,3 s car presque
    # tous en cache, mais une génération à 14 tokens/s : pas de capture.
    chunk = _final_chunk(
        generated=46, seconds=3.2, prompt_tokens=3528, prompt_seconds=1.33
    )
    client = OllamaClient(client=_FakeOllama(chunk), vision="auto")
    client.respond(_screen_history())
    assert not client.wants_images()


def test_capture_envoyee_au_cloud_avec_le_resultat() -> None:
    messages = _to_anthropic(_screen_history())
    content = messages[-1]["content"][0]["content"]
    assert content[0] == {"type": "text", "text": "[1] bouton « OK »"}
    assert content[1]["type"] == "image"
    assert content[1]["source"]["media_type"] == "image/jpeg"


def test_resultat_sans_capture_reste_du_texte() -> None:
    history = _screen_history()
    del history[-1]["image"]
    assert _to_anthropic(history)[-1]["content"][0]["content"] == "[1] bouton « OK »"


def test_cloud_sans_outils_de_l_ecran_si_desactive() -> None:
    client = ClaudeClient(client=object(), screen_control=False)
    names = {tool["name"] for tool in client._tools}  # pylint: disable=protected-access
    assert not names & COMPUTER_TOOLS
    with_screen = ClaudeClient(client=object())
    names = {tool["name"] for tool in with_screen._tools}  # pylint: disable=protected-access
    assert COMPUTER_TOOLS <= names


# ---- réponse illisible (appel d'outil perdu par Ollama) ----------------------------


def _tool_chunk(name: str, args: dict) -> SimpleNamespace:
    call = SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))
    return SimpleNamespace(
        message=SimpleNamespace(content="", tool_calls=[call]),
        done=True,
        eval_count=40,
        eval_duration=int(4e9),
    )


def _empty_chunk(generated: int) -> SimpleNamespace:
    # Vu en VM : 791 tokens générés, mais ni texte ni appel d'outil renvoyés.
    return SimpleNamespace(
        message=SimpleNamespace(content="", tool_calls=None),
        done=True,
        eval_count=generated,
        eval_duration=int(50e9),
    )


class _ScriptedOllama:
    def __init__(self, answers: list[SimpleNamespace]) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(kwargs)
        return [self.answers.pop(0)]


def test_reponse_illisible_redemandee_une_fois() -> None:
    fake = _ScriptedOllama(
        [_empty_chunk(791), _tool_chunk("type_text", {"text": "Première strophe"})]
    )
    plan = OllamaClient(client=fake, vision="off").respond(_screen_history())
    assert [call.tool for call in plan.tool_calls] == ["type_text"]
    assert len(fake.calls) == 2
    note = fake.calls[1]["messages"][-1]
    assert note["role"] == "user" and "n'a pas pu être lue" in note["content"]
    assert len(fake.calls[0]["messages"]) + 1 == len(fake.calls[1]["messages"])


def test_reponse_illisible_deux_fois_signalee() -> None:
    fake = _ScriptedOllama([_empty_chunk(791), _empty_chunk(555)])
    with pytest.raises(UnreadableResponse, match="illisible"):
        OllamaClient(client=fake, vision="off").respond(_screen_history())


def test_reponse_vide_courte_acceptee_sans_relance() -> None:
    fake = _ScriptedOllama([_empty_chunk(2)])
    plan = OllamaClient(client=fake, vision="off").respond(_screen_history())
    assert plan.narration == "" and plan.tool_calls == []
    assert len(fake.calls) == 1
