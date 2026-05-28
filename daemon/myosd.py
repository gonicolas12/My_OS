"""Point d'entrée du daemon ``myosd`` — cycle de vie du service résident.

Service ``systemd`` **utilisateur** (jamais root, cf. docs/SECURITY.md menace 3).
Au jalon 1, le daemon :
  - expose la socket IPC ;
  - écoute le raccourci global et ordonne au popup de s'afficher (message ``show``) ;
  - répond à un ``user_message`` par un accusé de réception (stub, pas d'IA).

L'orchestrateur réel (modèle + outils + permissions) arrive au jalon 2.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable

from core.config import load_config
from core.logger import get_logger
from daemon.hotkey_listener import HotkeyListener
from daemon.ipc_server import IPCServer

_log = get_logger("myosd")


def handle_user_message(message: dict, reply: Callable[[dict], None]) -> None:
    """Stub du jalon 1 : accuse réception d'un message, sans modèle ni outils.

    Remplacé par ``daemon/orchestrator.py`` au jalon 2. Le contenu reçu est une
    DONNÉE non fiable : il n'est jamais interprété comme une instruction.
    """
    request_id = message.get("id")
    _log.info("user_message reçu (id=%s)", request_id)
    reply(
        {
            "type": "token",
            "id": request_id,
            "text": "(daemon : message reçu — jalon 1, pas encore d'IA)",
        }
    )
    reply({"type": "done", "id": request_id})


def main() -> None:
    """Démarre le daemon et bloque jusqu'à réception de SIGINT/SIGTERM."""
    config = load_config()
    _log.info("Démarrage de myosd (utilisateur, sans privilège root)")

    server = IPCServer(config.socket_path, on_user_message=handle_user_message)
    server.start()

    def on_hotkey() -> None:
        _log.info("Raccourci activé → demande d'affichage du popup")
        if not server.send_to_client({"type": "show"}):
            _log.warning("Aucun popup connecté ; affichage impossible")

    hotkey = HotkeyListener(config.hotkey, on_hotkey)
    hotkey.start()

    stop_event = threading.Event()

    def _on_signal(signum: int, _frame: object) -> None:
        _log.info("Signal %s reçu, arrêt en cours", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        stop_event.wait()
    finally:
        hotkey.stop()
        server.stop()
        _log.info("myosd arrêté proprement")


if __name__ == "__main__":
    main()
