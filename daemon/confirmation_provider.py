"""ConfirmationProvider basé sur l'IPC : envoie + bloque jusqu'à la réponse.

Implémente le protocole :class:`daemon.orchestrator.ConfirmationProvider` en
utilisant le transport IPC du daemon. Chaque appel à :meth:`ask` :

1. envoie le ``confirmation_needed`` au popup connecté ;
2. bloque le thread appelant sur une ``queue.Queue`` indexée par ``request_id`` ;
3. débloque dès que :meth:`deliver_response` est invoquée par l'IPC pour ce
   ``request_id`` ;
4. en cas de timeout, d'échec d'envoi, de popup absent ou de réponse
   malformée, renvoie un ``ConfirmationResponse(decision="deny")``
   (politique conservatrice : sans réponse claire, on refuse).

L'IPC dispatche les ``user_message`` dans un thread dédié (cf.
:meth:`daemon.ipc_server.IPCServer._dispatch`), ce qui permet à
l'orchestrator de bloquer sur cette ``ask`` sans deadlocker la boucle de
lecture qui reçoit la réponse.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from permissions.confirmation import (
    ConfirmationResponse,
    parse_confirmation_response,
)

_DEFAULT_TIMEOUT_S = 300.0  # 5 minutes max pour répondre


def _deny(request_id: str) -> ConfirmationResponse:
    return ConfirmationResponse(request_id=request_id, decision="deny")


class IPCConfirmationProvider:
    """Pont entre l'orchestrator (bloquant) et l'IPC (asynchrone)."""

    def __init__(
        self,
        send_to_client: Callable[[dict], bool],
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._send_to_client = send_to_client
        self._timeout_s = timeout_s
        self._waiters: dict[str, queue.Queue[dict]] = {}
        self._lock = threading.Lock()

    def ask(self, payload: dict) -> ConfirmationResponse:
        """Envoie le payload, bloque jusqu'à la réponse ou au timeout."""
        request_id = str(payload.get("request_id", ""))
        if not request_id:
            return _deny(request_id)

        waiter: queue.Queue[dict] = queue.Queue(maxsize=1)
        with self._lock:
            self._waiters[request_id] = waiter

        try:
            if not self._send_to_client(payload):
                return _deny(request_id)
            try:
                response_msg = waiter.get(timeout=self._timeout_s)
            except queue.Empty:
                return _deny(request_id)
        finally:
            with self._lock:
                self._waiters.pop(request_id, None)

        try:
            return parse_confirmation_response(response_msg)
        except ValueError:
            return _deny(request_id)

    def deliver_response(self, message: dict) -> None:
        """À appeler depuis l'IPC quand un ``confirmation_response`` arrive."""
        request_id = str(message.get("request_id", ""))
        with self._lock:
            waiter = self._waiters.get(request_id)
        if waiter is None:
            return  # aucune ask() en attente : on jette silencieusement
        try:
            waiter.put_nowait(message)
        except queue.Full:
            pass  # une seule réponse par requête
