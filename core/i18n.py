"""Langue de My_OS : français (langue de référence) et anglais.

Tout texte montré à l'utilisateur ou au modèle est écrit **en français** dans le code
et passe par :func:`tr` au moment de l'affichage : en anglais, :func:`tr` renvoie sa
traduction, tirée du catalogue :mod:`core.locales.en` (clé = le texte français). Un
texte défini avant de connaître la langue (constante de module) est marqué par
:func:`N_` et traduit là où il sert. Les valeurs ``{nom}`` sont remplies après la
traduction : ``tr("Volume réglé à {percent} %", percent=30)``.

**Quelle langue ?** (cf. docs/INTERFACES.md §11)

* Le **popup** choisit : le choix de l'utilisateur (menu de langue de la barre de
  titre, mémorisé), sinon ``language`` de ``config.yaml``, sinon la langue du système
  (:func:`system_language` : français si le système est en français, anglais pour
  toute autre langue).
* Le **daemon** répond dans la langue de chaque requête (champ ``language`` du
  ``user_message``), posée pour le thread de la requête par :func:`use_language` :
  outils, confirmations, notices et prompt du modèle la suivent sans paramètre de
  plus. Sans langue dans la requête : celle du processus (:func:`set_language`).

La langue ne change **aucune** décision de permission : niveaux, liste de blocage,
escalade et grants restent du code statique ; seuls les textes changent.
"""

from __future__ import annotations

import importlib
import os
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from functools import cache

SUPPORTED_LANGUAGES: tuple[str, ...] = ("fr", "en")
# Langue des textes sources du code (et des documents du projet).
DEFAULT_LANGUAGE = "fr"
# Nom de chaque langue dans cette langue (menu de choix de la langue).
LANGUAGE_NAMES: dict[str, str] = {"fr": "Français", "en": "English"}

# Variables de locale POSIX, de la plus prioritaire à la moins prioritaire.
_LOCALE_VARIABLES = ("LC_ALL", "LC_MESSAGES", "LANG")
_C_LOCALES = frozenset({"c", "posix"})

_local = threading.local()
# Langue du processus (popup : celle de l'interface ; daemon : langue par défaut).
_PROCESS = {"language": DEFAULT_LANGUAGE}


def _base(locale_name: str) -> str:
    """Langue d'un nom de locale : ``fr_FR.UTF-8@euro`` → ``fr``, ``C.UTF-8`` → ``c``."""
    return re.split(r"[_.@-]", locale_name.strip(), maxsplit=1)[0].casefold()


def normalize_language(value: object) -> str | None:
    """Code de langue prise en charge (``fr`` / ``en``), ou ``None``.

    Accepte un code (``"EN"``) comme un nom de locale (``"fr_CA.UTF-8"``, ``"en-US"``).
    """
    if not isinstance(value, str):
        return None
    base = _base(value)
    return base if base in SUPPORTED_LANGUAGES else None


def system_language(environ: Mapping[str, str] | None = None) -> str:
    """``"fr"`` si le système est en français, ``"en"`` pour toute autre langue.

    Même ordre que gettext : ``LC_ALL``, puis ``LC_MESSAGES``, puis ``LANG`` ; si la
    locale n'est pas ``C``/``POSIX``, la première langue de ``LANGUAGE`` l'emporte
    (GNOME, KDE y mettent la langue d'affichage choisie). Une locale absente,
    ``C`` ou ``POSIX`` (messages en anglais) donne l'anglais.
    """
    env = os.environ if environ is None else environ
    locale_name = next(
        (env[name] for name in _LOCALE_VARIABLES if env.get(name, "").strip()), ""
    )
    primary = _base(locale_name)
    if primary and primary not in _C_LOCALES:
        preferred = [
            part for part in env.get("LANGUAGE", "").split(":") if part.strip()
        ]
        if preferred:
            primary = _base(preferred[0])
    return "fr" if primary == "fr" else "en"


def resolve_language(
    choice: object = None,
    configured: object = "auto",
    environ: Mapping[str, str] | None = None,
) -> str:
    """Langue retenue : ``choice`` (choix de l'utilisateur), sinon ``configured``
    (``language`` de la config, ``"auto"`` = aucune), sinon la langue du système."""
    for value in (choice, configured):
        language = normalize_language(value)
        if language is not None:
            return language
    return system_language(environ)


def current_language() -> str:
    """Langue courante : celle de la requête en cours (thread), sinon du processus."""
    return getattr(_local, "language", None) or _PROCESS["language"]


def set_language(language: str) -> None:
    """Change la langue du processus (popup : choix de l'utilisateur ; daemon : défaut).

    Lève :class:`ValueError` pour une langue non prise en charge.
    """
    normalized = normalize_language(language)
    if normalized is None:
        raise ValueError(f"langue non prise en charge : {language!r}")
    _PROCESS["language"] = normalized


@contextmanager
def use_language(language: str | None) -> Iterator[str]:
    """Langue du thread courant le temps du bloc (une requête du daemon).

    Une langue absente ou inconnue garde la langue courante. Renvoie la langue retenue.
    """
    chosen = normalize_language(language) or current_language()
    previous = getattr(_local, "language", None)
    _local.language = chosen
    try:
        yield chosen
    finally:
        _local.language = previous


def N_(message: str) -> str:  # pylint: disable=invalid-name
    """Marque un texte à traduire plus tard (au moment de l'afficher, par :func:`tr`)."""
    return message


@cache
def catalog(language: str) -> Mapping[str, str]:
    """Traductions d'une langue (clé : texte français) ; vide pour le français."""
    if language == DEFAULT_LANGUAGE:
        return {}
    module = importlib.import_module(f"core.locales.{language}")
    return module.MESSAGES


def _translated(key: str, message: str, language: str, values: dict) -> str:
    text = catalog(normalize_language(language) or DEFAULT_LANGUAGE).get(key, message)
    if not values:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return message.format(**values)


def translate(message: str, language: str, /, **values: object) -> str:
    """``message`` (texte français) dans ``language``, valeurs ``{nom}`` remplies.

    Un texte absent du catalogue reste en français (les tests vérifient qu'aucun ne
    manque) ; une traduction aux valeurs mal recopiées retombe sur le français.
    """
    return _translated(message, message, language, values)


def tr(message: str, /, **values: object) -> str:
    """``message`` (texte français) dans la langue courante, valeurs remplies."""
    return translate(message, current_language(), **values)


def trc(context: str, message: str, /, **values: object) -> str:
    """Comme :func:`tr`, pour un texte français qui se traduit selon son usage
    (« Annuler » : *Undo* dans un menu d'édition, *Cancel* sur un bouton). Clé du
    catalogue : ``"contexte|texte"``."""
    return _translated(f"{context}|{message}", message, current_language(), values)


def language_name(language: str) -> str:
    """Nom d'une langue dans cette langue : ``"Français"``, ``"English"``."""
    return LANGUAGE_NAMES.get(language, language)
