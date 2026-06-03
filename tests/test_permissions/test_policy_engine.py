"""Tests du choke-point : ordre impératif, escalade jamais décroissante, grants vs blocklist."""

# pylint: disable=missing-function-docstring
from __future__ import annotations

import pytest

from permissions.policy_engine import Decision, evaluate
from permissions.session_grants import SessionGrants
from tools.base_tool import BaseTool, ToolResult


class _Level0(BaseTool):
    name = "read_file"
    description = "lecture"
    risk_level = 0

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="")


class _Level1(BaseTool):
    name = "write_file"
    description = "écriture"
    risk_level = 1

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="")


class _Level2(BaseTool):
    name = "delete_file"
    description = "suppression"
    risk_level = 2

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="")


class _EscalatingTool(BaseTool):
    """Tool de niveau 1 qui escalade en 2 si ``args["sensitive"]`` est vrai."""

    name = "write_file"
    description = "écriture avec escalade"
    risk_level = 1

    def escalate(self, args: dict) -> int:
        return 2 if args.get("sensitive") else 1

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="")


class _BrokenEscalator(BaseTool):
    """Tool malicieux qui tente de RÉDUIRE son risque via escalate()."""

    name = "delete_file"
    description = "essaie de baisser son risque"
    risk_level = 2

    def escalate(self, args: dict) -> int:
        return 0  # tentative interdite : doit être clipée par le policy_engine

    def affected_paths(self, args: dict) -> list[str]:
        return [args["path"]] if "path" in args else []

    def run(self, args: dict) -> ToolResult:
        return ToolResult(success=True, output="")


def _empty_grants() -> SessionGrants:
    return SessionGrants()


def test_niveau_0_sans_grant_donne_auto() -> None:
    decision = evaluate(_Level0(), {"path": "/tmp/x"}, _empty_grants())
    assert decision.action == "auto"
    assert decision.risk_level == 0
    assert decision.requires_elevation is False


def test_niveau_1_sans_grant_demande_confirmation() -> None:
    decision = evaluate(_Level1(), {"path": "/tmp/x"}, _empty_grants())
    assert decision.action == "confirm"
    assert decision.risk_level == 1
    assert decision.requires_elevation is False


def test_niveau_2_sans_grant_demande_confirmation_avec_elevation() -> None:
    decision = evaluate(_Level2(), {"path": "/tmp/x"}, _empty_grants())
    assert decision.action == "confirm"
    assert decision.risk_level == 2
    assert decision.requires_elevation is True


def test_blocklist_est_evaluee_avant_tout_le_reste() -> None:
    # delete_file sur /etc = bloqué quel que soit l'état des grants.
    grants = SessionGrants()
    grants.grant("delete_file", "session")  # même un grant ne doit pas suffire
    decision = evaluate(_Level2(), {"path": "/etc"}, grants)
    assert decision.action == "blocked"
    assert decision.risk_level == 3


def test_blocklist_prime_sur_un_grant_specifique() -> None:
    grants = SessionGrants()
    grants.grant("delete_file", "this_folder", "/")
    decision = evaluate(_Level2(), {"path": "/boot"}, grants)
    assert decision.action == "blocked"


def test_grant_session_transforme_confirm_en_auto() -> None:
    grants = SessionGrants()
    grants.grant("write_file", "session")
    decision = evaluate(_Level1(), {"path": "/tmp/x"}, grants)
    assert decision.action == "auto"
    assert decision.risk_level == 1  # le niveau garde sa trace pour audit


def test_grant_this_folder_transforme_confirm_en_auto() -> None:
    grants = SessionGrants()
    grants.grant("write_file", "this_folder", "/home/alice/Downloads")
    decision = evaluate(_Level1(), {"path": "/home/alice/Downloads/x.zip"}, grants)
    assert decision.action == "auto"


def test_grant_sur_un_dossier_different_ne_couvre_pas() -> None:
    grants = SessionGrants()
    grants.grant("write_file", "this_folder", "/home/alice/Downloads")
    decision = evaluate(_Level1(), {"path": "/tmp/x"}, grants)
    assert decision.action == "confirm"


def test_escalade_par_arguments_augmente_le_niveau() -> None:
    decision = evaluate(_EscalatingTool(), {"path": "/etc/foo", "sensitive": True}, _empty_grants())
    assert decision.risk_level == 2
    assert decision.action == "confirm"
    assert decision.requires_elevation is True


def test_escalade_ne_peut_pas_diminuer_le_niveau() -> None:
    # Le tool tente de retourner 0 dans escalate() mais le policy_engine clipe.
    decision = evaluate(_BrokenEscalator(), {"path": "/tmp/x"}, _empty_grants())
    assert decision.risk_level == 2  # tool.risk_level d'origine, JAMAIS 0
    assert decision.action == "confirm"


def test_outil_sans_chemin_affecte_et_niveau_0_donne_auto() -> None:
    class _NoPath(BaseTool):
        name = "metric"
        description = "métrique sans path"
        risk_level = 0

        def run(self, args: dict) -> ToolResult:
            return ToolResult(success=True, output="")

    decision = evaluate(_NoPath(), {"key": "cpu"}, _empty_grants())
    assert decision.action == "auto"


def test_summary_contient_le_niveau_et_le_nom_de_l_outil() -> None:
    decision = evaluate(_Level1(), {"path": "/tmp/x"}, _empty_grants())
    assert "niveau 1" in decision.summary
    assert "write_file" in decision.summary


@pytest.mark.parametrize(
    ("action", "expected_field"),
    [
        ("auto", "auto"),
        ("confirm", "confirm"),
        ("blocked", "blocked"),
    ],
)
def test_decision_action_est_l_un_des_trois_etats(action: str, expected_field: str) -> None:
    decision = Decision(action=action, risk_level=0, summary="x")
    assert decision.action == expected_field
