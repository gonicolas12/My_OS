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
QtTest = pytest.importorskip("PySide6.QtTest")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ui.confirm_dialog import ConfirmDialog


@pytest.fixture(scope="module")
def app() -> object:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


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
