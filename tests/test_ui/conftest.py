"""Fixtures communes des tests de l'interface (Qt hors écran).

Une **seule** ``QApplication`` pour toute la session de tests : si chaque module créait
la sienne, elle serait détruite en fin de module puis recréée par le suivant.

En fin de session, les fenêtres encore vivantes (retenues par les lambdas connectées à
leurs boutons) sont fermées et détruites **tant que l'application existe** : sinon
PySide6 les détruit à la sortie de l'interpréteur, dans le désordre, et le processus
plante (code 139) alors que tous les tests sont passés.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def app() -> Iterator[object]:
    """``QApplication`` partagée (les modules Qt la demandent sous le nom ``app``)."""
    qt_core = pytest.importorskip("PySide6.QtCore")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets")
    application = qt_widgets.QApplication.instance() or qt_widgets.QApplication([])
    yield application
    application.closeAllWindows()
    for widget in qt_widgets.QApplication.topLevelWidgets():
        widget.deleteLater()
    qt_core.QCoreApplication.sendPostedEvents(None, qt_core.QEvent.Type.DeferredDelete)
    application.processEvents()
