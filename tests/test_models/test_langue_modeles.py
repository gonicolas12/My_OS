"""Langue côté modèles : un prompt système par langue, descriptions des outils
traduites, notes au modèle dans la langue de la requête (local, cloud, stub)."""

# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from core.i18n import SUPPORTED_LANGUAGES, use_language
from daemon.orchestrator import ToolCall
from models import cloud_router
from models.cloud_router import ClaudeClient
from models.local_llm import (
    ALL_TOOLS_SCHEMA,
    OllamaClient,
    tools_schema,
    with_language_reminders,
)
from models.prompts import BASE_SYSTEM_PROMPTS, SCREEN_PROMPTS
from models.stub_model import StubRuleModel


class _FakeOllama:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(kwargs)
        return [
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
                done=True,
                eval_count=1,
                eval_duration=1,
            )
        ]


def _system(fake: _FakeOllama) -> str:
    return fake.calls[-1]["messages"][0]["content"]


def _descriptions(tools: list[dict]) -> dict[str, str]:
    return {t["function"]["name"]: t["function"]["description"] for t in tools}


def test_un_prompt_par_langue() -> None:
    for prompts in (BASE_SYSTEM_PROMPTS, SCREEN_PROMPTS, cloud_router._CLOUD_NOTES):
        assert set(prompts) == set(SUPPORTED_LANGUAGES)


@pytest.mark.parametrize("prompts", [BASE_SYSTEM_PROMPTS, SCREEN_PROMPTS])
def test_prompts_des_deux_langues_citent_les_memes_outils(
    prompts: dict[str, str],
) -> None:
    # Garde-fou : une consigne ajoutée à un prompt ne doit pas manquer dans l'autre.
    def tools(text: str) -> set[str]:
        return set(re.findall(r"\b[a-z]+_[a-z_]+\b", text))

    assert tools(prompts["fr"]) == tools(prompts["en"])
    assert tools(prompts["fr"])


def test_meme_client_suit_la_langue_de_chaque_requete() -> None:
    fake = _FakeOllama()
    client = OllamaClient(client=fake)
    history = [{"role": "user", "content": "hello"}]
    with use_language("en"):
        client.respond(history)
    english = _system(fake)
    assert english.startswith(BASE_SYSTEM_PROMPTS["en"])
    assert SCREEN_PROMPTS["en"] in english
    assert "You run LOCALLY" in english and "Machine context" in english
    assert "Tu es My_OS" not in english
    tools = _descriptions(fake.calls[-1]["tools"])
    assert tools["read_file"] == "Reads the contents of a text file."
    client.respond(history)  # requête suivante, en français
    assert _system(fake).startswith(BASE_SYSTEM_PROMPTS["fr"])
    assert _descriptions(fake.calls[-1]["tools"])["read_file"] == (
        "Lit le contenu d'un fichier texte."
    )


def test_schema_traduit_garde_noms_et_parametres() -> None:
    english = tools_schema("en")
    french = tools_schema("fr")
    assert french == ALL_TOOLS_SCHEMA  # le français est le texte d'origine

    def shape(schema: list[dict]) -> list:
        return [
            (
                t["function"]["name"],
                {
                    name: {k: v for k, v in spec.items() if k != "description"}
                    for name, spec in t["function"]["parameters"]["properties"].items()
                },
                t["function"]["parameters"]["required"],
            )
            for t in schema
        ]

    assert shape(english) == shape(french)
    for tool in english:
        assert (
            tool["function"]["description"]
            != _descriptions(french)[tool["function"]["name"]]
        )


def test_jamais_de_guillemets_droits_pour_le_modele_anglais() -> None:
    # Dans le schéma JSON des outils, un exemple entre guillemets droits ressemble à
    # une valeur toute prête : « Open a terminal and run ls » ouvrait l'exemple
    # « Mousepad » 11 fois sur 20 (cf. core/locales/en.py). Le français met « ».
    def descriptions(value: object) -> list[str]:
        if isinstance(value, dict):
            own = value.get("description")
            found = [own] if isinstance(own, str) else []
            return found + [d for item in value.values() for d in descriptions(item)]
        if isinstance(value, list):
            return [d for item in value for d in descriptions(item)]
        return []

    texts = [
        BASE_SYSTEM_PROMPTS["en"],
        SCREEN_PROMPTS["en"],
        cloud_router._CLOUD_NOTES["en"],
        *descriptions(tools_schema("en")),
    ]
    assert all('"' not in text for text in texts)


def test_notes_au_modele_dans_la_langue_de_la_requete() -> None:
    history = [
        {"role": "user", "content": "read /tmp/f"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [ToolCall("read_file", {"path": "/tmp/f"})],
        },
        {"role": "tool", "tool": "read_file", "content": "x" * 100_000},
    ]
    fake = _FakeOllama()
    with use_language("en"):
        OllamaClient(client=fake).respond(history)
    sent = fake.calls[-1]["messages"][-1]["content"]
    assert "result truncated for the context window" in sent


class _ClaudeStream:
    """Imite ``client.messages.stream(...)`` : aucun texte, message final vide."""

    text_stream: tuple[str, ...] = ()

    # typing.Self n'existe qu'en 3.11 ; le projet vise 3.10+.
    def __enter__(self) -> _ClaudeStream:  # noqa: PYI034
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    @staticmethod
    def get_final_message() -> object:
        return SimpleNamespace(content=[])


def test_cloud_dans_la_langue_de_la_requete() -> None:
    received: dict[str, Any] = {}

    def stream_call(**kwargs: Any) -> _ClaudeStream:
        received.update(kwargs)
        return _ClaudeStream()

    anthropic = SimpleNamespace(messages=SimpleNamespace(stream=stream_call))
    client = ClaudeClient(client=anthropic)
    with use_language("en"):
        client.respond([{"role": "user", "content": "hi"}])
    assert received["system"].startswith(BASE_SYSTEM_PROMPTS["en"])
    assert "CLOUD mode" in received["system"]
    tools = {t["name"]: t["description"] for t in received["tools"]}
    assert (
        tools["install_package"] == "Installs a package from the official repositories."
    )


# ---- conversation qui change de langue ------------------------------------------


def _mixed() -> list[dict]:
    return [
        {"role": "user", "content": "Baisse le volume à 30 %", "language": "fr"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [ToolCall("set_volume", {"percent": 30})],
        },
        {"role": "tool", "tool": "set_volume", "content": "Volume réglé à 30 %"},
        {"role": "assistant", "content": "C'est fait.", "tool_calls": []},
        {"role": "user", "content": "Lower the volume to 45%", "language": "en"},
    ]


def test_conversation_dans_une_seule_langue_envoyee_telle_quelle() -> None:
    history = [m for m in _mixed() if m.get("language") != "en"]
    assert with_language_reminders(history) is history


def test_chaque_demande_rappelle_sa_langue_si_la_conversation_en_change() -> None:
    history = _mixed()
    sent = with_language_reminders(history)
    assert sent[0]["content"] == "Baisse le volume à 30 %\n\n(Réponds en français.)"
    assert sent[4]["content"] == "Lower the volume to 45%\n\n(Answer in English.)"
    assert sent[1:4] == history[1:4]  # réponses et résultats d'outils intacts
    assert history[0]["content"] == "Baisse le volume à 30 %"  # historique intact


def test_rappels_stables_d_une_requete_a_l_autre() -> None:
    # Le début de l'envoi reste identique : Ollama reprend ce qu'il a déjà lu.
    history = _mixed()
    first = with_language_reminders(history)
    history += [
        {"role": "assistant", "content": "Done.", "tool_calls": []},
        {"role": "user", "content": "And mute it", "language": "en"},
    ]
    second = with_language_reminders(history)
    assert second[: len(first)] == first


def test_le_modele_local_recoit_les_rappels() -> None:
    fake = _FakeOllama()
    with use_language("en"):
        OllamaClient(client=fake).respond(_mixed())
    users = [m["content"] for m in fake.calls[-1]["messages"] if m["role"] == "user"]
    assert users[-1].endswith("(Answer in English.)")
    assert users[0].endswith("(Réponds en français.)")


def test_stub_en_anglais() -> None:
    with use_language("en"):
        plan = StubRuleModel().respond([{"role": "user", "content": "hello"}])
        assert plan.narration.startswith("I did not understand (stub model")
        plan = StubRuleModel().respond(
            [{"role": "user", "content": "write hello into /tmp/out.txt"}]
        )
        assert plan.tool_calls[0].args == {"content": "hello", "path": "/tmp/out.txt"}
        assert plan.narration.startswith("OK — I will run write_file(")
        done = StubRuleModel().respond([{"role": "tool", "content": "x"}])
        assert done.narration == "Done."
