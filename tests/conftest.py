"""Fixtures communes à tous les tests.

**Langue.** Chaque test tourne sur un système simulé en français (``LANG=fr_FR``),
langue de référence de My_OS : les tests existants vérifient les textes français, et
la langue détectée ne dépend pas de la machine qui lance les tests (Windows, WSL,
ISO en ``C.UTF-8``). Un test qui change la langue du processus (le popup, par son
menu) la retrouve en français au test suivant.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core import i18n


@pytest.fixture(autouse=True)
def _systeme_en_francais(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Système en français pendant le test ; langue du processus remise ensuite."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
