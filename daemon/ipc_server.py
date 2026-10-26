"""Serveur IPC du daemon : socket Unix locale (daemon = serveur, popup = client).

Le daemon crée et écoute la socket ``$XDG_RUNTIME_DIR/myos.sock``. Le popup s'y
connecte et reste connecté. Le serveur sait :
  - pousser des messages de contrôle vers le popup (ex. ``show``) ;
  - recevoir les ``user_message`` du popup et y répondre.

Surface réseau minimale (cf. docs/SECURITY.md menace 3) : socket Unix locale
uniquement, permissions ``0600`` (propriétaire seul), aucun port réseau ouvert.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
from collections.abc import Callable
from pathlib import Path

from core.ipc import iter_messages, send_message
from core.logger import get_logger

_log = get_logger("myosd.ipc")

# Callback appelé à la réception d'un user_message.
# Reçoit (message, reply) où reply(dict) renvoie un message au popup émetteur.
UserMessageHandler = Callable[[dict, Callable[[dict], None]], None]
# Callback pour les confirmation_response (route vers IPCConfirmationProvider).
ConfirmationResponseHandler = Callable[[dict], None]
# Callback pour le message de contrôle reset (vide l'historique conversationnel).
ResetHandler = Callable[[], None]
# Contrôle de l'ordinateur : accusé d'affichage de l'indicateur, demande d'arrêt.
ControlReadyHandler = Callable[[dict], None]
StopControlHandler = Callable[[], None]
# Popup connecté (PID du processus, None s'il est inconnu) ou déconnecté (False).
ClientHandler = Callable[[int | None | bool], None]


def peer_pid(conn: socket.socket) -> int | None:
    """PID du processus à l'autre bout de la socket Unix (``SO_PEERCRED``, Linux)."""
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        return None
    try:
        raw = conn.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        pid, _uid, _gid = struct.unpack("3i", raw)
    except (OSError, struct.error):
        return None
    return pid or None


class IPCServer:  # pylint: disable=too-many-instance-attributes
    """Serveur socket Unix pour la communication daemon ↔ popup."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        socket_path: Path,
        on_user_message: UserMessageHandler,
        on_confirmation_response: ConfirmationResponseHandler | None = None,
        on_reset: ResetHandler | None = None,
        *,
        on_control_ready: ControlReadyHandler | None = None,
        on_stop_control: StopControlHandler | None = None,
        on_client: ClientHandler | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._on_user_message = on_user_message
        self._on_confirmation_response = on_confirmation_response
        self._on_reset = on_reset
        self._on_control_ready = on_control_ready
        self._on_stop_control = on_stop_control
        self._on_client = on_client
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._client_lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        """Crée la socket, la verrouille en 0600 et lance la boucle d'acceptation."""
        self._unlink_stale_socket()
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self._socket_path))
        os.chmod(self._socket_path, 0o600)
        sock.listen(1)
        self._server_sock = sock
        self._running = True

        threading.Thread(
            target=self._accept_loop, name="ipc-accept", daemon=True
        ).start()
        _log.info("Serveur IPC à l'écoute sur %s", self._socket_path)

    def send_to_client(self, message: dict) -> bool:
        """Envoie un message au popup connecté. Renvoie False si aucun client."""
        with self._client_lock:
            if self._client_sock is None:
                return False
            try:
                send_message(self._client_sock, message)
                return True
            except OSError:
                self._client_sock = None
                return False

    def stop(self) -> None:
        """Ferme proprement le client, le serveur et supprime le fichier socket."""
        self._running = False
        with self._client_lock:
            if self._client_sock is not None:
                self._close_quietly(self._client_sock)
                self._client_sock = None
        if self._server_sock is not None:
            self._close_quietly(self._server_sock)
            self._server_sock = None
        self._unlink_stale_socket()
        _log.info("Serveur IPC arrêté")

    def _accept_loop(self) -> None:
        assert self._server_sock is not None
        while self._running:
            try:
                conn, _ = self._server_sock.accept()
            except OSError:
                break  # socket fermée pendant stop()
            with self._client_lock:
                # Un seul popup à la fois ; un nouveau remplace l'ancien.
                if self._client_sock is not None:
                    self._close_quietly(self._client_sock)
                self._client_sock = conn
            _log.info("Popup connecté")
            if self._on_client is not None:
                self._on_client(peer_pid(conn))
            threading.Thread(
                target=self._read_loop, args=(conn,), name="ipc-read", daemon=True
            ).start()

    def _read_loop(self, conn: socket.socket) -> None:
        try:
            for message in iter_messages(conn):
                self._dispatch(message, conn)
        except OSError:
            pass
        finally:
            with self._client_lock:
                current = self._client_sock is conn
                if current:
                    self._client_sock = None
            self._close_quietly(conn)
            _log.info("Popup déconnecté")
            if current and self._on_client is not None:
                self._on_client(False)

    def _dispatch(self, message: dict, conn: socket.socket) -> None:
        """Route un message reçu selon son ``type``.

        ``user_message`` est traité dans un thread dédié pour ne pas bloquer la
        boucle de lecture (l'orchestrator peut attendre une confirmation, et
        celle-ci arrive précisément via cette boucle — sans thread on
        deadlockerait).
        """
        mtype = message.get("type")
        if mtype == "user_message":
            threading.Thread(
                target=self._on_user_message,
                args=(message, lambda m: self._reply(conn, m)),
                name="ipc-user-msg",
                daemon=True,
            ).start()
        elif mtype == "confirmation_response" and self._on_confirmation_response:
            self._on_confirmation_response(message)
        elif mtype == "reset" and self._on_reset:
            self._on_reset()
        elif mtype == "control_ready" and self._on_control_ready:
            self._on_control_ready(message)
        elif mtype == "stop_control" and self._on_stop_control:
            self._on_stop_control()

    def _reply(self, conn: socket.socket, message: dict) -> None:
        try:
            send_message(conn, message)
        except OSError:
            pass

    def _unlink_stale_socket(self) -> None:
        try:
            self._socket_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            _log.warning("Impossible de supprimer la socket existante : %s", exc)

    @staticmethod
    def _close_quietly(sock: socket.socket) -> None:
        try:
            sock.close()
        except OSError:
            pass
