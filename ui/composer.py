"""Zone de saisie du popup, dans l'esprit du composer de My_AI.

Un cadre arrondi contient le champ multiligne (Entrée envoie, Maj+Entrée va à la
ligne, la hauteur suit le texte jusqu'à 6 lignes) et une rangée d'actions : bascule
« ☁ Cloud » (opt-in PAR REQUÊTE, cf. docs/SECURITY.md menace 2), bouton de la clé
API et bouton d'envoi. Pendant une réponse, l'envoi est suspendu : la saisie reste
possible pour préparer la demande suivante.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QContextMenuEvent, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMenu,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.icons import make_icon
from ui.styles import PALETTE

_MAX_LINES = 6
PLACEHOLDER = "Demandez quelque chose à My_OS…"


class _InputEdit(QTextEdit):
    """Champ multiligne en texte brut, avec un menu contextuel en français."""

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Annuler, Rétablir, Couper, Copier, Coller, Tout sélectionner."""
        has_selection = self.textCursor().hasSelection()
        menu = QMenu(self)
        for label, slot, enabled in (
            ("Annuler", self.undo, self.document().isUndoAvailable()),
            ("Rétablir", self.redo, self.document().isRedoAvailable()),
            (None, None, False),
            ("Couper", self.cut, has_selection),
            ("Copier", self.copy, has_selection),
            ("Coller", self.paste, self.canPaste()),
            (None, None, False),
            ("Tout sélectionner", self.selectAll, not self.document().isEmpty()),
        ):
            if label is None:
                menu.addSeparator()
                continue
            action = menu.addAction(label)
            action.setEnabled(enabled)
            action.triggered.connect(slot)
        menu.exec(event.globalPos())
        menu.deleteLater()


class Composer(QWidget):
    """Saisie + actions. Émet :attr:`submitted` avec le texte à envoyer."""

    submitted = Signal(str)
    cloud_toggled = Signal(bool)
    key_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.frame = QFrame()
        self.frame.setObjectName("composer")
        box = QVBoxLayout(self.frame)
        box.setContentsMargins(14, 10, 10, 8)
        box.setSpacing(6)

        self.input = _InputEdit()
        self.input.setObjectName("composerInput")
        self.input.setAcceptRichText(False)
        self.input.setPlaceholderText(PLACEHOLDER)
        self.input.setFrameShape(QFrame.Shape.NoFrame)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.setTabChangesFocus(True)
        self.input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.input.document().setDocumentMargin(2)
        self.input.installEventFilter(self)
        self.input.textChanged.connect(self._on_text_changed)
        self.input.document().documentLayout().documentSizeChanged.connect(
            self._fit_height
        )
        box.addWidget(self.input)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.cloud_toggle = QToolButton()
        self.cloud_toggle.setObjectName("cloudToggle")
        self.cloud_toggle.setText("☁  Cloud")
        self.cloud_toggle.setCheckable(True)
        self.cloud_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cloud_toggle.setToolTip(
            "Envoyer CETTE requête au cloud (Claude) au lieu du modèle local.\n"
            "Opt-in, par requête. Nécessite une clé API (bouton clé)."
        )
        self.cloud_toggle.toggled.connect(self.cloud_toggled)
        actions.addWidget(self.cloud_toggle)

        self.key_button = QToolButton()
        self.key_button.setObjectName("titleButton")
        self.key_button.setIcon(
            make_icon("key", PALETTE.text_3, 16, hover=PALETTE.text)
        )
        self.key_button.setIconSize(QSize(16, 16))
        self.key_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.key_button.setToolTip(
            "Configurer la clé API Anthropic\n(stockée dans le trousseau du système)"
        )
        self.key_button.clicked.connect(self.key_requested)
        actions.addWidget(self.key_button)
        actions.addStretch()

        self.send_button = QToolButton()
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedSize(32, 32)
        self.send_button.setIcon(make_icon("send", PALETTE.on_accent, 18))
        self.send_button.setIconSize(QSize(18, 18))
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self.submit)
        actions.addWidget(self.send_button)
        box.addLayout(actions)

        outer.addWidget(self.frame)
        self._fit_height()
        self._refresh_send()

    # ---- API -----------------------------------------------------------------
    def text(self) -> str:
        """Texte saisi, sans espaces superflus autour."""
        return self.input.toPlainText().strip()

    def set_text(self, text: str) -> None:
        """Remplace la saisie (suggestion de l'écran d'accueil) et place le curseur au bout."""
        self.input.setPlainText(text)
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        self.focus_input()

    def focus_input(self) -> None:
        """Donne le focus au champ de saisie."""
        self.input.setFocus(Qt.FocusReason.OtherFocusReason)

    def is_busy(self) -> bool:
        """Vrai pendant une réponse (envoi suspendu)."""
        return self._busy

    def set_busy(self, busy: bool) -> None:
        """Suspend (ou rétablit) l'envoi pendant une réponse."""
        self._busy = busy
        self._refresh_send()

    def set_cloud_checked(self, checked: bool) -> None:
        """Positionne la bascule Cloud sans émettre :attr:`cloud_toggled`."""
        self.cloud_toggle.blockSignals(True)
        self.cloud_toggle.setChecked(checked)
        self.cloud_toggle.blockSignals(False)

    def submit(self) -> None:
        """Envoie la saisie (si non vide et si aucune réponse n'est en cours)."""
        text = self.text()
        if not text or self._busy:
            return
        self.input.clear()
        self.submitted.emit(text)

    # ---- interne -------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Entrée envoie, Maj+Entrée va à la ligne ; reflète le focus sur le cadre."""
        if watched is self.input:
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                ):
                    self.submit()
                    return True
            elif event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self.frame.setProperty(
                    "focused",
                    "true" if event.type() == QEvent.Type.FocusIn else "false",
                )
                self.frame.style().unpolish(self.frame)
                self.frame.style().polish(self.frame)
        return super().eventFilter(watched, event)

    def _on_text_changed(self) -> None:
        self._refresh_send()

    def _refresh_send(self) -> None:
        enabled = bool(self.text()) and not self._busy
        self.send_button.setEnabled(enabled)
        if self._busy:
            tip = "Réponse en cours… (vous pouvez déjà écrire la suite)"
        else:
            tip = "Envoyer (Entrée)"
        self.send_button.setToolTip(tip)
        color = PALETTE.on_accent if enabled else PALETTE.text_3
        self.send_button.setIcon(make_icon("send", color, 18))

    def _fit_height(self, *_args: object) -> None:
        """La hauteur du champ suit le texte, d'une à six lignes."""
        line = self.input.fontMetrics().lineSpacing()
        margin = 2 * int(self.input.document().documentMargin())
        content = math.ceil(self.input.document().size().height())
        height = max(line + margin, min(content, _MAX_LINES * line + margin))
        self.input.setFixedHeight(height)
        overflow = content > _MAX_LINES * line + margin
        self.input.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if overflow
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
