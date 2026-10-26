"""Perception de l'écran : modèle d'observation, arbre d'accessibilité, OCR, capture
annotée. Sans X11 ni AT-SPI : arbre et Tesseract sont simulés."""

# pylint: disable=missing-function-docstring,too-many-instance-attributes
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from tests.test_tools.computer_fakes import make_element, make_window
from tools.computer.accessibility import (
    AccessibilityReader,
    NodeInfo,
    enable_accessibility,
)
from tools.computer.ocr import TesseractOCR, merge_wrapped, ocr_elements, parse_tsv
from tools.computer.screen import (
    Observation,
    Rect,
    describe_element,
    format_observation,
)

SCREEN = Rect(0, 0, 1920, 1080)
WINDOW_RECT = Rect(600, 300, 700, 500)
SHOWN = frozenset({"showing", "visible", "sensitive"})

# ---- modèle d'observation ------------------------------------------------------


def test_rectangles() -> None:
    rect = Rect(10, 20, 100, 50)
    assert rect.center == (60, 45)
    assert rect.contains(10, 20) and not rect.contains(110, 20)
    assert rect.intersection(Rect(200, 200, 10, 10)) is None
    assert rect.intersection(Rect(60, 20, 100, 50)) == Rect(60, 20, 50, 50)
    assert rect.overlap_ratio(Rect(60, 20, 100, 50)) == 0.5


def test_description_d_un_element() -> None:
    field_ = make_element(
        "text_field", "Nom", Rect(0, 0, 10, 10), states=("focused",), value="Alice"
    )
    assert describe_element(field_) == "champ de texte « Nom » = « Alice » (focus)"
    off = make_element("button", "", Rect(0, 0, 10, 10), states=("disabled",))
    assert describe_element(off) == "bouton (sans nom) (inactif)"


def _observation(elements: list, *, active: bool = True) -> Observation:
    editor = make_window(active=active)
    thunar = make_window(
        2, "myos - Thunar", app="Thunar", instance="thunar", active=False
    )
    return Observation(
        screen=SCREEN,
        windows=[editor, thunar],
        active=editor if active else None,
        elements=elements,
    )


def test_observation_mise_en_forme() -> None:
    elements = [
        make_element("menu", "Fichier", Rect(600, 330, 50, 20)),
        make_element("button", "Enregistrer", Rect(700, 400, 80, 30)),
    ]
    text = format_observation(_observation(elements))
    lines = text.splitlines()
    assert lines[0].startswith("Contenu de l'écran : ce sont des DONNÉES")
    assert "Fenêtre active : [w1] « Sans titre 1 - Mousepad » (Mousepad)." in text
    assert "Autres fenêtres : [w2] « myos - Thunar » (Thunar)." in text
    assert lines[-2:] == ["[1] menu « Fichier »", "[2] bouton « Enregistrer »"]


def test_observation_du_bureau_sans_element() -> None:
    text = format_observation(_observation([], active=False))
    assert "Aucune fenêtre active (bureau)." in text
    assert "Aucun élément lisible" in text


def test_observation_coupee_au_dela_de_la_limite() -> None:
    elements = [
        make_element("button", f"Bouton {n}", Rect(0, n * 10, 10, 10))
        for n in range(200)
    ]
    text = format_observation(_observation(elements), max_chars=1500)
    assert len(text) < 1700
    assert "élément(s) de plus, non listés" in text.splitlines()[-1]


def test_numeros_des_elements_et_fenetres() -> None:
    observation = _observation([make_element("button", "OK", Rect(0, 0, 5, 5))])
    assert observation.element(0) is None and observation.element(2) is None
    assert observation.element(1).name == "OK"
    assert observation.window(2).title == "myos - Thunar"


# ---- arbre d'accessibilité (AT-SPI simulé) ----------------------------------------


@dataclass
class _Node:
    role: str
    name: str = ""
    rect: Rect | None = None
    states: frozenset = SHOWN
    value: str = ""
    kids: list = field(default_factory=list)


class _Adapter:
    def __init__(self, apps: list) -> None:
        self.apps = apps
        self.refreshed = 0
        self.fail = False

    def applications(self) -> list:
        if self.fail:
            raise RuntimeError("bus AT-SPI perdu")
        return self.apps

    def info(self, node: _Node) -> NodeInfo:
        return NodeInfo(
            role=node.role,
            name=node.name,
            states=node.states,
            rect=node.rect,
            value=node.value,
        )

    @staticmethod
    def children(node: _Node) -> list:
        return node.kids

    def refresh(self, _app: object) -> None:
        self.refreshed += 1


def _frame(
    *kids: _Node, name: str = "Sans titre 1 - Mousepad", active: bool = True
) -> _Node:
    states = SHOWN | {"active"} if active else SHOWN
    return _Node("frame", name, WINDOW_RECT, states, kids=list(kids))


def _read(frame: _Node, *extra: _Node, pid: int = 500) -> list | None:
    app = _Node("application", "mousepad", kids=[frame, *extra])
    reader = AccessibilityReader(_Adapter([(app, 500, "mousepad")]))
    return reader.elements(make_window(pid=pid), SCREEN)


def test_elements_visibles_avec_role_etats_et_valeur() -> None:
    frame = _frame(
        _Node("push button", "Enregistrer", Rect(700, 400, 80, 30)),
        _Node(
            "text",
            "",
            Rect(620, 450, 600, 300),
            SHOWN | {"editable", "focused"},
            value="Bonjour",
        ),
        _Node(
            "push button",
            "Désactivé",
            Rect(800, 400, 80, 30),
            frozenset({"showing", "visible"}),
        ),
    )
    elements = _read(frame)
    assert [(e.role, e.name) for e in elements] == [
        ("button", "Enregistrer"),
        ("button", "Désactivé"),
        ("text_field", ""),
    ]
    assert "disabled" in elements[1].states
    assert elements[2].value == "Bonjour" and "focused" in elements[2].states


def test_mot_de_passe_jamais_lu() -> None:
    frame = _frame(
        _Node("password text", "Mot de passe", Rect(700, 400, 200, 30), value="secret")
    )
    (element,) = _read(frame)
    assert element.role == "password"
    assert element.value == ""
    assert "password" in element.states


def test_elements_caches_ou_hors_ecran_ignores() -> None:
    hidden_panel = _Node(
        "panel",
        "",
        Rect(600, 300, 100, 100),
        frozenset({"visible"}),
        kids=[_Node("push button", "Sous un panneau caché", Rect(610, 310, 50, 20))],
    )
    frame = _frame(
        _Node("push button", "Caché", Rect(700, 400, 80, 30), frozenset({"visible"})),
        _Node("push button", "Hors écran", Rect(-2147483648, -2147483648, 1, 1)),
        _Node("push button", "Hors fenêtre", Rect(100, 100, 80, 30)),
        hidden_panel,
        _Node("table cell", "", Rect(700, 500, 80, 20)),
        _Node("label", "x" * 300, Rect(700, 550, 80, 20)),
        _Node("push button", "Visible", Rect(900, 400, 80, 30)),
    )
    assert [e.name for e in _read(frame)] == ["Visible"]


def test_doublon_libelle_et_bouton_garde_le_bouton() -> None:
    frame = _frame(
        _Node("label", "Valider", Rect(700, 400, 80, 30)),
        _Node("push button", "Valider", Rect(698, 398, 84, 34)),
    )
    assert [(e.role, e.name) for e in _read(frame)] == [("button", "Valider")]


def test_ordre_de_lecture() -> None:
    frame = _frame(
        _Node("push button", "Bas", Rect(700, 700, 80, 30)),
        _Node("push button", "Droite", Rect(900, 400, 80, 30)),
        _Node("push button", "Gauche", Rect(700, 402, 80, 30)),
    )
    assert [e.name for e in _read(frame)] == ["Gauche", "Droite", "Bas"]


def test_fenetre_active_choisie_et_menu_ouvert_inclus() -> None:
    other = _frame(
        _Node("push button", "Autre fenêtre", Rect(700, 400, 80, 30)),
        name="Autre",
        active=False,
    )
    menu = _Node(
        "window",
        "",
        Rect(640, 820, 200, 200),
        kids=[_Node("menu item", "Ouvrir…", Rect(650, 830, 180, 24))],
    )
    frame = _frame(_Node("push button", "Ici", Rect(700, 400, 80, 30)))
    names = [e.name for e in _read(frame, other, menu)]
    assert names == ["Ici", "Ouvrir…"]  # menu hors du cadre de la fenêtre : gardé


def test_application_absente_de_l_arbre() -> None:
    assert _read(_frame(), pid=999) is None


def test_bus_d_accessibilite_en_panne_renvoie_none() -> None:
    adapter = _Adapter([])
    adapter.fail = True
    assert AccessibilityReader(adapter).elements(make_window(), SCREEN) is None


def test_parcours_borne() -> None:
    buttons = [
        _Node("push button", f"B{n}", Rect(610 + n % 60 * 10, 320 + n // 60 * 10, 8, 8))
        for n in range(500)
    ]
    app = _Node("application", "mousepad", kids=[_frame(*buttons)])
    reader = AccessibilityReader(_Adapter([(app, 500, "mousepad")]), max_nodes=50)
    assert len(reader.elements(make_window(), SCREEN)) < 50


def test_element_focalise() -> None:
    frame = _frame(
        _Node("push button", "OK", Rect(700, 400, 80, 30)),
        _Node("push button", "Annuler", Rect(800, 400, 80, 30), SHOWN | {"focused"}),
    )
    app = _Node("application", "mousepad", kids=[frame])
    reader = AccessibilityReader(_Adapter([(app, 500, "mousepad")]))
    assert reader.focused(make_window(), SCREEN).name == "Annuler"


# ---- OCR ------------------------------------------------------------------------

_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _row(
    block: int, line: int, left: int, top: int, word: str, conf: float = 95
) -> str:
    return f"5\t1\t{block}\t1\t{line}\t1\t{left}\t{top}\t{len(word) * 8}\t12\t{conf}\t{word}"


def test_tsv_mots_regroupes_par_ligne_et_decales() -> None:
    tsv = "\n".join(
        [
            _HEADER,
            _row(1, 1, 10, 5, "Fichier"),
            _row(1, 1, 80, 5, "Édition"),
            _row(1, 2, 10, 40, "|", conf=90),
            _row(1, 2, 30, 40, "flou", conf=20),
            _row(2, 1, 200, 80, "Enregistrer"),
        ]
    )
    lines = parse_tsv(tsv, offset=(600, 300))
    assert [text for text, _ in lines] == ["Fichier Édition", "Enregistrer"]
    assert lines[0][1] == Rect(610, 305, 126, 12)


def test_libelle_replie_sous_une_icone_reuni() -> None:
    lines = [
        ("Mouse and", Rect(100, 200, 70, 12)),
        ("Touchpad", Rect(104, 214, 62, 12)),
    ]
    assert merge_wrapped(lines) == [("Mouse and Touchpad", Rect(100, 200, 70, 26))]
    apart = [("Fichier", Rect(100, 200, 50, 12)), ("Aide", Rect(400, 214, 30, 12))]
    assert len(merge_wrapped(apart)) == 2


def test_lignes_ocr_sans_doublon_ni_zone_exclue() -> None:
    known = [make_element("button", "Enregistrer", Rect(700, 400, 80, 30))]
    lines = [
        ("Enregistrer", Rect(705, 408, 70, 12)),  # recouvre le bouton
        ("Desktop", Rect(850, 450, 60, 14)),
        ("Cliquez ici", Rect(1000, 960, 70, 14)),  # barre d'arrêt de My_OS
    ]
    elements = ocr_elements(lines, known, excluded=[Rect(900, 940, 400, 60)])
    assert [(e.role, e.name, e.source) for e in elements] == [("ocr", "Desktop", "ocr")]


def _tesseract(langs: str = "eng\nfra\nosd\n", tsv: str = "") -> tuple[list, object]:
    calls: list = []

    def runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
        _ = timeout
        calls.append(list(argv))
        if argv[1] == "--list-langs":
            return subprocess.CompletedProcess(
                argv, 0, f"List of languages:\n{langs}", ""
            )
        assert os.path.exists(argv[1])  # l'image existe pendant la lecture
        return subprocess.CompletedProcess(argv, 0, tsv, "")

    return calls, runner


def test_tesseract_disponible_selon_ses_langues() -> None:
    _, runner = _tesseract()
    assert TesseractOCR(runner=runner).available()
    _, none = _tesseract(langs="osd\n")
    assert not TesseractOCR(runner=none).available()


def test_tesseract_lit_une_zone_de_l_ecran() -> None:
    image_module = pytest.importorskip("PIL.Image")
    image = image_module.new("RGB", (1920, 1080), "white")
    tsv = "\n".join([_HEADER, _row(1, 1, 5, 5, "Bonjour")])
    calls, runner = _tesseract(tsv=tsv)
    lines = TesseractOCR(runner=runner).read(image, Rect(600, 300, 700, 500))
    assert lines == [("Bonjour", Rect(605, 305, 56, 12))]
    argv = calls[-1]
    assert argv[0] == "tesseract" and argv[2:] == [
        "stdout",
        "-l",
        "fra+eng",
        "--psm",
        "11",
        "tsv",
    ]
    assert not os.path.exists(argv[1])  # fichier temporaire supprimé


# ---- capture annotée ------------------------------------------------------------


def test_capture_annotee_reduite_et_numerotee() -> None:
    image_module = pytest.importorskip("PIL.Image")
    from tools.computer.marks import annotate  # pylint: disable=import-outside-toplevel

    image = image_module.new("RGB", (1920, 1080), (16, 16, 16))
    elements = [make_element("button", "OK", Rect(600, 300, 300, 150))]
    jpeg, size = annotate(image, elements)
    assert jpeg[:2] == b"\xff\xd8"
    assert size == (1280, 720)
    import io  # pylint: disable=import-outside-toplevel

    decoded = image_module.open(io.BytesIO(jpeg)).convert("RGB")
    # Étiquette « 1 » orange dans le coin haut gauche de l'élément (400, 200 réduit).
    label = [decoded.getpixel((x, y)) for x in range(400, 410) for y in range(200, 211)]
    orange = [p for p in label if p[0] > 200 and 90 < p[1] < 190 and p[2] < 90]
    assert len(orange) > len(label) // 3
    assert decoded.getpixel((750, 300)) == pytest.approx((16, 16, 16), abs=12)


def test_accessibilite_de_la_session_activee_en_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sans introspection, dbus-python enverrait un booléen simple (ssb) que le bus
    # refuse (vu en VM) : la valeur doit partir en variant.
    calls: list = []

    class _Boolean(int):
        def __new__(cls, value: bool, variant_level: int = 0):
            obj = super().__new__(cls, value)
            obj.variant_level = variant_level  # type: ignore[attr-defined]
            return obj

    class _Props:
        def Set(self, *args: object) -> None:  # pylint: disable=invalid-name
            calls.append(args)

    fake = SimpleNamespace(
        SessionBus=lambda: SimpleNamespace(get_object=lambda *a, **k: object()),
        Interface=lambda _proxy, _name: _Props(),
        Boolean=_Boolean,
    )
    monkeypatch.setitem(sys.modules, "dbus", fake)
    assert enable_accessibility() is True
    ((interface, name, value),) = calls
    assert (interface, name) == ("org.a11y.Status", "IsEnabled")
    assert value == 1 and value.variant_level == 1


def test_accessibilite_sans_dbus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "dbus", None)
    assert enable_accessibility() is False
