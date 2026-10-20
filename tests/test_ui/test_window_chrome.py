"""Tests du cadre de la fenêtre sans bordure (barre de titre, poignées)."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# pylint: disable=wrong-import-position  # import après importorskip (Qt optionnel)
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ui.window_chrome import ResizeGrips, TitleBar, hotkey_label

Edge = QtCore.Qt.Edge


@pytest.mark.parametrize(
    ("hotkey", "label"),
    [
        ("<ctrl>+<alt>+<space>", "Ctrl+Alt+Espace"),
        ("<ctrl>+<alt>+m", "Ctrl+Alt+M"),
        ("<cmd>+<shift>+<enter>", "Super+Maj+Entrée"),
    ],
)
def test_libelle_du_raccourci(hotkey: str, label: str) -> None:
    assert hotkey_label(hotkey) == label


def test_rappel_echap_et_boutons(app: object) -> None:
    bar = TitleBar()
    assert bar.esc_key.text() == "Échap"
    assert bar.esc_text.text() == "pour fermer"
    assert "Échap" in bar.close_button.toolTip()
    assert "Ctrl+L" in bar.new_chat_button.toolTip()


def test_barre_compacte_quand_la_fenetre_est_etroite(app: object) -> None:
    bar = TitleBar()
    bar.resize(900, 44)
    bar.show()
    QtWidgets.QApplication.processEvents()
    assert bar.mode_pill.isVisible() and bar.esc_text.isVisible()
    bar.resize(420, 44)
    QtWidgets.QApplication.processEvents()
    assert not bar.mode_pill.isVisible() and not bar.esc_text.isVisible()
    assert bar.esc_key.isVisible()  # la touche Échap reste toujours rappelée
    bar.close()


def test_etats_de_la_fenetre(app: object) -> None:
    bar = TitleBar()
    bar.set_window_state(maximized=False, fullscreen=True)
    assert bar.fullscreen_button.toolTip().startswith("Quitter le plein écran")
    assert not bar.maximize_button.isEnabled()
    bar.set_window_state(maximized=True, fullscreen=False)
    assert bar.maximize_button.toolTip() == "Restaurer"
    assert bar.maximize_button.isEnabled()


def test_pastille_de_mode(app: object) -> None:
    bar = TitleBar()
    bar.set_mode("offline", "service injoignable")
    assert "Hors ligne" in bar.mode_pill.text()
    bar.set_mode("cloud")
    assert bar.mode_pill.property("mode") == "cloud"


def test_double_clic_agrandit(app: object) -> None:
    bar = TitleBar()
    asked: list[bool] = []
    bar.maximize_clicked.connect(lambda: asked.append(True))
    from PySide6.QtTest import QTest  # pylint: disable=import-outside-toplevel

    QTest.mouseDClick(bar, QtCore.Qt.MouseButton.LeftButton)
    assert asked == [True]


def test_huit_poignees_sur_les_bords(app: object) -> None:
    window = QtWidgets.QWidget()
    window.resize(500, 400)
    grips = ResizeGrips(window, thickness=6, corner=14)
    grips.place()
    edges = {g.edges: g.geometry() for g in grips.grips}
    assert len(edges) == 8
    assert edges[Edge.LeftEdge] == QtCore.QRect(0, 14, 6, 372)
    assert edges[Edge.BottomEdge] == QtCore.QRect(14, 394, 472, 6)
    assert edges[Edge.RightEdge | Edge.BottomEdge] == QtCore.QRect(486, 386, 14, 14)

    grips.set_active(False)  # fenêtre agrandie / plein écran
    assert all(g.isHidden() for g in grips.grips)


def test_redimensionnement_manuel_respecte_la_taille_minimale(app: object) -> None:
    window = QtWidgets.QWidget()
    window.setMinimumSize(300, 200)
    window.setGeometry(100, 100, 500, 400)
    grips = ResizeGrips(window)
    grips.place()
    grip = next(g for g in grips.grips if g.edges == Edge.LeftEdge)
    # Repli manuel (sans gestionnaire de fenêtres) : on simule le début du glisser.
    grip._start = (QtCore.QPoint(100, 300), window.geometry())  # pylint: disable=protected-access
    event = _move_event(QtCore.QPointF(700, 300))
    grip.mouseMoveEvent(event)
    assert window.width() == 300  # bloqué à la largeur minimale
    assert window.geometry().right() == 599  # le bord droit ne bouge pas


def _move_event(global_pos: object) -> object:
    from PySide6.QtGui import QMouseEvent  # pylint: disable=import-outside-toplevel

    return QMouseEvent(
        QtCore.QEvent.Type.MouseMove,
        QtCore.QPointF(0, 0),
        global_pos,
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
