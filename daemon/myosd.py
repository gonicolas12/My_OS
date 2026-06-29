"""Point d'entrée du daemon ``myosd`` — cycle de vie du service résident.

Service ``systemd`` **utilisateur** (jamais root, cf. docs/SECURITY.md menace 3).
Au jalon 2, le daemon assemble la chaîne complète :

* :class:`permissions.audit_log.AuditLog` — journal SQLite.
* :class:`permissions.session_grants.SessionGrants` — grants en RAM.
* :class:`models.stub_model.StubRuleModel` — modèle stub (remplacé par
  Ollama au jalon 7-8).
* outils des jalons 2 et 3, fusionnés : ``FILE_TOOLS`` (fichiers) +
  ``PROCESS_TOOLS`` (psutil) + ``PACKAGE_TOOLS`` (pacman) + ``SETTINGS_TOOLS``
  (D-Bus / pactl).
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

from core.config import Config, ModelConfig, load_config
from core.logger import get_logger
from daemon.confirmation_provider import IPCConfirmationProvider
from daemon.hotkey_listener import HotkeyListener
from daemon.ipc_server import IPCServer
from daemon.orchestrator import Model, Orchestrator
from models import secrets
from models.cloud_router import CloudRouter
from models.stub_model import StubRuleModel
from permissions.audit_log import AuditLog
from permissions.session_grants import SessionGrants
from tools.base_tool import BaseTool
from tools.files import FILE_TOOLS
from tools.packages import PACKAGE_TOOLS
from tools.processes import PROCESS_TOOLS
from tools.system_settings import SETTINGS_TOOLS

_log = get_logger("myosd")


def _build_tools() -> dict[str, BaseTool]:
    """Fusionne tous les registres d'outils câblés dans le daemon.

    Les noms d'outils sont uniques entre registres (vérifié par les tests) ;
    en cas de collision accidentelle, la dernière source l'emporterait, donc on
    garde des préfixes de noms distincts par domaine.
    """
    return {
        **FILE_TOOLS,
        **PROCESS_TOOLS,
        **PACKAGE_TOOLS,
        **SETTINGS_TOOLS,
    }


def _build_model(model_config: ModelConfig) -> Model:
    """Construit le backend modèle demandé dans la config.

    Le client Ollama n'est importé que si nécessaire — ça évite d'exiger la
    présence du module ``ollama`` quand on utilise le stub.
    """
    backend = (model_config.backend or "stub").lower()
    if backend == "ollama":
        from models.local_llm import OllamaClient  # pylint: disable=import-outside-toplevel

        _log.info(
            "Modèle : Ollama (%s, raisonnement %s)",
            model_config.name,
            "activé" if model_config.think else "désactivé",
        )
        return OllamaClient(
            model=model_config.name,
            host=model_config.host,
            think=model_config.think,
        )
    if backend != "stub":
        _log.warning(
            "Backend modèle inconnu %r, repli sur le stub à base de règles",
            backend,
        )
    _log.info("Modèle : stub à base de règles")
    return StubRuleModel()


class Daemon:  # pylint: disable=too-many-instance-attributes
    """Cycle de vie du service myosd — assemble et démarre tous les composants."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._audit = AuditLog(config.audit_db_path)
        self._grants = SessionGrants()
        self._model = _build_model(config.model)
        # Routeur cloud (jalon 4) : sélection du backend Claude PAR REQUÊTE selon
        # use_cloud. Le local reste le défaut ; le cloud n'est utilisable qu'une fois
        # une clé saisie dans le popup (trousseau OS). Construit même sans clé : son
        # is_available() reflète dynamiquement la présence de la clé.
        self._cloud_router = CloudRouter(model=config.model.cloud_model)
        self._server = IPCServer(
            config.socket_path,
            on_user_message=self._on_user_message,
            on_confirmation_response=self._on_confirmation_response,
            on_reset=self._on_reset,
        )
        self._confirmation = IPCConfirmationProvider(
            send_to_client=self._server.send_to_client
        )
        self._orchestrator = Orchestrator(
            model=self._model,
            tools=_build_tools(),
            grants=self._grants,
            audit=self._audit,
            confirmation_provider=self._confirmation,
            cloud_router=self._cloud_router,
        )
        self._hotkey = HotkeyListener(config.hotkey, self._on_hotkey)
        self._stop_event = threading.Event()

    def run(self) -> None:
        """Démarre serveur + raccourci et bloque jusqu'à SIGINT/SIGTERM."""
        _log.info(
            "Démarrage de myosd (utilisateur, sans privilège root) — modèle=%s",
            getattr(self._model, "name", type(self._model).__name__),
        )
        _log.info(
            "Routeur cloud : %s (modèle %s, opt-in par requête)",
            (
                "clé configurée"
                if secrets.has_api_key()
                else "aucune clé (cloud indisponible jusqu'à saisie via le popup)"
            ),
            self._config.model.cloud_model,
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

    def _on_reset(self) -> None:
        _log.info("Reset de la conversation demandé par le popup")
        self._orchestrator.reset_history()

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
