"""Tests de la zone de saisie du popup (Qt hors écran)."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# pylint: disable=wrong-import-position  # import après importorskip (Qt optionnel)
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
QtTest = pytest.importorskip("PySide6.QtTest")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ui.composer import Composer

Key = QtCore.Qt.Key
Mod = QtCore.Qt.KeyboardModifier


@pytest.fixture
def composer(app: object) -> Composer:
    widget = Composer()
    widget.show()
    widget.focus_input()
    return widget


def test_entree_envoie_et_vide_la_saisie(composer: Composer) -> None:
    sent: list[str] = []
    composer.submitted.connect(sent.append)
    QtTest.QTest.keyClicks(composer.input, "  bonjour  ")
    QtTest.QTest.keyClick(composer.input, Key.Key_Return)
    assert sent == ["bonjour"]
    assert composer.text() == ""


def test_maj_entree_va_a_la_ligne(composer: Composer) -> None:
    sent: list[str] = []
    composer.submitted.connect(sent.append)
    QtTest.QTest.keyClicks(composer.input, "ligne 1")
    QtTest.QTest.keyClick(composer.input, Key.Key_Return, Mod.ShiftModifier)
    QtTest.QTest.keyClicks(composer.input, "ligne 2")
    assert sent == []
    assert composer.text() == "ligne 1\nligne 2"


def test_rien_n_est_envoye_vide_ou_pendant_une_reponse(composer: Composer) -> None:
    sent: list[str] = []
    composer.submitted.connect(sent.append)
    QtTest.QTest.keyClick(composer.input, Key.Key_Return)
    assert not composer.send_button.isEnabled()

    composer.set_busy(True)
    QtTest.QTest.keyClicks(composer.input, "suite")
    assert not composer.send_button.isEnabled()
    QtTest.QTest.keyClick(composer.input, Key.Key_Return)
    assert sent == [] and composer.text() == "suite"  # la saisie est gardée

    composer.set_busy(False)
    assert composer.send_button.isEnabled()
    composer.send_button.click()
    assert sent == ["suite"]


def test_suggestion_remplit_sans_envoyer(composer: Composer) -> None:
    sent: list[str] = []
    composer.submitted.connect(sent.append)
    composer.set_text("Baisse le volume à 30 %")
    assert composer.text() == "Baisse le volume à 30 %" and sent == []


def test_bascule_cloud(composer: Composer) -> None:
    toggled: list[bool] = []
    composer.cloud_toggled.connect(toggled.append)
    composer.cloud_toggle.click()
    assert toggled == [True]
    composer.set_cloud_checked(False)  # sans signal (annulation de la clé)
    assert toggled == [True] and not composer.cloud_toggle.isChecked()


def test_la_hauteur_suit_le_texte_jusqu_a_six_lignes(composer: Composer) -> None:
    one_line = composer.input.height()
    composer.set_text("\n".join(f"ligne {i}" for i in range(4)))
    four_lines = composer.input.height()
    composer.set_text("\n".join(f"ligne {i}" for i in range(20)))
    assert one_line < four_lines < composer.input.height()
    line = composer.input.fontMetrics().lineSpacing()
    assert composer.input.height() <= 6 * line + 8
