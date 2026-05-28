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
import threading
from collections.abc import Callable
from pathlib import Path

from core.ipc import iter_messages, send_message
from core.logger import get_logger

_log = get_logger("myosd.ipc")

# Callback appelé à la réception d'un user_message.
# Reçoit (message, reply) où reply(dict) renvoie un message au popup émetteur.
UserMessageHandler = Callable[[dict, Callable[[dict], None]], None]


class IPCServer:
    """Serveur socket Unix pour la communication daemon ↔ popup."""

    def __init__(self, socket_path: Path, on_user_message: UserMessageHandler) -> None:
        self._socket_path = socket_path
        self._on_user_message = on_user_message
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

        threading.Thread(target=self._accept_loop, name="ipc-accept", daemon=True).start()
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
            threading.Thread(
                target=self._read_loop, args=(conn,), name="ipc-read", daemon=True
            ).start()

    def _read_loop(self, conn: socket.socket) -> None:
        try:
            for message in iter_messages(conn):
                if message.get("type") == "user_message":
                    self._on_user_message(message, lambda m: self._reply(conn, m))
        except OSError:
            pass
        finally:
            with self._client_lock:
                if self._client_sock is conn:
                    self._client_sock = None
            self._close_quietly(conn)
            _log.info("Popup déconnecté")

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
