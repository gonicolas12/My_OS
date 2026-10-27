"""Langue de My_OS : détection, choix, langue d'une requête, catalogue anglais.

Le catalogue est vérifié contre le code lui-même : chaque texte passé à ``tr`` /
``N_`` / ``translate`` / ``trc`` doit avoir sa traduction anglaise, avec les mêmes
valeurs ``{nom}``, et aucune traduction ne doit rester sans texte qui l'utilise.
"""

# pylint: disable=missing-function-docstring
from __future__ import annotations

import ast
import string
import threading
from pathlib import Path

import pytest

from core import i18n
from core.i18n import (
    N_,
    current_language,
    normalize_language,
    resolve_language,
    set_language,
    system_language,
    tr,
    translate,
    trc,
    use_language,
)
from core.locales import en

_ROOT = Path(__file__).resolve().parents[2]
_PACKAGES = ("core", "daemon", "models", "permissions", "tools", "ui")


# ---- détection de la langue du système ------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("fr", "fr"),
        ("FR", "fr"),
        ("fr_FR.UTF-8", "fr"),
        ("fr_CA.UTF-8@euro", "fr"),
        ("en-US", "en"),
        ("en_GB.UTF-8", "en"),
        ("de_DE.UTF-8", None),
        ("C.UTF-8", None),
        ("auto", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_normalisation_d_une_langue(value: object, expected: str | None) -> None:
    assert normalize_language(value) == expected


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"LANG": "fr_FR.UTF-8"}, "fr"),
        ({"LANG": "fr_BE.UTF-8"}, "fr"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        # Toute autre langue : l'anglais.
        ({"LANG": "de_DE.UTF-8"}, "en"),
        ({"LANG": "es_ES.UTF-8"}, "en"),
        # Locale C ou absente (ISO live, conteneur) : messages en anglais.
        ({"LANG": "C.UTF-8"}, "en"),
        ({"LANG": "POSIX"}, "en"),
        ({}, "en"),
        ({"LANG": "   "}, "en"),
        # Ordre de gettext : LC_ALL, puis LC_MESSAGES, puis LANG.
        ({"LANG": "en_US.UTF-8", "LC_MESSAGES": "fr_FR.UTF-8"}, "fr"),
        ({"LANG": "fr_FR.UTF-8", "LC_ALL": "en_US.UTF-8"}, "en"),
        # LANGUAGE : langue d'affichage choisie dans GNOME / KDE.
        ({"LANG": "en_US.UTF-8", "LANGUAGE": "fr_FR:fr:en"}, "fr"),
        ({"LANG": "fr_FR.UTF-8", "LANGUAGE": "en_GB:en"}, "en"),
        # ... ignorée quand la locale est C, comme le fait gettext.
        ({"LANG": "C.UTF-8", "LANGUAGE": "fr"}, "en"),
    ],
)
def test_langue_du_systeme(environ: dict[str, str], expected: str) -> None:
    assert system_language(environ) == expected


def test_choix_de_l_utilisateur_puis_config_puis_systeme() -> None:
    french = {"LANG": "fr_FR.UTF-8"}
    english = {"LANG": "en_US.UTF-8"}
    assert resolve_language("en", "fr", french) == "en"  # choix mémorisé
    assert resolve_language(None, "en", french) == "en"  # config
    assert resolve_language(None, "auto", french) == "fr"  # système
    assert resolve_language(None, "auto", english) == "en"
    assert resolve_language("auto", "de", english) == "en"  # valeurs inconnues ignorées


# ---- langue courante -----------------------------------------------------------


def test_langue_du_processus() -> None:
    assert current_language() == "fr"  # cf. tests/conftest.py
    set_language("en_US.UTF-8")
    assert current_language() == "en"
    with pytest.raises(ValueError):
        set_language("de")
    assert current_language() == "en"


def test_langue_d_une_requete_propre_a_son_thread() -> None:
    seen: dict[str, str] = {}

    def other_request() -> None:
        seen["autre thread"] = current_language()

    with use_language("en") as chosen:
        assert chosen == "en"
        assert current_language() == "en"
        worker = threading.Thread(target=other_request)
        worker.start()
        worker.join()
    assert seen["autre thread"] == "fr"  # langue du processus, pas de la requête
    assert current_language() == "fr"  # rétablie à la fin du bloc


def test_langue_de_requete_inconnue_garde_la_langue_courante() -> None:
    with use_language("en"):
        with use_language("klingon") as chosen:
            assert chosen == "en"
        with use_language(None):
            assert current_language() == "en"


# ---- traduction ------------------------------------------------------------------


def test_texte_traduit_et_complete() -> None:
    message = "Volume réglé à {percent} %"
    assert tr(message, percent=30) == "Volume réglé à 30 %"
    with use_language("en"):
        assert tr(message, percent=30) == "Volume set to 30%"
    assert translate(message, "en", percent=5) == "Volume set to 5%"


def test_texte_sans_traduction_reste_en_francais() -> None:
    with use_language("en"):
        assert tr("texte absent du catalogue {n}", n=1) == "texte absent du catalogue 1"


def test_texte_sans_valeur_jamais_formate() -> None:
    # Des accolades dans un texte sans valeurs ne sont pas interprétées.
    assert tr("{pas une valeur}") == "{pas une valeur}"


def test_traduction_defectueuse_retombe_sur_le_francais(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(en.MESSAGES, "Bonjour {name}", "Hello {nom}")
    with use_language("en"):
        assert tr("Bonjour {name}", name="Alice") == "Bonjour Alice"


def test_texte_selon_son_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(en.MESSAGES, "édition|Annuler", "Undo")  # menu d'édition
    monkeypatch.setitem(en.MESSAGES, "Annuler", "Cancel")  # bouton d'un dialogue
    assert trc("édition", "Annuler") == "Annuler"
    with use_language("en"):
        assert trc("édition", "Annuler") == "Undo"
        assert tr("Annuler") == "Cancel"


def test_marqueur_de_texte_a_traduire_plus_tard() -> None:
    assert N_("Le modèle réfléchit") == "Le modèle réfléchit"


# ---- catalogue anglais, vérifié contre le code ----------------------------------


def _sources() -> list[Path]:
    paths = []
    for package in _PACKAGES:
        paths += [
            p for p in (_ROOT / package).rglob("*.py") if "locales" not in p.parts
        ]
    return paths


def _calls(name_filter: set[str]) -> list[tuple[Path, ast.Call, str]]:
    """Appels des fonctions de core.i18n (toujours importées par leur nom : un
    ``painter.translate(...)`` de Qt n'en est pas un)."""
    found = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and node.args
                and isinstance(node.func, ast.Name)
                and node.func.id in name_filter
            ):
                found.append((path, node, node.func.id))
    return found


def _literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _messages_in_code() -> dict[str, str]:
    """Textes à traduire trouvés dans le code → emplacement (pour les messages)."""
    messages: dict[str, str] = {}
    for path, node, name in _calls({"tr", "N_", "translate", "trc"}):
        where = f"{path.relative_to(_ROOT)}:{node.lineno}"
        if name == "trc":
            context, text = (_literal(arg) for arg in node.args[:2])
            if context is not None and text is not None:
                messages.setdefault(f"{context}|{text}", where)
            continue
        text = _literal(node.args[0])
        if text is not None:
            messages.setdefault(text, where)
    return messages


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def test_aucun_texte_sans_traduction_anglaise() -> None:
    missing = {
        text: where
        for text, where in _messages_in_code().items()
        if text not in en.MESSAGES
    }
    assert not missing, f"textes sans traduction anglaise : {missing}"


def test_aucune_traduction_orpheline() -> None:
    orphans = set(en.MESSAGES) - set(_messages_in_code())
    assert not orphans, f"traductions qu'aucun texte n'utilise : {orphans}"


def test_memes_valeurs_dans_les_deux_langues() -> None:
    for french, english in en.MESSAGES.items():
        source = french.split("|", 1)[1] if "|" in french else french
        assert _placeholders(source) == _placeholders(english), french
        assert english.strip(), french


def test_jamais_de_texte_compose_avant_traduction() -> None:
    # tr(f"…") traduirait un texte déjà rempli : jamais dans le catalogue.
    for path, node, name in _calls({"tr", "N_", "translate"}):
        first = node.args[0]
        assert not isinstance(first, (ast.JoinedStr, ast.BinOp)), (
            f"{path.relative_to(_ROOT)}:{node.lineno} : {name}() d'un texte composé"
        )


def test_catalogue_charge_une_seule_fois_et_vide_en_francais() -> None:
    assert i18n.catalog("fr") == {}
    assert i18n.catalog("en") is en.MESSAGES
