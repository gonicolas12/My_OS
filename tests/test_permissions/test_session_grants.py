"""Tests des grants de session (portées this_file / this_folder / session)."""

# pylint: disable=missing-function-docstring
from __future__ import annotations

import pytest

from permissions.session_grants import SessionGrants


def test_sans_grant_rien_n_est_autorise() -> None:
    grants = SessionGrants()
    assert grants.is_granted("move_file", ["/tmp/a"]) is False


def test_grant_this_file_couvre_seulement_ce_fichier() -> None:
    grants = SessionGrants()
    grants.grant("move_file", "this_file", "/tmp/a")
    assert grants.is_granted("move_file", ["/tmp/a"]) is True
    assert grants.is_granted("move_file", ["/tmp/b"]) is False


def test_grant_this_folder_couvre_les_fichiers_du_dossier() -> None:
    grants = SessionGrants()
    grants.grant("write_file", "this_folder", "/home/alice/Downloads")
    assert grants.is_granted("write_file", ["/home/alice/Downloads/x.zip"]) is True
    assert grants.is_granted("write_file", ["/home/alice/Downloads/y.pdf"]) is True


def test_grant_this_folder_ne_couvre_pas_les_sous_dossiers() -> None:
    # Choix volontairement conservateur : pas de récursion.
    grants = SessionGrants()
    grants.grant("write_file", "this_folder", "/home/alice/Downloads")
    assert grants.is_granted("write_file", ["/home/alice/Downloads/sub/x"]) is False
    assert grants.is_granted("write_file", ["/home/alice/Other/x"]) is False


def test_grant_session_couvre_tous_les_appels_de_l_outil() -> None:
    grants = SessionGrants()
    grants.grant("move_file", "session")
    assert grants.is_granted("move_file", ["/tmp/a"]) is True
    assert grants.is_granted("move_file", ["/etc/passwd"]) is True
    assert grants.is_granted("move_file", []) is True  # session : pas besoin de paths


def test_grant_ne_s_applique_qu_a_l_outil_concerne() -> None:
    grants = SessionGrants()
    grants.grant("move_file", "session")
    grants.grant("write_file", "this_file", "/tmp/a")
    assert grants.is_granted("delete_file", ["/tmp/a"]) is False
    assert grants.is_granted("delete_file", []) is False


def test_plusieurs_paths_doivent_tous_etre_couverts() -> None:
    grants = SessionGrants()
    grants.grant("move_file", "this_file", "/tmp/a")
    # Un seul couvert sur deux : refusé.
    assert grants.is_granted("move_file", ["/tmp/a", "/tmp/b"]) is False
    grants.grant("move_file", "this_file", "/tmp/b")
    assert grants.is_granted("move_file", ["/tmp/a", "/tmp/b"]) is True


def test_paths_vides_sans_session_grant_renvoie_false() -> None:
    grants = SessionGrants()
    grants.grant("write_file", "this_file", "/tmp/a")
    assert grants.is_granted("write_file", []) is False


def test_normalisation_des_chemins() -> None:
    grants = SessionGrants()
    grants.grant("write_file", "this_file", "/tmp/a")
    assert grants.is_granted("write_file", ["/tmp/./a"]) is True
    assert grants.is_granted("write_file", ["/tmp/sub/../a"]) is True


def test_scope_inconnu_leve_value_error() -> None:
    grants = SessionGrants()
    with pytest.raises(ValueError, match="scope"):
        grants.grant("move_file", "forever", target="/tmp/a")


def test_scope_par_fichier_ou_dossier_requiert_un_target() -> None:
    grants = SessionGrants()
    with pytest.raises(ValueError, match="target"):
        grants.grant("move_file", "this_file")
    with pytest.raises(ValueError, match="target"):
        grants.grant("move_file", "this_folder")


def test_clear_vide_tout() -> None:
    grants = SessionGrants()
    grants.grant("move_file", "session")
    grants.grant("write_file", "this_file", "/tmp/a")
    grants.grant("write_file", "this_folder", "/tmp")
    grants.clear()
    assert grants.is_granted("move_file", []) is False
    assert grants.is_granted("write_file", ["/tmp/a"]) is False
