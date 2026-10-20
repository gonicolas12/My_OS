"""Dialogue modal de confirmation d'une action proposée par l'orchestrator.

Reçoit un payload ``confirmation_needed`` (cf. INTERFACES §1), affiche le
niveau de risque, l'outil et ses arguments, puis propose à l'utilisateur :

* **Refuser** → ``decision="deny"`` — bouton par défaut (Entrée, Échap) ;
* **Une fois** → ``decision="approve_once"`` (pas de grant créé) ;
* **Pour ce dossier** → ``decision="approve_scope"`` + ``scope="this_folder"`` ;
* **Pour cette session** → ``decision="approve_scope"`` + ``scope="session"``.

Les deux portées ne sont affichées que si le daemon les propose
(``allowed_scopes``) : jamais au niveau 2 (confirmation renforcée), et « ce
dossier » seulement pour un outil qui touche des chemins.

La fenêtre reste **au-dessus** du popup (lui-même « toujours au-dessus ») : sinon
le gestionnaire de fenêtres la place dessous et l'utilisateur ne voit pas ce
qu'il approuve.

La portée *this_file* est volontairement absente du jalon 2 (rarement utile
en pratique pour les outils fichiers ; pourra être ajoutée plus tard).

Présentation : pastille du niveau (orange au niveau 1, rouge au niveau 2), détail de
l'action dans une carte, « Refuser » à gauche (bouton par défaut, focus), les
approbations à droite (« Une fois » en orange, en rouge au niveau 2). L'ordre de
tabulation reste Refuser → Une fois → Pour ce dossier → Pour cette session.
"""

from __future__ import annotations

import html
import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.chat_view import RichText
from ui.icons import logo_icon
from ui.styles import build_stylesheet

_LEVEL_BADGE = {1: "NIVEAU 1 · CONFIRMATION", 2: "NIVEAU 2 · CONFIRMATION RENFORCÉE"}
_LABEL_CELL = '<td style="color:#a3a3a3; padding-right:14px" valign="top">'


class ConfirmDialog(QDialog):
    """Dialog modal de confirmation. Le résultat est lu via :attr:`response`."""

    def __init__(self, payload: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._payload = payload
        self._response: tuple[str, str | None] | None = None
        self._deny_btn: QPushButton | None = None
        # Même couche que le popup (WindowStaysOnTopHint) : sans ce drapeau, xfwm
        # et consorts ouvrent la confirmation SOUS le popup, donc invisible.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setObjectName("confirmDialog")
        self.setWindowIcon(logo_icon())
        if parent is None:
            # Ouvert seul (tests, outils) : le thème ne vient pas d'un parent.
            self.setStyleSheet(build_stylesheet())
        self._build_ui()

    def showEvent(self, event: QShowEvent) -> None:
        """Au premier plan, focus sur « Refuser » (Entrée ne valide jamais par réflexe)."""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        if self._deny_btn is not None:
            self._deny_btn.setFocus()

    @property
    def response(self) -> tuple[str, str | None]:
        """``(decision, scope)`` choisi. Si la fenêtre est fermée → ``('deny', None)``."""
        return self._response or ("deny", None)

    # pylint: disable-next=too-many-locals,too-many-statements
    def _build_ui(self) -> None:
        risk_level = int(self._payload.get("risk_level", 1))
        tool = str(self._payload.get("tool", ""))
        summary = str(self._payload.get("summary", ""))
        args = self._payload.get("args", {})
        # Élévation : flag explicite du policy_engine, vrai seulement si l'outil
        # passera par polkit (ex. pacman -S). Jamais déduit du niveau : annoncer un
        # mot de passe qui ne sera pas demandé tromperait l'utilisateur.
        requires_elevation = bool(self._payload.get("requires_elevation", False))
        allowed_scopes = self._payload.get("allowed_scopes") or []

        title = "Confirmation requise"
        if requires_elevation:
            title += " — privilèges élevés"
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        badge_row = QHBoxLayout()
        level_label = QLabel(_LEVEL_BADGE.get(risk_level, f"NIVEAU {risk_level}"))
        level_label.setObjectName("riskBadge")
        level_label.setProperty("level", str(min(max(risk_level, 1), 2)))
        badge_row.addWidget(level_label)
        badge_row.addStretch()
        layout.addLayout(badge_row)

        heading = QLabel(f"My_OS demande votre accord pour « {tool} »")
        heading.setObjectName("dialogTitle")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        layout.addWidget(heading)

        # Détail en texte échappé, dans une carte à hauteur ajustée (défilement
        # interne au-delà) ; aucun lien suivi, aucune ressource chargée.
        card = QFrame()
        card.setObjectName("confirmDetails")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        details = RichText(object_name="confirmText", max_height=240)
        card_layout.addWidget(details)
        args_str = json.dumps(args, ensure_ascii=False, indent=2)
        details.setHtml(
            '<table cellspacing="0" cellpadding="4">'
            f"<tr>{_LABEL_CELL}Outil</td>"
            f'<td style="font-family:monospace">{html.escape(tool)}</td></tr>'
            f"<tr>{_LABEL_CELL}Action</td><td>{html.escape(summary)}</td></tr>"
            f"<tr>{_LABEL_CELL}Arguments</td>"
            f'<td><pre style="margin:0">{html.escape(args_str)}</pre></td></tr>'
            "</table>"
        )
        layout.addWidget(card)

        if requires_elevation:
            warning = QLabel(
                "⚠ Action sensible : nécessite une élévation de privilèges "
                "via polkit (mot de passe administrateur) lors de l'exécution."
            )
            warning.setObjectName("elevationWarning")
            warning.setWordWrap(True)
            layout.addWidget(warning)

        if risk_level >= 2:
            reinforced = QLabel(
                "Confirmation renforcée : cette action est redemandée à chaque fois."
            )
            reinforced.setObjectName("dialogHint")
            reinforced.setWordWrap(True)
            layout.addWidget(reinforced)

        layout.addSpacing(4)
        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        deny_btn = QPushButton("Refuser")
        deny_btn.setObjectName("denyButton")
        deny_btn.clicked.connect(lambda: self._respond("deny", None))
        # Entrée = Refuser : la fenêtre peut s'ouvrir pendant la frappe, une
        # approbation doit toujours être un choix explicite.
        deny_btn.setDefault(True)
        button_row.addWidget(deny_btn)
        button_row.addStretch()
        self._deny_btn = deny_btn

        once_btn = QPushButton("Une fois")
        once_btn.setObjectName("approveButton")
        once_btn.setProperty("danger", "true" if risk_level >= 2 else "false")
        once_btn.clicked.connect(lambda: self._respond("approve_once", None))
        button_row.addWidget(once_btn)

        # Portées de grant : seulement celles proposées par le daemon (aucune au
        # niveau 2). Le daemon ignore de toute façon une portée non proposée.
        if "this_folder" in allowed_scopes:
            folder_btn = QPushButton("Pour ce dossier")
            folder_btn.clicked.connect(
                lambda: self._respond("approve_scope", "this_folder")
            )
            button_row.addWidget(folder_btn)

        if "session" in allowed_scopes:
            session_btn = QPushButton("Pour cette session")
            session_btn.clicked.connect(
                lambda: self._respond("approve_scope", "session")
            )
            button_row.addWidget(session_btn)

        layout.addLayout(button_row)

        # Échap par défaut Qt ferme via reject() → response reste None → "deny".
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _respond(self, decision: str, scope: str | None) -> None:
        self._response = (decision, scope)
        self.accept()
