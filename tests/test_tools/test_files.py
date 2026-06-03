"""Tests des outils fichiers (lecture/écriture/déplacement/suppression).

Vérifient le contrat ToolResult, les escalades par chemin sensible, et que
les erreurs OS sont renvoyées proprement (pas de stack trace, success=False).
"""

# pylint: disable=missing-function-docstring
from __future__ import annotations

from pathlib import Path

import pytest

from tools.files import (
    FILE_TOOLS,
    CreateFile,
    DeleteFile,
    ListDir,
    MoveFile,
    ReadFile,
    WriteFile,
)


# ---------- registre ----------


def test_registre_contient_tous_les_outils_jalon_2() -> None:
    attendus = {
        "read_file",
        "list_dir",
        "write_file",
        "create_file",
        "move_file",
        "delete_file",
    }
    assert set(FILE_TOOLS.keys()) == attendus


def test_chaque_outil_du_registre_declare_son_risque() -> None:
    for tool in FILE_TOOLS.values():
        assert tool.risk_level in (0, 1, 2, 3)


# ---------- read_file ----------


def test_read_file_renvoie_le_contenu(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("hello", encoding="utf-8")
    result = ReadFile().run({"path": str(target)})
    assert result.success is True
    assert result.output == "hello"


def test_read_file_inexistant_echoue_proprement(tmp_path: Path) -> None:
    result = ReadFile().run({"path": str(tmp_path / "absent.txt")})
    assert result.success is False
    assert "introuvable" in result.output


def test_read_file_refuse_un_repertoire(tmp_path: Path) -> None:
    result = ReadFile().run({"path": str(tmp_path)})
    assert result.success is False
    assert "pas un fichier" in result.output


def test_read_file_arg_manquant() -> None:
    result = ReadFile().run({})
    assert result.success is False
    assert "path" in result.output


# ---------- list_dir ----------


def test_list_dir_renvoie_les_noms_tries(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "sub").mkdir()
    result = ListDir().run({"path": str(tmp_path)})
    assert result.success is True
    assert result.output.split("\n") == ["a.txt", "b.txt", "sub"]


def test_list_dir_refuse_un_fichier(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("")
    result = ListDir().run({"path": str(target)})
    assert result.success is False


# ---------- write_file ----------


def test_write_file_ecrit_le_contenu(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    result = WriteFile().run({"path": str(target), "content": "hello"})
    assert result.success is True
    assert target.read_text(encoding="utf-8") == "hello"


def test_write_file_cree_les_dossiers_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c.txt"
    result = WriteFile().run({"path": str(target), "content": "ok"})
    assert result.success is True
    assert target.read_text(encoding="utf-8") == "ok"


def test_write_file_escalade_en_niveau_2_sur_chemin_sensible() -> None:
    tool = WriteFile()
    assert tool.escalate({"path": "/tmp/x"}) == 1
    assert tool.escalate({"path": "/etc/foo"}) == 2
    assert tool.escalate({"path": "/boot/grub/themes/x.txt"}) == 2


def test_write_file_content_doit_etre_chaine(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    result = WriteFile().run({"path": str(target), "content": 42})
    assert result.success is False
    assert "content" in result.output


# ---------- create_file ----------


def test_create_file_cree_un_fichier_vide(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    result = CreateFile().run({"path": str(target)})
    assert result.success is True
    assert target.exists()
    assert target.read_text(encoding="utf-8") == ""


def test_create_file_refuse_un_fichier_existant(tmp_path: Path) -> None:
    target = tmp_path / "exists.txt"
    target.write_text("déjà là", encoding="utf-8")
    result = CreateFile().run({"path": str(target)})
    assert result.success is False
    assert "existe déjà" in result.output
    # Le contenu d'origine n'a PAS été touché.
    assert target.read_text(encoding="utf-8") == "déjà là"


def test_create_file_escalade_sur_chemin_sensible() -> None:
    tool = CreateFile()
    assert tool.escalate({"path": "/tmp/x"}) == 1
    assert tool.escalate({"path": "/etc/foo"}) == 2


# ---------- move_file ----------


def test_move_file_deplace(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    src.write_text("data")
    dst = tmp_path / "dst.txt"
    result = MoveFile().run({"src": str(src), "dst": str(dst)})
    assert result.success is True
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == "data"


def test_move_file_source_inexistante(tmp_path: Path) -> None:
    result = MoveFile().run(
        {"src": str(tmp_path / "absent"), "dst": str(tmp_path / "d")}
    )
    assert result.success is False
    assert "source" in result.output


def test_move_file_escalade_si_src_ou_dst_sensible() -> None:
    tool = MoveFile()
    assert tool.escalate({"src": "/tmp/a", "dst": "/tmp/b"}) == 1
    assert tool.escalate({"src": "/etc/a", "dst": "/tmp/b"}) == 2
    assert tool.escalate({"src": "/tmp/a", "dst": "/etc/b"}) == 2


def test_move_file_affected_paths_contient_src_et_dst() -> None:
    paths = MoveFile().affected_paths({"src": "/a", "dst": "/b"})
    assert paths == ["/a", "/b"]


# ---------- delete_file ----------


def test_delete_file_supprime_un_fichier(tmp_path: Path) -> None:
    target = tmp_path / "x.txt"
    target.write_text("")
    result = DeleteFile().run({"path": str(target)})
    assert result.success is True
    assert not target.exists()


def test_delete_file_refuse_un_repertoire(tmp_path: Path) -> None:
    result = DeleteFile().run({"path": str(tmp_path)})
    assert result.success is False
    assert "répertoire" in result.output
    # Le répertoire est toujours là.
    assert tmp_path.exists()


def test_delete_file_introuvable(tmp_path: Path) -> None:
    result = DeleteFile().run({"path": str(tmp_path / "absent")})
    assert result.success is False
    assert "introuvable" in result.output


def test_delete_file_reste_au_niveau_2_meme_sur_chemin_quelconque() -> None:
    # delete_file est déjà niveau 2 de base ; pas de mécanisme d'escalade.
    assert DeleteFile().escalate({"path": "/tmp/x"}) == 2


# ---------- niveau 0 doit rester 0 ----------


def test_lectures_ne_escaladent_pas() -> None:
    # Même sur /etc/passwd, read_file et list_dir restent niveau 0
    # (la lecture seule est sûre par construction).
    assert ReadFile().escalate({"path": "/etc/passwd"}) == 0
    assert ListDir().escalate({"path": "/etc"}) == 0


# ---------- args manquants : noms partagés ----------


@pytest.mark.parametrize(
    "tool",
    [ReadFile(), ListDir(), WriteFile(), CreateFile(), DeleteFile()],
)
def test_tools_avec_path_renvoient_erreur_si_path_manquant(tool: object) -> None:
    result = tool.run({})  # type: ignore[attr-defined]
    assert result.success is False
    assert "path" in result.output
