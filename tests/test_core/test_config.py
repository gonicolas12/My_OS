"""Tests du chargement de configuration et de la fusion config.yaml + local."""

# pylint: disable=missing-function-docstring
from __future__ import annotations

from pathlib import Path

from core.config import DEFAULT_HOTKEY, _deep_merge, load_config


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


def test_load_config_ignore_les_cles_inconnues(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("model:\n  backend: ollama\n  inconnu: 42\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.model.backend == "ollama"


def test_socket_path_jamais_pris_du_fichier(tmp_path: Path) -> None:
    # Même si le fichier tente d'imposer un socket_path, il est recalculé.
    path = tmp_path / "config.yaml"
    path.write_text("socket_path: /tmp/pirate.sock\n", encoding="utf-8")
    cfg = load_config(path)
    assert "pirate" not in str(cfg.socket_path)
