"""Rendu du markdown de l'assistant pour ``QTextBrowser`` (moteur de texte Qt).

Deux étapes :

* :func:`split_markdown` sépare la narration en segments « texte » et « code »
  (blocs délimités par trois accents graves ou tildes), pour afficher chaque bloc de
  code dans sa propre carte (langage, bouton Copier). Fonction pure, sans Qt.
* :func:`render_markdown` remplit un ``QTextDocument`` avec un segment de texte et
  le met en forme (code en ligne, liens, tableaux, citations, interligne).

Sécurité : le texte de l'assistant peut reprendre des données non fiables (contenu
d'un fichier lu). Le HTML brut est **désactivé** (``MarkdownNoHTML`` : une balise
reste du texte, elle ne met jamais en forme l'interface) et une image markdown est
remplacée par une mention en texte. Aucun script n'existe dans ce moteur, les liens
ne s'ouvrent pas et le widget ne charge aucune ressource (cf. ``ui/chat_view.py``,
docs/SECURITY.md §2.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtGui import (
    QColor,
    QFont,
    QTextBlock,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextFrameFormat,
    QTextTable,
)

from ui.styles import FONT_MONO, FS_SM, PALETTE

_FEATURES = (
    QTextDocument.MarkdownFeature.MarkdownDialectGitHub
    | QTextDocument.MarkdownFeature.MarkdownNoHTML
)

# Interligne des paragraphes, en % de la hauteur de ligne naturelle de la police
# (≈ 1,6 × la taille du texte, proche du line-height 1.7 de My_AI).
_LINE_HEIGHT = 118

# Ouverture d'un bloc de code : 3+ accents graves (sans accent grave dans l'info)
# ou 3+ tildes, indentés de 3 espaces au plus (CommonMark).
_FENCE_OPEN = re.compile(r"^ {0,3}(?P<fence>`{3,}(?=[^`]*$)|~{3,})(?P<info>.*)$")


@dataclass(frozen=True)
class Segment:
    """Morceau de narration : ``kind`` vaut ``"text"`` (markdown) ou ``"code"``."""

    kind: str
    text: str
    lang: str = ""


def split_markdown(text: str) -> list[Segment]:
    """Découpe ``text`` en segments texte / bloc de code, dans l'ordre.

    Un bloc encore ouvert (réponse en cours de streaming) devient un segment de code
    jusqu'à la fin du texte. Les segments de texte vides sont omis.
    """
    segments: list[Segment] = []
    lines: list[str] = []
    fence: str | None = None
    lang = ""
    for line in text.split("\n"):
        if fence is None:
            match = _FENCE_OPEN.match(line)
            if match is None:
                lines.append(line)
                continue
            _flush_text(segments, lines)
            lines = []
            fence = match.group("fence")
            lang = (match.group("info").split() or [""])[0]
            continue
        if _closes(line, fence):
            segments.append(Segment("code", "\n".join(lines), lang))
            lines = []
            fence = None
        else:
            lines.append(line)
    if fence is not None:
        segments.append(Segment("code", "\n".join(lines).rstrip("\n"), lang))
    else:
        _flush_text(segments, lines)
    return segments


def _closes(line: str, fence: str) -> bool:
    indent = len(line) - len(line.lstrip(" "))
    stripped = line.strip()
    return indent <= 3 and len(stripped) >= len(fence) and set(stripped) == {fence[0]}


def _flush_text(segments: list[Segment], lines: list[str]) -> None:
    text = "\n".join(lines).strip("\n")
    if text.strip():
        segments.append(Segment("text", text))


def mono_font(pixel_size: int = FS_SM) -> QFont:
    """Police à chasse fixe de l'interface (code, sorties d'outils)."""
    font = QFont()
    font.setFamilies(FONT_MONO)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixel_size)
    return font


def render_markdown(document: QTextDocument, text: str) -> None:
    """Remplit ``document`` avec le markdown ``text`` (sans HTML) et le met en forme."""
    document.setMarkdown(text, _FEATURES)
    _style_blocks(document)
    _style_tables(document)


def _merge_range(
    document: QTextDocument, start: int, end: int, fmt: QTextCharFormat
) -> None:
    cursor = QTextCursor(document)
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    cursor.mergeCharFormat(fmt)


def _style_blocks(document: QTextDocument) -> None:
    """Interligne, code (en ligne et en bloc), liens, citations, images."""
    p = PALETTE
    inline_code = QTextCharFormat()
    inline_code.setFont(
        mono_font(),
        QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesSpecifiedOnly,
    )
    inline_code.setForeground(QColor(p.accent_text))
    link = QTextCharFormat()
    link.setForeground(QColor(p.accent_text))
    link.setFontUnderline(True)
    quote = QTextCharFormat()
    quote.setForeground(QColor(p.text_2))
    code_block = QTextCharFormat()
    code_block.setFont(
        mono_font(),
        QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesSpecifiedOnly,
    )
    code_block.setForeground(QColor(p.code_text))

    char_ranges: list[tuple[int, int, QTextCharFormat]] = []
    images: list[tuple[int, int, str]] = []
    block = document.begin()
    while block.isValid():
        block_fmt = block.blockFormat()
        cursor = QTextCursor(block)
        extra = QTextBlockFormat()
        if block_fmt.nonBreakableLines():
            # Bloc de code resté dans le texte (indenté, dans une liste…) : il se
            # replie au lieu de déborder (la vue n'a pas de défilement horizontal).
            extra.setBackground(QColor(p.code_bg))
            extra.setNonBreakableLines(False)
            char_ranges.append(
                (block.position(), block.position() + block.length() - 1, code_block)
            )
        else:
            extra.setLineHeight(
                _LINE_HEIGHT, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
            )
            extra.setTopMargin(0)
            extra.setBottomMargin(_spacing_after(block))
            if block_fmt.headingLevel() > 0:
                extra.setTopMargin(8)
                extra.setBottomMargin(4)
            if block_fmt.intProperty(QTextFormat.Property.BlockQuoteLevel) > 0:
                char_ranges.append(
                    (block.position(), block.position() + block.length() - 1, quote)
                )
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    start = fragment.position()
                    end = start + fragment.length()
                    if fmt.isImageFormat():
                        images.append((start, end, fmt.toImageFormat().name()))
                    elif fmt.isAnchor():
                        char_ranges.append((start, end, link))
                    elif fmt.fontFixedPitch():
                        char_ranges.append((start, end, inline_code))
                it += 1
        cursor.mergeBlockFormat(extra)
        block = block.next()

    for start, end, fmt in char_ranges:
        _merge_range(document, start, end, fmt)
    # Images : jamais chargées (ni fichier local ni réseau), remplacées par du texte.
    # En partant de la fin pour que les positions restent valables.
    for start, end, name in reversed(images):
        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(f"[image non affichée : {name}]", quote)


def _spacing_after(block: QTextBlock) -> int:
    """Espace sous un bloc : serré entre deux éléments d'une liste, aéré sinon."""
    following = block.next()
    if not following.isValid():
        return 0
    if block.textList() is not None and following.textList() is not None:
        return 3
    return 10


def _style_tables(document: QTextDocument) -> None:
    """Tableaux : bordure fine, cellules aérées, ligne d'en-tête teintée."""
    p = PALETTE
    for frame in document.rootFrame().childFrames():
        if not isinstance(frame, QTextTable):
            continue
        fmt = frame.format()
        fmt.setBorder(1)
        fmt.setBorderBrush(QColor(p.border_strong))
        fmt.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
        fmt.setBorderCollapse(True)
        fmt.setCellPadding(6)
        fmt.setCellSpacing(0)
        frame.setFormat(fmt)
        for column in range(frame.columns()):
            cell = frame.cellAt(0, column)
            cell_fmt = cell.format()
            cell_fmt.setBackground(QColor(p.surface_hover))
            cell.setFormat(cell_fmt)
