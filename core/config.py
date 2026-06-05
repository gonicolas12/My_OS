"""Chargement et résolution de la configuration de My_OS.

La configuration provient de ``config.yaml`` (défauts versionnés), surchargée
si présent par ``config.local.yaml`` (overrides personnels, **non versionnés** —
cf. ``.gitignore``). Cela permet d'avoir des réglages propres à une machine
(ex. ``backend: ollama``) sans entrer en conflit avec ``config.yaml`` à chaque
mise à jour du dépôt.

Le chemin de la socket IPC n'est jamais lu depuis le fichier : il est calculé à
l'exécution et partagé entre le daemon et le popup, afin que les deux processus
ne puissent pas diverger (cf. docs/INTERFACES.md §1).

Aucun secret ne transite par ces fichiers (clé API cloud → ``keyring``,
cf. docs/SECURITY.md menace 4).
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"
_LOCAL_CONFIG_PATH = _PROJECT_ROOT / "config.local.yaml"

DEFAULT_HOTKEY = "<ctrl>+<alt>+<space>"
SOCKET_NAME = "myos.sock"


def resolve_socket_path() -> Path:
    """Calcule le chemin de la socket Unix IPC.

    Préfère ``$XDG_RUNTIME_DIR/myos.sock`` (cas normal en session systemd
    utilisateur) ; à défaut, replie sur ``/run/user/<uid>/myos.sock``.
    """
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        getuid = getattr(os, "getuid", None)
        if getuid is not None:
            runtime_dir = f"/run/user/{getuid()}"
        else:
            # Ni XDG_RUNTIME_DIR ni getuid (ex. Windows en dev/test) : repli sur
            # un dossier temporaire. La cible de déploiement reste Linux.
            runtime_dir = tempfile.gettempdir()
    return Path(runtime_dir) / SOCKET_NAME


@dataclass
class UIConfig:
    """Paramètres du popup Qt."""

    theme: str = "dark"
    width: int = 600
    height: int = 400


@dataclass
class ModelConfig:
    """Choix du backend modèle utilisé par l'orchestrator."""

    backend: str = "stub"  # "stub" | "ollama"
    name: str = "qwen3.5:4b"  # modèle Ollama si backend="ollama"
    host: str | None = None  # URL HTTP Ollama (None = défaut local)
    think: bool = False  # raisonnement natif du modèle (latence ↑ ; OFF = réactif)


def default_audit_db_path() -> Path:
    """Chemin par défaut du journal d'audit (cf. docs/ARCHITECTURE.md §7)."""
    return _PROJECT_ROOT / "data" / "audit.db"


@dataclass
class Config:
    """Configuration résolue de My_OS."""

    hotkey: str = DEFAULT_HOTKEY
    socket_path: Path = field(default_factory=resolve_socket_path)
    audit_db_path: Path = field(default_factory=default_audit_db_path)
    ui: UIConfig = field(default_factory=UIConfig)
    model: ModelConfig = field(default_factory=ModelConfig)


def _read_yaml(path: Path) -> dict:
    """Lit un YAML en dict ; renvoie ``{}`` si absent ou si le contenu n'est pas un mapping."""
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Fusionne ``override`` dans ``base`` (récursif sur les sous-dicts)."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path | None = None) -> Config:
    """Charge la configuration et renvoie un :class:`Config` résolu.

    Sans ``path`` : lit ``config.yaml`` puis applique ``config.local.yaml`` en
    surcharge s'il existe. Avec ``path`` explicite (tests) : lit uniquement ce
    fichier, sans surcharge locale.

    Les clés inconnues sont ignorées. Le chemin de la socket est toujours
    recalculé (jamais pris dans le fichier) pour rester cohérent entre processus.
    """
    if path is not None:
        data = _read_yaml(path)
    else:
        data = _deep_merge(
            _read_yaml(_DEFAULT_CONFIG_PATH), _read_yaml(_LOCAL_CONFIG_PATH)
        )

    ui_raw = data.get("ui") or {}
    allowed_ui = {"theme", "width", "height"}
    ui = UIConfig(**{k: v for k, v in ui_raw.items() if k in allowed_ui})

    model_raw = data.get("model") or {}
    allowed_model = {"backend", "name", "host", "think"}
    model = ModelConfig(**{k: v for k, v in model_raw.items() if k in allowed_model})

    return Config(
        hotkey=data.get("hotkey", DEFAULT_HOTKEY),
        socket_path=resolve_socket_path(),
        audit_db_path=default_audit_db_path(),
        ui=ui,
        model=model,
    )
