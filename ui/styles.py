"""Thème visuel du popup (esprit My_AI : sombre, accent orange).

Feuille de style QSS appliquée au popup. Volontairement minimale au jalon 1.
"""

from __future__ import annotations

_ACCENT = "#ff8c00"

_DARK_QSS = f"""
QWidget {{
    background-color: #1e1e1e;
    color: #e6e6e6;
    font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 14px;
}}
QTextBrowser {{
    background-color: #252526;
    border: 1px solid #3a3a3a;
    border-radius: 8px;
    padding: 8px;
}}
QLineEdit {{
    background-color: #2d2d2d;
    border: 1px solid #3a3a3a;
    border-radius: 8px;
    padding: 8px;
    selection-background-color: {_ACCENT};
}}
QLineEdit:focus {{
    border: 1px solid {_ACCENT};
}}
"""


_THEMES = {"dark": _DARK_QSS}


def build_stylesheet(theme: str = "dark") -> str:
    """Renvoie la feuille de style QSS pour le thème demandé.

    Seul le thème « dark » existe au jalon 1 ; tout autre nom y retombe.
    """
    return _THEMES.get(theme, _DARK_QSS)
