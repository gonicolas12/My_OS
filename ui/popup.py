"""Popup Qt (PySide6) de My_OS — processus séparé du daemon.

Le popup est un **client** de la socket IPC. Il reste résident et caché, et
s'affiche (au-dessus de tout, focus sur la saisie) quand le daemon envoie ``show``
(déclenché par le raccourci global). L'utilisateur tape un message, le popup
l'envoie au daemon ; Échap referme le popup.

Fenêtre sans cadre système (``ui/window_chrome.py``) : barre de titre maison,
redimensionnement par les bords et les coins, agrandissement (double-clic ou
bouton), plein écran (F11 ou bouton). Taille, position et état sont mémorisés
(``~/.config/my_os/popup.conf``) : le popup revient là où on l'a laissé.

La conversation (``ui/chat_view.py``) affiche la réponse en streaming, rendue en
markdown par le moteur de texte de Qt (``QTextBrowser``, jamais ``QWebEngineView`` :
ouverture instantanée, aucun moteur web). Les sorties d'outils arrivent en messages
``tool_result`` et s'affichent en texte brut dans des cartes. Le contenu affiché
reste une DONNÉE : aucun script, aucun lien suivi, aucune ressource chargée (cf.
docs/SECURITY.md §2.2).
"""

from __future__ import annotations

import gc
import signal
import socket
import sys
import time
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEvent, QRect, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent, QCursor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.config import Config, load_config
from core.ipc import iter_messages, send_message
from core.logger import get_logger
from core.session import detect_session_type
from models import secrets
from ui.chat_view import AssistantMessage, ChatView
from ui.composer import Composer
from ui.confirm_dialog import ConfirmDialog
from ui.control_overlay import ControlOverlay
from ui.icons import logo_icon
from ui.styles import CHAT_COLUMN_MAX, build_stylesheet
from ui.wayland_layer import is_wayland, prepare_layer_shell
from ui.window_chrome import ResizeGrips, TitleBar, hotkey_label

_log = get_logger("popup")

_RECONNECT_MS = 500
_MIN_WIDTH = 380
_MIN_HEIGHT = 320
# États de fenêtre conservés d'une ouverture à l'autre.
_KEPT_STATES = Qt.WindowState.WindowFullScreen | Qt.WindowState.WindowMaximized

STATUS_THINKING = "Le modèle réfléchit"
STATUS_CONFIRMING = "En attente de votre confirmation"
STATUS_CONTROLLING = "My_OS contrôle l'ordinateur"
# Libellé de la barre d'arrêt pendant que le modèle choisit l'action suivante.
_CONTROL_STATUS = {
    "observe_screen": "Regarde l'écran",
    "click": "Clique",
    "type_text": "Tape au clavier",
    "press_keys": "Appuie sur des touches",
    "scroll": "Fait défiler",
    "open_app": "Ouvre une application",
    "focus_window": "Change de fenêtre",
}
MESSAGE_OFFLINE = (
    "Le service My_OS (myosd) ne répond pas : la demande n'a pas été envoyée. "
    "Vérifiez qu'il tourne (systemctl --user status myosd)."
)
MESSAGE_CONNECTION_LOST = (
    "Connexion au service My_OS perdue : la réponse est interrompue."
)


def _keep_above(dialog: QDialog) -> None:
    """Ouvre ``dialog`` dans la couche du popup, lui-même « toujours au-dessus ».

    Sans ``WindowStaysOnTopHint``, xfwm et consorts placent la fenêtre SOUS le
    popup : modale, elle le bloque alors sans être visible (vu en VM avec la saisie
    de la clé API ; même correctif que :class:`ui.confirm_dialog.ConfirmDialog`).
    """
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)


class IPCClient(QThread):
    """Connexion au daemon dans un thread, exposée à l'UI via des signaux Qt."""

    show_requested = Signal()
    message_received = Signal(dict)
    connection_changed = Signal(bool)

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
            self.connection_changed.emit(True)
            try:
                for message in iter_messages(self._sock):
                    if message.get("type") == "show":
                        self.show_requested.emit()
                    else:
                        self.message_received.emit(message)
            except OSError:
                pass
            self._close_socket()
            self.connection_changed.emit(False)

    def send(self, message: dict) -> bool:
        """Envoie un message au daemon ; ``False`` si déconnecté ou en échec."""
        if self._sock is None:
            return False
        try:
            send_message(self._sock, message)
        except OSError:
            return False
        return True

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


class Popup(QWidget):  # pylint: disable=too-many-instance-attributes
    """Fenêtre popup : barre de titre, conversation, zone de saisie."""

    def __init__(
        self,
        config: Config,
        session_type: str | None = None,
        *,
        client: IPCClient | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        # Type de session (x11/wayland) : conditionne la présentation (placement X11
        # vs surface layer-shell sous Wayland). Détecté par défaut, injectable pour
        # les tests (cf. docs/INTERFACES.md §9).
        self._session_type = session_type or detect_session_type()
        # Taille/position/état mémorisés entre deux ouvertures (et deux sessions).
        self._settings = (
            settings if settings is not None else QSettings("my_os", "popup")
        )
        self._placed = False
        self._connected = False
        self._was_maximized = False  # état à retrouver en quittant le plein écran
        # État (agrandi / plein écran) et géométrie normale au moment de la fermeture :
        # le gestionnaire de fenêtres les efface d'une fenêtre masquée (cf. present).
        self._shown_state = Qt.WindowState.WindowNoState
        self._normal_geometry: QRect | None = None
        # Réponses en cours, par id de requête : un message du daemon n'est routé
        # que vers SA réponse (les fragments d'une requête oubliée sont ignorés).
        self._turns: dict[str, AssistantMessage] = {}
        # Contrôle de l'ordinateur : requête qui pilote l'écran, et son indicateur
        # (bords lumineux + barre d'arrêt), créé à la demande.
        self._control_request: str | None = None
        self._overlay: ControlOverlay | None = None
        self._build_window()
        self._build_ui()

        self._client = client if client is not None else IPCClient(config.socket_path)
        self._client.show_requested.connect(self.present)
        self._client.message_received.connect(self._on_message)
        self._client.connection_changed.connect(self._on_connection_changed)
        self._client.start()

    # ---- construction --------------------------------------------------------
    def _build_window(self) -> None:
        self.setWindowTitle("My_OS")
        self.setWindowIcon(logo_icon())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # Taille de départ (config) ; ensuite celle choisie à la souris est gardée.
        self.resize(
            max(self._config.ui.width, _MIN_WIDTH),
            max(self._config.ui.height, _MIN_HEIGHT),
        )
        self.setMinimumSize(_MIN_WIDTH, _MIN_HEIGHT)
        self.setStyleSheet(build_stylesheet(self._config.ui.theme))

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._root = QFrame()
        self._root.setObjectName("popupRoot")
        outer.addWidget(self._root)
        layout = QVBoxLayout(self._root)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.title_bar = TitleBar()
        self.title_bar.new_chat_clicked.connect(self.new_conversation)
        self.title_bar.fullscreen_clicked.connect(self.toggle_fullscreen)
        self.title_bar.maximize_clicked.connect(self.toggle_maximized)
        self.title_bar.close_clicked.connect(self.hide)
        layout.addWidget(self.title_bar)

        self.chat = ChatView(hotkey_label(self._config.hotkey))
        self.chat.suggestion_clicked.connect(self._use_suggestion)
        layout.addWidget(self.chat, 1)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 6, 16, 10)
        bottom_layout.addStretch(1)
        column = QWidget()
        column.setMaximumWidth(CHAT_COLUMN_MAX)
        column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(6)

        # Indicateur « mode cloud actif » : très visible, caché tant que la bascule
        # est OFF. Rend l'envoi cloud VISIBLE (cf. SECURITY menace 2).
        self._cloud_banner = QLabel(
            "☁ Mode cloud actif : la prochaine requête sera envoyée à Anthropic (Claude)."
        )
        self._cloud_banner.setObjectName("cloudBanner")
        self._cloud_banner.setWordWrap(True)
        self._cloud_banner.hide()
        column_layout.addWidget(self._cloud_banner)

        self.composer = Composer()
        self.composer.submitted.connect(self._send)
        self.composer.cloud_toggled.connect(self._on_cloud_toggled)
        self.composer.key_requested.connect(self._prompt_api_key)
        column_layout.addWidget(self.composer)

        self._footer = QLabel(
            "Entrée : envoyer · Maj+Entrée : nouvelle ligne · "
            "Ctrl+L : nouvelle conversation · F11 : plein écran"
        )
        self._footer.setObjectName("footerHint")
        self._footer.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column_layout.addWidget(self._footer)
        bottom_layout.addWidget(column, 100)
        bottom_layout.addStretch(1)
        layout.addWidget(bottom)

        self._grips = ResizeGrips(self)

        # Échap cache le popup. Raccourci au niveau fenêtre (et non keyPressEvent)
        # car le champ de saisie focalisé avale la touche avant qu'elle remonte.
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self.hide)
        # Ctrl+L : nouvelle conversation (efface l'affichage ET demande au daemon
        # d'oublier l'historique, pour rester cohérents — cf. INTERFACES §1 reset).
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.new_conversation)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self).activated.connect(
            self.toggle_fullscreen
        )
        self._refresh_mode()
        self._refresh_window_state()

    # ---- affichage -----------------------------------------------------------
    def present(self) -> None:
        """Affiche le popup au premier plan, focus sur la saisie.

        Première ouverture : taille/position mémorisées, sinon centré sur l'écran
        du curseur. Ensuite, le popup revient où on l'a laissé (recentré seulement
        s'il n'est plus sur aucun écran, ex. moniteur débranché).
        """
        # Sous Wayland : pas de positionnement global côté client ; la surface
        # layer-shell est placée par le compositeur (cf. ui/wayland_layer.py).
        if not is_wayland(self._session_type):
            if not self._placed:
                self._placed = True
                if not self._restore_geometry():
                    self._center_on_screen()
            elif not self._is_on_screen():
                self._center_on_screen()
        if not self.isVisible():
            self._show_in_kept_state()
        self.raise_()
        self.activateWindow()
        self.composer.focus_input()

    def _show_in_kept_state(self) -> None:
        """Réaffiche le popup agrandi / en plein écran s'il l'était à la fermeture.

        xfwm4 (comme d'autres gestionnaires) retire ``_NET_WM_STATE`` d'une fenêtre
        masquée : un simple ``show()`` la rouvrirait à la taille de l'écran mais en
        fenêtre normale, débordant sous le panneau. On repart donc de la géométrie
        normale, puis on redemande l'état : quitter le plein écran retrouve la taille
        choisie par l'utilisateur.
        """
        state = self._shown_state
        if not state & _KEPT_STATES:
            self.show()
            return
        self.setWindowState(Qt.WindowState.WindowNoState)
        if self._normal_geometry is not None and self._normal_geometry.isValid():
            self.setGeometry(self._normal_geometry)
        if state & Qt.WindowState.WindowFullScreen:
            self.showFullScreen()
        else:
            self.showMaximized()

    def toggle_fullscreen(self) -> None:
        """Plein écran ⇄ état précédent (fenêtre normale ou agrandie)."""
        if self.isFullScreen():
            if self._was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()

    def toggle_maximized(self) -> None:
        """Agrandir ⇄ restaurer (sans effet en plein écran)."""
        if self.isFullScreen():
            return
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event: QEvent) -> None:
        """Suit les changements d'état (agrandi, plein écran) de la fenêtre."""
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._refresh_window_state()

    def resizeEvent(self, event: QEvent) -> None:
        """Replace les poignées ; cache l'aide clavier si la fenêtre est étroite."""
        super().resizeEvent(event)
        self._grips.place()
        self._footer.setVisible(self.width() >= 560 and self.height() >= 420)

    def hideEvent(self, event: QEvent) -> None:
        """Mémorise taille, position et état à chaque fermeture."""
        super().hideEvent(event)
        self._remember_state()
        self._save_geometry()

    def _remember_state(self) -> None:
        self._shown_state = self.windowState() & _KEPT_STATES
        self._normal_geometry = self.normalGeometry()

    def _refresh_window_state(self) -> None:
        maximized, fullscreen = self.isMaximized(), self.isFullScreen()
        self.title_bar.set_window_state(maximized=maximized, fullscreen=fullscreen)
        framed = not (maximized or fullscreen)
        self._grips.set_active(framed)
        self._root.setProperty("edge", "border" if framed else "none")
        self._root.style().unpolish(self._root)
        self._root.style().polish(self._root)

    def _restore_geometry(self) -> bool:
        geometry = self._settings.value("geometry")
        if geometry is None:
            return False
        try:
            restored = self.restoreGeometry(geometry)
        except (TypeError, ValueError):
            return False
        self._remember_state()
        return bool(restored) and self._is_on_screen()

    def _save_geometry(self) -> None:
        if is_wayland(self._session_type) or not self._placed:
            return
        self._settings.setValue("geometry", self.saveGeometry())
        self._settings.sync()

    def _is_on_screen(self) -> bool:
        frame = self.frameGeometry()
        return any(
            screen.availableGeometry().intersects(frame)
            for screen in QGuiApplication.screens()
        )

    def _center_on_screen(self) -> None:
        screen = (
            QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        )
        if screen is None:
            return
        available = screen.availableGeometry()
        self.setWindowState(Qt.WindowState.WindowNoState)
        width = min(self.width(), available.width())
        height = min(self.height(), available.height())
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    # ---- conversation ----------------------------------------------------------
    def new_conversation(self) -> None:
        """Nouvelle conversation : vide l'affichage et l'historique du daemon."""
        self._turns.clear()
        self.chat.clear()
        self.composer.set_busy(False)
        self._client.send({"type": "reset"})
        self.composer.focus_input()

    def _use_suggestion(self, text: str) -> None:
        self.composer.set_text(text)

    def _send(self, text: str) -> None:
        self.chat.add_user_message(text)
        turn = self.chat.add_assistant_message()
        request_id = str(uuid4())
        sent = self._connected and self._client.send(
            {
                "type": "user_message",
                "id": request_id,
                "content": text,
                # Opt-in cloud PAR REQUÊTE : porte l'état de la bascule. Le daemon reste
                # libre de replier en local si aucune clé n'est configurée.
                "use_cloud": self.composer.cloud_toggle.isChecked(),
            }
        )
        if not sent:
            turn.add_notice("error", MESSAGE_OFFLINE)
            turn.finish()
            return
        turn.set_status(STATUS_THINKING)
        self._turns[request_id] = turn
        self.composer.set_busy(True)

    def _finish(self, request_id: str) -> None:
        turn = self._turns.pop(request_id, None)
        if turn is not None:
            turn.finish()
        self.composer.set_busy(bool(self._turns))

    def _on_message(self, message: dict) -> None:
        mtype = message.get("type")
        if mtype == "confirmation_needed":
            self._show_confirmation_dialog(message)
            return
        if mtype == "control_state":
            self._on_control_state(message)
            return
        request_id = str(message.get("id", ""))
        turn = self._turns.get(request_id)
        if turn is None:
            return  # requête oubliée (nouvelle conversation) ou inconnue
        if mtype == "token":
            turn.append_text(str(message.get("text", "")))
        elif mtype == "tool_result":
            tool = str(message.get("tool", ""))
            text = str(message.get("text", ""))
            turn.add_tool_result(tool, str(message.get("status", "")), text)
            if request_id == self._control_request:
                self._show_control_step(tool, text)
        elif mtype == "notice":
            turn.add_notice(
                str(message.get("level", "info")), str(message.get("text", ""))
            )
        elif mtype == "done":
            self._finish(request_id)
        elif mtype == "error":
            turn.add_notice("error", f"Erreur : {message.get('message', '')}")
            self._finish(request_id)

    def _on_connection_changed(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            # Daemon perdu : son contrôle de l'écran s'arrête avec lui.
            self._end_control()
            for request_id, turn in list(self._turns.items()):
                turn.add_notice("error", MESSAGE_CONNECTION_LOST)
                self._finish(request_id)
        self._refresh_mode()

    # ---- contrôle de l'ordinateur ----------------------------------------------
    def _on_control_state(self, message: dict) -> None:
        """Allume ou éteint l'indicateur du contrôle de l'ordinateur.

        Allumage : le popup se cache (il masquerait l'écran piloté), les bords
        brillent et la barre d'arrêt apparaît, puis le daemon reçoit l'accusé
        ``control_ready`` avec le rectangle du bouton ■ — sans cet accusé, il ne
        commence pas. Seule une requête en cours de CETTE conversation l'obtient.
        """
        request_id = str(message.get("id", ""))
        if not message.get("active"):
            if request_id == self._control_request:
                self._end_control()
            return
        turn = self._turns.get(request_id)
        if turn is None or not self._connected:
            return  # requête oubliée : pas d'indicateur, donc pas de contrôle
        self._control_request = request_id
        turn.set_status(STATUS_CONTROLLING)
        self.hide()
        # Cycles Python libérés ici, à un moment sûr : le ramasse-miettes, s'il se
        # déclenchait pendant l'initialisation paresseuse des types PySide6 de
        # l'indicateur, ferait planter shiboken (vu dans les tests, Python 3.14).
        gc.collect()
        if self._overlay is None:
            self._overlay = ControlOverlay(self)
            self._overlay.stop_clicked.connect(self._stop_control)
        button = self._overlay.show_overlay(
            QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        )
        bar_area = self._overlay.bar_rect() or button
        self._client.send(
            {
                "type": "control_ready",
                "id": request_id,
                "stop_rect": [button.x(), button.y(), button.width(), button.height()],
                "bar_rect": [
                    bar_area.x(),
                    bar_area.y(),
                    bar_area.width(),
                    bar_area.height(),
                ],
            }
        )

    def _show_control_step(self, tool: str, text: str) -> None:
        """Dernière action du modèle, sous le titre de la barre d'arrêt."""
        if self._overlay is None:
            return
        first = text.strip().splitlines()[0] if text.strip() else ""
        label = _CONTROL_STATUS.get(tool, tool)
        self._overlay.set_status(f"{label} · {first}" if first else label)

    def _stop_control(self) -> None:
        """Bouton ■ : le daemon arrête aussitôt d'agir (l'indicateur s'éteint déjà)."""
        self._client.send({"type": "stop_control"})

    def _end_control(self) -> None:
        """Fin du contrôle : l'indicateur s'éteint en fondu, le popup revient."""
        if self._control_request is None:
            return
        request_id, self._control_request = self._control_request, None
        if self._overlay is not None:
            self._overlay.hide_overlay()
        turn = self._turns.get(request_id)
        if turn is not None:
            turn.set_status(STATUS_THINKING)
        self.present()

    def _refresh_mode(self) -> None:
        model = self._config.model
        if not self._connected:
            self.title_bar.set_mode(
                "offline", "Le service My_OS (myosd) ne répond pas pour l'instant."
            )
        elif self.composer.cloud_toggle.isChecked():
            self.title_bar.set_mode(
                "cloud",
                f"Les requêtes partent au cloud : {model.cloud_model} (Anthropic).",
            )
        else:
            backend = model.name if model.backend == "ollama" else "modèle de test"
            self.title_bar.set_mode(
                "local", f"Modèle local ({backend}) : rien ne quitte la machine."
            )

    # ---- cloud -----------------------------------------------------------------
    def _on_cloud_toggled(self, checked: bool) -> None:
        """Active/désactive le mode cloud pour les prochaines requêtes.

        À la première activation sans clé enregistrée, demande la clé (saisie
        masquée) et la stocke dans le trousseau. Si l'utilisateur annule, la
        bascule repasse sur OFF (jamais de cloud sans clé). L'indicateur visible
        suit l'état réel de la bascule.
        """
        if checked and not secrets.has_api_key() and not self._prompt_api_key():
            self.composer.set_cloud_checked(False)
        self._cloud_banner.setVisible(self.composer.cloud_toggle.isChecked())
        self._refresh_mode()

    def _prompt_api_key(self) -> bool:
        """Demande la clé API (saisie masquée) et la stocke dans le trousseau.

        Renvoie ``True`` si une clé a été enregistrée. La clé n'est jamais affichée
        ni envoyée par l'IPC : le popup l'écrit directement dans le trousseau du
        système (``models.secrets`` → keyring), cf. docs/SECURITY.md menace 4.
        """
        dialog = QInputDialog(self)
        _keep_above(dialog)
        dialog.setWindowTitle("Clé API Anthropic")
        dialog.setLabelText(
            "Clé API Anthropic (stockée dans le trousseau, jamais en clair) :"
        )
        dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
        dialog.setOkButtonText("Enregistrer")
        dialog.setCancelButtonText("Annuler")
        dialog.setMinimumWidth(460)
        if not dialog.exec():
            return False
        key = dialog.textValue()
        if not key.strip():
            return False
        try:
            secrets.set_api_key(key)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            alert = QMessageBox(
                QMessageBox.Icon.Warning,
                "Clé API",
                f"Impossible d'enregistrer la clé : {exc}",
                QMessageBox.StandardButton.Ok,
                self,
            )
            _keep_above(alert)
            alert.exec()
            return False
        return True

    # ---- confirmations ---------------------------------------------------------
    def _show_confirmation_dialog(self, payload: dict) -> None:
        """Affiche le dialog modal et renvoie la réponse au daemon."""
        request_id = str(payload.get("request_id", ""))
        turn_id = str(payload.get("id", ""))
        turn = self._turns.get(turn_id)
        if turn is None:
            # Action d'une requête abandonnée (nouvelle conversation entre-temps) :
            # refusée d'office, jamais exécutée sans un accord donné en contexte.
            _log.info("Confirmation d'une requête abandonnée : refus automatique")
            self._client.send(
                {
                    "type": "confirmation_response",
                    "request_id": request_id,
                    "decision": "deny",
                }
            )
            return
        turn.set_status(STATUS_CONFIRMING)
        if not self.isVisible() and self._control_request is None:
            self.present()  # la demande s'affiche sur la conversation qui la motive
        # Pendant le contrôle de l'écran, le popup reste caché : la confirmation
        # s'ouvre seule, au-dessus de l'application pilotée.
        dialog = ConfirmDialog(payload, parent=self)
        dialog.exec()
        decision, scope = dialog.response
        response: dict = {
            "type": "confirmation_response",
            "request_id": request_id,
            "decision": decision,
        }
        if scope is not None:
            response["scope"] = scope
        self._client.send(response)
        # Reprise : l'exécution de l'action suit la confirmation.
        turn.set_status(
            STATUS_CONTROLLING if self._control_request == turn_id else STATUS_THINKING
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        """Arrête proprement le thread IPC avant la fermeture de la fenêtre."""
        self._save_geometry()
        self._client.stop()
        self._client.wait(1000)
        super().closeEvent(event)


def main() -> None:
    """Lance le popup résident (caché jusqu'au premier ``show``)."""
    # La boucle Qt n'attrape pas les signaux Python : on rétablit le handler OS
    # par défaut pour que Ctrl+C dans le terminal de dev arrête le processus.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Sélection de présentation AVANT la QApplication (cf. docs/INTERFACES.md §9) :
    # sous Wayland, demande l'intégration shell layer-shell (overlay centré) ; sous
    # X11, no-op. Doit précéder QApplication pour être pris en compte par
    # l'intégration Qt Wayland.
    session_type = detect_session_type()
    prepare_layer_shell(session_type)

    started = time.monotonic()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # rester résident quand le popup se cache
    config = load_config()
    popup = Popup(config, session_type=session_type)
    _ = popup  # garde une référence vivante
    _log.info("Popup prêt (caché) en %.0f ms", (time.monotonic() - started) * 1000)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
