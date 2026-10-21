"""Vue de la conversation du popup : un widget par message, dans une colonne centrée.

Disposition reprise de My_AI : la demande de l'utilisateur dans une bulle à droite,
la réponse de My_OS en pleine largeur sous un en-tête (logo, nom, heure, durée),
les résultats d'outils dans des cartes, chaque échange séparé du suivant.

* :class:`RichText` — ``QTextBrowser`` en lecture seule à hauteur automatique.
  Il n'ouvre aucun lien et ne charge **aucune** ressource (image, fichier) : le
  contenu affiché reste une donnée (cf. docs/SECURITY.md §2.2).
* :class:`AssistantMessage` — narration en streaming (markdown + blocs de code),
  cartes d'outils (texte brut, jamais interprété), notices, indicateur d'activité.
* :class:`ChatView` — écran d'accueil (suggestions) ou fil des messages, avec
  défilement collé en bas pendant une réponse, sauf si l'utilisateur est remonté.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import (
    QEvent,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QResizeEvent,
    QTextOption,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.icons import icon_pixmap, logo_pixmap, make_icon
from ui.markdown_render import mono_font, render_markdown, split_markdown
from ui.styles import CHAT_COLUMN_MAX, PALETTE

# Délai de regroupement des fragments en streaming (un rendu markdown par tranche).
_RENDER_DELAY_MS = 40
# Hauteur max d'une sortie d'outil avant défilement interne (px).
_TOOL_OUTPUT_MAX = 260
# Distance au bas (px) sous laquelle la vue reste « collée » au dernier message.
_BOTTOM_THRESHOLD = 48
# Part de la largeur occupée au plus par la bulle de l'utilisateur.
_USER_BUBBLE_RATIO = 0.8

_TOOL_STATUS = {
    "ok": ("check", "Terminé", PALETTE.success),
    "error": ("alert", "Échec", PALETTE.error),
    "denied": ("close", "Refusé", PALETTE.warning),
    "blocked": ("ban", "Bloqué", PALETTE.error),
}

SUGGESTIONS = (
    "Quels processus utilisent le plus de mémoire ?",
    "Liste les fichiers de mon dossier Téléchargements",
    "Baisse le volume à 30 %",
    "Cherche un paquet pour éditer des PDF",
)


def format_duration(seconds: float) -> str:
    """Durée lisible en français : ``3,2 s``, ``1 min 52 s``."""
    if seconds < 60:
        return f"{seconds:.1f} s".replace(".", ",")
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest:02d} s"


def _elide(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class RichText(QTextBrowser):
    """Texte en lecture seule, à hauteur automatique (sans barre de défilement).

    ``max_height`` (facultatif) plafonne la hauteur : au-delà, défilement interne.
    La molette repart vers la conversation quand ce texte ne peut plus défiler.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        object_name: str = "richText",
        max_height: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._max_height = max_height
        self._fitting = False
        # Un lien ne s'ouvre pas et n'est jamais suivi dans la vue (sinon un lien
        # vers un fichier local afficherait ce fichier à la place du message).
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if max_height is None
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Link, QColor(PALETTE.accent_text))
        self.setPalette(palette)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self.setFixedHeight(self.fontMetrics().height() + 2)

    def loadResource(self, type_: int, name: object) -> object:
        """Aucune ressource n'est jamais chargée (image, feuille de style, fichier)."""
        _ = (type_, name)
        return None

    def set_markdown(self, text: str) -> None:
        """Affiche du markdown (sans HTML brut ni image)."""
        render_markdown(self.document(), text)
        self._fit_height()

    def set_plain(self, text: str) -> None:
        """Affiche du texte brut, tel quel (espaces et retours à la ligne gardés)."""
        self.setPlainText(text)
        self._fit_height()

    def natural_width(self) -> int:
        """Largeur du texte sans retour à la ligne automatique (px)."""
        clone = self.document().clone()  # sans parent : libéré avec la variable
        clone.setTextWidth(-1)
        return math.ceil(clone.idealWidth()) + 2 * self.frameWidth() + 2

    def _fit_height(self, *_args: object) -> None:
        if self._fitting:
            return
        self._fitting = True
        try:
            margins = self.contentsMargins()
            height = (
                math.ceil(self.document().size().height())
                + margins.top()
                + margins.bottom()
            )
            if self._max_height is not None:
                height = min(height, self._max_height)
            height = max(height, self.fontMetrics().height())
            if height != self.height():
                self.setFixedHeight(height)
        finally:
            self._fitting = False

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Recalcule la hauteur quand la largeur (donc les retours à la ligne) change."""
        super().resizeEvent(event)
        self._fit_height()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Laisse défiler la conversation quand ce texte est au bout (ou sans défilement)."""
        bar = self.verticalScrollBar()
        delta = event.angleDelta().y()
        if (
            bar.maximum() == 0
            or (delta > 0 and bar.value() <= bar.minimum())
            or (delta < 0 and bar.value() >= bar.maximum())
        ):
            event.ignore()
            return
        super().wheelEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Menu contextuel en français : Copier, Tout sélectionner."""
        menu = QMenu(self)
        copy = menu.addAction("Copier")
        copy.setEnabled(self.textCursor().hasSelection())
        copy.triggered.connect(self.copy)
        menu.addAction("Tout sélectionner").triggered.connect(self.selectAll)
        menu.exec(event.globalPos())
        menu.deleteLater()


class _LogoMark(QWidget):
    """Logo de My_OS qui « respire » (opacité) tant que la réponse est en cours."""

    def __init__(self, size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = logo_pixmap(size)
        self._opacity = 1.0
        self.setFixedSize(size, size)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(1.0)
        self._anim.setKeyValueAt(0.5, 0.35)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1600)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._set_opacity)

    def set_active(self, active: bool) -> None:
        """Lance ou arrête la pulsation."""
        if active:
            self._anim.start()
        else:
            self._anim.stop()
            self._set_opacity(1.0)

    def _set_opacity(self, value: object) -> None:
        self._opacity = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Dessine le logo à l'opacité courante."""
        _ = event
        painter = QPainter(self)
        painter.setOpacity(self._opacity)
        painter.drawPixmap(0, 0, self._pixmap)


class ShimmerLabel(QWidget):
    """Texte d'attente traversé d'un reflet (« Le modèle réfléchit… »), comme My_AI."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusText")
        self._text = ""
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(2200)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._set_phase)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        """Texte affiché (sans les points de suspension)."""
        return self._text

    def setText(self, text: str) -> None:
        """Change le texte d'attente."""
        self._text = text
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        """Taille du texte affiché."""
        metrics = self.fontMetrics()
        return QSize(
            metrics.horizontalAdvance(self._text + "…") + 4, metrics.height() + 4
        )

    def showEvent(self, event: QEvent) -> None:
        """Démarre le reflet."""
        super().showEvent(event)
        self._anim.start()

    def hideEvent(self, event: QEvent) -> None:
        """Arrête le reflet (aucun calcul quand rien n'est affiché)."""
        super().hideEvent(event)
        self._anim.stop()

    def _set_phase(self, value: object) -> None:
        self._phase = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        """Dessine le texte avec un dégradé qui glisse de gauche à droite."""
        _ = event
        text = self._text + "…"
        width = max(1, self.fontMetrics().horizontalAdvance(text))
        center = -0.3 * width + self._phase * 1.6 * width
        gradient = QLinearGradient(center - 0.3 * width, 0, center + 0.3 * width, 0)
        gradient.setColorAt(0.0, QColor(PALETTE.text_3))
        gradient.setColorAt(0.5, QColor(PALETTE.text))
        gradient.setColorAt(1.0, QColor(PALETTE.text_3))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(QPen(gradient, 1))
        painter.setFont(self.font())
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )


class _CopyButton(QToolButton):
    """Bouton « copier » discret ; l'icône devient une coche un instant."""

    def __init__(self, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ghostButton")
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(14, 14))
        self.setIcon(make_icon("copy", PALETTE.text_3, 14, hover=PALETTE.text))
        self._source: object = None
        self.clicked.connect(self._copy)

    def set_source(self, source: object) -> None:
        """``source`` : fonction sans argument qui renvoie le texte à copier."""
        self._source = source

    def _copy(self) -> None:
        if not callable(self._source):
            return
        QGuiApplication.clipboard().setText(str(self._source()))
        self.setIcon(make_icon("check", PALETTE.success, 14))
        QTimer.singleShot(
            1200,
            lambda: self.setIcon(
                make_icon("copy", PALETTE.text_3, 14, hover=PALETTE.text)
            ),
        )


class CodeBlock(QFrame):
    """Bloc de code d'une réponse : langage, bouton Copier, texte monospace."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("codeBlock")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget()
        header.setObjectName("codeHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(12, 4, 6, 4)
        self._lang = QLabel()
        self._lang.setObjectName("codeLang")
        header_row.addWidget(self._lang)
        header_row.addStretch()
        self._copy = _CopyButton("Copier le code")
        self._copy.set_source(lambda: self._code)
        header_row.addWidget(self._copy)
        layout.addWidget(header)
        self._text = RichText(object_name="codeText")
        self._text.document().setDefaultFont(mono_font())
        body = QVBoxLayout()
        body.setContentsMargins(12, 8, 12, 10)
        body.addWidget(self._text)
        layout.addLayout(body)
        self._code = ""

    def set_code(self, code: str, lang: str) -> None:
        """Met à jour le code (et son langage) affiché."""
        self._lang.setText(lang or "code")
        if code != self._code:
            self._code = code
            self._text.set_plain(code)


class MarkdownView(QWidget):
    """Narration markdown, découpée en texte et blocs de code, mise à jour en direct."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._parts: list[tuple[str, QWidget]] = []
        self._text = ""

    @property
    def text(self) -> str:
        """Markdown affiché."""
        return self._text

    def set_text(self, text: str) -> None:
        """Affiche ``text`` en réutilisant les widgets existants (pas de clignotement)."""
        self._text = text
        segments = split_markdown(text)
        for index, segment in enumerate(segments):
            if index < len(self._parts) and self._parts[index][0] != segment.kind:
                self._truncate(index)
            if index >= len(self._parts):
                widget: QWidget = CodeBlock() if segment.kind == "code" else RichText()
                self._layout.addWidget(widget)
                self._parts.append((segment.kind, widget))
            widget = self._parts[index][1]
            if isinstance(widget, CodeBlock):
                widget.set_code(segment.text, segment.lang)
            elif isinstance(widget, RichText):
                widget.set_markdown(segment.text)
        self._truncate(len(segments))
        self.setVisible(bool(segments))

    def _truncate(self, count: int) -> None:
        while len(self._parts) > count:
            _, widget = self._parts.pop()
            widget.setParent(None)
            widget.deleteLater()


class ToolCard(QFrame):
    """Résultat d'un outil : nom, statut, sortie en texte brut (jamais interprétée)."""

    def __init__(
        self, tool: str, status: str, text: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("toolCard")
        icon_name, label, color = _TOOL_STATUS.get(
            status, ("alert", status or "?", PALETTE.text_2)
        )
        self.status = status
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("toolHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 5, 10, 5)
        row.setSpacing(6)
        term = QLabel()
        term.setPixmap(icon_pixmap("terminal", PALETTE.text_3, 14))
        row.addWidget(term)
        name = QLabel(_elide(tool, 48))
        name.setObjectName("toolName")
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setToolTip(f"Outil : {_elide(tool, 200)}")
        row.addWidget(name)
        row.addStretch()
        status_icon = QLabel()
        status_icon.setPixmap(icon_pixmap(icon_name, color, 13))
        row.addWidget(status_icon)
        self.status_label = QLabel(label)
        self.status_label.setObjectName("toolStatus")
        self.status_label.setProperty("status", status)
        row.addWidget(self.status_label)
        layout.addWidget(header)

        self.output = RichText(object_name="toolText", max_height=_TOOL_OUTPUT_MAX)
        self.output.document().setDefaultFont(mono_font())
        self.output.set_plain(text)
        body = QVBoxLayout()
        body.setContentsMargins(12, 8, 8, 10)
        body.addWidget(self.output)
        layout.addLayout(body)
        if not text.strip():
            self.output.hide()


class Notice(QLabel):
    """Notice du daemon (envoi au cloud, repli local, erreur), hors narration."""

    def __init__(self, level: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("notice")
        self.setProperty(
            "level", level if level in ("info", "warning", "error") else "info"
        )
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


class UserMessage(QWidget):
    """Demande de l'utilisateur : bulle orangée alignée à droite, texte brut."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text = text
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch()
        self._bubble = QFrame()
        self._bubble.setObjectName("userBubble")
        bubble_layout = QVBoxLayout(self._bubble)
        bubble_layout.setContentsMargins(14, 9, 14, 9)
        self._label = RichText(object_name="userText")
        self._label.set_plain(text)
        self._label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        bubble_layout.addWidget(self._label)
        row.addWidget(self._bubble)
        self._natural: int | None = None

    def resizeEvent(self, event: QResizeEvent) -> None:
        """La bulle épouse le texte, sans dépasser 80 % de la largeur."""
        super().resizeEvent(event)
        self._label.ensurePolished()
        if self._natural is None:
            self._natural = self._label.natural_width()
        limit = max(80, int(self.width() * _USER_BUBBLE_RATIO) - 30)
        width = min(self._natural, limit)
        if width != self._label.width():
            self._label.setFixedWidth(width)


class AssistantMessage(QWidget):  # pylint: disable=too-many-instance-attributes
    """Réponse de My_OS à une demande, construite au fil du flux IPC."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._started = time.monotonic()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._logo = _LogoMark(18)
        header.addWidget(self._logo)
        name = QLabel("My_OS")
        name.setObjectName("assistantName")
        header.addWidget(name)
        self._meta = QLabel(time.strftime("%H:%M"))
        self._meta.setObjectName("assistantMeta")
        header.addWidget(self._meta)
        header.addStretch()
        self._copy = _CopyButton("Copier la réponse")
        self._copy.set_source(lambda: self.text)
        self._copy.hide()
        header.addWidget(self._copy)
        self._layout.addLayout(header)

        self._parts: list[QWidget] = []
        self._current: MarkdownView | None = None
        self._buffer = ""
        self._texts: list[MarkdownView] = []

        self._status = ShimmerLabel()
        self._layout.addWidget(self._status)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(_RENDER_DELAY_MS)
        self._render_timer.timeout.connect(self._render_current)
        self.finished = False
        self.set_status("Le modèle réfléchit")

    # ---- contenu -------------------------------------------------------------
    @property
    def text(self) -> str:
        """Narration complète (markdown), pour le bouton Copier."""
        self._render_current()
        return "\n\n".join(
            view.text.strip() for view in self._texts if view.text.strip()
        )

    @property
    def parts(self) -> list[QWidget]:
        """Widgets du corps de la réponse, dans l'ordre (texte, cartes, notices)."""
        return list(self._parts)

    def append_text(self, fragment: str) -> None:
        """Ajoute un fragment de narration (rendu regroupé toutes les 40 ms)."""
        if not fragment:
            return
        if self._current is None:
            self._current = MarkdownView()
            self._texts.append(self._current)
            self._add_part(self._current)
            self._buffer = ""
        self._buffer += fragment
        if not self._render_timer.isActive():
            self._render_timer.start()

    def add_tool_result(self, tool: str, status: str, text: str) -> ToolCard:
        """Ajoute la carte du résultat d'un outil (la narration suivante repart dessous)."""
        self._close_text()
        card = ToolCard(tool, status, text)
        self._add_part(card)
        return card

    def add_notice(self, level: str, text: str) -> Notice:
        """Ajoute une notice (cloud, repli local, erreur)."""
        self._close_text()
        notice = Notice(level, text)
        self._add_part(notice)
        return notice

    def set_status(self, text: str | None) -> None:
        """Affiche (ou cache, si ``None``) l'indicateur d'activité."""
        if text is None or self.finished:
            self._status.hide()
            self._logo.set_active(False)
            return
        self._status.setText(text)
        self._status.show()
        self._logo.set_active(True)

    def status_text(self) -> str | None:
        """Texte de l'indicateur d'activité, ou ``None`` s'il est caché."""
        return self._status.text() if not self._status.isHidden() else None

    def finish(self) -> None:
        """Fin de la réponse : rendu final, durée, bouton Copier."""
        if self.finished:
            return
        self._close_text()
        self.set_status(None)
        self.finished = True
        elapsed = format_duration(time.monotonic() - self._started)
        self._meta.setText(f"{self._meta.text()} · {elapsed}")
        self._copy.setVisible(bool(self.text))

    # ---- interne -------------------------------------------------------------
    def _add_part(self, widget: QWidget) -> None:
        self._layout.insertWidget(self._layout.indexOf(self._status), widget)
        self._parts.append(widget)

    def _close_text(self) -> None:
        self._render_current()
        self._current = None

    def _render_current(self) -> None:
        self._render_timer.stop()
        if self._current is not None and self._current.text != self._buffer:
            self._current.set_text(self._buffer)


class _ScrollPage(QWidget):
    """Fil des messages défilant, avec le bouton « revenir en bas » par-dessus."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setObjectName("chatViewport")
        layout.addWidget(self.scroll)

        self.jump = QToolButton(self)
        self.jump.setObjectName("jumpButton")
        self.jump.setFixedSize(32, 32)
        self.jump.setIcon(
            make_icon("arrow_down", PALETTE.text_2, 16, hover=PALETTE.text)
        )
        self.jump.setIconSize(QSize(16, 16))
        self.jump.setToolTip("Revenir au dernier message")
        self.jump.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump.hide()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Garde le bouton centré en bas de la zone de lecture."""
        super().resizeEvent(event)
        self.jump.move((self.width() - self.jump.width()) // 2, self.height() - 44)
        self.jump.raise_()


class EmptyState(QWidget):
    """Écran d'accueil : logo, invitation et suggestions cliquables."""

    suggestion_clicked = Signal(str)

    def __init__(self, hotkey_label: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.addStretch(3)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(52))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(logo)
        outer.addSpacing(12)
        title = QLabel("My_OS")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(title)
        subtitle = QLabel(
            "Votre assistant système, local et privé. Que puis-je faire ?"
        )
        subtitle.setObjectName("emptySubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)
        outer.addSpacing(18)

        box = QWidget()
        box.setMaximumWidth(460)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        self.buttons: list[QPushButton] = []
        for suggestion in SUGGESTIONS:
            button = QPushButton(suggestion)
            button.setObjectName("suggestion")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, text=suggestion: self.suggestion_clicked.emit(
                    text
                )
            )
            column.addWidget(button)
            self.buttons.append(button)
        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(box, 100)
        centered.addStretch(1)
        outer.addLayout(centered)
        outer.addStretch(4)
        if hotkey_label:
            reminder = QLabel(f"{hotkey_label} ouvre My_OS à tout moment")
            reminder.setObjectName("footerHint")
            reminder.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            outer.addWidget(reminder)


class ChatView(QStackedWidget):
    """Écran d'accueil tant que la conversation est vide, puis fil des messages."""

    suggestion_clicked = Signal(str)

    def __init__(self, hotkey_label: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.empty_state = EmptyState(hotkey_label)
        self.empty_state.suggestion_clicked.connect(self.suggestion_clicked)
        self.addWidget(self.empty_state)

        self._page = _ScrollPage()
        self.addWidget(self._page)
        self._scroll = self._page.scroll
        content = QWidget()
        content.setObjectName("chatContent")
        centered = QHBoxLayout(content)
        centered.setContentsMargins(18, 14, 18, 18)
        centered.addStretch(1)
        self._column = QWidget()
        self._column.setObjectName("chatColumn")
        self._column.setMaximumWidth(CHAT_COLUMN_MAX)
        self._column.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._messages = QVBoxLayout(self._column)
        self._messages.setContentsMargins(0, 0, 0, 0)
        self._messages.setSpacing(0)
        self._messages.addStretch(1)
        centered.addWidget(self._column, 100)
        centered.addStretch(1)
        self._scroll.setWidget(content)

        self._stick = True
        bar = self._scroll.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)
        self._page.jump.clicked.connect(lambda: self.scroll_to_bottom(force=True))
        self._turns = 0

    # ---- messages ------------------------------------------------------------
    def is_empty(self) -> bool:
        """Vrai tant qu'aucun message n'a été affiché."""
        return self._turns == 0

    def add_user_message(self, text: str) -> UserMessage:
        """Ouvre un nouvel échange avec la demande de l'utilisateur."""
        if self._turns:
            self._add_spacing(14)
            divider = QFrame()
            divider.setObjectName("turnDivider")
            self._add(divider)
            self._add_spacing(18)
        self._turns += 1
        self.setCurrentWidget(self._page)
        message = UserMessage(text)
        self._add(message)
        self._add_spacing(14)
        self.scroll_to_bottom(force=True)
        return message

    def add_assistant_message(self) -> AssistantMessage:
        """Ajoute la réponse (vide, indicateur d'activité visible) de l'échange en cours."""
        message = AssistantMessage()
        self._add(message)
        self.scroll_to_bottom(force=True)
        return message

    def clear(self) -> None:
        """Vide la conversation et revient à l'écran d'accueil."""
        while self._messages.count() > 1:
            item = self._messages.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._turns = 0
        self._stick = True
        self._page.jump.hide()
        self.setCurrentWidget(self.empty_state)

    def message_widgets(self) -> list[QWidget]:
        """Messages affichés (bulles et réponses), dans l'ordre."""
        widgets = []
        for index in range(self._messages.count()):
            widget = self._messages.itemAt(index).widget()
            if isinstance(widget, (UserMessage, AssistantMessage)):
                widgets.append(widget)
        return widgets

    # ---- défilement ------------------------------------------------------------
    def scroll_to_bottom(self, force: bool = False) -> None:
        """Descend au dernier message (``force`` : même si l'utilisateur était remonté)."""
        if force:
            self._stick = True
        if self._stick:
            bar = self._scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
            self._page.jump.hide()

    def _add(self, widget: QWidget) -> None:
        self._messages.insertWidget(self._messages.count() - 1, widget)

    def _add_spacing(self, height: int) -> None:
        self._messages.insertSpacing(self._messages.count() - 1, height)

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        if self._stick:
            self._scroll.verticalScrollBar().setValue(maximum)

    def _on_value_changed(self, value: int) -> None:
        bar = self._scroll.verticalScrollBar()
        self._stick = value >= bar.maximum() - _BOTTOM_THRESHOLD
        self._page.jump.setVisible(not self._stick)
        if not self._stick:
            self._page.jump.raise_()


__all__ = [
    "SUGGESTIONS",
    "AssistantMessage",
    "ChatView",
    "CodeBlock",
    "EmptyState",
    "MarkdownView",
    "Notice",
    "RichText",
    "ShimmerLabel",
    "ToolCard",
    "UserMessage",
    "format_duration",
]
