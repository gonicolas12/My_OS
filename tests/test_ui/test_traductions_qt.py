"""Menus standard de Qt dans la langue de My_OS (catalogue ``qtbase`` de Qt).

Le clic droit dans un champ (Annuler, Copier, Coller…) est dessiné par Qt : en
français, son catalogue ``qtbase_fr`` est installé ; en anglais, il est retiré. Les
tests qui lisent le vrai catalogue sont ignorés si Qt n'a pas ses traductions
(``qt6-translations`` absent) ; le repli sans catalogue est toujours testé.
"""

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
from tests.test_ui.test_popup import FakeClient
from ui.popup import Popup
from ui.qt_translations import (
    CATALOG,
    apply_qt_language,
    qt_language,
    translations_dir,
)

needs_french_catalog = pytest.mark.skipif(
    not Path(translations_dir(), f"{CATALOG}_fr.qm").is_file(),
    reason="Qt installé sans ses traductions (qt6-translations)",
)


@pytest.fixture
def settings(tmp_path: Path) -> object:
    return QtCore.QSettings(
        str(tmp_path / "popup.conf"), QtCore.QSettings.Format.IniFormat
    )


def _popup(settings: object) -> Popup:
    return Popup(Config(), session_type="x11", client=FakeClient(), settings=settings)


def _close(popup: Popup) -> None:
    popup.hide()
    popup.deleteLater()


def _menu(widget: object) -> list[str]:
    """Libellés du menu du clic droit, sans raccourci clavier ni accélérateur."""
    menu = widget.createStandardContextMenu()
    try:
        return [
            action.text().split("\t")[0].replace("&", "")
            for action in menu.actions()
            if action.text()
        ]
    finally:
        menu.deleteLater()


@needs_french_catalog
def test_clic_droit_d_un_champ_en_francais_puis_en_anglais(app: object) -> None:
    field = QtWidgets.QLineEdit()
    assert apply_qt_language("fr")
    assert qt_language() == "fr"
    labels = _menu(field)
    assert {"Annuler", "Copier", "Coller", "Tout sélectionner"} <= set(labels)

    assert not apply_qt_language("en")
    assert qt_language() is None
    assert {"Undo", "Copy", "Paste", "Select All"} <= set(_menu(field))


@needs_french_catalog
def test_meme_langue_redemandee_sans_rien_recharger(app: object) -> None:
    assert apply_qt_language("fr")
    assert apply_qt_language("fr")
    assert qt_language() == "fr"
    assert "Copier" in _menu(QtWidgets.QLineEdit())


def test_catalogue_introuvable_menus_en_anglais_sans_erreur(
    app: object, tmp_path: Path
) -> None:
    assert not apply_qt_language("fr", directory=str(tmp_path))
    assert qt_language() is None
    assert "Copy" in _menu(QtWidgets.QLineEdit())


@needs_french_catalog
def test_le_popup_suit_sa_langue_dans_les_menus_de_qt(
    app: object, settings: object
) -> None:
    popup = _popup(settings)  # système en français (cf. tests/conftest.py)
    try:
        assert qt_language() == "fr"
        assert "Copier" in _menu(popup.composer.input)
        popup.set_language("en")
        assert qt_language() is None
        assert "Copy" in _menu(popup.composer.input)
        popup.set_language("fr")
        assert qt_language() == "fr"
        assert "Coller" in _menu(popup.composer.input)
    finally:
        _close(popup)


def test_popup_en_anglais_n_installe_aucun_catalogue(
    app: object, settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply_qt_language("fr")  # reste d'un test précédent, retiré par le popup
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    popup = _popup(settings)
    try:
        assert qt_language() is None
        assert "Copy" in _menu(popup.composer.input)
    finally:
        _close(popup)
