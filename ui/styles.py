"""Thème visuel du popup : jetons de design + feuille de style QSS.

Charte noir / orange de My_OS. Les jetons suivent ceux de l'interface de My_AI
(``frontend/src/styles/tokens.css`` : niveaux de gris, arrondis, tailles de texte),
avec l'orange de My_OS (``#ff8c00``, celui du menu de démarrage) comme accent.

Toutes les couleurs passent par :data:`PALETTE` : la feuille QSS, les icônes
dessinées (``ui/icons.py``) et le rendu markdown (``ui/markdown_render.py``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:  # pylint: disable=too-many-instance-attributes
    """Couleurs de l'interface (thème sombre)."""

    bg: str = "#0f0f0f"  # fond de la fenêtre
    surface: str = "#171717"  # composer, cartes
    surface_hover: str = "#1f1f1f"  # survols, en-têtes
    surface_strong: str = "#262626"  # puces, code en ligne
    border: str = "#262626"
    border_strong: str = "#363636"
    text: str = "#ececec"
    text_2: str = "#a3a3a3"
    text_3: str = "#6e6e6e"
    accent: str = "#ff8c00"
    accent_hover: str = "#ffa031"
    accent_pressed: str = "#e07a00"
    accent_text: str = "#ffad4d"  # orange clair lisible sur noir (liens, code)
    accent_soft: str = "#261806"  # fond teinté d'orange (bandeau cloud)
    accent_border: str = "#5c3a0c"
    accent_focus: str = "#9a5a0a"  # bordure de la saisie active
    on_accent: str = "#141414"  # texte posé sur l'orange
    user_bg: str = "#2a1d0e"  # bulle de l'utilisateur
    user_border: str = "#4a3215"
    code_bg: str = "#131313"
    code_text: str = "#e3e3e3"
    success: str = "#22c55e"
    warning: str = "#f5a524"
    warning_soft: str = "#261d08"
    warning_border: str = "#5c4612"
    error: str = "#f05252"
    error_soft: str = "#2a1111"
    error_border: str = "#5e1f1f"
    danger_button: str = "#c93c3c"
    danger_button_hover: str = "#db4b4b"


PALETTE = Palette()

FONT_UI = ["Inter", "Noto Sans", "DejaVu Sans", "Segoe UI", "sans-serif"]
FONT_MONO = [
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "Noto Sans Mono",
    "Consolas",
    "monospace",
]

# Tailles de texte (px) et arrondis, comme les jetons de My_AI.
FS_2XS = 11
FS_XS = 12
FS_SM = 13
FS_BASE = 14
FS_TITLE = 24

RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14
RADIUS_XL = 18

# Largeur maximale de la colonne de lecture (en plein écran, le texte reste lisible).
CHAT_COLUMN_MAX = 860


def _families(families: list[str]) -> str:
    return ", ".join(f'"{name}"' if " " in name else name for name in families)


def build_stylesheet(theme: str = "dark") -> str:
    """Renvoie la feuille de style QSS du popup (et de ses fenêtres filles).

    Seul le thème « dark » existe ; tout autre nom y retombe.
    """
    _ = theme
    p = PALETTE
    ui_font = _families(FONT_UI)
    mono = _families(FONT_MONO)
    return f"""
/* ---- base ------------------------------------------------------------- */
QWidget {{
    color: {p.text};
    font-family: {ui_font};
    font-size: {FS_BASE}px;
}}
#popupRoot {{
    background-color: {p.bg};
    border: 1px solid {p.border_strong};
}}
#popupRoot[edge="none"] {{
    border: none;
}}
QMenu {{
    background-color: {p.surface_hover};
    border: 1px solid {p.border_strong};
    padding: 4px;
}}
QMenu::item {{
    color: {p.text};
    padding: 5px 22px 5px 12px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background-color: {p.surface_strong};
}}
QMenu::item:disabled {{
    color: {p.text_3};
}}
QMenu::separator {{
    height: 1px;
    background-color: {p.border_strong};
    margin: 4px 6px;
}}
QToolTip {{
    background-color: {p.surface_hover};
    color: {p.text};
    border: 1px solid {p.border_strong};
    padding: 4px 7px;
    font-size: {FS_XS}px;
}}

/* ---- barre de titre ---------------------------------------------------- */
#titleBar {{
    background-color: {p.bg};
    border-bottom: 1px solid {p.border};
}}
#appTitle {{
    color: {p.text};
    font-size: {FS_BASE}px;
    font-weight: 700;
}}
#modePill {{
    color: {p.text_2};
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 1px 8px;
    font-size: {FS_2XS}px;
}}
#modePill[mode="cloud"] {{
    color: {p.accent_text};
    background-color: {p.accent_soft};
    border-color: {p.accent_border};
}}
#modePill[mode="offline"] {{
    color: {p.error};
    background-color: {p.error_soft};
    border-color: {p.error_border};
}}
#escKey {{
    color: {p.text_2};
    background-color: {p.surface};
    border: 1px solid {p.border_strong};
    border-bottom-width: 2px;
    border-radius: 4px;
    padding: 0px 5px;
    font-size: {FS_2XS}px;
    font-weight: 600;
}}
#escText {{
    color: {p.text_3};
    font-size: {FS_2XS}px;
}}
QToolButton#titleButton, QToolButton#closeButton {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 4px;
}}
QToolButton#titleButton:hover {{
    background-color: {p.surface_hover};
}}
QToolButton#titleButton:pressed {{
    background-color: {p.surface_strong};
}}
QToolButton#closeButton:hover {{
    background-color: {p.danger_button};
}}
QToolButton#languageButton {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 2px 6px;
    color: {p.text_2};
    font-size: {FS_2XS}px;
    font-weight: 700;
}}
QToolButton#languageButton:hover {{
    background-color: {p.surface_hover};
    color: {p.text};
}}
QToolButton#languageButton::menu-indicator {{
    image: none;
    width: 0px;
}}

/* ---- conversation ------------------------------------------------------ */
QScrollArea#chatScroll, #chatViewport, #chatContent, #chatColumn,
QScrollArea#emptyScroll, #emptyViewport, #emptyContent {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0px;
}}
QScrollBar::handle:vertical {{
    background-color: {p.border_strong};
    border-radius: 3px;
    min-height: 32px;
    margin: 0px 2px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: #4a4a4a;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px 2px 2px 2px;
}}
QScrollBar::handle:horizontal {{
    background-color: {p.border_strong};
    border-radius: 3px;
    min-width: 32px;
    margin: 2px 0px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0px;
    height: 0px;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}
QTextBrowser#richText, QTextBrowser#codeText, QTextBrowser#toolText {{
    background: transparent;
    border: none;
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
QTextBrowser#codeText, QTextBrowser#toolText {{
    font-family: {mono};
    font-size: {FS_SM}px;
    color: {p.code_text};
}}
#userBubble {{
    background-color: {p.user_bg};
    border: 1px solid {p.user_border};
    border-radius: {RADIUS_LG}px;
    border-bottom-right-radius: 4px;
}}
QTextBrowser#userText {{
    background: transparent;
    border: none;
    color: {p.text};
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
QLabel#assistantName {{
    color: {p.text};
    font-weight: 700;
    font-size: {FS_SM}px;
}}
QLabel#assistantMeta {{
    color: {p.text_3};
    font-size: {FS_2XS}px;
}}
#statusText {{
    font-size: {FS_SM}px;
}}
#turnDivider {{
    background-color: {p.border};
    max-height: 1px;
    min-height: 1px;
}}
QToolButton#ghostButton {{
    background: transparent;
    border: none;
    border-radius: {RADIUS_SM}px;
    padding: 3px;
}}
QToolButton#ghostButton:hover {{
    background-color: {p.surface_hover};
}}
#codeBlock, #toolCard {{
    background-color: {p.code_bg};
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
}}
#codeHeader, #toolHeader {{
    background: transparent;
    border-bottom: 1px solid {p.border};
}}
QLabel#codeLang, QLabel#toolName {{
    color: {p.text_2};
    font-family: {mono};
    font-size: {FS_XS}px;
}}
QLabel#toolStatus {{
    font-size: {FS_XS}px;
    font-weight: 600;
}}
QLabel#toolStatus[status="ok"] {{ color: {p.success}; }}
QLabel#toolStatus[status="error"] {{ color: {p.error}; }}
QLabel#toolStatus[status="blocked"] {{ color: {p.error}; }}
QLabel#toolStatus[status="denied"] {{ color: {p.warning}; }}
QLabel#notice {{
    border-radius: 8px;
    padding: 6px 10px;
    font-size: {FS_SM}px;
}}
QLabel#notice[level="info"] {{
    color: {p.accent_text};
    background-color: {p.accent_soft};
    border: 1px solid {p.accent_border};
}}
QLabel#notice[level="warning"] {{
    color: {p.warning};
    background-color: {p.warning_soft};
    border: 1px solid {p.warning_border};
}}
QLabel#notice[level="error"] {{
    color: #ff8a8a;
    background-color: {p.error_soft};
    border: 1px solid {p.error_border};
}}
QToolButton#jumpButton {{
    background-color: {p.surface_hover};
    border: 1px solid {p.border_strong};
    border-radius: 16px;
}}
QToolButton#jumpButton:hover {{
    border-color: {p.accent};
}}

/* ---- écran d'accueil --------------------------------------------------- */
QLabel#emptyTitle {{
    color: {p.text};
    font-size: {FS_TITLE}px;
    font-weight: 800;
}}
QLabel#emptySubtitle {{
    color: {p.text_2};
}}
QPushButton#suggestion {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
    padding: 9px 14px;
    color: {p.text_2};
    text-align: left;
    font-size: {FS_SM}px;
}}
QPushButton#suggestion:hover {{
    color: {p.text};
    border-color: {p.accent_border};
    background-color: {p.surface_hover};
}}

/* ---- zone de saisie ---------------------------------------------------- */
#composer {{
    background-color: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS_XL}px;
}}
#composer[focused="true"] {{
    border-color: {p.accent_focus};
}}
QTextEdit#composerInput {{
    background: transparent;
    border: none;
    color: {p.text};
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
QToolButton#cloudToggle {{
    background: transparent;
    border: 1px solid {p.border_strong};
    border-radius: 13px;
    padding: 3px 10px;
    color: {p.text_2};
    font-size: {FS_XS}px;
}}
QToolButton#cloudToggle:hover {{
    color: {p.text};
    border-color: #4a4a4a;
}}
QToolButton#cloudToggle:checked {{
    color: {p.on_accent};
    background-color: {p.accent};
    border-color: {p.accent};
    font-weight: 700;
}}
QToolButton#sendButton {{
    background-color: {p.accent};
    border: none;
    border-radius: 16px;
}}
QToolButton#sendButton:hover {{
    background-color: {p.accent_hover};
}}
QToolButton#sendButton:pressed {{
    background-color: {p.accent_pressed};
}}
QToolButton#sendButton:disabled {{
    background-color: {p.surface_strong};
}}
QLabel#cloudBanner {{
    color: {p.accent_text};
    background-color: {p.accent_soft};
    border: 1px solid {p.accent};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: {FS_SM}px;
    font-weight: 600;
}}
QLabel#footerHint {{
    color: {p.text_3};
    font-size: {FS_2XS}px;
}}

/* ---- fenêtres filles (confirmation, clé API, messages) ------------------ */
QDialog, QMessageBox, QInputDialog {{
    background-color: {p.surface};
}}
QDialogButtonBox {{
    dialogbuttonbox-buttons-have-icons: 0;
}}
QDialog QLabel, QMessageBox QLabel {{
    background: transparent;
}}
QDialog QLineEdit {{
    background-color: {p.surface};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
QDialog QLineEdit:focus {{
    border-color: {p.accent};
}}
QDialog QPushButton {{
    background-color: {p.surface_hover};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
    padding: 7px 14px;
    color: {p.text};
}}
QDialog QPushButton:hover {{
    border-color: #555555;
    background-color: {p.surface_strong};
}}
QDialog QPushButton:focus {{
    border: 2px solid {p.accent_text};
    padding: 6px 13px;
}}
QPushButton#approveButton {{
    background-color: {p.accent};
    border-color: {p.accent};
    color: {p.on_accent};
    font-weight: 700;
}}
QPushButton#approveButton:hover {{
    background-color: {p.accent_hover};
}}
QPushButton#approveButton[danger="true"] {{
    background-color: {p.danger_button};
    border-color: {p.danger_button};
    color: #ffffff;
}}
QPushButton#approveButton[danger="true"]:hover {{
    background-color: {p.danger_button_hover};
}}
QLabel#riskBadge {{
    border-radius: 11px;
    padding: 3px 10px;
    font-size: {FS_XS}px;
    font-weight: 700;
}}
QLabel#riskBadge[level="1"] {{
    color: {p.on_accent};
    background-color: {p.accent};
}}
QLabel#riskBadge[level="2"] {{
    color: #ffffff;
    background-color: {p.danger_button};
}}
QLabel#dialogTitle {{
    font-size: 16px;
    font-weight: 700;
}}
QLabel#dialogHint {{
    color: {p.text_2};
    font-size: {FS_SM}px;
}}
QLabel#elevationWarning {{
    color: {p.warning};
    background-color: {p.warning_soft};
    border: 1px solid {p.warning_border};
    border-radius: 8px;
    padding: 7px 10px;
    font-size: {FS_SM}px;
}}
#confirmDetails {{
    background-color: {p.code_bg};
    border: 1px solid {p.border};
    border-radius: {RADIUS_MD}px;
}}
QTextBrowser#confirmText {{
    background: transparent;
    border: none;
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
"""
