"""Tests du backend cloud et du routeur, sans réseau ni clé réelle.

Le client ``anthropic`` est un faux (streaming simulé) et ``keyring`` est mocké via
``models.secrets``. On vérifie : la traduction de l'historique au format Anthropic
(appariement tool_use ↔ tool_result), l'assemblage du Plan, le routage par
disponibilité de clé, et surtout que **la clé ne fuit jamais** dans ce qui est
envoyé au modèle. Puis la réflexion de Claude Sonnet 5.5 : blocs renvoyés tels quels
dans une boucle d'outils, réponse coupée ou refusée sans outil, réglages envoyés et
aucun paramètre d'échantillonnage (le vrai SDK est couvert par test_cloud_sdk.py).
"""

# pylint: disable=missing-function-docstring
from __future__ import annotations

import logging
import os
from types import SimpleNamespace

import pytest

from core.i18n import use_language
from daemon.orchestrator import ToolCall
from models import secrets
from models.cloud_router import (
    DEFAULT_MAX_TOKENS,
    THINKING_BINDING_BETA,
    ClaudeClient,
    CloudRouter,
    CloudUnavailable,
    _to_anthropic,
    _to_anthropic_tools,
)
from models.local_llm import ALL_TOOLS_SCHEMA

# ---------- faux client anthropic (context manager de streaming) ----------


class _FakeStream:
    """Imite ``client.beta.messages.stream(...)`` : itérable de texte + message final."""

    def __init__(self, text_parts: list[str], final_message: object) -> None:
        self.text_stream = list(text_parts)
        self._final = final_message

    # typing.Self n'existe qu'en 3.11 ; le projet vise 3.10+.
    def __enter__(self) -> _FakeStream:  # noqa: PYI034
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def get_final_message(self) -> object:
        return self._final


class _FakeMessages:
    """Un flux par appel, dans l'ordre (le dernier resservi ensuite)."""

    def __init__(self, *streams: _FakeStream) -> None:
        self._streams = list(streams)
        self.calls: list[dict] = []

    @property
    def last_kwargs(self) -> dict | None:
        return self.calls[-1] if self.calls else None

    def stream(self, **kwargs: object) -> _FakeStream:
        self.calls.append(kwargs)
        return self._streams.pop(0) if len(self._streams) > 1 else self._streams[0]


class _FakeAnthropic:
    def __init__(self, *streams: _FakeStream) -> None:
        self.messages = _FakeMessages(*streams)
        self.beta = SimpleNamespace(messages=self.messages)


def _final(blocks: list[object], stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, args: dict, block_id: str = "srv-id") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=args, id=block_id)


def _thinking_block(signature: str) -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking="", signature=signature)


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
    ClaudeClient(client=anthropic, model="claude-sonnet-5-5").respond(
        [{"role": "user", "content": "salut"}]
    )

    kwargs = anthropic.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["model"] == "claude-sonnet-5-5"
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


# ---------- réflexion de Claude Sonnet 5.5 ----------


def test_blocs_de_reflexion_renvoyes_tels_quels_dans_la_boucle_d_outils() -> None:
    thinking = _thinking_block("sig-1")
    first = [
        thinking,
        _text_block("Je lis."),
        _tool_use_block("read_file", {"path": "/tmp/x"}, "toolu_srv1"),
    ]
    anthropic = _FakeAnthropic(
        _FakeStream(["Je lis."], _final(first, "tool_use")),
        _FakeStream(["C'est fait."], _final([_text_block("C'est fait.")])),
    )
    client = ClaudeClient(client=anthropic)
    history: list[dict] = [{"role": "user", "content": "lis /tmp/x", "language": "fr"}]
    plan = client.respond(history)
    assert plan.raw_content == first

    history += [
        {
            "role": "assistant",
            "content": plan.narration,
            "tool_calls": plan.tool_calls,
            "raw_content": plan.raw_content,
        },
        {"role": "tool", "tool": "read_file", "content": "contenu"},
    ]
    client.respond(history)

    sent = anthropic.messages.calls[1]["messages"]
    assert sent[1] == {"role": "assistant", "content": first}  # tour renvoyé tel quel
    assert sent[1]["content"][0] is thinking  # bloc signé, rien de reconstruit
    assert sent[2]["content"][0]["tool_use_id"] == "toolu_srv1"  # vrai identifiant


def test_reflexion_des_requetes_precedentes_non_renvoyee() -> None:
    earlier = [_thinking_block("sig-0"), _text_block("Bonjour !")]
    out = _to_anthropic(
        [
            {"role": "user", "content": "salut"},
            {
                "role": "assistant",
                "content": "Bonjour !",
                "tool_calls": [],
                "raw_content": earlier,
            },
            {"role": "user", "content": "et ensuite ?"},
        ]
    )
    # Retirée du début de l'historique (permis), la réflexion d'une requête passée
    # ne peut plus être invalidée par l'élagage ou un changement de langue.
    assert out[1] == {"role": "assistant", "content": [earlier[1]]}


def test_contenu_brut_qui_ne_correspond_plus_aux_appels_ignore() -> None:
    raw = [_tool_use_block("read_file", {}, "toolu_srv1")]
    out = _to_anthropic(
        [
            {"role": "user", "content": "x"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [ToolCall("list_dir", {})],
                "raw_content": raw,
            },
            {"role": "tool", "tool": "list_dir", "content": "r"},
        ]
    )
    [block] = out[1]["content"]
    assert block["name"] == "list_dir" and block["id"] != "toolu_srv1"
    assert out[2]["content"][0]["tool_use_id"] == block["id"]


@pytest.mark.parametrize("stop", ["max_tokens", "model_context_window_exceeded"])
def test_reponse_coupee_n_execute_aucun_outil(stop: str) -> None:
    final = _final(
        [_thinking_block("sig"), _tool_use_block("write_file", {"path": "/tm"})], stop
    )
    plan = ClaudeClient(client=_FakeAnthropic(_FakeStream([], final))).respond(
        [{"role": "user", "content": "écris /tmp/x"}]
    )
    assert plan.tool_calls == []  # appel tronqué : jamais exécuté
    assert plan.raw_content is None
    assert plan.interrupted.startswith("⚠ Réponse coupée")


def test_reponse_coupee_signalee_dans_la_langue_de_la_requete() -> None:
    final = _final([_text_block("Hal")], "max_tokens")
    with use_language("en"):
        plan = ClaudeClient(client=_FakeAnthropic(_FakeStream(["Hal"], final))).respond(
            [{"role": "user", "content": "write a long story"}]
        )
    assert plan.narration == "Hal"
    assert plan.interrupted.startswith("⚠ Answer cut off")


def test_refus_signale_sans_outil() -> None:
    final = SimpleNamespace(
        content=[_text_block("Je "), _tool_use_block("run_command", {})],
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber"),
    )
    plan = ClaudeClient(client=_FakeAnthropic(_FakeStream(["Je "], final))).respond(
        [{"role": "user", "content": "..."}]
    )
    assert plan.tool_calls == []
    assert "refusé" in plan.interrupted


def test_reflexion_adaptative_par_defaut_sans_echantillonnage() -> None:
    anthropic = _FakeAnthropic(_FakeStream([], _final([_text_block("ok")])))
    ClaudeClient(client=anthropic).respond([{"role": "user", "content": "salut"}])

    kwargs = anthropic.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["max_tokens"] == DEFAULT_MAX_TOKENS == 16000
    assert kwargs["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert kwargs["betas"] == [THINKING_BINDING_BETA]
    assert "output_config" not in kwargs  # effort : celui de l'API
    assert not {"temperature", "top_p", "top_k", "extra_body"} & kwargs.keys()


def test_reglages_de_reflexion_pour_la_mesure() -> None:
    anthropic = _FakeAnthropic(_FakeStream([], _final([_text_block("ok")])))
    ClaudeClient(client=anthropic, thinking="between_tools", effort="medium").respond(
        [{"role": "user", "content": "salut"}]
    )

    kwargs = anthropic.messages.last_kwargs
    assert kwargs is not None
    assert kwargs["thinking"] == {"type": "between_tools"}  # aucun autre champ permis
    assert "betas" not in kwargs
    assert kwargs["output_config"] == {"effort": "medium"}
    assert not {"temperature", "top_p", "top_k"} & kwargs.keys()


@pytest.mark.parametrize(
    ("thinking", "effort"),
    [
        ("disabled", None),  # 400 sur Claude Sonnet 5.5
        ("adaptive", "extreme"),
        ("between_tools", "xhigh"),  # between_tools : effort high au plus
        ("between_tools", "max"),
    ],
)
def test_reglages_que_l_api_refuserait_rejetes_d_emblee(
    thinking: str, effort: str | None
) -> None:
    with pytest.raises(ValueError):
        ClaudeClient(
            client=_FakeAnthropic(_FakeStream([], _final([]))),
            thinking=thinking,
            effort=effort,
        )


def test_reponse_journalisee_sans_contenu(caplog: pytest.LogCaptureFixture) -> None:
    final = SimpleNamespace(
        content=[_text_block("secret du fichier")],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=1200,
            output_tokens=80,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=0,
        ),
        input_transformations=[
            SimpleNamespace(type="thinking_dropped", reason="prefix_binding_mismatch")
        ],
    )
    anthropic = _FakeAnthropic(_FakeStream(["secret du fichier"], final))
    with caplog.at_level(logging.INFO, logger="myosd.cloud"):
        ClaudeClient(client=anthropic).respond([{"role": "user", "content": "lis"}])

    [text] = [r.getMessage() for r in caplog.records if r.name == "myosd.cloud"]
    assert "entrée 1200" in text and "sortie 80" in text
    assert "blocs de réflexion écartés 1" in text
    assert "secret" not in text


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
