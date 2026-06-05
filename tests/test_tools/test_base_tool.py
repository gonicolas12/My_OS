"""Tests du contrat de :mod:`tools.base_tool`.

Vérifient l'invariant de sécurité « un outil sans ``risk_level`` n'est pas
chargé » (cf. docs/SECURITY.md §4) et les comportements par défaut.
"""

# Les noms des fonctions de test font déjà office de description ; comparer
# explicitement avec ``[]`` est plus clair en test que ``not <expr>`` ; on
# définit volontairement une sous-classe abstraite incomplète pour vérifier
# qu'elle lève NotImplementedError.
# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison,abstract-method
from __future__ import annotations

import pytest

from tools.base_tool import BaseTool, ToolResult


class _Dummy(BaseTool):
    """Outil minimal valide pour tester les comportements par défaut."""

    name = "dummy"
    description = "test"
    risk_level = 0

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="ok")


def test_subclass_sans_risk_level_est_refusee() -> None:
    with pytest.raises(TypeError, match="risk_level"):

        class _NoLevel(BaseTool):
            name = "no_level"
            description = "test"

            def run(self, args: dict) -> ToolResult:
                return ToolResult(success=True, output="ok")


@pytest.mark.parametrize("invalid", [-1, 4, 99, "1", 1.5, None])
def test_subclass_avec_risk_level_invalide_est_refusee(invalid: object) -> None:
    with pytest.raises(TypeError, match="risk_level"):

        class _Bad(BaseTool):
            name = "bad"
            description = "test"
            risk_level = invalid  # type: ignore[assignment]

            def run(self, args: dict) -> ToolResult:
                return ToolResult(success=True, output="ok")


@pytest.mark.parametrize("valid", [0, 1, 2, 3])
def test_subclass_avec_risk_level_valide_est_acceptee(valid: int) -> None:
    class _Good(BaseTool):
        name = "good"
        description = "test"
        risk_level = valid

        def run(self, args: dict) -> ToolResult:
            return ToolResult(success=True, output="ok")

    assert _Good().risk_level == valid


def test_escalate_par_defaut_renvoie_risk_level() -> None:
    assert _Dummy().escalate({"any": "arg"}) == 0


def test_affected_paths_par_defaut_est_vide() -> None:
    assert _Dummy().affected_paths({"any": "arg"}) == []


def test_requires_elevation_par_defaut_est_false() -> None:
    assert _Dummy().requires_elevation({"any": "arg"}) is False


def test_normalize_args_par_defaut_est_identite() -> None:
    args = {"path": "~/x", "content": "y"}
    assert _Dummy().normalize_args(args) == args


def test_run_non_surcharge_leve_not_implemented() -> None:
    class _NoRun(BaseTool):
        name = "norun"
        description = "test"
        risk_level = 0

    with pytest.raises(NotImplementedError):
        _NoRun().run({})


def test_tool_result_defaults() -> None:
    result = ToolResult(success=True, output="x")
    assert result.success is True
    assert result.output == "x"
    assert result.reversible is False
    assert result.undo_data is None
