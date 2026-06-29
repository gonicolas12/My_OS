"""Tests de routage du serveur IPC (sans ouvrir de vraie socket).

On appelle directement ``_dispatch`` : le constructeur n'ouvre la socket qu'à
``start()``, donc on peut tester l'aiguillage des types de messages de façon
unitaire et déterministe.
"""

# pylint: disable=missing-function-docstring,protected-access,unused-argument
from __future__ import annotations

from pathlib import Path

from daemon.ipc_server import IPCServer


def _noop_user_message(message: dict, reply: object) -> None:
    pass


def _server(**handlers: object) -> IPCServer:
    on_user_message = handlers.get("on_user_message", _noop_user_message)
    return IPCServer(
        Path("/tmp/myos-test-unused.sock"),
        on_user_message=on_user_message,  # type: ignore[arg-type]
        on_confirmation_response=handlers.get("on_confirmation_response"),  # type: ignore[arg-type]
        on_reset=handlers.get("on_reset"),  # type: ignore[arg-type]
    )


def test_dispatch_reset_appelle_le_handler() -> None:
    appels: list[bool] = []
    server = _server(on_reset=lambda: appels.append(True))

    server._dispatch({"type": "reset"}, conn=None)  # type: ignore[arg-type]

    assert appels == [True]


def test_dispatch_reset_sans_handler_ne_plante_pas() -> None:
    server = _server()  # pas de on_reset fourni
    # Ne doit pas lever (le garde `and self._on_reset` court-circuite).
    server._dispatch({"type": "reset"}, conn=None)  # type: ignore[arg-type]


def test_dispatch_confirmation_response_route_vers_le_handler() -> None:
    recus: list[dict] = []
    server = _server(on_confirmation_response=recus.append)

    msg = {"type": "confirmation_response", "request_id": "x", "decision": "deny"}
    server._dispatch(msg, conn=None)  # type: ignore[arg-type]

    assert recus == [msg]
