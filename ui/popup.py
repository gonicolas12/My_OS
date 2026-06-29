"""Popup Qt (PySide6) de My_OS — processus séparé du daemon.

Le popup est un **client** de la socket IPC. Il reste résident et caché, et
s'affiche (centré, au-dessus de tout, focus) quand le daemon envoie ``show``
(déclenché par le raccourci global). L'utilisateur tape un message, le popup
l'envoie au daemon ; Échap referme le popup.

La réponse du modèle s'affiche en streaming (token par token) et est rendue en
markdown via ``QTextBrowser.setMarkdown`` (et non ``QWebEngineView`` : ouverture
instantanée, aucun moteur web). Le contenu affiché reste une DONNÉE — QTextBrowser
n'exécute aucun script et les liens ne s'ouvrent pas seuls (cf. docs/SECURITY.md
§2.2 et ui/markdown_render.py).
"""

from __future__ import annotations

import signal
import socket
import sys
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizeGrip,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.config import Config, load_config
from core.ipc import iter_messages, send_message
from core.logger import get_logger
from models import secrets
from ui.confirm_dialog import ConfirmDialog
from ui.markdown_render import conversation_to_markdown
from ui.styles import build_stylesheet

_log = get_logger("popup")

_RECONNECT_MS = 500


class IPCClient(QThread):
    """Connexion au daemon dans un thread, exposée à l'UI via des signaux Qt."""

    show_requested = Signal()
    message_received = Signal(dict)

    def __init__(self, socket_path: Path) -> None:
        super().__init__()
        self._socket_path = socket_path
        self._sock: socket.socket | None = None
        self._running = True

    def run(self) -> None:
        """Boucle de connexion puis de lecture : relaie les messages à l'UI."""
        while self._running:
            if not self._connect():
                self.msleep(_RECONNECT_MS)
                continue
            assert self._sock is not None
            try:
                for message in iter_messages(self._sock):
                    if message.get("type") == "show":
                        self.show_requested.emit()
                    else:
                        self.message_received.emit(message)
            except OSError:
                pass
            self._close_socket()

    def send(self, message: dict) -> None:
        """Envoie un message au daemon (sans rien faire si déconnecté)."""
        if self._sock is None:
            return
        try:
            send_message(self._sock, message)
        except OSError:
            pass

    def stop(self) -> None:
        """Stoppe la boucle et ferme la connexion."""
        self._running = False
        self._close_socket()

    def _connect(self) -> bool:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self._socket_path))
        except OSError:
            sock.close()
            return False
        self._sock = sock
        _log.info("Connecté au daemon (%s)", self._socket_path)
        return True

    def _close_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class _DragBar(QWidget):
    """Barre de titre qui permet de déplacer une fenêtre frameless à la souris."""

    def __init__(self, window: QWidget) -> None:
        super().__init__()
        self._window = window
        self._press_offset: QPoint | None = None
        self.setObjectName("dragbar")
        self.setFixedHeight(30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        title = QLabel("My_OS")
        title.setObjectName("dragtitle")
        layout.addWidget(title)
        layout.addStretch()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Mémorise l'écart curseur ↔ coin de la fenêtre au début du glisser."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_offset = (
                event.globalPosition().toPoint()
                - self._window.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Déplace la fenêtre en suivant le curseur tant que le bouton est tenu."""
        if (
            self._press_offset is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            self._window.move(event.globalPosition().toPoint() - self._press_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Termine le glisser."""
        self._press_offset = None
        event.accept()


class Popup(QWidget):  # pylint: disable=too-many-instance-attributes
    """Fenêtre popup : champ de saisie + zone d'affichage de la conversation."""

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._status_base = ""
        self._status_dots = 0
        self._messages: list[dict[str, str]] = []
        self._build_window()
        self._build_ui()

        self._client = IPCClient(config.socket_path)
        self._client.show_requested.connect(self._show_centered)
        self._client.message_received.connect(self._on_message)
        self._client.start()

    def _build_window(self) -> None:
        self.setWindowTitle("My_OS")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # Taille initiale (depuis la config) mais fenêtre redimensionnable :
        # on fixe seulement un minimum pour rester utilisable.
        self.resize(self._config.ui.width, self._config.ui.height)
        self.setMinimumSize(360, 240)
        self.setStyleSheet(build_stylesheet(self._config.ui.theme))

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Barre de titre déplaçable.
        root.addWidget(_DragBar(self))

        body = QVBoxLayout()
        body.setContentsMargins(8, 6, 8, 8)
        body.setSpacing(6)

        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(False)
        body.addWidget(self._view)

        # Contrôles cloud (jalon 4) : toggle opt-in PAR REQUÊTE + bouton clé.
        cloud_row = QHBoxLayout()
        cloud_row.setContentsMargins(2, 0, 2, 0)
        self._cloud_checkbox = QCheckBox("☁ Cloud (Claude)")
        self._cloud_checkbox.setObjectName("cloudtoggle")
        self._cloud_checkbox.setToolTip(
            "Envoyer CETTE requête au cloud (Claude) au lieu du modèle local.\n"
            "Opt-in, par requête. Nécessite une clé API (bouton 🔑)."
        )
        self._cloud_checkbox.toggled.connect(self._on_cloud_toggled)
        self._key_button = QPushButton("🔑")
        self._key_button.setObjectName("keybutton")
        self._key_button.setToolTip(
            "Configurer la clé API Anthropic\n(stockée dans le trousseau du système)"
        )
        self._key_button.clicked.connect(self._prompt_api_key)
        cloud_row.addWidget(self._cloud_checkbox)
        cloud_row.addWidget(self._key_button)
        cloud_row.addStretch()
        body.addLayout(cloud_row)

        # Indicateur « mode cloud actif » : très visible, caché tant que le toggle
        # est OFF. Rend l'envoi cloud VISIBLE (cf. SECURITY menace 2).
        self._cloud_banner = QLabel(
            "☁ Mode cloud actif — cette requête sera envoyée à Anthropic (Claude)."
        )
        self._cloud_banner.setObjectName("cloudbanner")
        self._cloud_banner.setWordWrap(True)
        self._cloud_banner.hide()
        body.addWidget(self._cloud_banner)

        # Indicateur d'activité (caché au repos), animé par un QTimer.
        self._status = QLabel()
        self._status.setObjectName("status")
        self._status.hide()
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._tick_status)
        body.addWidget(self._status)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Tapez votre message…")
        self._input.returnPressed.connect(self._send_current_input)
        body.addWidget(self._input)

        # Poignée de redimensionnement en bas à droite (fonctionne en frameless).
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch()
        grip_row.addWidget(QSizeGrip(self))
        body.addLayout(grip_row)

        root.addLayout(body)

        # Échap cache le popup. Raccourci au niveau fenêtre (et non keyPressEvent)
        # car le QLineEdit focalisé avale la touche avant qu'elle remonte.
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(self.hide)

        # Ctrl+L : nouvelle conversation (efface l'affichage ET demande au daemon
        # d'oublier l'historique, pour rester cohérents — cf. INTERFACES §1 reset).
        new_chat = QShortcut(QKeySequence("Ctrl+L"), self)
        new_chat.activated.connect(self._new_conversation)

    def _set_status(self, text: str) -> None:
        """Affiche l'indicateur d'activité avec une animation de points."""
        self._status_base = text
        self._status_dots = 0
        self._status.setText(text)
        self._status.show()
        self._status_timer.start(400)

    def _clear_status(self) -> None:
        """Cache l'indicateur d'activité."""
        self._status_timer.stop()
        self._status.clear()
        self._status.hide()

    def _tick_status(self) -> None:
        """Anime les points de suspension de l'indicateur."""
        self._status_dots = (self._status_dots + 1) % 4
        self._status.setText(self._status_base + "." * self._status_dots)

    def _show_centered(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            frame = self.frameGeometry()
            frame.moveCenter(screen.availableGeometry().center())
            self.move(frame.topLeft())
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

    def _render(self) -> None:
        """Rend toute la conversation en markdown et défile vers le bas."""
        self._view.setMarkdown(conversation_to_markdown(self._messages))
        scrollbar = self._view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _append_to_assistant(self, text: str) -> None:
        """Ajoute du texte au message assistant en cours (en crée un au besoin)."""
        if not self._messages or self._messages[-1]["role"] != "assistant":
            self._messages.append({"role": "assistant", "text": ""})
        self._messages[-1]["text"] += text

    def _new_conversation(self) -> None:
        """Démarre une nouvelle conversation : vide l'affichage et l'historique daemon."""
        self._messages.clear()
        self._render()
        self._clear_status()
        self._client.send({"type": "reset"})

    def _send_current_input(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._messages.append({"role": "user", "text": text})
        # Bulle assistant vide, prête à recevoir le streaming.
        self._messages.append({"role": "assistant", "text": ""})
        self._render()
        self._set_status("Le modèle réfléchit")
        self._client.send(
            {
                "type": "user_message",
                "id": str(uuid4()),
                "content": text,
                # Opt-in cloud PAR REQUÊTE : porte l'état du toggle. Le daemon reste
                # libre de replier en local si aucune clé n'est configurée.
                "use_cloud": self._cloud_checkbox.isChecked(),
            }
        )

    def _on_cloud_toggled(self, checked: bool) -> None:
        """Active/désactive le mode cloud pour les prochaines requêtes.

        À la première activation sans clé enregistrée, demande la clé (saisie
        masquée) et la stocke dans le trousseau. Si l'utilisateur annule, on
        rebascule le toggle sur OFF (jamais de cloud sans clé). L'indicateur visible
        suit l'état réel du toggle.
        """
        if checked and not secrets.has_api_key():
            if not self._prompt_api_key():
                # Annulation : repasse OFF (ré-entre ici avec checked=False).
                self._cloud_checkbox.setChecked(False)
                return
        self._cloud_banner.setVisible(self._cloud_checkbox.isChecked())

    def _prompt_api_key(self) -> bool:
        """Demande la clé API (saisie masquée) et la stocke dans le trousseau.

        Renvoie ``True`` si une clé a été enregistrée. La clé n'est jamais affichée
        ni envoyée par l'IPC : le popup l'écrit directement dans le trousseau du
        système (``models.secrets`` → keyring), cf. docs/SECURITY.md menace 4.
        """
        key, ok = QInputDialog.getText(
            self,
            "Clé API Anthropic",
            "Clé API Anthropic (stockée dans le trousseau, jamais en clair) :",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not key.strip():
            return False
        try:
            secrets.set_api_key(key)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            QMessageBox.warning(
                self, "Clé API", f"Impossible d'enregistrer la clé : {exc}"
            )
            return False
        return True

    def _on_message(self, message: dict) -> None:
        mtype = message.get("type")
        if mtype == "token":
            # Streaming : chaque fragment s'ajoute à la réponse en cours.
            self._append_to_assistant(str(message.get("text", "")))
            self._render()
        elif mtype == "done":
            self._clear_status()
        elif mtype == "error":
            self._clear_status()
            self._append_to_assistant(f"\n\n⚠ Erreur : {message.get('message', '')}")
            self._render()
        elif mtype == "confirmation_needed":
            self._show_confirmation_dialog(message)

    def _show_confirmation_dialog(self, payload: dict) -> None:
        """Affiche le dialog modal et renvoie la réponse au daemon."""
        self._set_status("En attente de votre confirmation")
        dialog = ConfirmDialog(payload, parent=self)
        dialog.exec()
        decision, scope = dialog.response
        response: dict = {
            "type": "confirmation_response",
            "request_id": str(payload.get("request_id", "")),
            "decision": decision,
        }
        if scope is not None:
            response["scope"] = scope
        self._client.send(response)
        # Reprise : l'exécution de l'action suit la confirmation.
        self._set_status("Le modèle réfléchit")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Arrête proprement le thread IPC avant la fermeture de la fenêtre."""
        self._client.stop()
        self._client.wait(1000)
        super().closeEvent(event)


def main() -> None:
    """Lance le popup résident (caché jusqu'au premier ``show``)."""
    # La boucle Qt n'attrape pas les signaux Python : on rétablit le handler OS
    # par défaut pour que Ctrl+C dans le terminal de dev arrête le processus.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # rester résident quand le popup se cache
    config = load_config()
    popup = Popup(config)
    _ = popup  # garde une référence vivante
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
