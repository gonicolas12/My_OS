"""Tests de l'IPCConfirmationProvider (pont async/sync entre IPC et orchestrator)."""

# pylint: disable=missing-function-docstring,redefined-outer-name,use-implicit-booleaness-not-comparison
from __future__ import annotations

import threading
import time

import pytest

from daemon.confirmation_provider import IPCConfirmationProvider
from permissions.confirmation import ConfirmationResponse


def _payload(request_id: str = "rid-1") -> dict:
    return {
        "type": "confirmation_needed",
        "id": "uid",
        "request_id": request_id,
        "tool": "write_file",
        "args": {"path": "/tmp/x"},
        "risk_level": 1,
        "summary": "écriture de /tmp/x",
    }


def test_envoie_payload_et_renvoie_la_reponse_lorsque_livree() -> None:
    sent: list[dict] = []

    def fake_send(msg: dict) -> bool:
        sent.append(msg)
        return True

    provider = IPCConfirmationProvider(fake_send, timeout_s=2.0)

    def respond_async() -> None:
        time.sleep(0.05)
        provider.deliver_response(
            {
                "type": "confirmation_response",
                "request_id": "rid-1",
                "decision": "approve_once",
            }
        )

    threading.Thread(target=respond_async, daemon=True).start()
    result = provider.ask(_payload("rid-1"))

    assert sent == [_payload("rid-1")]
    assert result == ConfirmationResponse(request_id="rid-1", decision="approve_once")


def test_timeout_renvoie_deny() -> None:
    provider = IPCConfirmationProvider(lambda _msg: True, timeout_s=0.1)
    result = provider.ask(_payload("rid-1"))
    assert result.decision == "deny"


def test_send_qui_echoue_renvoie_deny() -> None:
    provider = IPCConfirmationProvider(lambda _msg: False, timeout_s=2.0)
    result = provider.ask(_payload("rid-1"))
    assert result.decision == "deny"


def test_request_id_vide_renvoie_deny_sans_envoyer() -> None:
    sent: list[dict] = []
    provider = IPCConfirmationProvider(lambda m: sent.append(m) or True)
    payload = _payload("")
    result = provider.ask(payload)
    assert result.decision == "deny"
    assert sent == []


def test_reponse_avec_request_id_inconnu_est_ignoree() -> None:
    provider = IPCConfirmationProvider(lambda _msg: True, timeout_s=0.1)
    # Personne n'a appelé ask("rid-X") → la deliver doit être no-op silencieux.
    provider.deliver_response(
        {"type": "confirmation_response", "request_id": "rid-X", "decision": "deny"}
    )


def test_reponse_malformee_donne_deny() -> None:
    provider = IPCConfirmationProvider(lambda _msg: True, timeout_s=2.0)

    def respond_async() -> None:
        time.sleep(0.05)
        provider.deliver_response({"type": "wrong_type", "request_id": "rid-1"})

    threading.Thread(target=respond_async, daemon=True).start()
    result = provider.ask(_payload("rid-1"))
    assert result.decision == "deny"


@pytest.mark.parametrize("scope", ["this_file", "this_folder", "session"])
def test_approve_scope_est_propage(scope: str) -> None:
    provider = IPCConfirmationProvider(lambda _msg: True, timeout_s=2.0)

    def respond_async() -> None:
        time.sleep(0.05)
        provider.deliver_response(
            {
                "type": "confirmation_response",
                "request_id": "rid-1",
                "decision": "approve_scope",
                "scope": scope,
            }
        )

    threading.Thread(target=respond_async, daemon=True).start()
    result = provider.ask(_payload("rid-1"))
    assert result.decision == "approve_scope"
    assert result.scope == scope
