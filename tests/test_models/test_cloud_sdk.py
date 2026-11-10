"""Backend cloud contre le vrai SDK ``anthropic``, sans réseau ni clé : un faux serveur.

Le SDK installé (1.x depuis les sources, 2.x sur l'ISO) envoie ses requêtes à un
serveur HTTP de test sur 127.0.0.1, qui répond en flux (SSE) comme l'API Messages. On
vérifie ce qui part vraiment : blocs de réflexion renvoyés à l'identique au tour
suivant d'une boucle d'outils, vrais identifiants des appels d'outils, en-tête bêta et
réglages de la réflexion, aucun paramètre d'échantillonnage ; et ce qui revient : une
réponse coupée en plein appel d'outil n'en exécute aucun. Ignoré sans le SDK.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name
# pylint: disable=wrong-import-position,invalid-name
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

anthropic = pytest.importorskip("anthropic")

from models.cloud_router import THINKING_BINDING_BETA, ClaudeClient

_SAMPLING = {"temperature", "top_p", "top_k"}


def _sse(blocks: list[dict], stop_reason: str) -> bytes:
    """Réponse en flux de l'API Messages pour ces blocs (réflexion, texte, outil)."""
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 1},
                "input_transformations": [],
            },
        }
    ]
    for index, block in enumerate(blocks):
        if block["type"] == "thinking":
            start = {"type": "thinking", "thinking": "", "signature": ""}
            delta = {"type": "signature_delta", "signature": block["signature"]}
        elif block["type"] == "text":
            start = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
        else:
            start = {
                "type": "tool_use",
                "id": block["id"],
                "name": block["name"],
                "input": {},
            }
            delta = {"type": "input_json_delta", "partial_json": block["json"]}
        events += [
            {"type": "content_block_start", "index": index, "content_block": start},
            {"type": "content_block_delta", "index": index, "delta": delta},
            {"type": "content_block_stop", "index": index},
        ]
    events += [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 30},
        },
        {"type": "message_stop"},
    ]
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
        for event in events
    )


class _Handler(BaseHTTPRequestHandler):
    """Enregistre chaque requête et sert la réponse suivante de la file."""

    server: _Server

    def do_POST(self) -> None:  # nom imposé par http.server
        length = int(self.headers.get("content-length", "0"))
        self.server.requests.append(
            {
                "path": self.path,
                "beta": self.headers.get("anthropic-beta", ""),
                "body": json.loads(self.rfile.read(length)),
            }
        )
        payload = self.server.responses.pop(0)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: object) -> None:
        """Silencieux."""


class _Server(ThreadingHTTPServer):
    requests: list[dict]
    responses: list[bytes]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Server]:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    srv = _Server(("127.0.0.1", 0), _Handler)
    srv.requests, srv.responses = [], []
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _client(server: _Server, **options: Any) -> ClaudeClient:
    sdk = anthropic.Anthropic(
        api_key="sk-ant-test",
        base_url=f"http://127.0.0.1:{server.server_address[1]}",
        max_retries=0,
    )
    return ClaudeClient(client=sdk, **options)


def test_boucle_d_outils_avec_le_vrai_sdk(server: _Server) -> None:
    server.responses += [
        _sse(
            [
                {"type": "thinking", "signature": "sig-1"},
                {"type": "text", "text": "Je lis."},
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "read_file",
                    "json": '{"path": "/tmp/x"}',
                },
            ],
            "tool_use",
        ),
        _sse(
            [
                {"type": "thinking", "signature": "sig-2"},
                {"type": "text", "text": "Fini."},
            ],
            "end_turn",
        ),
    ]
    client = _client(server)
    history: list[dict] = [{"role": "user", "content": "lis /tmp/x", "language": "fr"}]
    tokens: list[str] = []

    plan = client.respond(history, tokens.append)
    assert tokens == ["Je lis."]
    assert [(c.tool, c.args) for c in plan.tool_calls] == [
        ("read_file", {"path": "/tmp/x"})
    ]
    history += [
        {
            "role": "assistant",
            "content": plan.narration,
            "tool_calls": plan.tool_calls,
            "raw_content": plan.raw_content,
        },
        {"role": "tool", "tool": "read_file", "content": "contenu"},
    ]
    assert client.respond(history).narration == "Fini."

    first, second = server.requests
    for request in (first, second):
        body = request["body"]
        assert not _SAMPLING & body.keys()
        assert body["max_tokens"] == 16000
        assert body["thinking"] == {
            "type": "adaptive",
            "block_binding": {"prefix_mismatch_behavior": "drop_block"},
        }
        assert THINKING_BINDING_BETA in request["beta"]
        assert "sk-ant-test" not in json.dumps(body)
    assistant = second["body"]["messages"][1]
    assert assistant["role"] == "assistant"
    thinking, text, tool_use = assistant["content"]
    assert thinking == {"type": "thinking", "thinking": "", "signature": "sig-1"}
    assert text["text"] == "Je lis."
    assert (tool_use["id"], tool_use["name"], tool_use["input"]) == (
        "toolu_01",
        "read_file",
        {"path": "/tmp/x"},
    )
    [result] = second["body"]["messages"][2]["content"]
    assert (result["type"], result["tool_use_id"]) == ("tool_result", "toolu_01")


def test_reponse_coupee_en_plein_appel_d_outil_avec_le_vrai_sdk(
    server: _Server,
) -> None:
    server.responses.append(
        _sse(
            [
                {"type": "thinking", "signature": "sig"},
                {
                    "type": "tool_use",
                    "id": "toolu_02",
                    "name": "write_file",
                    "json": '{"path": "/tm',
                },
            ],
            "max_tokens",
        )
    )
    plan = _client(server).respond([{"role": "user", "content": "écris /tmp/x"}])
    assert plan.tool_calls == []
    assert plan.raw_content is None
    assert plan.interrupted.startswith("⚠ Réponse coupée")


def test_reglages_de_mesure_avec_le_vrai_sdk(server: _Server) -> None:
    server.responses.append(_sse([{"type": "text", "text": "ok"}], "end_turn"))
    _client(server, thinking="between_tools", effort="medium").respond(
        [{"role": "user", "content": "salut"}]
    )
    [request] = server.requests
    assert request["body"]["thinking"] == {"type": "between_tools"}
    assert request["body"]["output_config"] == {"effort": "medium"}
    assert THINKING_BINDING_BETA not in request["beta"]
    assert not _SAMPLING & request["body"].keys()
