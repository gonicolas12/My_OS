"""Tests du chargement de configuration et de la fusion config.yaml + local."""

# pylint: disable=missing-function-docstring
from __future__ import annotations

from pathlib import Path

from core import config as config_module
from core.config import (
    DEFAULT_HOTKEY,
    _deep_merge,
    default_audit_db_path,
    load_config,
)


def test_deep_merge_surcharge_les_cles_simples() -> None:
    base = {"a": 1, "b": 2}
    assert _deep_merge(base, {"b": 3}) == {"a": 1, "b": 3}


def test_deep_merge_fusionne_les_sous_dicts() -> None:
    base = {"model": {"backend": "stub", "name": "x"}}
    override = {"model": {"backend": "ollama"}}
    # backend surchargé, name conservé.
    assert _deep_merge(base, override) == {"model": {"backend": "ollama", "name": "x"}}


def test_deep_merge_ne_mute_pas_la_base() -> None:
    base = {"model": {"backend": "stub"}}
    _deep_merge(base, {"model": {"backend": "ollama"}})
    assert base == {"model": {"backend": "stub"}}


def test_load_config_defauts_si_fichier_absent(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "absent.yaml")
    assert cfg.hotkey == DEFAULT_HOTKEY
    assert cfg.model.backend == "stub"
    assert cfg.ui.theme == "dark"


def test_load_config_lit_un_fichier_explicite(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "hotkey: '<ctrl>+<alt>+m'\nmodel:\n  backend: ollama\n  name: qwen3.5:2b\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.hotkey == "<ctrl>+<alt>+m"
    assert cfg.model.backend == "ollama"
    assert cfg.model.name == "qwen3.5:2b"


def test_load_config_fenetre_de_contexte(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("model:\n  backend: ollama\n  num_ctx: 8192\n", encoding="utf-8")
    assert load_config(path).model.num_ctx == 8192
    assert load_config(tmp_path / "absent.yaml").model.num_ctx == 16384


def test_load_config_duree_de_chargement_du_modele(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("model:\n  keep_alive: 30m\n", encoding="utf-8")
    assert load_config(path).model.keep_alive == "30m"
    path.write_text("model:\n  keep_alive: -1\n", encoding="utf-8")
    assert load_config(path).model.keep_alive == -1
    assert load_config(tmp_path / "absent.yaml").model.keep_alive == "1h"


def test_load_config_ignore_les_cles_inconnues(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("model:\n  backend: ollama\n  inconnu: 42\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.model.backend == "ollama"


def test_load_config_controle_de_l_ordinateur(tmp_path: Path) -> None:
    defaults = load_config(tmp_path / "absent.yaml").computer
    assert (defaults.enabled, defaults.vision, defaults.ocr) == (True, "auto", "auto")
    path = tmp_path / "config.yaml"
    # YAML lit « on » / « off » comme des booléens : ramenés aux mots attendus.
    path.write_text(
        "computer:\n  enabled: false\n  vision: on\n  ocr: off\n  inconnu: 1\n",
        encoding="utf-8",
    )
    computer = load_config(path).computer
    assert (computer.enabled, computer.vision, computer.ocr) == (False, "on", "off")


def test_load_config_langue(tmp_path: Path) -> None:
    assert load_config(tmp_path / "absent.yaml").language == "auto"
    path = tmp_path / "config.yaml"
    for value, expected in (
        ("en", "en"),
        ("FR", "fr"),
        ("fr_CA.UTF-8", "fr"),
        ("auto", "auto"),
        ("de", "auto"),  # langue non prise en charge : celle du système
    ):
        path.write_text(f"language: {value}\n", encoding="utf-8")
        assert load_config(path).language == expected, value


def test_socket_path_jamais_pris_du_fichier(tmp_path: Path) -> None:
    # Même si le fichier tente d'imposer un socket_path, il est recalculé.
    path = tmp_path / "config.yaml"
    path.write_text("socket_path: /tmp/pirate.sock\n", encoding="utf-8")
    cfg = load_config(path)
    assert "pirate" not in str(cfg.socket_path)


def test_audit_db_dans_xdg_data_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert default_audit_db_path() == tmp_path / "data" / "my_os" / "audit.db"


def test_audit_db_hors_du_dossier_du_code(monkeypatch) -> None:
    # Installé (ex. /opt/my_os), le dossier du code n'est pas inscriptible par
    # l'utilisateur du daemon : l'audit ne doit jamais y être écrit.
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    project_root = Path(config_module.__file__).resolve().parent.parent
    path = default_audit_db_path()
    assert path == Path.home() / ".local" / "share" / "my_os" / "audit.db"
    assert project_root not in path.parents
