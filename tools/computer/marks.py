"""Capture annotée (« Set-of-Marks ») : chaque élément encadré avec son numéro.

Quand le modèle reçoit l'image (modèle multimodal sur carte graphique, ou Claude),
il voit les mêmes numéros que dans la liste texte : il désigne un élément par son
numéro au lieu de deviner des coordonnées, ce qui rend fiable un petit modèle local.

L'image est réduite à :data:`MAX_WIDTH` pixels de large avant le dessin (étiquettes
lisibles à la taille vue par le modèle, ~1 000 tokens d'image), puis encodée en
JPEG. Pillow est importé paresseusement.
"""

from __future__ import annotations

import io
from typing import Any

from tools.computer.screen import Element

MAX_WIDTH = 1280
JPEG_QUALITY = 80
# Couleurs vives alternées : deux éléments voisins ne se confondent pas.
_COLORS = ("#ff8c00", "#e0245e", "#1d9bf0", "#17bf63", "#9b51e0", "#d4a20b")
_FONT_SIZE = 11
_FONT_NAMES = ("DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf", "arialbd.ttf")


def _font(size: int) -> Any:
    from PIL import ImageFont  # pylint: disable=import-outside-toplevel

    for name in _FONT_NAMES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def annotate(
    image: Any, elements: list[Element], *, max_width: int = MAX_WIDTH
) -> tuple[bytes, tuple[int, int]]:
    """Image JPEG réduite, éléments encadrés et numérotés ; renvoie ``(jpeg, taille)``."""
    from PIL import ImageDraw  # pylint: disable=import-outside-toplevel

    scale = min(1.0, max_width / max(image.width, 1))
    if scale < 1.0:
        size = (round(image.width * scale), round(image.height * scale))
        canvas = image.resize(size)
    else:
        canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    font = _font(_FONT_SIZE)
    for number, element in enumerate(elements, start=1):
        color = _COLORS[(number - 1) % len(_COLORS)]
        rect = element.rect
        left, top = rect.x * scale, rect.y * scale
        right = (rect.x + rect.width) * scale
        bottom = (rect.y + rect.height) * scale
        draw.rectangle((left, top, right, bottom), outline=color, width=1)
        label = str(number)
        box = draw.textbbox((0, 0), label, font=font)
        text_w, text_h = box[2] - box[0] + 4, box[3] - box[1] + 2
        # Étiquette DANS le coin supérieur gauche : posée au-dessus, elle masquerait
        # le texte de l'élément précédent (listes, menus).
        draw.rectangle((left, top, left + text_w, top + text_h), fill=color)
        draw.text((left + 2, top + 1 - box[1]), label, fill="#ffffff", font=font)
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue(), (canvas.width, canvas.height)
