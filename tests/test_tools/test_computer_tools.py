"""Outils du contrôle de l'ordinateur, sur un bureau scripté (sans X11).

On vérifie la résolution de la cible réelle (``normalize_args``), les revérifications
juste avant d'agir (fenêtre changée, élément déplacé), et que chaque action renvoie
la nouvelle observation (texte + capture pour le modèle).
"""

# pylint: disable=missing-function-docstring
from __future__ import annotations

from tests.test_tools.computer_fakes import (
    POPUP_PID,
    FakeBackend,
    make_element,
    make_window,
    screen_tools,
)
from tools.computer.apps import AppEntry
from tools.computer.screen import Rect
from tools.computer.tools import (
    ELEMENT_MOVED,
    NO_OBSERVATION,
    SCREEN_CHANGED,
    STOPPED,
)

SAVE = Rect(700, 400, 80, 30)
FIELD = Rect(620, 360, 600, 300)


def _run(backend: FakeBackend, tool_name: str, args: dict, *, observe: bool = True):
    tools = screen_tools(backend)
    if observe:
        backend.observe()
    tool = tools[tool_name]
    return tool.run(tool.normalize_args(args))


def _editor() -> FakeBackend:
    field = make_element("text_field", "", FIELD, states=("focused",))
    return FakeBackend(
        [make_window()],
        [make_element("button", "Enregistrer", SAVE), field],
        focused=field,
    )


# ---- observer ------------------------------------------------------------------


def test_observer_renvoie_le_texte_et_la_capture() -> None:
    backend = _editor()
    result = _run(backend, "observe_screen", {}, observe=False)
    assert result.success
    assert "DONNÉES" in result.output
    assert "[1] bouton « Enregistrer »" in result.output
    assert "[w1]" in result.output
    assert result.image == b"jpeg"


def test_observer_apres_l_arret_ne_lit_rien() -> None:
    backend = _editor()
    backend.guard_ok = False
    result = _run(backend, "observe_screen", {}, observe=False)
    assert not result.success
    assert result.output == STOPPED


# ---- cliquer -------------------------------------------------------------------


def test_clic_au_centre_de_l_element_puis_nouvelle_observation() -> None:
    backend = _editor()
    result = _run(backend, "click", {"element": 1})
    assert backend.actions == [("click", 740, 415, "left", 1)]
    assert result.success
    assert "Clic sur [1] « Enregistrer »" in result.output
    assert backend.observations == 2  # avant (numéros) et après (effet)
    assert result.image == b"jpeg"


def test_double_clic_et_clic_droit() -> None:
    backend = _editor()
    _run(backend, "click", {"element": "1", "double": True})
    _run(backend, "click", {"element": 1, "button": "right"})
    assert backend.actions == [
        ("click", 740, 415, "left", 2),
        ("click", 740, 415, "right", 1),
    ]


def test_clic_sans_observation_refuse() -> None:
    result = _run(_editor(), "click", {"element": 1}, observe=False)
    assert not result.success and result.output == NO_OBSERVATION


def test_clic_sur_un_numero_inconnu_refuse() -> None:
    backend = _editor()
    result = _run(backend, "click", {"element": 42})
    assert not result.success and "42" in result.output
    assert backend.actions == []


def test_clic_annule_si_une_autre_fenetre_est_passee_devant() -> None:
    backend = _editor()
    tools = screen_tools(backend)
    backend.observe()
    args = tools["click"].normalize_args({"element": 1})
    # Une fenêtre surgit au-dessus entre la décision et le clic (confirmation…).
    backend.windows_list.insert(
        0, make_window(2, "Alerte", rect=Rect(0, 0, 1920, 1080))
    )
    result = tools["click"].run(args)
    assert not result.success and result.output == SCREEN_CHANGED
    assert backend.actions == []


def test_clic_annule_si_l_element_accessible_a_bouge() -> None:
    backend = _editor()
    tools = screen_tools(backend)
    backend.observe()
    args = tools["click"].normalize_args({"element": 1})
    backend.accessible = [make_element("button", "Supprimer", SAVE)]
    result = tools["click"].run(args)
    assert not result.success and result.output == ELEMENT_MOVED
    assert backend.actions == []


def test_texte_lu_par_ocr_pas_relu_avant_le_clic() -> None:
    word = make_element("ocr", "Desktop", Rect(850, 450, 60, 14), source="ocr")
    backend = FakeBackend([make_window()], [word])
    backend.accessible = []
    result = _run(backend, "click", {"element": 1, "double": True})
    assert result.success
    assert backend.actions == [("click", 880, 457, "left", 2)]


def test_clic_apres_l_arret_n_agit_pas() -> None:
    backend = _editor()
    tools = screen_tools(backend)
    backend.observe()
    args = tools["click"].normalize_args({"element": 1})
    backend.guard_ok = False
    result = tools["click"].run(args)
    assert not result.success and result.output == STOPPED
    assert backend.actions == []


# ---- taper ---------------------------------------------------------------------


def test_saisie_dans_le_champ_qui_a_le_focus() -> None:
    backend = _editor()
    result = _run(backend, "type_text", {"text": "Liste de courses", "enter": True})
    assert result.success
    typed = "".join(a[1] for a in backend.actions if a[0] == "type")
    assert typed == "Liste de courses"
    assert backend.actions[-1] == ("key", "Return", 1)
    assert "puis Entrée" in result.output


def test_saisie_dans_un_element_designe_clique_d_abord() -> None:
    backend = _editor()
    _run(backend, "type_text", {"text": "bonjour", "element": 2})
    assert backend.actions[0] == ("click", 920, 510, "left", 1)
    assert backend.actions[1] == ("type", "bonjour")


def test_saisie_longue_decoupee_en_morceaux() -> None:
    backend = _editor()
    text = "é" * 50
    _run(backend, "type_text", {"text": text})
    chunks = [a[1] for a in backend.actions if a[0] == "type"]
    assert "".join(chunks) == text
    assert max(len(c) for c in chunks) <= 16


def test_caracteres_de_controle_refuses() -> None:
    backend = _editor()
    result = _run(backend, "type_text", {"text": "a\x1bb"})
    assert not result.success and "contrôle" in result.output
    assert backend.actions == []
    ok = _run(backend, "type_text", {"text": "ligne 1\nligne 2\tfin"})
    assert ok.success


def test_saisie_vide_ou_trop_longue_refusee() -> None:
    backend = _editor()
    assert not _run(backend, "type_text", {"text": ""}).success
    assert not _run(backend, "type_text", {"text": "x" * 2001}).success
    assert backend.actions == []


def test_saisie_rend_le_focus_a_la_fenetre_visee() -> None:
    backend = _editor()
    tools = screen_tools(backend)
    backend.observe()
    args = tools["type_text"].normalize_args({"text": "ok"})
    # Une confirmation de My_OS a pris le focus puis s'est fermée sans le rendre.
    other = make_window(7, "Autre", active=True, rect=Rect(0, 0, 300, 200))
    backend.windows_list = [make_window(active=False), other]
    result = tools["type_text"].run(args)
    assert result.success
    assert backend.actions[0] == ("activate", 1)
    assert backend.actions[1] == ("type", "ok")


def test_saisie_annulee_si_la_fenetre_change_en_cours() -> None:
    backend = _editor()
    tools = screen_tools(backend)
    backend.observe()
    args = tools["type_text"].normalize_args({"text": "x" * 40})
    original = backend.type_text
    calls = {"n": 0}

    def type_then_switch(text: str) -> None:
        original(text)
        calls["n"] += 1
        backend.windows_list = [
            make_window(active=False),
            make_window(9, "Intrus", rect=Rect(0, 0, 100, 100)),
        ]

    backend.type_text = type_then_switch  # type: ignore[method-assign]
    result = tools["type_text"].run(args)
    assert not result.success and result.output == SCREEN_CHANGED
    assert calls["n"] == 1


# ---- touches -------------------------------------------------------------------


def test_combinaison_canonique_et_repetition_bornee() -> None:
    backend = _editor()
    result = _run(backend, "press_keys", {"keys": "Ctrl+S"})
    _run(backend, "press_keys", {"keys": "Tab", "repeat": 500})
    assert result.success
    assert backend.actions == [("key", "ctrl+s", 1), ("key", "Tab", 20)]


def test_combinaison_illisible_refusee() -> None:
    backend = _editor()
    result = _run(backend, "press_keys", {"keys": "ctrl+a+b"})
    assert not result.success and "une seule touche" in result.output
    assert backend.actions == []


# ---- défiler, fenêtres, applications ------------------------------------------------


def test_defilement_au_centre_de_la_zone_utile() -> None:
    backend = _editor()
    result = _run(backend, "scroll", {"direction": "bas", "amount": 99})
    assert result.success
    assert backend.actions == [("scroll", 950, 565, "down", 15)]


def test_defilement_direction_inconnue() -> None:
    result = _run(_editor(), "scroll", {"direction": "diagonale"})
    assert not result.success


def test_passer_a_une_autre_fenetre() -> None:
    backend = FakeBackend(
        [make_window(), make_window(2, "myos - Thunar", app="Thunar", active=False)]
    )
    result = _run(backend, "focus_window", {"window": "w2"})
    assert result.success
    assert backend.actions == [("activate", 2)]
    assert not _run(backend, "focus_window", {"window": 9}).success


def _catalog() -> dict[str, AppEntry]:
    entry = AppEntry(
        app_id="org.xfce.mousepad",
        name="Mousepad",
        path="/usr/share/applications/org.xfce.mousepad.desktop",
        executable="mousepad",
    )
    return {"mousepad": entry}


def test_ouvrir_une_application_attend_sa_fenetre() -> None:
    backend = FakeBackend(
        [make_window(5, "myos - Thunar", app="Thunar", instance="thunar")],
        apps=_catalog(),
    )
    backend.new_window_on_launch = make_window(
        8, "Sans titre 1 - Mousepad", active=False
    )
    result = _run(backend, "open_app", {"name": "Mousepad"}, observe=False)
    assert result.success and "Mousepad ouvert" in result.output
    assert backend.actions == [("launch", "org.xfce.mousepad"), ("activate", 8)]


def test_ouvrir_une_application_sans_nouvelle_fenetre() -> None:
    backend = FakeBackend(
        [make_window(5, "myos - Thunar", app="Thunar", instance="thunar")],
        apps=_catalog(),
    )
    result = _run(backend, "open_app", {"name": "Mousepad"}, observe=False)
    assert result.success and "aucune nouvelle fenêtre" in result.output


def test_application_deja_ouverte_sa_fenetre_passe_au_premier_plan() -> None:
    # Vu en VM : « Dans Firefox, remplis le formulaire… » relançait Firefox, et une
    # seconde fenêtre (page d'accueil) recouvrait le bureau.
    backend = FakeBackend(
        [
            make_window(5, "myos - Thunar", app="Thunar", instance="thunar"),
            make_window(8, "liste.txt - Mousepad", active=False),
        ],
        apps=_catalog(),
    )
    result = _run(backend, "open_app", {"name": "Mousepad"}, observe=False)
    assert backend.actions == [("activate", 8)]  # aucun lancement
    assert result.success
    assert "Mousepad est déjà ouvert" in result.output
    assert "« liste.txt - Mousepad »" in result.output


def test_application_deja_au_premier_plan_rien_a_faire() -> None:
    backend = FakeBackend([make_window()], apps=_catalog())
    result = _run(backend, "open_app", {"name": "Mousepad"}, observe=False)
    assert backend.actions == []
    assert result.success and "déjà ouvert" in result.output


def test_une_fenetre_de_my_os_n_est_jamais_reprise_par_open_app() -> None:
    backend = FakeBackend([make_window(9, "My_OS", pid=POPUP_PID)], apps=_catalog())
    backend.new_window_on_launch = make_window(10, "Sans titre 1 - Mousepad")
    _run(backend, "open_app", {"name": "Mousepad"}, observe=False)
    assert backend.actions[0] == ("launch", "org.xfce.mousepad")


def test_un_lanceur_generique_ne_designe_aucune_fenetre() -> None:
    # Exec=python3 outil.py : une autre fenêtre « python3 » n'est pas cette application.
    entry = AppEntry(
        app_id="outil",
        name="Outil",
        path="/usr/share/applications/outil.desktop",
        executable="python3",
    )
    backend = FakeBackend(
        [make_window(4, "Autre", app="python3", instance="python3")],
        apps={"outil": entry},
    )
    _run(backend, "open_app", {"name": "Outil"}, observe=False)
    assert backend.actions[0] == ("launch", "outil")


def test_ouvrir_une_application_inconnue_liste_les_installees() -> None:
    backend = FakeBackend([make_window()], apps=_catalog())
    result = _run(backend, "open_app", {"name": "Photoshop"}, observe=False)
    assert not result.success
    assert "Mousepad" in result.output
    assert backend.actions == []
