"""Chargement et résolution de la configuration de My_OS.

La configuration provient de ``config.yaml`` (racine du projet), avec des
valeurs par défaut sûres. Le chemin de la socket IPC n'est jamais lu depuis le
fichier : il est calculé à l'exécution et partagé entre le daemon et le popup,
afin que les deux processus ne puissent pas diverger (cf. docs/INTERFACES.md §1).

Aucun secret ne transite par ce fichier (clé API cloud → ``keyring``,
cf. docs/SECURITY.md menace 4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"

DEFAULT_HOTKEY = "<ctrl>+<alt>+space"
SOCKET_NAME = "myos.sock"


def resolve_socket_path() -> Path:
    """Calcule le chemin de la socket Unix IPC.

    Préfère ``$XDG_RUNTIME_DIR/myos.sock`` (cas normal en session systemd
    utilisateur) ; à défaut, replie sur ``/run/user/<uid>/myos.sock``.
    """
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        runtime_dir = f"/run/user/{os.getuid()}"
    return Path(runtime_dir) / SOCKET_NAME


@dataclass
class UIConfig:
    """Paramètres du popup Qt."""

    theme: str = "dark"
    width: int = 600
    height: int = 400


@dataclass
class Config:
    """Configuration résolue de My_OS."""

    hotkey: str = DEFAULT_HOTKEY
    socket_path: Path = field(default_factory=resolve_socket_path)
    ui: UIConfig = field(default_factory=UIConfig)


def load_config(path: Path | None = None) -> Config:
    """Charge la configuration depuis ``config.yaml`` avec des valeurs par défaut.

    Les clés inconnues sont ignorées. Le chemin de la socket est toujours
    recalculé (jamais pris dans le fichier) pour rester cohérent entre processus.
    """
    config_path = path or _DEFAULT_CONFIG_PATH
    data: dict = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded

    ui_raw = data.get("ui") or {}
    allowed_ui = {"theme", "width", "height"}
    ui = UIConfig(**{k: v for k, v in ui_raw.items() if k in allowed_ui})

    return Config(
        hotkey=data.get("hotkey", DEFAULT_HOTKEY),
        socket_path=resolve_socket_path(),
        ui=ui,
    )
