"""Tests des outils fichiers (lecture/écriture/déplacement/suppression).

Vérifient le contrat ToolResult, les escalades par chemin sensible, et que
les erreurs OS sont renvoyées proprement (pas de stack trace, success=False).
"""

# pylint: disable=missing-function-docstring
from __future__ import annotations

import os
from pathlib import Path

import pytest

from permissions.policy_engine import evaluate
from permissions.session_grants import SessionGrants
from tools.files import (
    FILE_TOOLS,
    CreateFile,
    DeleteFile,
    ListDir,
    MoveFile,
    ReadFile,
    WriteFile,
)

# Tests de sémantique de chemins propre à la cible Linux (résolution réelle, HOME).
posix_only = pytest.mark.skipif(
    os.name != "posix", reason="sémantique des chemins de la cible Linux"
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


def test_introuvable_suggere_le_contenu_du_parent(tmp_path: Path) -> None:
    # Le vrai nom (demo) doit apparaître quand on vise un nom proche (demon),
    # pour que la boucle agentique puisse se corriger.
    (tmp_path / "demo").mkdir()
    result = ListDir().run({"path": str(tmp_path / "demon")})
    assert result.success is False
    assert "introuvable" in result.output
    assert "demo" in result.output


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


# ---------- normalize_args : expansion de ~ ----------


@pytest.mark.parametrize(
    "tool", [ReadFile(), ListDir(), WriteFile(), CreateFile(), DeleteFile()]
)
def test_normalize_args_expanduser_path(tool: object) -> None:
    out = tool.normalize_args({"path": "~/demo"})  # type: ignore[attr-defined]
    assert not out["path"].startswith("~")
    assert out["path"].endswith("demo")


def test_normalize_args_preserve_les_autres_champs() -> None:
    out = WriteFile().normalize_args({"path": "~/x.txt", "content": "garde-moi"})
    assert not out["path"].startswith("~")
    assert out["content"] == "garde-moi"


def test_move_normalize_args_expanduser_src_et_dst() -> None:
    out = MoveFile().normalize_args({"src": "~/a", "dst": "~/b"})
    assert not out["src"].startswith("~")
    assert not out["dst"].startswith("~")


def test_normalize_args_laisse_les_chemins_absolus_intacts() -> None:
    out = WriteFile().normalize_args({"path": "/tmp/x", "content": "y"})
    assert out["path"] == "/tmp/x"


@posix_only
def test_regression_securite_tilde_ssh_escalade_en_niveau_2() -> None:
    # ~/.ssh est sensible : après normalisation, l'escalade DOIT passer à 2.
    # Sans expansion, "~/.ssh/id_rsa" échapperait à la détection (faille).
    # Linux seulement : sous Windows, HOME n'existe pas et posixpath.expanduser
    # laisse « ~ » tel quel (sémantique de la cible Linux).
    tool = WriteFile()
    normalized = tool.normalize_args({"path": "~/.ssh/id_rsa", "content": "x"})
    assert tool.escalate(normalized) == 2


# ---------- normalize_args : chemin réel (relatif, liens, //) — cible Linux ----------
# Constats du labo VM : sans résolution, un chemin relatif, un lien symbolique ou
# « //etc » échappaient à l'escalade, à la blocklist et aux grants.


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home" / "u"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@posix_only
def test_chemin_relatif_resolu_depuis_le_repertoire_du_daemon(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "opt" / "my_os"  # WorkingDirectory du service sur l'ISO
    workdir.mkdir(parents=True)
    monkeypatch.chdir(workdir)
    relatif = os.path.relpath(fake_home / ".ssh" / "authorized_keys", workdir)
    tool = WriteFile()
    out = tool.normalize_args({"path": relatif, "content": "x"})
    assert out["path"] == os.path.realpath(fake_home / ".ssh" / "authorized_keys")
    assert tool.escalate(out) == 2


@posix_only
def test_ecrire_a_travers_un_lien_vers_ssh_escalade(
    fake_home: Path, tmp_path: Path
) -> None:
    (tmp_path / "sshlink").symlink_to(fake_home / ".ssh")
    tool = WriteFile()
    out = tool.normalize_args(
        {"path": str(tmp_path / "sshlink" / "authorized_keys"), "content": "x"}
    )
    assert out["path"] == os.path.realpath(fake_home / ".ssh" / "authorized_keys")
    assert tool.escalate(out) == 2


@posix_only
def test_grant_dossier_ne_couvre_pas_un_lien_vers_ssh(
    fake_home: Path, tmp_path: Path
) -> None:
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "ak").symlink_to(fake_home / ".ssh" / "authorized_keys")
    grants = SessionGrants()
    grants.grant("write_file", "this_folder", str(lab))
    tool = WriteFile()
    decision = evaluate(
        tool, tool.normalize_args({"path": str(lab / "ak"), "content": "x"}), grants
    )
    assert decision.action == "confirm"
    assert decision.risk_level == 2


@posix_only
@pytest.mark.parametrize("path", ["//etc", "/tmp/../etc", "/etc/", "../" * 12 + "etc"])
def test_racine_systeme_deguisee_reste_bloquee(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    tool = DeleteFile()
    decision = evaluate(tool, tool.normalize_args({"path": path}), SessionGrants())
    assert decision.action == "blocked"


@posix_only
def test_supprimer_un_lien_supprime_le_lien_pas_sa_cible(tmp_path: Path) -> None:
    cible = tmp_path / "precieux.txt"
    cible.write_text("x", encoding="utf-8")
    lien = tmp_path / "raccourci"
    lien.symlink_to(cible)
    tool = DeleteFile()
    out = tool.normalize_args({"path": str(lien)})
    assert out["path"] == os.path.join(os.path.realpath(tmp_path), "raccourci")
    assert tool.run(out).success is True
    assert cible.exists()
    assert not lien.is_symlink()


@posix_only
def test_deplacer_dans_un_lien_vers_ssh_escalade(
    fake_home: Path, tmp_path: Path
) -> None:
    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    (tmp_path / "vers_ssh").symlink_to(fake_home / ".ssh")
    tool = MoveFile()
    out = tool.normalize_args({"src": str(src), "dst": str(tmp_path / "vers_ssh")})
    assert out["dst"] == os.path.realpath(fake_home / ".ssh")
    assert tool.escalate(out) == 2


def test_chemin_avec_octet_nul_ne_fait_pas_planter_la_normalisation() -> None:
    # realpath lève ValueError sur un octet nul ; normalize_args est appelé hors
    # du try de l'orchestrator : il ne doit jamais lever (l'erreur surgit dans
    # run, que l'orchestrator transforme en « Erreur d'exécution »).
    tool = WriteFile()
    out = tool.normalize_args({"path": "/tmp/a\x00b", "content": "x"})
    assert out["path"] == "/tmp/a\x00b"


@posix_only
def test_home_derriere_un_lien_garde_ssh_sensible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ex. /home → /var/home : chemins réels d'un côté, HOME symbolique de l'autre.
    real = tmp_path / "var" / "home" / "u"
    (real / ".ssh").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "u").symlink_to(real)
    monkeypatch.setenv("HOME", str(tmp_path / "home" / "u"))
    tool = WriteFile()
    out = tool.normalize_args({"path": "~/.ssh/authorized_keys", "content": "x"})
    assert out["path"] == os.path.realpath(real / ".ssh" / "authorized_keys")
    assert tool.escalate(out) == 2
