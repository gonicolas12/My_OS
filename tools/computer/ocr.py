"""Texte lu à l'écran par OCR (Tesseract), là où l'accessibilité ne dit rien.

Complément de l'arbre d'accessibilité : certaines zones n'y exposent aucun
élément (vue des fichiers de Thunar, icônes du bureau, applications sans
accessibilité, jeux). Tesseract lit le texte de la fenêtre active ; chaque ligne
qui ne recouvre aucun élément déjà connu devient un élément « texte lu », cliquable
par son numéro.

Mesuré en VM (fenêtre de 650×514, processeur) : 0,3 s. ``tesseract`` est lancé
sans shell via :func:`core.elevation.run_command` ; l'image passe par un fichier
d'un dossier temporaire privé (0700), supprimé aussitôt. Le texte lu est une
DONNÉE (cf. docs/SECURITY.md §2.2).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from typing import Any

from core.elevation import run_command
from core.logger import get_logger
from tools.computer.screen import Element, Rect

_log = get_logger("myosd.computer.ocr")

# Confiance minimale d'un mot (0-100) : en dessous, ce sont des icônes lues comme
# des lettres (« « », « | », « = »).
MIN_CONFIDENCE = 60.0
_TIMEOUT_S = 20.0
# Langues préférées, dans l'ordre (paquets tesseract-data-*).
_PREFERRED_LANGS = ("fra", "eng")
# Une ligne lue recouvrant un élément accessible à plus de cette part est un doublon.
_OVERLAP_DUPLICATE = 0.4
# Les images sombres (texte clair sur fond sombre) sont inversées avant lecture.
_DARK_MEAN = 110


def parse_tsv(
    tsv: str, *, offset: tuple[int, int] = (0, 0), min_conf: float = MIN_CONFIDENCE
) -> list[tuple[str, Rect]]:
    """Lignes de texte (texte, rectangle d'écran) d'une sortie TSV de Tesseract.

    Les mots d'une même ligne (bloc, paragraphe, ligne) sont réunis ; les mots peu
    sûrs ou sans lettre ni chiffre sont écartés. ``offset`` décale les rectangles
    de l'image vers l'écran.
    """
    lines: dict[tuple[str, str, str], list[tuple[str, int, int, int, int]]] = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) != 12 or not cols[11].strip():
            continue
        try:
            conf = float(cols[10])
            left, top, width, height = (int(v) for v in cols[6:10])
        except ValueError:
            continue
        word = cols[11].strip()
        if conf < min_conf or not any(ch.isalnum() for ch in word):
            continue
        key = (cols[2], cols[3], cols[4])  # bloc, paragraphe, ligne
        lines.setdefault(key, []).append((word, left, top, width, height))
    result = []
    for words in lines.values():
        text = " ".join(w[0] for w in words)
        if len(text) < 2 and not text.isdigit():
            continue
        left = min(w[1] for w in words)
        top = min(w[2] for w in words)
        right = max(w[1] + w[3] for w in words)
        bottom = max(w[2] + w[4] for w in words)
        result.append(
            (text, Rect(left + offset[0], top + offset[1], right - left, bottom - top))
        )
    return merge_wrapped(result)


def merge_wrapped(lines: list[tuple[str, Rect]]) -> list[tuple[str, Rect]]:
    """Réunit les libellés repliés sous une icône (« Mouse and » + « Touchpad »).

    Deux lignes courtes centrées l'une sous l'autre, sans espace notable entre
    elles, forment un seul élément (grilles d'icônes, bureau).
    """
    ordered = sorted(lines, key=lambda line: (line[1].y, line[1].x))
    merged: list[tuple[str, Rect]] = []
    for text, rect in ordered:
        for index, (prev_text, prev) in enumerate(merged):
            gap = rect.y - (prev.y + prev.height)
            centered = abs(rect.center[0] - prev.center[0]) <= max(12, prev.width // 4)
            short = len(prev_text) <= 24 and len(text) <= 24
            if short and centered and 0 <= gap <= prev.height * 0.8:
                left, top = min(prev.x, rect.x), prev.y
                right = max(prev.x + prev.width, rect.x + rect.width)
                bottom = rect.y + rect.height
                merged[index] = (
                    f"{prev_text} {text}",
                    Rect(left, top, right - left, bottom - top),
                )
                break
        else:
            merged.append((text, rect))
    return merged


class TesseractOCR:
    """Lecture du texte d'une zone de l'écran par Tesseract (si installé)."""

    def __init__(self, runner: Callable[..., Any] | None = None) -> None:
        self._runner = runner
        self._langs: str | None = None
        self._checked = False

    def available(self) -> bool:
        """``True`` si ``tesseract`` est installé avec au moins une langue utile."""
        return bool(self._languages())

    def _languages(self) -> str:
        if not self._checked:
            self._checked = True
            result = run_command(
                ["tesseract", "--list-langs"], timeout=10.0, runner=self._runner
            )
            found = set(result.stdout.split()) if result.ok else set()
            langs = [lang for lang in _PREFERRED_LANGS if lang in found]
            self._langs = "+".join(langs)
            if not self._langs:
                _log.info("OCR indisponible (tesseract ou ses langues absents)")
        return self._langs or ""

    def read(self, image: Any, region: Rect) -> list[tuple[str, Rect]]:
        """Lignes lues dans ``region`` de ``image`` (capture Pillow de l'écran)."""
        langs = self._languages()
        if not langs or region.area <= 0:
            return []
        crop = image.crop(
            (region.x, region.y, region.x + region.width, region.y + region.height)
        ).convert("L")
        if _mean(crop) < _DARK_MEAN:
            from PIL import ImageOps  # pylint: disable=import-outside-toplevel

            crop = ImageOps.invert(crop)
        with tempfile.TemporaryDirectory(prefix="myos-ocr-") as folder:
            os.chmod(folder, 0o700)
            path = os.path.join(folder, "zone.png")
            crop.save(path, format="PNG")
            # --psm 11 : texte épars (une interface n'est pas une page de livre).
            result = run_command(
                ["tesseract", path, "stdout", "-l", langs, "--psm", "11", "tsv"],
                timeout=_TIMEOUT_S,
                runner=self._runner,
            )
        if not result.ok:
            _log.warning("tesseract a échoué : %s", result.stderr.strip()[:200])
            return []
        return parse_tsv(result.stdout, offset=(region.x, region.y))


def _mean(gray: Any) -> float:
    histogram = gray.histogram()
    total = sum(histogram)
    return sum(i * n for i, n in enumerate(histogram)) / total if total else 255.0


def _fold(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _same_row(rect: Rect, other: Rect) -> bool:
    """Même ligne visuelle, à moins de 80 px (icône + libellé d'une même rangée)."""
    middle = rect.y + rect.height // 2
    near = other.x - 80 <= rect.x + rect.width and rect.x <= other.x + other.width + 80
    return other.y <= middle <= other.y + other.height and near


def _repeats(text: str, rect: Rect, element: Element) -> bool:
    """La ligne répète le nom de ``element`` sur la même rangée."""
    folded = _fold(text)
    known = _fold(element.name) or _fold(element.value)
    return bool(folded) and folded in known and _same_row(rect, element.rect)


def _covered(rect: Rect, known: list[Element]) -> float:
    """Part de ``rect`` recouverte par les éléments connus (plusieurs menus d'une
    même ligne lue d'un bloc, par exemple)."""
    area = sum(
        inter.area
        for inter in (rect.intersection(e.rect) for e in known)
        if inter is not None
    )
    return min(area / rect.area, 1.0) if rect.area else 1.0


def ocr_elements(
    lines: list[tuple[str, Rect]],
    known: list[Element],
    *,
    excluded: list[Rect] | None = None,
) -> list[Element]:
    """Éléments « texte lu » des lignes qui ne doublonnent aucun élément connu.

    Une ligne recouverte par des éléments accessibles (leur libellé, le texte d'un
    champ déjà lu, une rangée de menus), ou qui répète le nom d'un élément sur la
    même rangée (la cellule accessible ne couvre parfois que le texte, pas
    l'icône), n'apporte rien : elle est écartée. Une ligne dont le centre tombe dans
    une zone ``excluded`` (la barre d'arrêt de My_OS) n'est pas le contenu de
    l'écran : écartée aussi.
    """
    result = []
    for text, rect in lines:
        if any(zone.contains(*rect.center) for zone in excluded or []):
            continue
        if _covered(rect, known) > _OVERLAP_DUPLICATE:
            continue
        if any(_repeats(text, rect, element) for element in known):
            continue
        result.append(Element(role="ocr", name=text, rect=rect, source="ocr"))
    return result
