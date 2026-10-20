"""Tests du rendu markdown du popup (découpage des blocs de code, mise en forme).

Le texte de l'assistant peut reprendre un contenu non fiable : on vérifie que le
HTML brut reste du texte et qu'aucune image n'est jamais chargée.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
# pylint: disable=wrong-import-position  # import après importorskip (Qt optionnel)
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtGui = pytest.importorskip("PySide6.QtGui")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ui.markdown_render import Segment, render_markdown, split_markdown
from ui.styles import PALETTE

# ---------- découpage texte / code ----------


def test_texte_seul_donne_un_segment() -> None:
    assert split_markdown("Bonjour **toi**") == [Segment("text", "Bonjour **toi**")]


def test_bloc_de_code_isole_avec_son_langage() -> None:
    text = "Tapez :\n\n```bash\nsudo pacman -S htop\n```\n\nPuis lancez-le."
    assert split_markdown(text) == [
        Segment("text", "Tapez :"),
        Segment("code", "sudo pacman -S htop", "bash"),
        Segment("text", "Puis lancez-le."),
    ]


def test_bloc_encore_ouvert_pendant_le_streaming() -> None:
    assert split_markdown("Voici :\n```python\nprint(1)\n") == [
        Segment("text", "Voici :"),
        Segment("code", "print(1)", "python"),
    ]


def test_tildes_et_fence_plus_longue() -> None:
    text = "~~~\na\n```\nb\n~~~\n````sh\nc\n````"
    assert split_markdown(text) == [
        Segment("code", "a\n```\nb", ""),
        Segment("code", "c", "sh"),
    ]


def test_bloc_indente_de_4_espaces_reste_dans_le_texte() -> None:
    text = "1. liste\n\n    ```\n    code\n    ```"
    assert [s.kind for s in split_markdown(text)] == ["text"]


def test_segments_vides_omis() -> None:
    assert split_markdown("\n\n```\nx\n```\n\n") == [Segment("code", "x", "")]
    assert split_markdown("") == []


# ---------- mise en forme du document ----------


def _doc(text: str) -> object:
    document = QtGui.QTextDocument()
    render_markdown(document, text)
    return document


def _formats(document: object) -> list[tuple[str, object]]:
    result = []
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                result.append((fragment.text(), fragment.charFormat()))
            it += 1
        block = block.next()
    return result


def test_html_brut_reste_du_texte(app: object) -> None:
    document = _doc('<b>gras ?</b> <font color="red">rouge</font>')
    assert "<b>gras ?</b>" in document.toPlainText()
    assert '<font color="red">' in document.toPlainText()


def test_image_jamais_chargee_remplacee_par_une_mention(app: object) -> None:
    document = _doc("avant ![photo](/etc/hostname) après")
    plain = document.toPlainText()
    assert "[image non affichée : /etc/hostname]" in plain
    assert all(not fmt.isImageFormat() for _, fmt in _formats(document))


def test_code_en_ligne_et_liens_en_orange(app: object) -> None:
    document = _doc("lancez `htop` ou lisez [la doc](https://example.org)")
    colors = {text: fmt.foreground().color().name() for text, fmt in _formats(document)}
    assert colors["htop"] == PALETTE.accent_text
    assert colors["la doc"] == PALETTE.accent_text


def test_tableau_mis_en_forme(app: object) -> None:
    document = _doc("| a | b |\n|---|---|\n| 1 | 2 |")
    tables = [
        f for f in document.rootFrame().childFrames() if isinstance(f, QtGui.QTextTable)
    ]
    assert len(tables) == 1
    assert tables[0].format().cellPadding() == 6
