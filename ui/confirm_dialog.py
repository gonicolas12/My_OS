"""Dialogue modal de confirmation d'une action proposée par l'orchestrator.

Reçoit un payload ``confirmation_needed`` (cf. INTERFACES §1), affiche le
niveau de risque, l'outil et ses arguments, puis propose à l'utilisateur :

* **Refuser** → ``decision="deny"``
* **Une fois** → ``decision="approve_once"`` (pas de grant créé)
* **Pour ce dossier** → ``decision="approve_scope"`` + ``scope="this_folder"``
* **Pour cette session** → ``decision="approve_scope"`` + ``scope="session"``

La portée *this_file* est volontairement absente du jalon 2 (rarement utile
en pratique pour les outils fichiers ; pourra être ajoutée plus tard).
"""

from __future__ import annotations

import html
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

_LEVEL_COLOR = {1: "#ff8c00", 2: "#ff4444"}


class ConfirmDialog(QDialog):
    """Dialog modal de confirmation. Le résultat est lu via :attr:`response`."""

    def __init__(self, payload: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._payload = payload
        self._response: tuple[str, str | None] | None = None
        self._build_ui()

    @property
    def response(self) -> tuple[str, str | None]:
        """``(decision, scope)`` choisi. Si la fenêtre est fermée → ``('deny', None)``."""
        return self._response or ("deny", None)

    # pylint: disable-next=too-many-locals
    def _build_ui(self) -> None:
        risk_level = int(self._payload.get("risk_level", 1))
        tool = str(self._payload.get("tool", ""))
        summary = str(self._payload.get("summary", ""))
        args = self._payload.get("args", {})
        # Élévation : flag explicite du policy_engine (orthogonal au niveau ; un
        # pacman -S de niveau 1 l'exige). Repli sur risk_level>=2 pour les
        # anciens payloads sans le champ.
        requires_elevation = bool(
            self._payload.get("requires_elevation", risk_level >= 2)
        )

        title = "Confirmation requise"
        if requires_elevation:
            title += " — privilèges élevés"
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        level_label = QLabel(f"Niveau de risque {risk_level}")
        color = _LEVEL_COLOR.get(risk_level, "#cccccc")
        level_label.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 15px;"
        )
        layout.addWidget(level_label)

        details = QTextBrowser()
        details.setMaximumHeight(180)
        details.setOpenExternalLinks(False)
        args_str = json.dumps(args, ensure_ascii=False, indent=2)
        details.setHtml(
            f"<p><b>Outil :</b> {html.escape(tool)}</p>"
            f"<p><b>Résumé :</b> {html.escape(summary)}</p>"
            f"<p><b>Arguments :</b></p>"
            f"<pre>{html.escape(args_str)}</pre>"
        )
        layout.addWidget(details)

        if requires_elevation:
            warning = QLabel(
                "⚠ Action sensible : nécessite une élévation de privilèges "
                "via polkit (mot de passe administrateur) lors de l'exécution."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #ff8c00;")
            layout.addWidget(warning)

        button_row = QHBoxLayout()

        deny_btn = QPushButton("Refuser")
        deny_btn.clicked.connect(lambda: self._respond("deny", None))
        button_row.addWidget(deny_btn)

        once_btn = QPushButton("Une fois")
        once_btn.setDefault(True)
        once_btn.clicked.connect(lambda: self._respond("approve_once", None))
        button_row.addWidget(once_btn)

        folder_btn = QPushButton("Pour ce dossier")
        folder_btn.clicked.connect(
            lambda: self._respond("approve_scope", "this_folder")
        )
        button_row.addWidget(folder_btn)

        session_btn = QPushButton("Pour cette session")
        session_btn.clicked.connect(lambda: self._respond("approve_scope", "session"))
        button_row.addWidget(session_btn)

        layout.addLayout(button_row)

        # Échap par défaut Qt ferme via reject() → response reste None → "deny".
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _respond(self, decision: str, scope: str | None) -> None:
        self._response = (decision, scope)
        self.accept()
