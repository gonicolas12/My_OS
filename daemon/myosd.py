"""Point d'entrée du daemon ``myosd`` — cycle de vie du service résident.

Service ``systemd`` **utilisateur** (jamais root, cf. docs/SECURITY.md menace 3).
Au jalon 2, le daemon assemble la chaîne complète :

* :class:`permissions.audit_log.AuditLog` — journal SQLite.
* :class:`permissions.session_grants.SessionGrants` — grants en RAM.
* :class:`models.stub_model.StubRuleModel` — modèle stub (remplacé par
  Ollama au jalon 7-8).
* :class:`tools.files.FILE_TOOLS` — outils du jalon 2.
* :class:`daemon.confirmation_provider.IPCConfirmationProvider` — pont IPC
  pour les confirmations modales du popup.
* :class:`daemon.orchestrator.Orchestrator` — choke point logique.
* :class:`daemon.ipc_server.IPCServer` — transport socket Unix.
* :class:`daemon.hotkey_listener.HotkeyListener` — raccourci global X11.

L'orchestrateur réel remplace le stub d'ack du jalon 1.
"""

from __future__ import annotations

import signal
import threading

from core.config import Config, load_config
from core.logger import get_logger
from daemon.confirmation_provider import IPCConfirmationProvider
from daemon.hotkey_listener import HotkeyListener
from daemon.ipc_server import IPCServer
from daemon.orchestrator import Orchestrator
from models.stub_model import StubRuleModel
from permissions.audit_log import AuditLog
from permissions.session_grants import SessionGrants
from tools.files import FILE_TOOLS

_log = get_logger("myosd")


class Daemon:  # pylint: disable=too-many-instance-attributes
    """Cycle de vie du service myosd — assemble et démarre tous les composants."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._audit = AuditLog(config.audit_db_path)
        self._grants = SessionGrants()
        self._model = StubRuleModel()
        self._server = IPCServer(
            config.socket_path,
            on_user_message=self._on_user_message,
            on_confirmation_response=self._on_confirmation_response,
        )
        self._confirmation = IPCConfirmationProvider(
            send_to_client=self._server.send_to_client
        )
        self._orchestrator = Orchestrator(
            model=self._model,
            tools=FILE_TOOLS,
            grants=self._grants,
            audit=self._audit,
            confirmation_provider=self._confirmation,
        )
        self._hotkey = HotkeyListener(config.hotkey, self._on_hotkey)
        self._stop_event = threading.Event()

    def run(self) -> None:
        """Démarre serveur + raccourci et bloque jusqu'à SIGINT/SIGTERM."""
        _log.info(
            "Démarrage de myosd (utilisateur, sans privilège root) — modèle=%s",
            self._model.name,
        )
        self._server.start()
        self._hotkey.start()
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)
        try:
            self._stop_event.wait()
        finally:
            self._shutdown()

    def _on_user_message(self, message: dict, reply) -> None:  # noqa: ANN001
        self._orchestrator.handle(message, reply)

    def _on_confirmation_response(self, message: dict) -> None:
        self._confirmation.deliver_response(message)

    def _on_hotkey(self) -> None:
        _log.info("Raccourci activé → demande d'affichage du popup")
        if not self._server.send_to_client({"type": "show"}):
            _log.warning("Aucun popup connecté ; affichage impossible")

    def _on_signal(self, signum: int, _frame: object) -> None:
        _log.info("Signal %s reçu, arrêt en cours", signum)
        self._stop_event.set()

    def _shutdown(self) -> None:
        self._hotkey.stop()
        self._server.stop()
        self._audit.close()
        _log.info("myosd arrêté proprement")


def main() -> None:
    """Lance le daemon avec la configuration par défaut."""
    Daemon(load_config()).run()


if __name__ == "__main__":
    main()
