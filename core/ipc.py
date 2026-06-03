r"""Cadrage des messages IPC, partagé entre le daemon et le popup.

Protocole (cf. docs/INTERFACES.md §1) : JSON encodé en UTF-8, **un message par
ligne**, ``\n`` comme délimiteur. Ce module est l'unique endroit qui encode et
décode le cadrage, pour que daemon et popup ne divergent jamais sur le format.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator

_RECV_SIZE = 4096


def encode_message(message: dict) -> bytes:
    """Sérialise un message en une ligne JSON terminée par ``\\n`` (UTF-8)."""
    return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")


def send_message(sock: socket.socket, message: dict) -> None:
    """Envoie un message cadré sur la socket."""
    sock.sendall(encode_message(message))


def iter_messages(sock: socket.socket) -> Iterator[dict]:
    """Lit la socket et produit les messages décodés, un par un.

    S'arrête quand la connexion est fermée (``recv`` renvoie ``b""``). Les
    lignes vides et les fragments JSON invalides sont ignorés silencieusement.
    """
    buffer = b""
    while True:
        chunk = sock.recv(_RECV_SIZE)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                yield json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
