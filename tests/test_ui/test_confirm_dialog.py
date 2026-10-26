"""Tests du dialogue de confirmation (Qt hors écran).

Constats du labo VM : la fenêtre s'ouvrait SOUS le popup et « Une fois » était le
bouton par défaut — un seul Entrée approuvait une suppression de niveau 2 sans que
rien ne soit visible. On verrouille ici : défaut sûr, portées venues du daemon,
premier plan, et mention polkit seulement quand elle est vraie.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# pylint: disable=wrong-import-position  # import après importorskip (Qt optionnel)
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
QtGui = pytest.importorskip("PySide6.QtGui")
QtTest = pytest.importorskip("PySide6.QtTest")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ui.chat_view import RichText
from ui.confirm_dialog import ConfirmDialog


def _payload(level: int, scopes: list[str], elevation: bool = False) -> dict:
    return {
        "type": "confirmation_needed",
        "id": "m1",
        "request_id": "r1",
        "tool": "delete_file",
        "args": {"path": "/tmp/x"},
        "risk_level": level,
        "summary": f"[niveau {level}] delete_file",
        "requires_elevation": elevation,
        "allowed_scopes": scopes,
    }


def _buttons(dialog: ConfirmDialog) -> dict:
    return {b.text(): b for b in dialog.findChildren(QtWidgets.QPushButton)}


def test_niveau_2_ne_propose_que_refuser_et_une_fois(app: object) -> None:
    dialog = ConfirmDialog(_payload(2, []))
    assert set(_buttons(dialog)) == {"Refuser", "Une fois"}


def test_niveau_1_propose_les_portees_annoncees_par_le_daemon(app: object) -> None:
    dialog = ConfirmDialog(_payload(1, ["this_folder", "session"]))
    assert set(_buttons(dialog)) == {
        "Refuser",
        "Une fois",
        "Pour ce dossier",
        "Pour cette session",
    }
    # Outil sans chemin (ex. pacman -S) : pas de « Pour ce dossier ».
    assert "Pour ce dossier" not in _buttons(ConfirmDialog(_payload(1, ["session"])))


def test_payload_sans_portees_ne_propose_aucun_grant(app: object) -> None:
    payload = _payload(1, [])
    del payload["allowed_scopes"]
    assert set(_buttons(ConfirmDialog(payload))) == {"Refuser", "Une fois"}


def test_entree_refuse_par_defaut(app: object) -> None:
    dialog = ConfirmDialog(_payload(2, []))
    dialog.show()
    QtWidgets.QApplication.processEvents()
    assert _buttons(dialog)["Refuser"].isDefault()
    assert not _buttons(dialog)["Une fois"].isDefault()
    QtTest.QTest.keyClick(dialog, QtCore.Qt.Key.Key_Return)
    assert dialog.response == ("deny", None)


def test_reste_au_dessus_du_popup(app: object) -> None:
    dialog = ConfirmDialog(_payload(1, ["session"]))
    assert dialog.windowFlags() & QtCore.Qt.WindowType.WindowStaysOnTopHint


def test_polkit_annonce_seulement_si_l_outil_s_eleve(app: object) -> None:
    dialog = ConfirmDialog(_payload(2, [], elevation=False))
    labels = " ".join(label.text() for label in dialog.findChildren(QtWidgets.QLabel))
    assert "privilèges élevés" not in dialog.windowTitle()
    assert "polkit" not in labels
    assert (
        "privilèges élevés"
        in ConfirmDialog(_payload(1, ["session"], True)).windowTitle()
    )


def test_chemin_long_jamais_tronque(app: object) -> None:
    # Constat du labo : un move_file aux chemins longs écrasait la colonne des
    # libellés (une lettre par ligne) et coupait les arguments à droite, alors que
    # l'utilisateur doit voir le chemin complet de ce qu'il approuve.
    long_path = "/home/myos/" + "Dossier-au-nom-tres-long/" * 6 + "vacances-plage.jpg"
    payload = _payload(1, ["this_folder", "session"])
    payload["tool"] = "move_file"
    payload["args"] = {"src": long_path, "dst": long_path.replace("Dossier", "IMG")}
    dialog = ConfirmDialog(payload)
    dialog.show()
    QtWidgets.QApplication.processEvents()
    details = dialog.findChild(RichText, "confirmText")
    document = details.document()
    assert document.idealWidth() <= details.viewport().width() + 1
    assert long_path in document.toPlainText()
    table = next(
        f for f in document.rootFrame().childFrames() if isinstance(f, QtGui.QTextTable)
    )
    for row in range(table.rows()):
        label = table.cellAt(row, 0).firstCursorPosition().block()
        assert label.layout().lineCount() == 1, label.text()
    dialog.close()


def test_accord_pour_le_controle_de_l_ordinateur(app: object) -> None:
    payload = {
        "type": "confirmation_needed",
        "id": "m1",
        "request_id": "r1",
        "tool": "computer_control",
        "args": {"demande": "écris une liste de courses"},
        "risk_level": 2,
        "summary": "Prendre le contrôle de l'ordinateur pour cette demande",
        "requires_elevation": False,
        "allowed_scopes": [],
        "control": True,
    }
    dialog = ConfirmDialog(payload)
    buttons = _buttons(dialog)
    assert set(buttons) == {"Refuser", "Autoriser pour cette demande"}
    assert buttons["Refuser"].isDefault()
    assert dialog.windowTitle() == "Contrôle de l'ordinateur"
    details = dialog.findChild(RichText, "confirmText").toPlainText()
    assert "écris une liste de courses" in details
    assert "Échap" in details


def test_accord_de_controle_jamais_rogne(app: object) -> None:
    # Vu en VM : la fenêtre, dimensionnée avant que le détail connaisse sa hauteur,
    # coupait la dernière ligne (« … pendant tout le contrôle. »). Puis, agrandie
    # au sizeHint, elle dépassait de ~60 px et la pastille du niveau gonflait.
    payload = {
        "type": "confirmation_needed",
        "id": "m1",
        "request_id": "r1",
        "tool": "computer_control",
        "args": {"demande": "Ouvre Mousepad et écris une liste de courses : pain."},
        "risk_level": 2,
        "summary": "Prendre le contrôle de l'ordinateur pour cette demande",
        "requires_elevation": False,
        "allowed_scopes": [],
        "control": True,
    }
    parent = QtWidgets.QWidget()
    dialog = ConfirmDialog(payload, parent=parent)
    dialog.show()
    QtWidgets.QApplication.processEvents()
    dialog.fit_contents()
    QtWidgets.QApplication.processEvents()
    card = dialog.findChild(QtWidgets.QFrame, "confirmDetails")
    details = dialog.findChild(RichText, "confirmText")
    assert card.height() >= card.sizeHint().height()
    assert details.viewport().height() >= details.document().size().height()
    badge = dialog.findChild(QtWidgets.QLabel, "riskBadge")
    assert dialog.height() == dialog.heightForWidth(dialog.width())
    assert badge.height() == badge.sizeHint().height()
    dialog.close()
    parent.deleteLater()


def test_cible_technique_de_l_ecran_non_affichee(app: object) -> None:
    payload = _payload(2, [])
    payload["tool"] = "type_text"
    payload["summary"] = "[niveau 2] Taper « ls » dans « Terminal » (Xfce4-terminal)"
    payload["args"] = {
        "text": "ls",
        "enter": False,
        "target": {"window_id": 48234499, "pid": 3319, "x": 406, "y": 360},
    }
    details = ConfirmDialog(payload).findChild(RichText, "confirmText").toPlainText()
    assert "Taper « ls » dans « Terminal »" in details
    assert '"text": "ls"' in details
    assert "window_id" not in details and "48234499" not in details
