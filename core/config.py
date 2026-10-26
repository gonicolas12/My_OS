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
    width: int = 720  # taille à la 1re ouverture (ensuite mémorisée par le popup)
    height: int = 540


@dataclass
class ModelConfig:
    """Choix du backend modèle utilisé par l'orchestrator.

    ``backend``/``name``/``host``/``think`` concernent le backend **local** (défaut).
    ``cloud_model`` est le modèle Claude utilisé quand une requête est routée vers le
    cloud (opt-in par requête, cf. docs/INTERFACES.md §5). La **clé API** n'est jamais
    ici : elle vit dans le trousseau (``models.secrets``), cf. SECURITY menace 4.
    """

    backend: str = "stub"  # "stub" | "ollama"
    name: str = "qwen3.5:4b"  # modèle Ollama si backend="ollama"
    host: str | None = None  # URL HTTP Ollama (None = $OLLAMA_HOST ou 127.0.0.1)
    think: bool = False  # raisonnement natif du modèle (latence ↑ ; OFF = réactif)
    num_ctx: int = 16384  # fenêtre de contexte du modèle local, en tokens
    keep_alive: str | int = "1h"  # modèle gardé chargé après une requête (Ollama)
    cloud_model: str = "claude-sonnet-5-5"  # modèle Claude si requête routée cloud


@dataclass
class ComputerConfig:
    """Contrôle de l'ordinateur (voir l'écran, souris, clavier ; X11).

    ``vision`` : capture annotée envoyée au modèle LOCAL en plus de la liste des
    éléments — ``auto`` (seulement si le modèle tourne sur une carte graphique),
    ``on`` ou ``off``. Le cloud, opt-in, reçoit toujours l'image. ``ocr`` : texte
    lu par Tesseract là où l'accessibilité ne dit rien (``auto`` ou ``off``).
    """

    enabled: bool = True
    vision: str = "auto"  # "auto" | "on" | "off"
    ocr: str = "auto"  # "auto" | "off"


def default_audit_db_path() -> Path:
    """Chemin par défaut du journal d'audit (cf. docs/ARCHITECTURE.md §7).

    ``$XDG_DATA_HOME/my_os/audit.db`` (défaut ``~/.local/share/my_os/audit.db``) :
    toujours inscriptible par l'utilisateur du daemon, contrairement au dossier du
    code, en lecture seule une fois installé (ex. ``/opt/my_os`` sur l'ISO).
    """
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home) / "my_os" / "audit.db"


@dataclass
class Config:
    """Configuration résolue de My_OS."""

    hotkey: str = DEFAULT_HOTKEY
    socket_path: Path = field(default_factory=resolve_socket_path)
    audit_db_path: Path = field(default_factory=default_audit_db_path)
    ui: UIConfig = field(default_factory=UIConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    computer: ComputerConfig = field(default_factory=ComputerConfig)


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
    allowed_model = {
        "backend",
        "name",
        "host",
        "think",
        "num_ctx",
        "keep_alive",
        "cloud_model",
    }
    model = ModelConfig(**{k: v for k, v in model_raw.items() if k in allowed_model})

    computer_raw = data.get("computer") or {}
    allowed_computer = {"enabled", "vision", "ocr"}
    computer = ComputerConfig(
        **{k: v for k, v in computer_raw.items() if k in allowed_computer}
    )
    # YAML lit « on » / « off » comme des booléens : on les ramène aux mots attendus.
    for key in ("vision", "ocr"):
        value = getattr(computer, key)
        if isinstance(value, bool):
            setattr(computer, key, "on" if value else "off")

    return Config(
        hotkey=data.get("hotkey", DEFAULT_HOTKEY),
        socket_path=resolve_socket_path(),
        audit_db_path=default_audit_db_path(),
        ui=ui,
        model=model,
        computer=computer,
    )
