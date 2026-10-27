"""Combinaisons de touches : du langage du modèle à la syntaxe de ``xdotool``.

Le modèle écrit « ctrl+s », « Entrée », « Ctrl+Maj+T » ou « alt+F4 » ; ce module
renvoie la forme canonique (``ctrl+shift+t``, ``Return``…) que ``xdotool key``
comprend, ou refuse ce qu'il ne sait pas lire. La forme canonique est aussi celle
que voient la liste de blocage (``ctrl+alt+BackSpace`` tue la session graphique)
et le journal d'audit : une combinaison ne peut pas échapper aux règles par une
autre écriture (« Contrôle+Alt+Retour arrière »).

Module pur, sans dépendance.
"""

from __future__ import annotations

import unicodedata

from core.i18n import tr

# Ordre canonique des modificateurs.
MODIFIERS = ("ctrl", "alt", "shift", "super")
_MODIFIER_NAMES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctl": "ctrl",
    "controle": "ctrl",
    "strg": "ctrl",
    "alt": "alt",
    "option": "alt",
    "shift": "shift",
    "maj": "shift",
    "majuscule": "shift",
    "super": "super",
    "win": "super",
    "windows": "super",
    "meta": "super",
    "cmd": "super",
    "command": "super",
    "logo": "super",
}
_KEY_NAMES = {
    "enter": "Return",
    "entree": "Return",
    "return": "Return",
    "retour": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "echap": "Escape",
    "echappement": "Escape",
    "tab": "Tab",
    "tabulation": "Tab",
    "backspace": "BackSpace",
    "retour arriere": "BackSpace",
    "effacement arriere": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "suppr": "Delete",
    "supprimer": "Delete",
    "space": "space",
    "espace": "space",
    "up": "Up",
    "haut": "Up",
    "fleche haut": "Up",
    "down": "Down",
    "bas": "Down",
    "fleche bas": "Down",
    "left": "Left",
    "gauche": "Left",
    "fleche gauche": "Left",
    "right": "Right",
    "droite": "Right",
    "fleche droite": "Right",
    "home": "Home",
    "debut": "Home",
    "end": "End",
    "fin": "End",
    "pageup": "Prior",
    "page up": "Prior",
    "pgup": "Prior",
    "page precedente": "Prior",
    "pagedown": "Next",
    "page down": "Next",
    "pgdn": "Next",
    "page suivante": "Next",
    "insert": "Insert",
    "inser": "Insert",
    "menu": "Menu",
    "print": "Print",
    "printscreen": "Print",
    "impr": "Print",
    "impr ecran": "Print",
    "sysrq": "Sys_Req",
    "sys_req": "Sys_Req",
    "plus": "plus",
    "minus": "minus",
    "moins": "minus",
}
_PUNCTUATION = {
    "-": "minus",
    "=": "equal",
    ",": "comma",
    ".": "period",
    "/": "slash",
    ";": "semicolon",
    ":": "colon",
    "'": "apostrophe",
    "[": "bracketleft",
    "]": "bracketright",
    "\\": "backslash",
    "`": "grave",
    "*": "asterisk",
    "!": "exclam",
    "?": "question",
}
MAX_REPEAT = 20


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.strip().casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.replace("_", " ").split())


def _key(part: str, *, with_modifiers: bool) -> str:
    folded = _fold(part)
    if folded in _KEY_NAMES:
        return _KEY_NAMES[folded]
    if folded.startswith("f") and folded[1:].isdigit() and 1 <= int(folded[1:]) <= 24:
        return f"F{int(folded[1:])}"
    if len(part.strip()) == 1:
        char = part.strip()
        if char in _PUNCTUATION:
            return _PUNCTUATION[char]
        if char.isalnum():
            # Avec un modificateur, la lettre est la touche (Ctrl+S = Ctrl+s).
            return char.lower() if with_modifiers else char
    raise ValueError(tr("touche inconnue : « {key} »", key=part.strip()))


def parse_combo(text: str) -> str:
    """Forme canonique xdotool d'une combinaison (``"Ctrl+Maj+T"`` → ``"ctrl+shift+t"``).

    Une seule touche non modificatrice, au plus quatre modificateurs. Lève
    :class:`ValueError` pour une combinaison illisible.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError(tr("combinaison vide"))
    raw = text.strip()
    if raw == "+":
        return "plus"
    parts = [p for p in raw.replace(" + ", "+").split("+")]
    if raw.endswith("++"):  # « ctrl++ » : la touche plus
        parts = [*parts[:-2], "plus"]
    parts = [p for p in parts if p.strip()]
    modifiers: list[str] = []
    keys: list[str] = []
    for part in parts:
        folded = _fold(part)
        if folded in _MODIFIER_NAMES:
            modifier = _MODIFIER_NAMES[folded]
            if modifier not in modifiers:
                modifiers.append(modifier)
        else:
            keys.append(part)
    if len(keys) > 1:
        raise ValueError(
            tr(
                "une seule touche par combinaison (reçu : {keys}) ; appelez "
                "press_keys une fois par combinaison",
                keys=", ".join(keys),
            )
        )
    ordered = [m for m in MODIFIERS if m in modifiers]
    if not keys:
        if not ordered:
            raise ValueError(tr("combinaison vide"))
        return "+".join(ordered)  # un modificateur seul (ex. « super »)
    return "+".join([*ordered, _key(keys[0], with_modifiers=bool(ordered))])


def combo_parts(combo: str) -> tuple[frozenset[str], str]:
    """``(modificateurs, touche)`` d'une combinaison canonique."""
    parts = combo.split("+")
    modifiers = frozenset(p for p in parts if p in MODIFIERS)
    keys = [p for p in parts if p not in MODIFIERS]
    return modifiers, (keys[-1] if keys else "")
