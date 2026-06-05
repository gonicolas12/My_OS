"""Tests du journal d'audit SQLite (write-before-execute, pas de secrets en clair)."""

# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

from pathlib import Path

import pytest

from permissions.audit_log import AuditLog


@pytest.fixture
def audit(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.db")
    yield log
    log.close()


def test_log_ecrit_une_entree_complete(audit: AuditLog) -> None:
    row_id = audit.log(
        tool="read_file",
        args={"path": "/tmp/x"},
        risk_level=0,
        decision="auto",
        success=True,
        reversible=False,
    )
    assert row_id >= 1
    entries = audit.fetch_all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["tool"] == "read_file"
    assert entry["args"] == {"path": "/tmp/x"}
    assert entry["risk_level"] == 0
    assert entry["decision"] == "auto"
    assert entry["success"] is True
    assert entry["reversible"] is False
    assert entry["timestamp"]  # ISO renseigné


def test_log_pending_laisse_le_resultat_a_null(audit: AuditLog) -> None:
    row_id = audit.log_pending(
        tool="delete_file", args={"path": "/tmp/x"}, risk_level=2, decision="approved"
    )
    entry = audit.fetch_all()[0]
    assert entry["id"] == row_id
    assert entry["success"] is None
    assert entry["reversible"] is None


def test_update_result_renseigne_le_resultat_apres_execution(audit: AuditLog) -> None:
    row_id = audit.log_pending("delete_file", {"path": "/tmp/x"}, 2, "approved")
    audit.update_result(row_id, success=True, reversible=False)
    entry = audit.fetch_all()[0]
    assert entry["success"] is True
    assert entry["reversible"] is False


def test_secrets_sont_masques_dans_args(audit: AuditLog) -> None:
    audit.log(
        tool="login",
        args={
            "username": "alice",
            "password": "hunter2",
            "api_key": "sk-xxx",
            "token": "t-yyy",
        },
        risk_level=1,
        decision="auto",
        success=True,
        reversible=False,
    )
    entry = audit.fetch_all()[0]
    assert entry["args"]["username"] == "alice"
    assert entry["args"]["password"] == "[REDACTED]"
    assert entry["args"]["api_key"] == "[REDACTED]"
    assert entry["args"]["token"] == "[REDACTED]"


def test_args_normaux_ne_sont_pas_masques(audit: AuditLog) -> None:
    audit.log(
        tool="write_file",
        args={"path": "/tmp/x", "content": "hello world"},
        risk_level=1,
        decision="approved",
        success=True,
        reversible=False,
    )
    entry = audit.fetch_all()[0]
    assert entry["args"] == {"path": "/tmp/x", "content": "hello world"}


def test_plusieurs_entrees_sont_ordonnees_par_id(audit: AuditLog) -> None:
    a = audit.log("read_file", {"path": "/a"}, 0, "auto", True, False)
    b = audit.log("read_file", {"path": "/b"}, 0, "auto", True, False)
    c = audit.log_pending("delete_file", {"path": "/c"}, 2, "approved")
    entries = audit.fetch_all()
    assert [e["id"] for e in entries] == [a, b, c]
    assert entries[2]["success"] is None  # pending pas encore résolu


def test_db_est_persistee_entre_deux_ouvertures(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    log_a = AuditLog(db_path)
    log_a.log("read_file", {"path": "/x"}, 0, "auto", True, False)
    log_a.close()

    log_b = AuditLog(db_path)
    entries = log_b.fetch_all()
    log_b.close()
    assert len(entries) == 1
    assert entries[0]["tool"] == "read_file"


def test_creation_de_dossiers_parents_implicite(tmp_path: Path) -> None:
    db_path = tmp_path / "sub" / "dir" / "audit.db"
    log = AuditLog(db_path)
    log.log("read_file", {"path": "/x"}, 0, "auto", True, False)
    log.close()
    assert db_path.exists()


def test_cle_partielle_sensible_est_aussi_masquee(audit: AuditLog) -> None:
    audit.log(
        tool="oauth",
        args={"refresh_token": "abc", "user_password_hash": "xx"},
        risk_level=1,
        decision="auto",
        success=True,
        reversible=False,
    )
    entry = audit.fetch_all()[0]
    assert entry["args"]["refresh_token"] == "[REDACTED]"
    assert entry["args"]["user_password_hash"] == "[REDACTED]"
