"""Textes standard de Qt dans la langue de My_OS (menus contextuels des champs).

Les textes de My_OS passent par :mod:`core.i18n`. Ceux que Qt dessine lui-même, comme
le clic droit dans un champ (Annuler, Couper, Copier, Coller, Tout sélectionner), ou
dans la conversation, viennent des catalogues de Qt : ``qtbase_<langue>.qm``, dans son
dossier de traductions (``QLibraryInfo.path(TranslationsPath)``, soit
``/usr/share/qt6/translations`` sur Arch, paquet ``qt6-translations``, que ``qt6-base``
tire déjà ; les roues PySide6 de PyPI ont le leur).

En français, le catalogue est installé sur l'application ; en anglais, la langue des
textes d'origine de Qt, il est retiré. Le changement vaut au prochain menu ouvert. Un
catalogue introuvable (Qt installé sans ses traductions) ne change rien : les menus
restent en anglais, sans erreur.

Un catalogue installé vaut pour toute l'application, d'où un état unique, au niveau
du module, comme la langue du processus dans :mod:`core.i18n`.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator

from core.logger import get_logger

_log = get_logger("popup")

# Catalogue des textes de QtWidgets et QtGui (menus contextuels, boutons standard).
CATALOG = "qtbase"
# Langue des textes d'origine de Qt : rien à charger.
SOURCE_LANGUAGE = "en"


def translations_dir() -> str:
    """Dossier des catalogues de Qt utilisé par défaut."""
    return QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)


class _Installed:
    """Le catalogue installé sur l'application, et sa langue."""

    translator: QTranslator | None = None
    language: str | None = None


_installed = _Installed()


def apply_qt_language(language: str, *, directory: str | None = None) -> bool:
    """Installe le catalogue ``qtbase`` de ``language``, à la place du précédent.

    ``directory`` remplace le dossier des traductions de Qt (tests). Renvoie ``True``
    si un catalogue de cette langue est installé. En anglais, sans application Qt, ou
    si le fichier manque, aucun ne l'est : Qt affiche alors ses textes d'origine.
    """
    if language == _installed.language and directory is None:
        return True
    _remove()
    app = QCoreApplication.instance()
    if language == SOURCE_LANGUAGE or app is None:
        return False
    folder = directory if directory is not None else translations_dir()
    translator = QTranslator()
    if not translator.load(f"{CATALOG}_{language}", folder):
        _log.info(
            "Traductions de Qt introuvables (%s_%s dans %s) : menus standard en anglais",
            CATALOG,
            language,
            folder,
        )
        return False
    app.installTranslator(translator)
    _installed.translator = translator
    _installed.language = language
    return True


def qt_language() -> str | None:
    """Langue du catalogue de Qt installé, ``None`` s'il n'y en a pas."""
    return _installed.language


def _remove() -> None:
    translator = _installed.translator
    app = QCoreApplication.instance()
    if translator is not None and app is not None:
        app.removeTranslator(translator)
    _installed.translator = None
    _installed.language = None
