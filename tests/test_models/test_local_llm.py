"""Tests du client Ollama avec un client mocké (pas de daemon Ollama requis)."""

# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from core.config import ModelConfig
from daemon.orchestrator import ToolCall
from models.local_llm import (
    ALL_TOOLS_SCHEMA,
    DEFAULT_HOST,
    DEFAULT_NUM_CTX,
    FILE_TOOLS_SCHEMA,
    OllamaClient,
    _ollama_host,
)


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


def test_adresse_par_defaut_127_0_0_1(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert _ollama_host(None) == DEFAULT_HOST == "http://127.0.0.1:11434"
    assert _ollama_host("  ") == DEFAULT_HOST


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("http://localhost:11434", "http://127.0.0.1:11434"),
        ("http://LocalHost:11434/", "http://127.0.0.1:11434/"),
        ("localhost", "127.0.0.1"),
        ("localhost:11500", "127.0.0.1:11500"),
        (
            "https://moi:secret@localhost:8443/ollama",
            "https://moi:secret@127.0.0.1:8443/ollama",
        ),
    ],
)
def test_localhost_devient_127_0_0_1(configured: str, expected: str) -> None:
    # « localhost » se résout d'abord en ::1, où Ollama n'écoute pas.
    assert _ollama_host(configured) == expected


@pytest.mark.parametrize(
    "configured",
    [
        "http://192.168.1.20:11434",  # Ollama sur une autre machine
        "http://localhost.lan:11434",  # autre nom qui commence par localhost
        "http://monlocalhost:11434",
        "http://[::1]:11434",  # IPv6 demandé explicitement
        "http://127.0.0.1:8080",
    ],
)
def test_autres_adresses_inchangees(configured: str) -> None:
    assert _ollama_host(configured) == configured


def test_ollama_host_de_l_environnement_sans_adresse_en_config(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "localhost:11500")
    assert _ollama_host(None) == "127.0.0.1:11500"


def test_adresse_de_la_config_prioritaire_sur_ollama_host(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://10.0.0.5:11434")
    assert _ollama_host("http://localhost:11434") == "http://127.0.0.1:11434"


def test_client_ollama_cree_sur_127_0_0_1(monkeypatch) -> None:
    # Câblage réel : sans adresse configurée, la lib ``ollama`` reçoit 127.0.0.1
    # explicitement (faux module : la lib n'est pas requise pour les tests).
    received: dict[str, Any] = {}

    class _Client:
        def __init__(self, host: str | None = None) -> None:
            received["host"] = host

    monkeypatch.setitem(sys.modules, "ollama", SimpleNamespace(Client=_Client))
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    OllamaClient()
    assert received["host"] == "http://127.0.0.1:11434"


def _turn(n: int, result: str) -> list[dict]:
    """Un tour complet : demande, appel d'outil, résultat, réponse finale."""
    return [
        {"role": "user", "content": f"demande {n}"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [ToolCall("list_dir", {"path": f"/tmp/{n}"})],
        },
        {"role": "tool", "tool": "list_dir", "content": result},
        {"role": "assistant", "content": f"réponse {n}", "tool_calls": []},
    ]


def _sent(fake: _FakeClient) -> list[dict]:
    """Messages envoyés à Ollama lors du dernier appel, sans le prompt système."""
    assert fake.last_kwargs is not None
    return fake.last_kwargs["messages"][1:]


def test_fenetre_de_contexte_envoyee_a_chaque_requete() -> None:
    # Toujours la même : Ollama recharge le modèle quand elle change.
    fake = _FakeClient([_chunk(content="ok")])
    OllamaClient(client=fake).respond(_history("ping"))
    assert fake.last_kwargs["options"]["num_ctx"] == DEFAULT_NUM_CTX
    OllamaClient(client=fake, num_ctx=8192).respond(_history("ping"))
    assert fake.last_kwargs["options"]["num_ctx"] == 8192


def test_modele_garde_charge_une_heure_par_defaut() -> None:
    # Avec les 5 min d'Ollama, une requête après une pause relisait tout le prompt
    # système (près d'une minute sans GPU).
    fake = _FakeClient([_chunk(content="ok")])
    OllamaClient(client=fake).respond(_history("ping"))
    assert fake.last_kwargs["keep_alive"] == "1h"
    OllamaClient(client=fake, keep_alive=-1).respond(_history("ping"))
    assert fake.last_kwargs["keep_alive"] == -1


@pytest.mark.parametrize("num_ctx", [4096, 16384])
def test_prompt_lu_par_lots_de_256_tokens(num_ctx: int) -> None:
    # Ollama passe à des lots de 1 024 dès 8 192 tokens de fenêtre ; llama.cpp
    # reprenant un prompt un lot avant la fin du précédent, une nouvelle
    # conversation relirait alors ~1 000 tokens du prompt système au lieu de ~250.
    fake = _FakeClient([_chunk(content="ok")])
    OllamaClient(client=fake, num_ctx=num_ctx).respond(_history("ping"))
    assert fake.last_kwargs["options"]["num_batch"] == 256


def test_daemon_transmet_la_fenetre_de_la_config(monkeypatch) -> None:
    from daemon.myosd import _build_model

    received: dict[str, Any] = {}

    class _Client:
        def __init__(self, host: str | None = None) -> None:
            self.host = host

        def chat(self, **kwargs: Any) -> list[SimpleNamespace]:
            received.update(kwargs)
            return [_chunk(content="ok")]

    monkeypatch.setitem(sys.modules, "ollama", SimpleNamespace(Client=_Client))
    model = _build_model(ModelConfig(backend="ollama", num_ctx=8192, keep_alive="30m"))
    model.respond(_history("ping"))
    assert received["options"]["num_ctx"] == 8192
    assert received["keep_alive"] == "30m"


def test_historique_court_envoye_en_entier() -> None:
    history = _turn(1, "a.txt b.txt") + _turn(2, "c.txt")
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake).respond(history)
    assert [m["content"] for m in _sent(fake)] == [m["content"] for m in history]


def test_resultat_d_outil_trop_long_tronque_et_signale() -> None:
    # Un fichier lu en entier occuperait toute la fenêtre : début gardé, coupure
    # signalée au modèle. L'historique du daemon, lui, reste intact.
    content = "x" * 100_000
    history = [
        {"role": "user", "content": "lis /tmp/f"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [ToolCall("read_file", {"path": "/tmp/f"})],
        },
        {"role": "tool", "tool": "read_file", "content": content},
    ]
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake).respond(history)
    sent = _sent(fake)[-1]["content"]
    assert sent.startswith("x" * 10_000)
    assert len(sent) < 20_000
    assert "tronqué" in sent
    assert history[-1]["content"] == content


@pytest.mark.parametrize("num_ctx", [4096, 8192, 16384])
def test_prompt_envoye_tient_dans_la_fenetre(num_ctx: int) -> None:
    # Avant : au-delà de la fenêtre, Ollama coupait le DÉBUT du prompt (règles et
    # outils compris) et la réponse s'arrêtait net, fenêtre pleine.
    history = [m for n in range(30) for m in _turn(n, "fichier.txt\n" * 400)]
    history.append({"role": "user", "content": "et maintenant ?"})
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake, num_ctx=num_ctx).respond(history)

    messages = fake.last_kwargs["messages"]
    tools = json.dumps(fake.last_kwargs["tools"], ensure_ascii=False)
    chars = len(tools) + sum(len(m["content"]) for m in messages)
    assert chars / 3 < num_ctx  # ~3,5 caractères par token mesurés : marge comprise
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"  # tours entiers : aucun résultat orphelin
    assert messages[-1] == {"role": "user", "content": "et maintenant ?"}


def test_debut_de_l_envoi_stable_d_une_requete_a_l_autre() -> None:
    # Les tours les plus anciens partent d'un bloc (jusqu'à la moitié du budget) :
    # tant que la fenêtre ne redéborde pas, le début de l'envoi ne bouge plus et
    # Ollama reprend ce qu'il a déjà lu au lieu de tout relire.
    fake = _FakeClient([_chunk()])
    client = OllamaClient(client=fake, num_ctx=8192)
    history = [m for n in range(12) for m in _turn(n, "y" * 3000)]
    history.append({"role": "user", "content": "suite 1"})
    client.respond(history)
    first = _sent(fake)[0]
    assert first["role"] == "user" and first["content"] != "demande 0"

    history += [
        {"role": "assistant", "content": "ok", "tool_calls": []},
        {"role": "user", "content": "suite 2"},
    ]
    client.respond(history)
    assert _sent(fake)[0] == first

    # Nouveau débordement : le début avance de nouveau, d'un bloc.
    history += [m for n in range(12, 20) for m in _turn(n, "y" * 3000)]
    history.append({"role": "user", "content": "suite 3"})
    client.respond(history)
    assert _sent(fake)[0]["content"] not in (first["content"], "demande 0")


def test_conversation_videe_envoyee_depuis_le_debut() -> None:
    fake = _FakeClient([_chunk()])
    client = OllamaClient(client=fake, num_ctx=8192)
    long_history = [m for n in range(12) for m in _turn(n, "y" * 3000)]
    client.respond(long_history + [{"role": "user", "content": "suite"}])
    # Ctrl+L : le daemon repart d'un historique neuf.
    client.respond(_history("bonjour"))
    assert _sent(fake) == [{"role": "user", "content": "bonjour"}]


def test_requete_en_cours_trop_longue_anciens_resultats_retires() -> None:
    # Une seule requête qui lit trois gros fichiers : les plus anciens résultats
    # cèdent la place, le dernier reste lisible en entier.
    history: list[dict] = [{"role": "user", "content": "compare trois fichiers"}]
    for n in range(3):
        history += [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [ToolCall("read_file", {"path": f"/tmp/{n}"})],
            },
            {"role": "tool", "tool": "read_file", "content": str(n) * 6000},
        ]
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake, num_ctx=8192).respond(history)

    results = [m["content"] for m in _sent(fake) if m["role"] == "tool"]
    assert len(results) == 3
    assert "retiré" in results[0]
    assert results[-1] == "2" * 6000
    assert _sent(fake)[0] == {"role": "user", "content": "compare trois fichiers"}


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


def test_think_desactive_par_defaut() -> None:
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake).respond(_history("ping"))
    assert fake.last_kwargs["think"] is False


def test_think_active_si_demande() -> None:
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake, think=True).respond(_history("ping"))
    assert fake.last_kwargs["think"] is True


def test_plan_passe_le_systeme_et_le_user_au_modele() -> None:
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake).respond(_history("salut"))

    assert fake.last_kwargs is not None
    messages = fake.last_kwargs["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user"]
    assert messages[1]["content"] == "salut"


def test_system_prompt_inclut_le_contexte_machine() -> None:
    # Le HOME réel est injecté pour éviter que le modèle invente un chemin
    # (ex. /home/user/... au lieu du vrai dossier personnel).
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake).respond(_history("salut"))
    system = fake.last_kwargs["messages"][0]["content"]
    assert os.path.expanduser("~") in system


def test_plan_envoie_le_schema_de_tous_les_outils() -> None:
    fake = _FakeClient([_chunk()])
    OllamaClient(client=fake).respond(_history("ping"))

    assert fake.last_kwargs is not None
    tools = fake.last_kwargs["tools"]
    names = {t["function"]["name"] for t in tools}
    assert names == {
        # fichiers (jalon 2)
        "read_file",
        "list_dir",
        "write_file",
        "create_file",
        "move_file",
        "delete_file",
        # processus (jalon 3)
        "list_processes",
        "kill_process",
        # paquets (jalon 3)
        "search_package",
        "install_package",
        "remove_package",
        "update_system",
        # réglages (jalon 3)
        "set_brightness",
        "set_volume",
        "set_mute",
        "set_wifi",
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


def test_all_tools_schema_coherent_avec_registres_et_risk_levels() -> None:
    # Garde-fou contre la dérive de la triple déclaration connue
    # (outil Python ↔ schéma JSON ↔ table risk_levels).
    from daemon.myosd import _build_tools
    from permissions.risk_levels import TOOL_RISK_LEVELS

    schema_names = {entry["function"]["name"] for entry in ALL_TOOLS_SCHEMA}
    registry_names = set(_build_tools())

    assert schema_names == registry_names
    # Tout outil câblé a un niveau de risque statique déclaré.
    assert registry_names <= set(TOOL_RISK_LEVELS)
    # Chaque schéma est bien formé.
    for entry in ALL_TOOLS_SCHEMA:
        assert entry["type"] == "function"
        assert entry["function"]["description"]
        assert entry["function"]["parameters"]["type"] == "object"


def test_arguments_none_devient_dict_vide() -> None:
    # Certains modèles renvoient arguments=None ; on ne doit pas paniquer.
    fake = _FakeClient(
        [_chunk(tool_calls=[("read_file", None)])]  # type: ignore[list-item]
    )
    plan = OllamaClient(client=fake).respond(_history("?"))
    assert plan.tool_calls[0].args == {}


def test_historique_est_traduit_au_format_ollama() -> None:
    # L'historique générique (user/assistant/tool) est passé après le system,
    # avec les tool_calls reconstruits (cohérence du template de chat Qwen).
    fake = _FakeClient([_chunk(content="ok")])
    history = [
        {"role": "user", "content": "liste /tmp"},
        {
            "role": "assistant",
            "content": "je liste",
            "tool_calls": [ToolCall("list_dir", {"path": "/tmp"})],
        },
        {"role": "tool", "tool": "list_dir", "content": "a.txt b.txt"},
    ]
    OllamaClient(client=fake).respond(history)

    assert fake.last_kwargs is not None
    sent = fake.last_kwargs["messages"]
    assert sent[0]["role"] == "system"
    assert [m["role"] for m in sent[1:]] == ["user", "assistant", "tool"]
    # L'assistant porte ses tool_calls au format Ollama.
    assert sent[2]["tool_calls"] == [
        {"function": {"name": "list_dir", "arguments": {"path": "/tmp"}}}
    ]
    # Le résultat d'outil est transmis avec le nom de l'outil.
    assert sent[-1] == {
        "role": "tool",
        "content": "a.txt b.txt",
        "tool_name": "list_dir",
    }
