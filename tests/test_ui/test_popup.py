"""Tests du popup avec un faux client IPC (Qt hors écran, sans daemon).

On vérifie le routage des messages du daemon vers LA réponse concernée, la
suspension de l'envoi pendant une réponse, la perte de connexion, la nouvelle
conversation, le refus d'office d'une confirmation orpheline et la mémoire de la
taille et de la position.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# pylint: disable=wrong-import-position,protected-access
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
QtGui = pytest.importorskip("PySide6.QtGui")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from core.config import Config
from ui.chat_view import AssistantMessage, Notice, UserMessage
from ui.popup import MESSAGE_CONNECTION_LOST, MESSAGE_OFFLINE, Popup


class FakeClient(QtCore.QObject):
    """Double de IPCClient : mêmes signaux, envois enregistrés."""

    show_requested = QtCore.Signal()
    message_received = QtCore.Signal(dict)
    connection_changed = QtCore.Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[dict] = []
        self.connected = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def wait(self, _ms: int = 0) -> bool:
        return True

    def send(self, message: dict) -> bool:
        if not self.connected:
            return False
        self.sent.append(message)
        return True

    def connect_daemon(self) -> None:
        self.connected = True
        self.connection_changed.emit(True)

    def drop(self) -> None:
        self.connected = False
        self.connection_changed.emit(False)


@pytest.fixture
def settings(tmp_path: Path) -> object:
    return QtCore.QSettings(
        str(tmp_path / "popup.conf"), QtCore.QSettings.Format.IniFormat
    )


@pytest.fixture
def popup(app: object, settings: object) -> Popup:
    widget = Popup(Config(), session_type="x11", client=FakeClient(), settings=settings)
    yield widget
    widget.hide()
    widget.deleteLater()


def _client(popup: Popup) -> FakeClient:
    return popup._client  # type: ignore[return-value]


def _send(popup: Popup, text: str) -> str:
    popup.composer.set_text(text)
    popup.composer.submit()
    return _client(popup).sent[-1]["id"]


def _last_turn(popup: Popup) -> AssistantMessage:
    return [w for w in popup.chat.message_widgets() if isinstance(w, AssistantMessage)][
        -1
    ]


def test_envoi_hors_ligne_signale_l_erreur_sans_rien_envoyer(popup: Popup) -> None:
    popup.composer.set_text("bonjour")
    popup.composer.submit()
    turn = _last_turn(popup)
    notices = [p for p in turn.parts if isinstance(p, Notice)]
    assert notices and notices[0].text() == MESSAGE_OFFLINE
    assert turn.finished and not popup.composer.is_busy()
    assert _client(popup).sent == []
    assert "Hors ligne" in popup.title_bar.mode_pill.text()


def test_message_envoye_puis_reponse_routee(popup: Popup) -> None:
    _client(popup).connect_daemon()
    assert "Local" in popup.title_bar.mode_pill.text()
    request_id = _send(popup, "quels processus ?")
    sent = _client(popup).sent[-1]
    assert sent["type"] == "user_message" and sent["use_cloud"] is False
    assert sent["content"] == "quels processus ?"
    assert popup.composer.is_busy()

    receive = _client(popup).message_received.emit
    receive({"type": "token", "id": request_id, "text": "Je regarde."})
    receive({"type": "token", "id": "autre", "text": "INTRUS"})  # ignoré
    receive(
        {
            "type": "tool_result",
            "id": request_id,
            "tool": "list_processes",
            "status": "ok",
            "text": "1 python",
        }
    )
    receive({"type": "notice", "id": request_id, "level": "info", "text": "☁ cloud"})
    receive({"type": "done", "id": request_id})

    turn = _last_turn(popup)
    kinds = [type(p).__name__ for p in turn.parts]
    assert kinds == ["MarkdownView", "ToolCard", "Notice"]
    assert "INTRUS" not in turn.text
    assert turn.finished and not popup.composer.is_busy()
    widgets = popup.chat.message_widgets()
    assert (
        isinstance(widgets[0], UserMessage) and widgets[0].text == "quels processus ?"
    )


def test_erreur_du_daemon_termine_la_reponse(popup: Popup) -> None:
    _client(popup).connect_daemon()
    request_id = _send(popup, "boum")
    _client(popup).message_received.emit(
        {"type": "error", "id": request_id, "message": "échec du modèle"}
    )
    turn = _last_turn(popup)
    assert turn.finished and not popup.composer.is_busy()
    assert any(
        "échec du modèle" in p.text() for p in turn.parts if isinstance(p, Notice)
    )


def test_perte_de_connexion_interrompt_la_reponse(popup: Popup) -> None:
    _client(popup).connect_daemon()
    _send(popup, "longue question")
    _client(popup).drop()
    turn = _last_turn(popup)
    assert turn.finished and not popup.composer.is_busy()
    assert [p.text() for p in turn.parts if isinstance(p, Notice)] == [
        MESSAGE_CONNECTION_LOST
    ]


def test_nouvelle_conversation_oublie_la_requete_en_cours(popup: Popup) -> None:
    client = _client(popup)
    client.connect_daemon()
    request_id = _send(popup, "question")
    popup.new_conversation()
    assert client.sent[-1] == {"type": "reset"}
    assert popup.chat.is_empty() and not popup.composer.is_busy()
    client.message_received.emit({"type": "token", "id": request_id, "text": "tard"})
    assert popup.chat.is_empty()  # fragment d'une requête oubliée : ignoré


def test_confirmation_orpheline_refusee_d_office(popup: Popup) -> None:
    client = _client(popup)
    client.connect_daemon()
    client.message_received.emit(
        {
            "type": "confirmation_needed",
            "id": "requete-oubliee",
            "request_id": "r1",
            "tool": "delete_file",
            "args": {"path": "/tmp/x"},
            "risk_level": 2,
            "summary": "[niveau 2] delete_file",
            "allowed_scopes": [],
        }
    )
    assert client.sent[-1] == {
        "type": "confirmation_response",
        "request_id": "r1",
        "decision": "deny",
    }


def test_cloud_sans_cle_annule_reste_en_local(
    popup: Popup, monkeypatch: object
) -> None:
    monkeypatch.setattr("ui.popup.secrets.has_api_key", lambda: False)
    monkeypatch.setattr(Popup, "_prompt_api_key", lambda self: False)
    _client(popup).connect_daemon()
    popup.composer.cloud_toggle.click()
    assert not popup.composer.cloud_toggle.isChecked()
    assert popup._cloud_banner.isHidden()
    _send(popup, "q")
    assert _client(popup).sent[-1]["use_cloud"] is False


def test_cloud_avec_cle_affiche_bandeau_et_pastille(
    popup: Popup, monkeypatch: object
) -> None:
    monkeypatch.setattr("ui.popup.secrets.has_api_key", lambda: True)
    _client(popup).connect_daemon()
    popup.composer.cloud_toggle.click()
    assert not popup._cloud_banner.isHidden()
    assert popup.title_bar.mode_pill.property("mode") == "cloud"
    _send(popup, "q")
    assert _client(popup).sent[-1]["use_cloud"] is True


def test_saisie_de_cle_au_dessus_du_popup(popup: Popup, monkeypatch: object) -> None:
    # Vu en VM (ISO v1.2, xfwm4) : sans le drapeau, la saisie de la clé, modale,
    # s'ouvrait SOUS le popup « toujours au-dessus » et le bloquait sans être visible.
    flags: list[object] = []

    class Saisie(QtWidgets.QInputDialog):
        def exec(self) -> int:
            flags.append(self.windowFlags())
            return 0  # Annuler

    monkeypatch.setattr("ui.popup.QInputDialog", Saisie)
    assert popup._prompt_api_key() is False
    assert flags[0] & QtCore.Qt.WindowType.WindowStaysOnTopHint


def test_echec_d_enregistrement_signale_au_dessus_du_popup(
    popup: Popup, monkeypatch: object
) -> None:
    flags: list[object] = []

    class Saisie(QtWidgets.QInputDialog):
        def exec(self) -> int:
            self.setTextValue("sk-ant-test")
            return 1  # Enregistrer

    class Alerte(QtWidgets.QMessageBox):
        def exec(self) -> int:
            flags.append(self.windowFlags())
            return 0

        @staticmethod
        def warning(*_args: object) -> object:
            # Raccourci statique : aucun drapeau réglable, l'alerte s'ouvrirait sous le
            # popup (et bloquerait ce test, faute de clic). Noté sans drapeau.
            flags.append(QtCore.Qt.WindowType.Widget)
            return QtWidgets.QMessageBox.StandardButton.Ok

    def trousseau_absent(_key: str) -> None:
        raise RuntimeError("aucun trousseau")

    monkeypatch.setattr("ui.popup.QInputDialog", Saisie)
    monkeypatch.setattr("ui.popup.QMessageBox", Alerte)
    monkeypatch.setattr("ui.popup.secrets.set_api_key", trousseau_absent)
    assert popup._prompt_api_key() is False
    assert flags[0] & QtCore.Qt.WindowType.WindowStaysOnTopHint


def _shortcut(popup: Popup, key: object) -> object:
    wanted = QtGui.QKeySequence(key)
    return next(s for s in popup.findChildren(QtGui.QShortcut) if s.key() == wanted)


def test_echap_ferme_le_popup(popup: Popup) -> None:
    popup.present()
    assert popup.isVisible()
    # La plateforme hors écran n'active pas les fenêtres : on déclenche le raccourci.
    _shortcut(popup, QtCore.Qt.Key.Key_Escape).activated.emit()
    assert not popup.isVisible()


def test_f11_bascule_le_plein_ecran(popup: Popup) -> None:
    popup.present()
    _shortcut(popup, QtCore.Qt.Key.Key_F11).activated.emit()
    assert popup.windowState() & QtCore.Qt.WindowState.WindowFullScreen
    _shortcut(popup, QtCore.Qt.Key.Key_F11).activated.emit()
    assert not popup.windowState() & QtCore.Qt.WindowState.WindowFullScreen


def test_plein_ecran_retrouve_a_la_reouverture(popup: Popup) -> None:
    popup.present()
    popup.setGeometry(100, 100, 700, 500)
    popup.toggle_fullscreen()
    popup.hide()
    # Comme xfwm4 : l'état d'une fenêtre masquée est effacé par le gestionnaire.
    popup.setWindowState(QtCore.Qt.WindowState.WindowNoState)
    popup.present()
    assert popup.windowState() & QtCore.Qt.WindowState.WindowFullScreen
    popup.toggle_fullscreen()
    assert not popup.windowState() & QtCore.Qt.WindowState.WindowFullScreen


def test_raccourci_sur_popup_ouvert_ne_change_pas_son_etat(popup: Popup) -> None:
    popup.present()
    popup.toggle_maximized()
    popup.hide()  # mémorise « agrandi »
    popup.present()
    popup.toggle_maximized()  # l'utilisateur restaure la fenêtre
    popup.present()  # nouvel appui sur le raccourci, popup déjà ouvert
    assert not popup.windowState() & QtCore.Qt.WindowState.WindowMaximized


def test_agrandi_retrouve_a_la_reouverture(popup: Popup) -> None:
    popup.present()
    popup.toggle_maximized()
    popup.hide()
    popup.setWindowState(QtCore.Qt.WindowState.WindowNoState)
    popup.present()
    assert popup.windowState() & QtCore.Qt.WindowState.WindowMaximized


def test_suggestion_remplit_la_saisie(popup: Popup) -> None:
    popup.chat.empty_state.buttons[2].click()
    assert popup.composer.text() == "Baisse le volume à 30 %"


def test_taille_et_position_memorisees(app: object, settings: object) -> None:
    first = Popup(Config(), session_type="x11", client=FakeClient(), settings=settings)
    first.present()
    first.setGeometry(40, 50, 640, 480)
    QtWidgets.QApplication.processEvents()
    first.hide()  # mémorise
    assert settings.value("geometry") is not None

    second = Popup(Config(), session_type="x11", client=FakeClient(), settings=settings)
    second.present()
    QtWidgets.QApplication.processEvents()
    assert (second.width(), second.height()) == (640, 480)
    first.deleteLater()
    second.hide()
    second.deleteLater()
