"""Icônes du popup, dessinées au ``QPainter`` (trait fin, style Lucide).

Pas de fichier image ni de module QtSvg (absent de l'ISO) : chaque icône est un
tracé vectoriel dans une grille 24 × 24, rendu à la taille et à la densité de
pixels demandées. Le logo de My_OS (carré orange, invite de terminal « >_ »)
sert à la barre de titre, aux réponses et à l'écran d'accueil.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from ui.styles import PALETTE

Painter = Callable[[QPainter], None]


def _path(*points: tuple[float, float]) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    return path


def _lines(painter: QPainter, *paths: tuple[tuple[float, float], ...]) -> None:
    for points in paths:
        painter.drawPath(_path(*points))


def _close(p: QPainter) -> None:
    _lines(p, ((6, 6), (18, 18)), ((18, 6), (6, 18)))


def _maximize(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(4.5, 4.5, 15, 15), 2.5, 2.5)


def _restore(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(3.5, 8.5, 12, 12), 2, 2)
    _lines(p, ((8.5, 8.5), (8.5, 5.5), (20.5, 5.5), (20.5, 15.5), (15.5, 15.5)))


def _fullscreen(p: QPainter) -> None:
    _lines(
        p,
        ((3, 8), (3, 3), (8, 3)),
        ((16, 3), (21, 3), (21, 8)),
        ((21, 16), (21, 21), (16, 21)),
        ((8, 21), (3, 21), (3, 16)),
    )


def _exit_fullscreen(p: QPainter) -> None:
    _lines(
        p,
        ((8, 3), (8, 8), (3, 8)),
        ((21, 8), (16, 8), (16, 3)),
        ((16, 21), (16, 16), (21, 16)),
        ((3, 16), (8, 16), (8, 21)),
    )


def _send(p: QPainter) -> None:
    _lines(p, ((12, 19), (12, 5)), ((5.5, 11.5), (12, 5), (18.5, 11.5)))


def _new_chat(p: QPainter) -> None:
    _lines(p, ((11, 4), (5, 4), (4, 5), (4, 19), (5, 20), (19, 20), (20, 19), (20, 13)))
    _lines(p, ((18.5, 3.5), (20.5, 5.5), (12, 14), (9, 15), (10, 12), (18.5, 3.5)))


def _key(p: QPainter) -> None:
    p.drawEllipse(QPointF(7.5, 16.5), 4, 4)
    _lines(p, ((10.5, 13.5), (20, 4)), ((16.5, 7.5), (19, 10)), ((14, 10), (16, 12)))


def _copy(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(9, 9, 12, 12), 2, 2)
    _lines(p, ((5, 15), (4, 15), (3, 14), (3, 4), (4, 3), (14, 3), (15, 4), (15, 5)))


def _check(p: QPainter) -> None:
    _lines(p, ((5, 12.5), (10, 17.5), (19.5, 7)))


def _arrow_down(p: QPainter) -> None:
    _lines(p, ((12, 5), (12, 19)), ((5.5, 12.5), (12, 19), (18.5, 12.5)))


def _terminal(p: QPainter) -> None:
    _lines(p, ((5, 7), (10, 12), (5, 17)), ((12.5, 17), (19, 17)))


def _ban(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    _lines(p, ((6, 6), (18, 18)))


def _alert(p: QPainter) -> None:
    p.drawEllipse(QPointF(12, 12), 8.5, 8.5)
    _lines(p, ((12, 7.5), (12, 12.5)), ((12, 16), (12, 16.2)))


_ICONS: dict[str, Painter] = {
    "close": _close,
    "maximize": _maximize,
    "restore": _restore,
    "fullscreen": _fullscreen,
    "exit_fullscreen": _exit_fullscreen,
    "send": _send,
    "new_chat": _new_chat,
    "key": _key,
    "copy": _copy,
    "check": _check,
    "arrow_down": _arrow_down,
    "terminal": _terminal,
    "ban": _ban,
    "alert": _alert,
}

ICON_NAMES = frozenset(_ICONS)


def _device_ratio() -> float:
    app = QGuiApplication.instance()
    screen = QGuiApplication.primaryScreen() if app is not None else None
    return max(1.0, screen.devicePixelRatio() if screen is not None else 1.0)


def _canvas(size: int) -> tuple[QPixmap, QPainter]:
    ratio = _device_ratio()
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def icon_pixmap(name: str, color: str, size: int = 16, stroke: float = 2.0) -> QPixmap:
    """Dessine l'icône ``name`` (cf. :data:`ICON_NAMES`) en ``color``, ``size`` px."""
    pixmap, painter = _canvas(size)
    painter.scale(size / 24, size / 24)
    pen = QPen(QColor(color), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _ICONS[name](painter)
    painter.end()
    return pixmap


def make_icon(
    name: str, color: str = PALETTE.text_2, size: int = 16, hover: str | None = None
) -> QIcon:
    """Icône Qt ; ``hover`` (facultatif) colore l'état actif (survol, clic)."""
    icon = QIcon(icon_pixmap(name, color, size))
    if hover is not None:
        icon.addPixmap(icon_pixmap(name, hover, size), QIcon.Mode.Active)
    icon.addPixmap(icon_pixmap(name, PALETTE.text_3, size), QIcon.Mode.Disabled)
    return icon


def logo_pixmap(size: int) -> QPixmap:
    """Logo de My_OS : carré orange arrondi et invite de terminal « >_ » noire."""
    pixmap, painter = _canvas(size)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(PALETTE.accent))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.26, size * 0.26)
    pen = QPen(QColor(PALETTE.on_accent), max(1.2, size * 0.11))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    s = size
    painter.drawPath(
        _path((s * 0.26, s * 0.32), (s * 0.45, s * 0.5), (s * 0.26, s * 0.68))
    )
    painter.drawPath(_path((s * 0.53, s * 0.69), (s * 0.75, s * 0.69)))
    painter.end()
    return pixmap


def logo_icon() -> QIcon:
    """Icône de fenêtre (barre des tâches, dialogues) aux couleurs de My_OS."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        icon.addPixmap(logo_pixmap(size))
    return icon
