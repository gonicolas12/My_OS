"""Cadre d'une fenêtre sans bordure système : barre de titre et redimensionnement.

Le popup est *frameless* (look sombre uniforme, aucune décoration du gestionnaire de
fenêtres). Ce module lui rend ce que le cadre système apportait :

* :class:`TitleBar` — logo, titre, mode (local / cloud / hors ligne), rappel
  « Échap pour fermer », menu de la langue (français, anglais), boutons (nouvelle
  conversation, plein écran, agrandir, fermer). Glisser déplace la fenêtre,
  double-cliquer l'agrandit.
* :class:`ResizeGrips` — huit poignées invisibles (bords et coins) pour
  redimensionner à la souris.

Déplacement et redimensionnement passent d'abord par le gestionnaire de fenêtres
(``QWindow.startSystemMove`` / ``startSystemResize`` : _NET_WM_MOVERESIZE sous X11,
xdg-shell sous Wayland), qui gère aimantation et limites d'écran ; à défaut, la
fenêtre est déplacée ou redimensionnée à la main.

Les textes suivent la langue courante (:mod:`core.i18n`) ; :meth:`TitleBar.retranslate`
les remet à jour quand l'utilisateur en change.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QWidget,
)

from core.i18n import N_, SUPPORTED_LANGUAGES, current_language, language_name, tr
from ui.icons import logo_pixmap, make_icon
from ui.styles import PALETTE

# Noms des touches du raccourci, traduits à l'affichage (« Espace », « Space »).
_KEY_NAMES = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "alt_gr": "AltGr",
    "shift": N_("Maj"),
    "cmd": "Super",
    "super": "Super",
    "space": N_("Espace"),
    "enter": N_("Entrée"),
    "esc": N_("Échap"),
    "tab": "Tab",
}


def hotkey_label(hotkey: str) -> str:
    """Raccourci pynput lisible : ``<ctrl>+<alt>+<space>`` → ``Ctrl+Alt+Espace``."""
    keys = []
    for part in hotkey.split("+"):
        key = part.strip().strip("<>").lower()
        if key:
            name = _KEY_NAMES.get(key)
            keys.append(tr(name) if name else (key.upper() if len(key) == 1 else key))
    return "+".join(keys)


def _title_button(name: str, *, object_name: str = "titleButton") -> QToolButton:
    button = QToolButton()
    button.setObjectName(object_name)
    hover = "#ffffff" if object_name == "closeButton" else PALETTE.text
    button.setIcon(make_icon(name, PALETTE.text_2, 16, hover=hover))
    button.setIconSize(QSize(16, 16))
    button.setFixedSize(30, 28)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


class TitleBar(QWidget):  # pylint: disable=too-many-instance-attributes
    """Barre de titre du popup (déplacement, état de la fenêtre, rappel Échap,
    langue)."""

    new_chat_clicked = Signal()
    fullscreen_clicked = Signal()
    maximize_clicked = Signal()
    close_clicked = Signal()
    # Langue choisie dans le menu (``"fr"``, ``"en"``).
    language_selected = Signal(str)

    # En dessous de ces largeurs, la barre se compacte (fenêtre étroite).
    _PILL_MIN_WIDTH = 560
    _ESC_TEXT_MIN_WIDTH = 470

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(44)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._press: QPoint | None = None
        self._manual_offset: QPoint | None = None
        self._mode = ("offline", "")
        self._window_state = (False, False)  # (agrandie, plein écran)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 8, 0)
        row.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(20))
        row.addWidget(logo)
        title = QLabel("My_OS")
        title.setObjectName("appTitle")
        row.addWidget(title)
        self.mode_pill = QLabel()
        self.mode_pill.setObjectName("modePill")
        self.mode_pill.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.mode_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch()

        self.esc_key = QLabel()
        self.esc_key.setObjectName("escKey")
        self.esc_text = QLabel()
        self.esc_text.setObjectName("escText")
        row.addWidget(self.esc_key, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.esc_text, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addSpacing(6)

        self.language_actions: dict[str, QAction] = {}
        self.language_button = self._language_button()
        row.addWidget(self.language_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.new_chat_button = _title_button("new_chat")
        self.new_chat_button.clicked.connect(self.new_chat_clicked)
        self.fullscreen_button = _title_button("fullscreen")
        self.fullscreen_button.clicked.connect(self.fullscreen_clicked)
        self.maximize_button = _title_button("maximize")
        self.maximize_button.clicked.connect(self.maximize_clicked)
        self.close_button = _title_button("close", object_name="closeButton")
        self.close_button.clicked.connect(self.close_clicked)
        for button in (
            self.new_chat_button,
            self.fullscreen_button,
            self.maximize_button,
            self.close_button,
        ):
            row.addWidget(button)
        self.retranslate()

    def _language_button(self) -> QToolButton:
        """Bouton « FR » / « EN » : menu des langues, chacune dans sa langue."""
        button = QToolButton()
        button.setObjectName("languageButton")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIcon(make_icon("globe", PALETTE.text_2, 15, hover=PALETTE.text))
        button.setIconSize(QSize(15, 15))
        button.setFixedHeight(28)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        menu = QMenu(button)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for language in SUPPORTED_LANGUAGES:
            action = menu.addAction(language_name(language))
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, code=language: self.language_selected.emit(code)
            )
            self.language_actions[language] = action
        button.setMenu(menu)
        return button

    def retranslate(self) -> None:
        """Textes et infobulles dans la langue courante (et langue cochée)."""
        self.esc_key.setText(tr("Échap"))
        self.esc_text.setText(tr("pour fermer"))
        esc_tip = tr(
            "Touche Échap (Esc) : fermer le popup.\nLe raccourci clavier le rouvre."
        )
        self.esc_key.setToolTip(esc_tip)
        self.esc_text.setToolTip(esc_tip)
        language = current_language()
        self.language_button.setText(language.upper())
        self.language_button.setToolTip(
            tr(
                "Langue de My_OS (interface et assistant) : {name}",
                name=language_name(language),
            )
        )
        self.language_actions[language].setChecked(True)
        self.new_chat_button.setToolTip(tr("Nouvelle conversation (Ctrl+L)"))
        self.close_button.setToolTip(tr("Fermer (Échap)"))
        self.set_mode(*self._mode)
        maximized, fullscreen = self._window_state
        self.set_window_state(maximized=maximized, fullscreen=fullscreen)

    def set_mode(self, mode: str, tooltip: str = "") -> None:
        """Pastille de mode : ``local``, ``cloud`` ou ``offline`` (service injoignable)."""
        self._mode = (mode, tooltip)
        dot = {"local": PALETTE.success, "offline": PALETTE.error}
        label = {
            "local": tr("Local"),
            "cloud": "☁&nbsp;&nbsp;Cloud",
            "offline": tr("Hors ligne"),
        }
        text = label.get(mode, mode)
        if mode in dot:
            text = f'<span style="color:{dot[mode]}">●</span>&nbsp;&nbsp;{text}'
        self.mode_pill.setText(text)
        self.mode_pill.setProperty("mode", mode)
        self.mode_pill.setToolTip(tooltip)
        self.mode_pill.style().unpolish(self.mode_pill)
        self.mode_pill.style().polish(self.mode_pill)

    def set_window_state(self, *, maximized: bool, fullscreen: bool) -> None:
        """Adapte les icônes et infobulles à l'état de la fenêtre."""
        self._window_state = (maximized, fullscreen)
        self.fullscreen_button.setIcon(
            make_icon(
                "exit_fullscreen" if fullscreen else "fullscreen",
                PALETTE.text_2,
                16,
                hover=PALETTE.text,
            )
        )
        self.fullscreen_button.setToolTip(
            tr("Quitter le plein écran (F11)")
            if fullscreen
            else tr("Plein écran (F11)")
        )
        self.maximize_button.setIcon(
            make_icon(
                "restore" if maximized else "maximize",
                PALETTE.text_2,
                16,
                hover=PALETTE.text,
            )
        )
        self.maximize_button.setToolTip(
            tr("Restaurer") if maximized else tr("Agrandir")
        )
        self.maximize_button.setEnabled(not fullscreen)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Fenêtre étroite : on cache la pastille, puis le texte du rappel Échap."""
        super().resizeEvent(event)
        width = self.width()
        self.mode_pill.setVisible(width >= self._PILL_MIN_WIDTH)
        self.esc_text.setVisible(width >= self._ESC_TEXT_MIN_WIDTH)

    # ---- déplacement ---------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Début d'un éventuel glisser (démarré au-delà de la distance de glisser)."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._manual_offset = None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Déplace la fenêtre : par le gestionnaire de fenêtres, sinon à la main."""
        window = self.window()
        if not event.buttons() & Qt.MouseButton.LeftButton or window.isFullScreen():
            return
        position = event.globalPosition().toPoint()
        if self._manual_offset is not None:
            window.move(position - self._manual_offset)
            return
        if self._press is None:
            return
        if (
            position - self._press
        ).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        handle = window.windowHandle()
        if handle is not None and handle.startSystemMove():
            return
        if window.isMaximized():
            window.showNormal()
        self._manual_offset = position - window.frameGeometry().topLeft()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Fin du glisser."""
        self._press = None
        self._manual_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Double-clic : agrandir / restaurer."""
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self.window().isFullScreen()
        ):
            self.maximize_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


_EDGE_CURSORS = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
}


class _Grip(QWidget):
    """Poignée invisible posée sur un bord ou un coin de la fenêtre."""

    def __init__(self, window: QWidget, edges: Qt.Edge) -> None:
        super().__init__(window)
        self.edges = edges
        self._start: tuple[QPoint, QRect] | None = None
        corner = edges.value.bit_count() == 2
        if corner:
            diagonal = edges in (
                Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
                Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
            )
            self.setCursor(
                Qt.CursorShape.SizeFDiagCursor
                if diagonal
                else Qt.CursorShape.SizeBDiagCursor
            )
        else:
            self.setCursor(_EDGE_CURSORS[edges])

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Redimensionnement par le gestionnaire de fenêtres, sinon à la main."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        window = self.window()
        handle = window.windowHandle()
        if handle is not None and handle.startSystemResize(self.edges):
            event.accept()
            return
        self._start = (event.globalPosition().toPoint(), window.geometry())
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Redimensionnement manuel (repli), en respectant la taille minimale."""
        if self._start is None:
            return
        origin, geometry = self._start
        delta = event.globalPosition().toPoint() - origin
        window = self.window()
        min_w, min_h = window.minimumWidth(), window.minimumHeight()
        rect = QRect(geometry)
        if self.edges & Qt.Edge.LeftEdge:
            rect.setLeft(min(geometry.left() + delta.x(), geometry.right() - min_w + 1))
        if self.edges & Qt.Edge.RightEdge:
            rect.setRight(
                max(geometry.right() + delta.x(), geometry.left() + min_w - 1)
            )
        if self.edges & Qt.Edge.TopEdge:
            rect.setTop(min(geometry.top() + delta.y(), geometry.bottom() - min_h + 1))
        if self.edges & Qt.Edge.BottomEdge:
            rect.setBottom(
                max(geometry.bottom() + delta.y(), geometry.top() + min_h - 1)
            )
        window.setGeometry(rect)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Fin du redimensionnement manuel."""
        _ = event
        self._start = None


class ResizeGrips:
    """Huit poignées (bords + coins) qui rendent une fenêtre frameless redimensionnable."""

    def __init__(self, window: QWidget, thickness: int = 6, corner: int = 14) -> None:
        self._window = window
        self._thickness = thickness
        self._corner = corner
        left, right = Qt.Edge.LeftEdge, Qt.Edge.RightEdge
        top, bottom = Qt.Edge.TopEdge, Qt.Edge.BottomEdge
        self.grips = [
            _Grip(window, edges)
            for edges in (
                left,
                right,
                top,
                bottom,
                top | left,
                top | right,
                bottom | left,
                bottom | right,
            )
        ]
        self._active = True

    def place(self) -> None:
        """Repositionne les poignées sur les bords (à appeler à chaque redimensionnement)."""
        width, height = self._window.width(), self._window.height()
        t, c = self._thickness, self._corner
        for grip in self.grips:
            edges = grip.edges
            x = 0 if edges & Qt.Edge.LeftEdge else width - t
            y = 0 if edges & Qt.Edge.TopEdge else height - t
            if edges in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
                grip.setGeometry(x, c, t, max(0, height - 2 * c))
            elif edges in (Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
                grip.setGeometry(c, y, max(0, width - 2 * c), t)
            else:
                cx = 0 if edges & Qt.Edge.LeftEdge else width - c
                cy = 0 if edges & Qt.Edge.TopEdge else height - c
                grip.setGeometry(cx, cy, c, c)
            grip.setVisible(self._active)
            grip.raise_()

    def set_active(self, active: bool) -> None:
        """Désactive les poignées (fenêtre agrandie ou en plein écran)."""
        self._active = active
        for grip in self.grips:
            grip.setVisible(active)
