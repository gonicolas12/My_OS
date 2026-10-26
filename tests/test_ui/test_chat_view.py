"""Tests de la vue de conversation (Qt hors écran)."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# pylint: disable=wrong-import-position  # import après importorskip (Qt optionnel)
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ui.chat_view import (
    AssistantMessage,
    ChatView,
    CodeBlock,
    EmptyState,
    MarkdownView,
    Notice,
    RichText,
    ToolCard,
    UserMessage,
    format_duration,
)


def test_format_duration() -> None:
    assert format_duration(3.24) == "3,2 s"
    assert format_duration(59.94) == "59,9 s"
    assert format_duration(112) == "1 min 52 s"


def test_richtext_ne_suit_aucun_lien_ni_ressource(app: object) -> None:
    text = RichText()
    assert not text.openLinks() and not text.openExternalLinks()
    assert text.loadResource(2, QtCore.QUrl("file:///etc/passwd")) is None


def test_richtext_epouse_la_hauteur_du_texte(app: object) -> None:
    text = RichText()
    text.resize(300, 10)
    text.show()
    text.set_plain("ligne\n" * 6)
    QtWidgets.QApplication.processEvents()
    tall = text.height()
    text.set_plain("une ligne")
    QtWidgets.QApplication.processEvents()
    assert text.height() < tall
    text.close()


def test_reponse_texte_outil_texte_dans_l_ordre(app: object) -> None:
    message = AssistantMessage()
    message.append_text("Je regarde ")
    message.append_text("les processus.")
    card = message.add_tool_result("list_processes", "ok", "1  python\n2  Xorg")
    message.append_text("Voici le **résultat**.")
    message.finish()

    parts = message.parts
    assert [type(p) for p in parts] == [MarkdownView, ToolCard, MarkdownView]
    assert parts[0].text == "Je regarde les processus."
    assert card.output.toPlainText() == "1  python\n2  Xorg"
    assert message.text == "Je regarde les processus.\n\nVoici le **résultat**."


def test_sortie_d_outil_jamais_interpretee(app: object) -> None:
    card = ToolCard("read_file", "ok", "**gras** <b>html</b> ![x](/etc/hostname)")
    assert card.output.toPlainText() == "**gras** <b>html</b> ![x](/etc/hostname)"


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("ok", "Terminé"),
        ("error", "Échec"),
        ("denied", "Refusé"),
        ("blocked", "Bloqué"),
    ],
)
def test_statuts_des_cartes(app: object, status: str, label: str) -> None:
    card = ToolCard("outil", status, "sortie")
    assert card.status_label.text() == label
    assert card.status_label.property("status") == status


def test_nom_d_outil_inconnu_tronque_et_en_texte_brut(app: object) -> None:
    card = ToolCard("<b>" + "x" * 200, "blocked", "Outil inconnu")
    names = [
        w for w in card.findChildren(QtWidgets.QLabel) if w.objectName() == "toolName"
    ]
    assert names[0].textFormat() == QtCore.Qt.TextFormat.PlainText
    assert len(names[0].text()) <= 48


def test_blocs_de_code_dans_leur_carte_et_reutilises(app: object) -> None:
    view = MarkdownView()
    view.set_text("Tapez :\n```bash\nsudo pacman -S htop")
    first = view.findChildren(CodeBlock)
    assert len(first) == 1
    view.set_text("Tapez :\n```bash\nsudo pacman -S htop\n```\nVoilà.")
    assert view.findChildren(CodeBlock) == first  # même widget, mis à jour
    assert first[0].findChild(RichText).toPlainText() == "sudo pacman -S htop"


def test_notice_et_statut(app: object) -> None:
    message = AssistantMessage()
    assert message.status_text() == "Le modèle réfléchit"
    notice = message.add_notice("warning", "⚠ repli local")
    assert isinstance(notice, Notice) and notice.property("level") == "warning"
    message.finish()
    message.finish()  # idempotent
    assert message.status_text() is None
    assert message.finished
    assert message.findChild(QtWidgets.QLabel, "assistantMeta").text().count("·") == 1


def test_bulle_utilisateur_en_texte_brut_et_bornee(app: object) -> None:
    message = UserMessage("<b>pas du html</b> " + "mot " * 80)
    message.resize(600, 200)
    message.show()
    QtWidgets.QApplication.processEvents()
    bubble = message.findChild(RichText)
    assert bubble.toPlainText().startswith("<b>pas du html</b>")
    assert bubble.width() <= int(600 * 0.8)
    message.close()


def test_accueil_puis_conversation_puis_accueil(app: object) -> None:
    view = ChatView("Ctrl+Alt+Espace")
    assert view.is_empty() and view.currentWidget() is view.empty_state
    clicked: list[str] = []
    view.suggestion_clicked.connect(clicked.append)
    view.empty_state.buttons[0].click()
    assert clicked and clicked[0].endswith("?")

    view.add_user_message("bonjour")
    view.add_assistant_message()
    assert not view.is_empty() and view.currentWidget() is not view.empty_state
    assert [type(w) for w in view.message_widgets()] == [UserMessage, AssistantMessage]

    view.clear()
    assert view.is_empty() and view.currentWidget() is view.empty_state
    assert not view.message_widgets()


def test_accueil_jamais_ecrase(app: object) -> None:
    # Longue demande en cours de saisie ou petite fenêtre : le logo s'efface, puis
    # l'accueil défile ; les suggestions ne perdent jamais de hauteur (écrasées, le
    # bas de leur texte était coupé : vu dans la démo du contrôle, en 1280x720).
    state = EmptyState("Ctrl+Alt+Espace")
    scroll = state.findChild(QtWidgets.QScrollArea).verticalScrollBar()
    state.resize(600, 700)
    state.show()
    QtWidgets.QApplication.processEvents()
    assert state.logo.isVisible() and scroll.maximum() == 0

    sans_logo = []
    for height in range(700, 90, -10):
        state.resize(600, height)
        QtWidgets.QApplication.processEvents()
        assert all(b.height() >= b.sizeHint().height() for b in state.buttons), height
        if state.logo.isHidden():
            sans_logo.append(scroll.maximum())
    assert sans_logo[0] == 0  # le logo s'efface avant qu'il faille défiler
    assert sans_logo[-1] > 0  # tout petit : l'accueil défile

    state.resize(600, 700)
    QtWidgets.QApplication.processEvents()
    assert state.logo.isVisible() and scroll.maximum() == 0
