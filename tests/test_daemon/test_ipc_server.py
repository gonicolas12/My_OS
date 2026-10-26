"""Tests de routage du serveur IPC (sans ouvrir de vraie socket).

On appelle directement ``_dispatch`` : le constructeur n'ouvre la socket qu'à
``start()``, donc on peut tester l'aiguillage des types de messages de façon
unitaire et déterministe.
"""

# pylint: disable=missing-function-docstring,protected-access,unused-argument
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from daemon.ipc_server import IPCServer, peer_pid


def _noop_user_message(message: dict, reply: object) -> None:
    pass


def _server(**handlers: object) -> IPCServer:
    on_user_message = handlers.get("on_user_message", _noop_user_message)
    return IPCServer(
        Path("/tmp/myos-test-unused.sock"),
        on_user_message=on_user_message,  # type: ignore[arg-type]
        on_confirmation_response=handlers.get("on_confirmation_response"),  # type: ignore[arg-type]
        on_reset=handlers.get("on_reset"),  # type: ignore[arg-type]
        on_control_ready=handlers.get("on_control_ready"),  # type: ignore[arg-type]
        on_stop_control=handlers.get("on_stop_control"),  # type: ignore[arg-type]
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


def test_dispatch_controle_de_l_ordinateur() -> None:
    accuses: list[dict] = []
    arrets: list[bool] = []
    server = _server(
        on_control_ready=accuses.append, on_stop_control=lambda: arrets.append(True)
    )
    ready = {"type": "control_ready", "id": "m1", "stop_rect": [1, 2, 3, 4]}
    server._dispatch(ready, conn=None)  # type: ignore[arg-type]
    server._dispatch({"type": "stop_control"}, conn=None)  # type: ignore[arg-type]
    assert accuses == [ready]
    assert arrets == [True]


def test_dispatch_controle_sans_handler_ne_plante_pas() -> None:
    server = _server()
    server._dispatch({"type": "stop_control"}, conn=None)  # type: ignore[arg-type]
    server._dispatch({"type": "control_ready", "id": "m1"}, conn=None)  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED : Linux seulement")
def test_pid_du_popup_connecte() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        import os  # pylint: disable=import-outside-toplevel

        assert peer_pid(left) == os.getpid()
    finally:
        left.close()
        right.close()
