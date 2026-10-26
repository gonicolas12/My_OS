"""Catalogue des applications installées (fichiers ``.desktop``) pour ``open_app``.

Ouvrir une application par son nom est plus fiable que de la chercher à l'écran
dans un menu : le modèle demande « Firefox » ou « éditeur de texte », le catalogue
trouve le fichier ``.desktop`` correspondant (nom, nom générique, mots-clés, en
français ou en anglais, quelle que soit la langue de My_OS) et ``gio launch`` la
lance comme le ferait le menu. Le nom montré (confirmations, résultats) est celui de
la langue de la requête.

Lecture seule de fichiers texte (``configparser``), sans dépendance : testable
partout avec un dossier d'applications factice.
"""

from __future__ import annotations

import configparser
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from core.i18n import current_language

_SECTION = "Desktop Entry"
# Traductions lues dans les fichiers .desktop pour chaque langue de My_OS, dans
# l'ordre de préférence (``Name[fr]``, sinon ``Name[fr_FR]``) ; sans traduction, le
# nom par défaut (``Name=``, en anglais le plus souvent).
_LOCALES: dict[str, tuple[str, ...]] = {
    "fr": ("fr", "fr_FR"),
    "en": ("en_US", "en"),
}


@dataclass(frozen=True)
class AppEntry:  # pylint: disable=too-many-instance-attributes
    """Application lançable : identifiant, nom affiché, fichier ``.desktop``."""

    app_id: str  # ex. « org.xfce.mousepad »
    name: str  # nom par défaut (``Name=``, en anglais le plus souvent)
    path: str
    generic: str = ""
    keywords: tuple[str, ...] = ()
    executable: str = ""  # nom du binaire (première partie d'Exec)
    terminal: bool = False  # Terminal=true : l'application tourne dans un terminal
    categories: tuple[str, ...] = ()
    command: str = ""  # ligne Exec complète
    # Nom traduit par langue de My_OS, s'il existe : (("fr", "Éditeur…"),).
    localized_names: tuple[tuple[str, str], ...] = ()

    @property
    def label(self) -> str:
        """Nom montré à l'utilisateur, dans la langue courante (sinon ``name``)."""
        return dict(self.localized_names).get(current_language(), self.name)

    @property
    def opens_terminal(self) -> bool:
        """``True`` si lancer cette entrée ouvre un terminal : émulateur de terminal
        (catégorie freedesktop ``TerminalEmulator``, ou lanceur générique comme
        ``exo-open --launch TerminalEmulator``), ou application en terminal."""
        return (
            self.terminal
            or "TerminalEmulator" in self.categories
            or "TerminalEmulator" in self.command
        )


def _fold(text: str) -> str:
    """Minuscules sans accents, espaces normalisés (« Éditeur » → « editeur »)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(
        "".join(c for c in decomposed if not unicodedata.combining(c)).split()
    )


def application_dirs(environ: dict[str, str] | None = None) -> list[Path]:
    """Dossiers ``applications`` XDG, du plus prioritaire au moins prioritaire."""
    env = os.environ if environ is None else environ
    home = Path(env.get("HOME", str(Path.home())))
    data_home = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    data_dirs = env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [data_home / "applications"]
    dirs += [Path(d) / "applications" for d in data_dirs.split(":") if d]
    dirs += [
        Path("/var/lib/flatpak/exports/share/applications"),
        data_home / "flatpak" / "exports" / "share" / "applications",
    ]
    unique: list[Path] = []
    for folder in dirs:
        if folder not in unique:
            unique.append(folder)
    return unique


def _translation(section: configparser.SectionProxy, key: str, language: str) -> str:
    """Traduction de ``key`` pour une langue de My_OS (``""`` s'il n'y en a pas)."""
    for locale in _LOCALES[language]:
        value = section.get(f"{key}[{locale}]", "")
        if value:
            return value
    return ""


def _localized(section: configparser.SectionProxy, key: str) -> list[str]:
    """Valeur par défaut de ``key`` et toutes ses traductions lues (recherche)."""
    values = [section.get(key, "")]
    values += [
        section.get(f"{key}[{locale}]", "")
        for locales in _LOCALES.values()
        for locale in locales
    ]
    return [v for v in dict.fromkeys(values) if v]


def _parse(path: Path) -> AppEntry | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return None
    if _SECTION not in parser:
        return None
    entry = parser[_SECTION]
    if entry.get("Type", "Application") != "Application":
        return None
    if entry.get("NoDisplay", "false").lower() == "true":
        return None
    if entry.get("Hidden", "false").lower() == "true":
        return None
    names = _localized(entry, "Name")
    if not names or not entry.get("Exec"):
        return None
    keywords: list[str] = []
    for value in _localized(entry, "Keywords"):
        keywords += [k for k in value.split(";") if k.strip()]
    executable = (
        os.path.basename(entry.get("Exec", "").split()[0]) if entry.get("Exec") else ""
    )
    translated = {
        language: _translation(entry, "Name", language) for language in _LOCALES
    }
    return AppEntry(
        app_id=path.stem,
        name=entry.get("Name", "") or names[0],
        localized_names=tuple(
            (lang, name) for lang, name in translated.items() if name
        ),
        path=str(path),
        generic=" / ".join(dict.fromkeys(_localized(entry, "GenericName"))),
        keywords=tuple(dict.fromkeys(names + keywords)),
        executable=executable,
        terminal=entry.get("Terminal", "false").lower() == "true",
        categories=tuple(c for c in entry.get("Categories", "").split(";") if c),
        command=entry.get("Exec", ""),
    )


class AppCatalog:
    """Applications des dossiers XDG, chargées à la demande."""

    def __init__(self, dirs: list[Path] | None = None) -> None:
        self._dirs = dirs
        self._entries: list[AppEntry] | None = None

    def entries(self) -> list[AppEntry]:
        """Toutes les applications visibles dans les menus (une par identifiant)."""
        if self._entries is None:
            seen: dict[str, AppEntry] = {}
            for folder in self._dirs or application_dirs():
                try:
                    paths = sorted(folder.glob("*.desktop"))
                except OSError:
                    continue
                for path in paths:
                    if path.stem in seen:
                        continue  # le dossier le plus prioritaire l'emporte
                    entry = _parse(path)
                    if entry is not None:
                        seen[path.stem] = entry
            self._entries = list(seen.values())
        return self._entries

    def reload(self) -> None:
        """Oublie le catalogue (une application vient d'être installée)."""
        self._entries = None

    def find(self, query: str) -> AppEntry | None:
        """Meilleure application pour ``query`` (nom, nom générique, mot-clé, binaire)."""
        wanted = _fold(query)
        if not wanted:
            return None
        best_score, best = 0, None
        for entry in self.entries():
            score = _score(entry, wanted)
            if score > best_score:
                best_score, best = score, entry
        return best

    def suggestions(self, limit: int = 25) -> list[str]:
        """Noms d'applications (langue courante), pour aider le modèle quand rien ne
        correspond."""
        return sorted({e.label for e in self.entries()})[:limit]


def _score(entry: AppEntry, wanted: str) -> int:
    names = [_fold(entry.name), _fold(entry.app_id), _fold(entry.executable)]
    names += [_fold(k) for k in entry.keywords]
    generic = [_fold(g) for g in entry.generic.split(" / ") if g]
    if wanted in names:
        return 100
    if wanted in generic:
        return 80
    if any(n.endswith("." + wanted) for n in names):  # org.xfce.mousepad
        return 70
    if any(wanted in n.split() for n in names):
        return 50
    if any(wanted in n for n in names + generic):
        return 30
    return 0
