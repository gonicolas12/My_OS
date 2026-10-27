"""Langue de l'interface (Qt hors écran) : langue de départ, menu de la barre de
titre, changement à chaud, et dialogues, indicateur du contrôle dans la langue
choisie."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# pylint: disable=wrong-import-position,protected-access
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from core.config import Config
from core.i18n import current_language, use_language
from tests.test_ui.test_popup import FakeClient
from ui.chat_view import AssistantMessage, ToolCard, format_duration
from ui.confirm_dialog import ConfirmDialog
from ui.control_overlay import StopBar
from ui.popup import Popup
from ui.window_chrome import hotkey_label


@pytest.fixture
def settings(tmp_path: Path) -> object:
    return QtCore.QSettings(
        str(tmp_path / "popup.conf"), QtCore.QSettings.Format.IniFormat
    )


def _popup(settings: object, config: Config | None = None) -> Popup:
    return Popup(
        config or Config(), session_type="x11", client=FakeClient(), settings=settings
    )


def _close(popup: Popup) -> None:
    popup.hide()
    popup.deleteLater()


def _client(popup: Popup) -> FakeClient:
    return popup._client  # type: ignore[return-value]


# ---- langue de départ ---------------------------------------------------------


def test_systeme_en_anglais_interface_en_anglais(
    app: object, settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    popup = _popup(settings)
    try:
        assert current_language() == "en"
        assert popup.title_bar.esc_text.text() == "to close"
        assert popup.title_bar.language_button.text() == "EN"
        assert popup.composer.input.placeholderText() == "Ask My_OS anything…"
        assert popup.chat.empty_state.buttons[2].text() == "Lower the volume to 30%"
        assert "Hors ligne" not in popup.title_bar.mode_pill.text()
        assert "Offline" in popup.title_bar.mode_pill.text()
    finally:
        _close(popup)


def test_systeme_dans_une_autre_langue_interface_en_anglais(
    app: object, settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    popup = _popup(settings)
    try:
        assert current_language() == "en"
    finally:
        _close(popup)


def test_choix_memorise_prioritaire_sur_le_systeme(
    app: object, settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    settings.setValue("language", "fr")
    popup = _popup(settings)
    try:
        assert current_language() == "fr"
        assert popup.title_bar.esc_text.text() == "pour fermer"
    finally:
        _close(popup)


def test_langue_de_la_config_si_rien_n_est_memorise(
    app: object, settings: object
) -> None:
    popup = _popup(settings, Config(language="en"))  # système en français
    try:
        assert current_language() == "en"
    finally:
        _close(popup)


# ---- menu de la langue ---------------------------------------------------------


def test_menu_de_la_langue_change_tout_et_memorise(
    app: object, settings: object
) -> None:
    popup = _popup(settings)
    try:
        bar = popup.title_bar
        assert bar.language_button.text() == "FR"
        assert bar.language_actions["fr"].isChecked()
        assert [a.text() for a in bar.language_button.menu().actions()] == [
            "Français",
            "English",
        ]
        bar.language_actions["en"].trigger()
        assert current_language() == "en"
        assert settings.value("language") == "en"
        assert bar.language_button.text() == "EN"
        assert bar.language_actions["en"].isChecked()
        assert bar.esc_key.text() == "Esc"
        assert bar.close_button.toolTip() == "Close (Esc)"
        assert popup._footer.text().startswith("Enter: send")
        assert popup.chat.empty_state.buttons[0].text().startswith("Which processes")
        bar.language_actions["fr"].trigger()
        assert current_language() == "fr"
        assert settings.value("language") == "fr"
        assert bar.esc_key.text() == "Échap"
        assert (
            popup.composer.input.placeholderText() == "Demandez quelque chose à My_OS…"
        )
    finally:
        _close(popup)


def test_chaque_requete_porte_la_langue(app: object, settings: object) -> None:
    popup = _popup(settings)
    try:
        client = _client(popup)
        client.connect_daemon()
        popup.composer.set_text("quels processus ?")
        popup.composer.submit()
        assert client.sent[-1]["language"] == "fr"
        client.message_received.emit({"type": "done", "id": client.sent[-1]["id"]})
        popup.set_language("en")
        popup.composer.set_text("which processes?")
        popup.composer.submit()
        assert client.sent[-1]["language"] == "en"
    finally:
        _close(popup)


def test_suggestion_dans_la_langue_affichee(app: object, settings: object) -> None:
    popup = _popup(settings)
    try:
        popup.set_language("en")
        popup.chat.empty_state.buttons[2].click()
        assert popup.composer.text() == "Lower the volume to 30%"
    finally:
        _close(popup)


def test_conversation_retraduite_a_chaud(app: object, settings: object) -> None:
    popup = _popup(settings)
    try:
        client = _client(popup)
        client.connect_daemon()
        popup.composer.set_text("quels processus ?")
        popup.composer.submit()
        first = client.sent[-1]["id"]
        receive = client.message_received.emit
        receive(
            {
                "type": "tool_result",
                "id": first,
                "tool": "list_processes",
                "status": "ok",
                "text": "Top 1 processus par memory :",
            }
        )
        receive({"type": "done", "id": first})
        popup.composer.set_text("et le CPU ?")
        popup.composer.submit()  # réponse en cours : « Le modèle réfléchit »

        popup.set_language("en")
        turns = [
            w for w in popup.chat.message_widgets() if isinstance(w, AssistantMessage)
        ]
        card = next(p for p in turns[0].parts if isinstance(p, ToolCard))
        assert card.status_label.text() == "Done"
        # Ce que le daemon a dit reste tel quel (c'était la langue de la requête).
        assert card.output.toPlainText() == "Top 1 processus par memory :"
        assert turns[1].status_text() == "The model is thinking"
        meta = turns[0].findChild(QtWidgets.QLabel, "assistantMeta").text()
        assert "," not in meta.split("·")[1]  # durée à l'anglaise : 0.0 s
    finally:
        _close(popup)


def test_langue_inconnue_ignoree(app: object, settings: object) -> None:
    popup = _popup(settings)
    try:
        popup.set_language("klingon")
        assert current_language() == "fr"
        assert settings.value("language") is None
    finally:
        _close(popup)


# ---- dialogues et indicateur ---------------------------------------------------


def _buttons(dialog: ConfirmDialog) -> set[str]:
    return {b.text() for b in dialog.findChildren(QtWidgets.QPushButton)}


def test_confirmation_en_anglais(app: object) -> None:
    payload = {
        "type": "confirmation_needed",
        "id": "m1",
        "request_id": "r1",
        "tool": "write_file",
        "args": {"path": "/tmp/x"},
        "risk_level": 1,
        "summary": "[level 1] write_file({'path': '/tmp/x'})",
        "requires_elevation": True,
        "allowed_scopes": ["this_folder", "session"],
    }
    with use_language("en"):
        dialog = ConfirmDialog(payload)
    assert _buttons(dialog) == {"Deny", "Once", "For this folder", "For this session"}
    assert dialog.windowTitle() == "Confirmation required — elevated privileges"
    badge = dialog.findChild(QtWidgets.QLabel, "riskBadge")
    assert badge.text() == "LEVEL 1 · CONFIRMATION"


def test_accord_de_controle_en_anglais(app: object) -> None:
    payload = {
        "type": "confirmation_needed",
        "id": "m1",
        "request_id": "r1",
        "tool": "computer_control",
        "args": {"demande": "open Mousepad and type a shopping list"},
        "risk_level": 2,
        "summary": "Take control of the computer for this request",
        "requires_elevation": False,
        "allowed_scopes": [],
        "control": True,
    }
    with use_language("en"):
        dialog = ConfirmDialog(payload)
    assert _buttons(dialog) == {"Deny", "Allow for this request"}
    assert dialog.windowTitle() == "Computer control"
    details = dialog.findChild(QtWidgets.QTextBrowser, "confirmText").toPlainText()
    assert "“open Mousepad and type a shopping list”" in details
    assert "Esc key" in details


def test_barre_d_arret_en_anglais(app: object) -> None:
    with use_language("en"):
        bar = StopBar(solid=True)
    texts = [label.text() for label in bar.findChildren(QtWidgets.QLabel)]
    assert "My_OS is controlling the computer" in texts
    assert {"Click here", "or press", "Esc", "to stop"} <= set(texts)
    bar.deleteLater()


def test_raccourci_et_duree_en_anglais() -> None:
    with use_language("en"):
        assert hotkey_label("<ctrl>+<alt>+<space>") == "Ctrl+Alt+Space"
        assert hotkey_label("<cmd>+<shift>+<enter>") == "Super+Shift+Enter"
        assert format_duration(3.24) == "3.2 s"
        assert format_duration(112) == "1 min 52 s"
