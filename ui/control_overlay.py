"""Indicateur du contrôle de l'ordinateur : bords lumineux et barre d'arrêt.

Pendant que My_OS pilote l'ordinateur, les bords de chaque écran brillent en orange
clair (la couleur de My_OS) et une barre, en bas, dit comment reprendre la main :
« Cliquez ici ■ ou appuyez sur Échap pour arrêter ». La lueur apparaît et disparaît
en fondu, et « respire » doucement tant que le contrôle dure.

Fenêtres X11 hors gestionnaire de fenêtres (``X11BypassWindowManagerHint``),
toujours au-dessus, qui ne prennent jamais le focus (le clavier reste à
l'application pilotée) :

* la lueur et le texte de la barre laissent passer la souris
  (``WindowTransparentForInput``) : ils ne gênent ni l'utilisateur ni les clics du
  modèle ;
* seul le bouton ■ reçoit les clics. Le popup envoie son rectangle au daemon
  (``control_ready``), qui interdit au modèle de cliquer dessus.

Sans compositeur (pas de transparence), la lueur devient un liseré orange franc et
les fondus sont sautés : l'indicateur reste visible, c'est l'essentiel.

**Fondus.** L'opacité de fenêtre (``setWindowOpacity``, propriété
``_NET_WM_WINDOW_OPACITY``) ne convient pas : xfwm4 n'applique pas ses changements
rapides (mesuré en VM : ni fondu ni respiration à l'écran, la lueur apparaissait d'un
coup). Les fenêtres restent donc opaques pour le compositeur, et c'est l'alpha de
leurs pixels qui varie : chaque étape repeint la lueur et le bouton avec une
opacité, la barre via un ``QGraphicsOpacityEffect``. Un dommage d'image, que tout
compositeur affiche aussitôt.
"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QScreen,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.i18n import N_, tr
from ui.icons import logo_pixmap
from ui.styles import FS_SM, FS_XS, PALETTE

# Lueur : orange clair de My_OS. Un halo large et doux, un cœur plus vif près du
# bord, et un liseré lumineux tout au bord.
GLOW_COLOR = "#ffa031"
GLOW_CORE_COLOR = "#ffb24d"
GLOW_EDGE_COLOR = "#ffd59a"
GLOW_THICKNESS = 88
GLOW_PEAK = 0.42
GLOW_CORE_THICKNESS = 24
GLOW_CORE_PEAK = 0.7
GLOW_RADIUS = 18.0
# Sans compositeur : liseré opaque.
SOLID_THICKNESS = 6
# Durées (ms) : apparition, disparition, cycle de respiration.
FADE_IN_MS = 700
FADE_OUT_MS = 600
BREATH_MS = 2600
BREATH_LOW = 0.72
# La respiration, lente, se repeint à 15 images/s : le processeur reste au modèle.
BREATH_FRAME_MS = 66
# Écart d'opacité en dessous duquel une bande n'est pas repeinte.
_OPACITY_STEP = 0.004
# Barre d'arrêt : distance au bas de l'écran (au-dessus d'un dock de ~48 px).
BAR_BOTTOM_GAP = 64
_BAR_MARGIN = 18  # halo autour de la barre (dans sa fenêtre transparente)
STOP_BUTTON_SIZE = 30
# Les fenêtres sont remontées régulièrement (une fenêtre gérée peut passer devant),
# sauf pendant une confirmation (cf. ControlOverlay.set_raising).
_RAISE_MS = 700

# Textes de la barre, dans la langue de l'interface à l'allumage de l'indicateur.
TITLE = N_("My_OS contrôle l'ordinateur")
IDLE_STATUS = N_("Le modèle observe et agit pour vous…")

_OVERLAY_FLAGS = (
    Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.WindowStaysOnTopHint
    | Qt.WindowType.X11BypassWindowManagerHint
    | Qt.WindowType.WindowDoesNotAcceptFocus
    | Qt.WindowType.Tool
)


def compositing_active() -> bool:
    """``True`` si un compositeur gère la transparence (sélection ``_NET_WM_CM_S0``).

    Sans moyen de le savoir (pas de python-xlib, pas de X11), on suppose que oui :
    c'est le cas de l'ISO (xfwm4) et de tous les bureaux courants.
    """
    try:
        from Xlib import display  # pylint: disable=import-outside-toplevel

        disp = display.Display()
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return True
    try:
        screen = disp.get_default_screen()
        owner = disp.get_selection_owner(disp.intern_atom(f"_NET_WM_CM_S{screen}"))
        return owner != 0
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return True
    finally:
        disp.close()


def _setup_overlay_window(widget: QWidget, *, input_transparent: bool) -> None:
    flags = _OVERLAY_FLAGS
    if input_transparent:
        flags |= Qt.WindowType.WindowTransparentForInput
    widget.setWindowFlags(flags)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)


def paint_glow(painter: QPainter, width: int, height: int, *, solid: bool) -> None:
    """Dessine la lueur de tout un écran ``width`` × ``height`` (repère de l'écran).

    Anneaux concentriques à coins arrondis, du bord vers l'intérieur, de moins en
    moins opaques : une lueur continue, coins compris, sans jointure entre les
    bandes (chaque bande dessine sa part de ce même champ).
    """
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if solid:
        pen = QPen(QColor(GLOW_COLOR), SOLID_THICKNESS * 2)
        painter.setPen(pen)
        painter.drawRect(QRectF(0, 0, width, height))
        return
    for thickness, peak, power, name in (
        (GLOW_THICKNESS, GLOW_PEAK, 2.2, GLOW_COLOR),  # halo
        (GLOW_CORE_THICKNESS, GLOW_CORE_PEAK, 1.5, GLOW_CORE_COLOR),  # cœur
    ):
        color = QColor(name)
        for step in range(thickness):
            color.setAlphaF(peak * (1.0 - step / thickness) ** power)
            painter.setPen(QPen(color, 1.6))
            inset = step + 0.5
            # Anneaux de plus en plus arrondis vers l'intérieur : la lueur des deux
            # bords se fond en quart de cercle dans les coins (pas de couture).
            radius = GLOW_RADIUS + step * 0.7
            painter.drawRoundedRect(
                QRectF(inset, inset, width - 2 * inset, height - 2 * inset),
                radius,
                radius,
            )
    painter.setPen(QPen(QColor(GLOW_EDGE_COLOR), 2.5))
    painter.drawRoundedRect(
        QRectF(1, 1, width - 2, height - 2), GLOW_RADIUS, GLOW_RADIUS
    )


def breath_value(phase: float) -> float:
    """Intensité de la respiration à ``phase`` (0 à 1) d'un cycle : 1 au début et à
    la fin, :data:`BREATH_LOW` au milieu, en cosinus (sans à-coup)."""
    return BREATH_LOW + (1.0 - BREATH_LOW) * (0.5 + 0.5 * math.cos(2 * math.pi * phase))


def strip_rects(screen: QRect, thickness: int) -> list[QRect]:
    """Quatre bandes d'un écran (haut, bas, gauche, droite), sans recouvrement."""
    x, y, w, h = screen.x(), screen.y(), screen.width(), screen.height()
    t = min(thickness, w // 2, h // 2)
    return [
        QRect(x, y, w, t),
        QRect(x, y + h - t, w, t),
        QRect(x, y + t, t, h - 2 * t),
        QRect(x + w - t, y + t, t, h - 2 * t),
    ]


class _FadingWidget(QWidget):
    """Fenêtre de l'indicateur à opacité peinte (cf. en-tête : « Fondus »)."""

    def __init__(self, *, input_transparent: bool) -> None:
        super().__init__(None)
        _setup_overlay_window(self, input_transparent=input_transparent)
        self.opacity = 0.0

    def set_opacity(self, value: float) -> None:
        """Nouvelle opacité (0 à 1) ; repeint si l'écart se voit."""
        value = min(max(value, 0.0), 1.0)
        changed = abs(value - self.opacity) >= _OPACITY_STEP
        if changed or (value != self.opacity and value in (0.0, 1.0)):
            self.opacity = value
            self.update()


class GlowStrip(_FadingWidget):
    """Une bande de lueur le long d'un bord d'écran (image calculée une fois)."""

    def __init__(
        self, screen: QRect, strip: QRect, *, solid: bool, ratio: float
    ) -> None:
        super().__init__(input_transparent=True)
        self.setGeometry(strip)
        pixmap = QPixmap(
            QSize(round(strip.width() * ratio), round(strip.height() * ratio))
        )
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.translate(screen.x() - strip.x(), screen.y() - strip.y())
        paint_glow(painter, screen.width(), screen.height(), solid=solid)
        painter.end()
        self._pixmap = pixmap

    def paintEvent(self, event: QPaintEvent) -> None:
        """Dessine la part de lueur de cette bande, à l'opacité courante."""
        _ = event
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        if self.opacity > 0.0:
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            painter.setOpacity(self.opacity)
            painter.drawPixmap(0, 0, self._pixmap)


class StopButton(_FadingWidget):
    """Le bouton ■ : seule partie de l'indicateur qui reçoit les clics."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__(input_transparent=False)
        self.setFixedSize(STOP_BUTTON_SIZE, STOP_BUTTON_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("Arrêter le contrôle de l'ordinateur (Échap)"))
        self._hover = False
        self._pressed = False

    def enterEvent(self, event: QEnterEvent) -> None:
        """Survol : bouton plus clair."""
        super().enterEvent(event)
        self._hover = True
        self.update()

    def leaveEvent(self, event: object) -> None:
        """Fin du survol."""
        super().leaveEvent(event)  # type: ignore[arg-type]
        self._hover = self._pressed = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Appui (bouton gauche)."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Relâché sur le bouton : arrêt demandé."""
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self.clicked.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Carré orange arrondi, carré d'arrêt sombre au centre."""
        _ = event
        if self.opacity <= 0.0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self.opacity)
        fill = PALETTE.accent
        if self._pressed:
            fill = PALETTE.accent_pressed
        elif self._hover:
            fill = PALETTE.accent_hover
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(QRectF(self.rect()), 8, 8)
        side = self.width() * 0.36
        center = QPointF(self.width() / 2, self.height() / 2)
        painter.setBrush(QColor(PALETTE.on_accent))
        painter.drawRoundedRect(
            QRectF(center.x() - side / 2, center.y() - side / 2, side, side), 2, 2
        )


class _BarContent(QWidget):
    """Contenu de la barre d'arrêt (pilule, halo, textes) : c'est lui qui s'estompe."""

    def __init__(self, *, solid: bool) -> None:
        super().__init__()
        self._solid = solid

    def paintEvent(self, event: QPaintEvent) -> None:
        """Pilule sombre bordée d'orange, avec un halo orange autour."""
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._solid:
            painter.fillRect(self.rect(), QColor(PALETTE.bg))
            painter.setPen(QPen(QColor(PALETTE.accent), 2))
            painter.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
            return
        pill = QRectF(self.rect()).adjusted(
            _BAR_MARGIN, _BAR_MARGIN, -_BAR_MARGIN, -_BAR_MARGIN
        )
        radius = pill.height() / 2
        halo = QColor(GLOW_COLOR)
        for step in range(_BAR_MARGIN, 0, -1):
            halo.setAlphaF(0.16 * (1 - step / _BAR_MARGIN) ** 2)
            painter.setPen(QPen(halo, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            grown = pill.adjusted(-step, -step, step, step)
            painter.drawRoundedRect(grown, radius + step, radius + step)
        path = QPainterPath()
        path.addRoundedRect(pill, radius, radius)
        background = QColor(PALETTE.bg)
        background.setAlphaF(0.95)
        painter.fillPath(path, background)
        border = QColor(PALETTE.accent)
        border.setAlphaF(0.75)
        painter.setPen(QPen(border, 1.2))
        painter.drawPath(path)


class StopBar(QWidget):
    """Barre en bas de l'écran : titre, action en cours, consigne d'arrêt.

    Ne reçoit aucun clic (le bouton ■, fenêtre à part, est posé sur son
    emplacement : :meth:`button_slot`).
    """

    def __init__(self, *, solid: bool) -> None:  # pylint: disable=too-many-statements
        super().__init__(None)
        _setup_overlay_window(self, input_transparent=True)
        self._solid = solid
        wrapper = QHBoxLayout(self)
        wrapper.setContentsMargins(0, 0, 0, 0)
        self._content = _BarContent(solid=solid)
        wrapper.addWidget(self._content)
        outer = QHBoxLayout(self._content)
        margin = 0 if solid else _BAR_MARGIN
        outer.setContentsMargins(margin + 14, margin + 9, margin + 16, margin + 9)
        outer.setSpacing(10)

        logo = QLabel()
        logo.setPixmap(logo_pixmap(26))
        outer.addWidget(logo, 0, Qt.AlignmentFlag.AlignVCenter)

        texts = QVBoxLayout()
        texts.setSpacing(1)
        self.title = QLabel(tr(TITLE))
        self.title.setStyleSheet(
            f"color:{PALETTE.text}; font-weight:700; font-size:{FS_SM}px;"
        )
        self.status = QLabel(tr(IDLE_STATUS))
        self.status.setStyleSheet(f"color:{PALETTE.text_2}; font-size:{FS_XS}px;")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setMinimumWidth(230)
        self.status.setMaximumWidth(300)
        texts.addWidget(self.title)
        texts.addWidget(self.status)
        outer.addLayout(texts)

        divider = QWidget()
        divider.setFixedSize(1, 30)
        divider.setStyleSheet(f"background:{PALETTE.border_strong};")
        outer.addWidget(divider, 0, Qt.AlignmentFlag.AlignVCenter)

        text_style = f"color:{PALETTE.text}; font-size:{FS_SM}px;"
        before = QLabel(tr("Cliquez ici"))
        before.setStyleSheet(text_style)
        outer.addWidget(before, 0, Qt.AlignmentFlag.AlignVCenter)
        self._slot = QWidget()
        self._slot.setFixedSize(STOP_BUTTON_SIZE, STOP_BUTTON_SIZE)
        outer.addWidget(self._slot, 0, Qt.AlignmentFlag.AlignVCenter)
        middle = QLabel(tr("ou appuyez sur"))
        middle.setStyleSheet(text_style)
        outer.addWidget(middle, 0, Qt.AlignmentFlag.AlignVCenter)
        key = QLabel(tr("Échap"))
        key.setStyleSheet(
            f"color:{PALETTE.text}; background:{PALETTE.surface_hover};"
            f"border:1px solid {PALETTE.border_strong}; border-bottom-width:2px;"
            f"border-radius:5px; padding:1px 7px; font-size:{FS_XS}px; font-weight:700;"
        )
        outer.addWidget(key, 0, Qt.AlignmentFlag.AlignVCenter)
        after = QLabel(tr("pour arrêter"))
        after.setStyleSheet(text_style)
        outer.addWidget(after, 0, Qt.AlignmentFlag.AlignVCenter)
        self._effect = QGraphicsOpacityEffect(self._content)
        self._effect.setOpacity(0.0)
        self._content.setGraphicsEffect(self._effect)
        self.adjustSize()

    @property
    def opacity(self) -> float:
        """Opacité courante de la barre (0 à 1)."""
        return float(self._effect.opacity())

    def set_opacity(self, value: float) -> None:
        """Nouvelle opacité de la barre et de ses textes (0 à 1)."""
        self._effect.setOpacity(min(max(value, 0.0), 1.0))

    def button_slot(self) -> QRect:
        """Emplacement du bouton ■, en coordonnées d'écran (barre affichée)."""
        offset = self._slot.mapTo(self, QPoint(0, 0))
        return QRect(self.geometry().topLeft() + offset, self._slot.size())


class ControlOverlay(QObject):  # pylint: disable=too-many-instance-attributes
    """Lueur des bords + barre d'arrêt, avec leurs fondus."""

    stop_clicked = Signal()

    def __init__(
        self, parent: QObject | None = None, *, solid: bool | None = None
    ) -> None:
        super().__init__(parent)
        self._solid = (not compositing_active()) if solid is None else solid
        self._strips: list[GlowStrip] = []
        self._bar: StopBar | None = None
        self._button: StopButton | None = None
        self._level = 0.0
        self._breath_value = 1.0
        self._showing = False
        self._fade = QVariantAnimation(self)
        self._fade.valueChanged.connect(self._apply_level)
        self._fade.finished.connect(self._fade_finished)
        # Respiration : minuterie à 15 images/s (cf. BREATH_FRAME_MS), phase mesurée.
        self._breath_timer = QTimer(self)
        self._breath_timer.setInterval(BREATH_FRAME_MS)
        self._breath_timer.timeout.connect(self._breathe)
        self._breath_clock = QElapsedTimer()
        self._raise_timer = QTimer(self)
        self._raise_timer.setInterval(_RAISE_MS)
        self._raise_timer.timeout.connect(self._raise_all)

    # ---- état ------------------------------------------------------------------
    @property
    def showing(self) -> bool:
        """``True`` tant que l'indicateur est affiché (ou apparaît)."""
        return self._showing

    @property
    def solid(self) -> bool:
        """``True`` sans compositeur (liseré opaque, sans fondu)."""
        return self._solid

    def windows(self) -> list[QWidget]:
        """Toutes les fenêtres de l'indicateur (tests, diagnostic)."""
        extra = [w for w in (self._bar, self._button) if w is not None]
        return [*self._strips, *extra]

    def bar_rect(self) -> QRect | None:
        """Rectangle de la barre d'arrêt à l'écran, halo compris (``None`` : éteinte)."""
        return self._bar.geometry() if self._bar is not None else None

    # ---- affichage -------------------------------------------------------------
    def show_overlay(self, screen: QScreen | None = None) -> QRect:
        """Allume l'indicateur (fondu) ; renvoie le rectangle du bouton ■ à l'écran."""
        if not self._showing:
            self._build(screen or QGuiApplication.primaryScreen())
        self._showing = True
        self._breath_timer.stop()
        assert self._bar is not None and self._button is not None
        for window in [*self._strips, self._bar]:
            window.show()
            window.raise_()
        # Le bouton ■ se pose sur son emplacement une fois la barre disposée.
        self._button.move(self._bar.button_slot().topLeft())
        self._button.show()
        self._button.raise_()
        self._raise_timer.start()
        if self._solid:
            self._apply_level(1.0)
        else:
            self._animate(1.0, FADE_IN_MS, QEasingCurve.Type.OutCubic)
        assert self._button is not None
        return self._button.geometry()

    def hide_overlay(self) -> None:
        """Éteint l'indicateur (fondu), puis ferme ses fenêtres."""
        if not self._showing:
            return
        self._showing = False
        self._breath_timer.stop()
        if self._solid:
            self._destroy()
            return
        self._animate(0.0, FADE_OUT_MS, QEasingCurve.Type.InOutSine)

    def set_raising(self, enabled: bool) -> None:
        """Suspend (``False``) ou reprend la remise au premier plan périodique.

        Le popup la suspend le temps d'une confirmation : aucune action n'a lieu
        pendant la question, et le dialogue doit rester entièrement visible, même
        sur un petit écran où la barre d'arrêt le chevaucherait.
        """
        if not enabled:
            self._raise_timer.stop()
        elif self._showing:
            self._raise_all()
            self._raise_timer.start()

    def set_status(self, text: str) -> None:
        """Action en cours, sous le titre de la barre (texte brut, coupé)."""
        if self._bar is None:
            return
        line = " ".join(text.split())
        metrics = self._bar.status.fontMetrics()
        self._bar.status.setText(
            metrics.elidedText(
                line, Qt.TextElideMode.ElideRight, self._bar.status.maximumWidth()
            )
        )

    # ---- interne ---------------------------------------------------------------
    def _build(self, screen: QScreen | None) -> None:
        self._destroy()
        thickness = SOLID_THICKNESS if self._solid else GLOW_THICKNESS
        for each in QGuiApplication.screens():
            geometry = each.geometry()
            ratio = max(1.0, each.devicePixelRatio())
            for strip in strip_rects(geometry, thickness):
                self._strips.append(
                    GlowStrip(geometry, strip, solid=self._solid, ratio=ratio)
                )
        target = (screen or QGuiApplication.primaryScreen()).geometry()
        stop_bar = StopBar(solid=self._solid)
        size = stop_bar.sizeHint().expandedTo(stop_bar.minimumSizeHint())
        stop_bar.resize(size)
        x = target.x() + (target.width() - size.width()) // 2
        y = target.y() + target.height() - size.height() - BAR_BOTTOM_GAP
        stop_bar.move(x, y)
        button = StopButton()
        button.clicked.connect(self._on_stop)
        self._bar, self._button = stop_bar, button
        self._apply_level(0.0)

    def _on_stop(self) -> None:
        self.stop_clicked.emit()
        self.hide_overlay()

    def _animate(self, target: float, duration: int, curve: QEasingCurve.Type) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._level)
        self._fade.setEndValue(target)
        self._fade.setDuration(duration)
        self._fade.setEasingCurve(curve)
        self._fade.start()

    def _fade_finished(self) -> None:
        if self._showing:
            # La lueur respire tant que le contrôle dure.
            self._breath_clock.start()
            self._breath_timer.start()
        else:
            self._destroy()

    def _breathe(self) -> None:
        phase = (self._breath_clock.elapsed() % BREATH_MS) / BREATH_MS
        self._apply_glow(breath_value(phase))

    def _apply_level(self, value: object) -> None:
        """Niveau du fondu : barre et bouton, et lueur (pondérée par la respiration)."""
        self._level = float(value)  # type: ignore[arg-type]
        for window in (self._bar, self._button):
            if window is not None:
                window.set_opacity(self._level)
        self._apply_glow(self._breath_value)

    def _apply_glow(self, breath: object) -> None:
        """Respiration : la lueur garde son intensité courante quand le fondu de
        sortie commence (il part de ce qui est affiché, sans à-coup)."""
        self._breath_value = float(breath)  # type: ignore[arg-type]
        opacity = self._level * self._breath_value
        for strip in self._strips:
            strip.set_opacity(opacity)

    def _raise_all(self) -> None:
        for window in self.windows():
            if window.isVisible():
                window.raise_()

    def _destroy(self) -> None:
        self._raise_timer.stop()
        self._fade.stop()
        self._breath_timer.stop()
        for window in self.windows():
            window.hide()
            window.deleteLater()
        self._strips = []
        self._bar = self._button = None
        self._level = 0.0
        self._breath_value = 1.0
