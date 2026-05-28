"""Popup Qt (PySide6) de My_OS — processus séparé du daemon.

Le popup est un **client** de la socket IPC. Il reste résident et caché, et
s'affiche (centré, au-dessus de tout, focus) quand le daemon envoie ``show``
(déclenché par le raccourci global). L'utilisateur tape un message, le popup
l'envoie au daemon ; Échap referme le popup.

Affichage via ``QTextBrowser`` (et non ``QWebEngineView``) pour une ouverture
instantanée. Tout texte dynamique est échappé avant insertion : un contenu reste
une DONNÉE, jamais du balisage de confiance (cf. docs/SECURITY.md §2.2).
"""

from __future__ import annotations

import html
import socket
import sys
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.config import Config, load_config
from core.ipc import iter_messages, send_message
from core.logger import get_logger
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


class Popup(QWidget):
    """Fenêtre popup : champ de saisie + zone d'affichage de la conversation."""

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
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
        self.setFixedSize(self._config.ui.width, self._config.ui.height)
        self.setStyleSheet(build_stylesheet(self._config.ui.theme))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(False)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Tapez votre message…")
        self._input.returnPressed.connect(self._send_current_input)
        layout.addWidget(self._view)
        layout.addWidget(self._input)

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

    def _send_current_input(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._view.append(f"<b>Vous :</b> {html.escape(text)}")
        self._input.clear()
        self._client.send(
            {
                "type": "user_message",
                "id": str(uuid4()),
                "content": text,
                "use_cloud": False,
            }
        )

    def _on_message(self, message: dict) -> None:
        mtype = message.get("type")
        if mtype == "token":
            self._view.append(f"<i>{html.escape(str(message.get('text', '')))}</i>")
        elif mtype == "error":
            self._view.append(
                f"<span style='color:#ff6b6b;'>Erreur : "
                f"{html.escape(str(message.get('message', '')))}</span>"
            )

    def keyPressEvent(self, event) -> None:  # noqa: ANN001 (signature Qt)
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001 (signature Qt)
        self._client.stop()
        self._client.wait(1000)
        super().closeEvent(event)


def main() -> None:
    """Lance le popup résident (caché jusqu'au premier ``show``)."""
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # rester résident quand le popup se cache
    config = load_config()
    popup = Popup(config)
    _ = popup  # garde une référence vivante
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
