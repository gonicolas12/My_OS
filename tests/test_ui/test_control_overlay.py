"""Indicateur du contrôle de l'ordinateur (Qt hors écran) : bords lumineux, barre
d'arrêt, bouton ■, fondus d'apparition et de disparition."""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# pylint: disable=wrong-import-position,protected-access
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtCore = pytest.importorskip("PySide6.QtCore")
QtTest = pytest.importorskip("PySide6.QtTest")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ui.control_overlay import (
    BREATH_LOW,
    FADE_IN_MS,
    FADE_OUT_MS,
    GLOW_THICKNESS,
    ControlOverlay,
    GlowStrip,
    StopBar,
    StopButton,
    breath_value,
    strip_rects,
)

Qt = QtCore.Qt


@pytest.fixture
def overlay(app: object) -> ControlOverlay:
    widget = ControlOverlay(solid=False)
    yield widget
    widget._destroy()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def _strips(overlay: ControlOverlay) -> list[GlowStrip]:
    return [w for w in overlay.windows() if isinstance(w, GlowStrip)]


def test_quatre_bandes_par_ecran_sans_recouvrement() -> None:
    screen = QtCore.QRect(0, 0, 1920, 1080)
    rects = strip_rects(screen, GLOW_THICKNESS)
    assert len(rects) == 4
    for i, first in enumerate(rects):
        assert screen.contains(first)
        for second in rects[i + 1 :]:
            assert not first.intersects(second)
    covered = sum(r.width() * r.height() for r in rects)
    inner = (1920 - 2 * GLOW_THICKNESS) * (1080 - 2 * GLOW_THICKNESS)
    assert covered == 1920 * 1080 - inner


def test_respiration_douce() -> None:
    assert breath_value(0.0) == pytest.approx(1.0)
    assert breath_value(0.5) == pytest.approx(BREATH_LOW)
    assert breath_value(1.0) == pytest.approx(1.0)
    assert BREATH_LOW < breath_value(0.3) < 1.0


def test_allumage_lueur_barre_et_bouton(overlay: ControlOverlay) -> None:
    button = overlay.show_overlay()
    screens = QtWidgets.QApplication.screens()
    assert len(_strips(overlay)) == 4 * len(screens)
    assert overlay.showing
    assert all(w.isVisible() for w in overlay.windows())
    assert overlay.bar_rect().contains(button)  # ■ posé dans la phrase de la barre


def test_fenetres_hors_gestionnaire_sans_focus(overlay: ControlOverlay) -> None:
    overlay.show_overlay()
    for window in overlay.windows():
        flags = window.windowFlags()
        assert flags & Qt.WindowType.X11BypassWindowManagerHint
        assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        through = bool(flags & Qt.WindowType.WindowTransparentForInput)
        # Seul le bouton ■ reçoit les clics : lueur et barre laissent passer la souris.
        assert through is not isinstance(window, StopButton)


def test_apparition_en_fondu(overlay: ControlOverlay) -> None:
    overlay.show_overlay()
    bar = next(w for w in overlay.windows() if isinstance(w, StopBar))
    assert bar.opacity < 0.5 and _strips(overlay)[0].opacity < 0.5
    QtTest.QTest.qWait(FADE_IN_MS + 250)
    assert bar.opacity == pytest.approx(1.0)
    assert _strips(overlay)[0].opacity >= BREATH_LOW - 0.01  # puis respire


def test_disparition_en_fondu_puis_fenetres_fermees(overlay: ControlOverlay) -> None:
    overlay.show_overlay()
    QtTest.QTest.qWait(FADE_IN_MS + 100)
    overlay.hide_overlay()
    assert not overlay.showing
    assert overlay.windows()  # encore là pendant le fondu
    QtTest.QTest.qWait(FADE_OUT_MS // 2)
    assert 0.0 < _strips(overlay)[0].opacity < 1.0
    QtTest.QTest.qWait(FADE_OUT_MS)
    assert overlay.windows() == []


def test_clic_sur_le_bouton_stop(overlay: ControlOverlay) -> None:
    clicked: list[bool] = []
    overlay.stop_clicked.connect(lambda: clicked.append(True))
    overlay.show_overlay()
    button = next(w for w in overlay.windows() if isinstance(w, StopButton))
    QtTest.QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert clicked == [True]
    assert not overlay.showing


def test_sans_compositeur_ni_fondu_ni_transparence(app: object) -> None:
    overlay = ControlOverlay(solid=True)
    overlay.show_overlay()
    bar = next(w for w in overlay.windows() if isinstance(w, StopBar))
    assert bar.opacity == 1.0 and _strips(overlay)[0].opacity == 1.0
    overlay.hide_overlay()
    assert overlay.windows() == []


def test_action_en_cours_coupee(overlay: ControlOverlay) -> None:
    overlay.show_overlay()
    overlay.set_status("Clique · " + "très long libellé " * 40)
    bar = next(w for w in overlay.windows() if isinstance(w, StopBar))
    assert bar.status.text().endswith("…")
    assert bar.status.text().startswith("Clique")


def test_opacite_repeinte_seulement_si_elle_se_voit(app: object) -> None:
    screen = QtCore.QRect(0, 0, 400, 300)
    strip = GlowStrip(screen, strip_rects(screen, 40)[0], solid=False, ratio=1.0)
    strip.set_opacity(0.001)
    assert strip.opacity == 0.0
    strip.set_opacity(0.5)
    assert strip.opacity == 0.5
    strip.set_opacity(2.0)
    assert strip.opacity == 1.0
    strip.deleteLater()
