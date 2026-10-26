"""Source de vérité statique : niveaux de risque + chemins sensibles.

C'est *ici* que sont déclarés les niveaux de base par outil et la liste des
préfixes système considérés sensibles (déclenchant une escalade
``write/move/delete → niveau 2``). Le LLM n'a aucune voix au chapitre :
l'évaluation passe par :mod:`permissions.policy_engine` qui lit cette table
(cf. docs/SECURITY.md §4 invariants 1 et 2).

Les comparaisons utilisent la sémantique POSIX (le projet cible Linux) pour
rester déterministes quel que soit l'OS de développement.
"""

from __future__ import annotations

import os
import posixpath
import re
import unicodedata
from pathlib import Path

# Niveaux de base par outil (avant escalade par arguments).
TOOL_RISK_LEVELS: dict[str, int] = {
    # Fichiers (jalon 2)
    "read_file": 0,
    "list_dir": 0,
    "write_file": 1,
    "move_file": 1,
    "create_file": 1,
    "delete_file": 2,
    # Processus (jalon 3)
    "list_processes": 0,
    "kill_process": 2,
    # Paquets / pacman (jalon 3)
    "search_package": 0,
    "install_package": 1,  # niveau 1 (install) MAIS requires_elevation (root)
    "update_system": 2,
    "remove_package": 2,
    # Réglages système / D-Bus (jalon 3)
    "set_brightness": 1,
    "set_volume": 1,
    "set_mute": 1,
    "set_wifi": 1,
    # Contrôle de l'ordinateur (écran, souris, clavier). Niveau 1, mais AUCUN ne
    # s'exécute sans la session de contrôle accordée pour la requête (cf.
    # COMPUTER_TOOLS et docs/SECURITY.md §4) ; escalade selon la cible.
    "observe_screen": 1,
    "click": 1,
    "type_text": 1,
    "press_keys": 1,
    "scroll": 1,
    "open_app": 1,
    "focus_window": 1,
}

# Outils du contrôle de l'ordinateur. Ils ne s'exécutent que dans une session de
# contrôle, accordée par l'utilisateur pour UNE requête (confirmation dédiée) :
# elle couvre leurs actions de niveau 1, jamais celles escaladées en niveau 2, et
# aucun grant « dossier » ou « session » ne leur est jamais proposé.
COMPUTER_TOOLS: frozenset[str] = frozenset(
    {
        "observe_screen",
        "click",
        "type_text",
        "press_keys",
        "scroll",
        "open_app",
        "focus_window",
    }
)

# Applications dans lesquelles l'IA n'agit JAMAIS (blocklist, niveau 3), par nom
# de classe/instance de fenêtre ou identifiant d'application : demandes de mot de
# passe (élévation polkit, trousseau), verrouillage de l'écran, gestionnaires de
# mots de passe, partitionnement et formatage des disques.
PROTECTED_APPS: frozenset[str] = frozenset(
    {
        # agents polkit (mot de passe administrateur)
        "polkit-gnome-authentication-agent-1",
        "xfce-polkit",
        "xfce4-polkit",
        "lxpolkit",
        "lxqt-policykit-agent",
        "polkit-kde-authentication-agent-1",
        "polkit-mate-authentication-agent-1",
        "mate-polkit",
        # trousseau et gestionnaires de mots de passe
        "gcr-prompter",
        "pinentry",
        "pinentry-gtk-2",
        "pinentry-qt",
        "seahorse",
        "kwalletmanager5",
        "keepassxc",
        "bitwarden",
        # verrouillage de l'écran
        "xfce4-screensaver",
        "xfce4-screensaver-dialog",
        "light-locker",
        "xscreensaver",
        "gnome-screensaver",
        "i3lock",
        "slock",
        "xsecurelock",
        # partitionnement / formatage
        "gparted",
        "gnome-disks",
        "partitionmanager",
        "blivet-gui",
    }
)

# Émulateurs de terminal : y taper, c'est exécuter des commandes. Toute action de
# clavier ou de souris qui les vise passe en niveau 2 (texte exact montré).
TERMINAL_APPS: frozenset[str] = frozenset(
    {
        "xfce4-terminal",
        "xterm",
        "uxterm",
        "gnome-terminal",
        "gnome-terminal-server",
        "kgx",
        "console",
        "ptyxis",
        "konsole",
        "kitty",
        "alacritty",
        "terminator",
        "tilix",
        "urxvt",
        "rxvt",
        "st",
        "foot",
        "wezterm",
        "wezterm-gui",
        "lxterminal",
        "mate-terminal",
        "qterminal",
        "sakura",
        "terminology",
        "guake",
        "tilda",
        "yakuake",
        "ghostty",
    }
)

# Lanceurs de commandes : comme dans un terminal, ce qu'on y tape est exécuté
# (« Exécuter un programme » d'Xfce, ouvert par Alt+F2 ; KRunner, rofi…). Mêmes règles
# que les terminaux : niveau 2.
COMMAND_LAUNCHERS: frozenset[str] = frozenset(
    {
        "xfce4-appfinder",
        "xfrun4",
        "krunner",
        "gmrun",
        "grun",
        "rofi",
        "dmenu",
        "ulauncher",
        "albert",
        "synapse",
        "kupfer",
    }
)

# Paramètres du système : les modifier est une « modification système » (niveau 2).
SYSTEM_SETTINGS_APPS: frozenset[str] = frozenset(
    {
        "xfce4-settings-manager",
        "xfce4-display-settings",
        "xfce4-keyboard-settings",
        "xfce4-mouse-settings",
        "xfce4-power-manager-settings",
        "xfce4-session-settings",
        "xfce4-accessibility-settings",
        "xfce4-mime-settings",
        "xfce4-appearance-settings",
        "xfwm4-settings",
        "xfwm4-tweaks-settings",
        "xfwm4-workspace-settings",
        "xfce4-panel",
        "nm-connection-editor",
        "lightdm-gtk-greeter-settings",
        "gnome-control-center",
        "systemsettings",
        "blueman-manager",
        "system-config-printer",
        "users-admin",
    }
)

# Libellés d'action irréversible ou sortante : un clic dessus passe en niveau 2.
# Comparés en minuscules sans accents, mot entier ou expression entière.
DESTRUCTIVE_LABELS: tuple[str, ...] = (
    # suppression, perte de données
    "supprimer",
    "suppression",
    "effacer",
    "vider la corbeille",
    "ecraser",
    "remplacer",
    "sans enregistrer",
    "ne pas enregistrer",
    "desinstaller",
    "formater",
    "reinitialiser",
    "delete",
    "remove",
    "erase",
    "empty trash",
    "overwrite",
    "replace",
    "discard",
    "don't save",
    "dont save",
    "close without saving",
    "uninstall",
    "reset",
    # envoi, publication, paiement
    "envoyer",
    "publier",
    "payer",
    "acheter",
    "commander",
    "transferer",
    "send",
    "submit",  # le pendant anglais d'« Envoyer » sur un formulaire
    "publish",
    "pay",
    "buy",
    "purchase",
    "checkout",
    "place order",
    "transfer",
    # fin de session
    "eteindre",
    "redemarrer",
    "deconnexion",
    "se deconnecter",
    "shut down",
    "shutdown",
    "restart",
    "reboot",
    "log out",
    "logout",
)

# Combinaisons jamais injectées (niveau 3), forme canonique de tools.computer.keys :
# tuer la session graphique, changer de console, redémarrer, touches système.
FORBIDDEN_COMBOS: frozenset[str] = frozenset(
    {
        "ctrl+alt+BackSpace",
        "ctrl+alt+Delete",
        *(f"ctrl+alt+F{n}" for n in range(1, 13)),
    }
)
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {"Sys_Req", "XF86PowerOff", "XF86Sleep", "XF86Suspend", "XF86LogOff"}
)
# Combinaisons à effet irréversible (niveau 2) : Maj+Suppr supprime définitivement,
# sans passer par la corbeille, dans les gestionnaires de fichiers.
DESTRUCTIVE_COMBOS: frozenset[str] = frozenset({"shift+Delete"})
# Raccourcis du bureau qui ouvrent un terminal ou un lanceur de commandes (Xfce, et les
# mêmes sous GNOME et KDE) : niveau 2, comme ouvrir un terminal par open_app. Vu en VM :
# après le refus de l'ouverture d'un terminal, le modèle passait par Ctrl+Alt+T.
LAUNCHER_COMBOS: frozenset[str] = frozenset({"ctrl+alt+t", "alt+F2", "alt+F3"})
# Touches qui activent l'élément focalisé (bouton, lien) comme un clic : sur un
# libellé destructeur, elles escaladent comme le clic lui-même.
ACTIVATION_KEYS: frozenset[str] = frozenset({"Return", "KP_Enter", "space"})

# Paquets dont la suppression rendrait le système inutilisable : la blocklist
# refuse ``remove_package`` sur ces noms (niveau 3, jamais exécuté). Liste
# volontairement conservatrice — on bloque le socle, pas chaque dépendance.
CRITICAL_PACKAGES: frozenset[str] = frozenset(
    {
        "base",
        "bash",
        "coreutils",
        "filesystem",
        "gcc-libs",
        "glibc",
        "linux",
        "linux-firmware",
        "pacman",
        "pacman-mirrorlist",
        "polkit",
        "systemd",
        "systemd-libs",
        "sudo",
        "util-linux",
    }
)

# Préfixes système toujours sensibles (liste « standard » choisie au jalon 2).
SYSTEM_SENSITIVE_ROOTS: tuple[str, ...] = (
    "/etc",
    "/boot",
    "/usr",
    "/var/lib",
    "/sys",
    "/proc",
    "/root",
    "/opt",
)

# Sous-chemins sensibles à l'intérieur du HOME utilisateur.
HOME_SENSITIVE_SUBPATHS: tuple[str, ...] = (
    ".ssh",
    ".config/systemd",
)


def app_keys(*names: str) -> frozenset[str]:
    """Noms comparables d'une application : en minuscules, avec et sans préfixe.

    ``"org.xfce.mousepad"`` donne ``{"org.xfce.mousepad", "mousepad"}`` ;
    ``"Xfce4-terminal"`` donne ``{"xfce4-terminal"}``.
    """
    keys: set[str] = set()
    for name in names:
        low = str(name or "").strip().casefold().removesuffix(".desktop")
        if not low:
            continue
        keys.add(low)
        keys.add(low.rsplit(".", 1)[-1])
    return frozenset(keys)


def _fold_label(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text).casefold().replace("’", "'"))
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " " + " ".join(re.sub(r"[^\w']+", " ", plain).split()) + " "


def is_destructive_label(label: str) -> bool:
    """``True`` si un libellé de bouton annonce une action irréversible ou sortante.

    Comparaison par mot ou expression entière : « Removable Drives » n'est pas
    « remove », « Delete » et « Supprimer définitivement » le sont.
    """
    folded = _fold_label(label)
    return any(f" {word} " in folded for word in DESTRUCTIVE_LABELS)


def _posix(path: str | Path) -> str:
    """Normalise une chaîne en chemin POSIX absolu non résolu."""
    return posixpath.normpath(str(path).replace("\\", "/"))


def sensitive_roots(home: str | Path | None = None) -> list[str]:
    """Renvoie la liste résolue des préfixes sensibles.

    ``home`` permet d'injecter un répertoire utilisateur (utile pour les
    tests) ; par défaut, ``~`` est expansé depuis l'environnement puis, sous
    Linux, résolu en chemin réel : les outils fichiers comparent des chemins
    réels, un HOME derrière un lien (``/home`` → ``/var/home``) ne doit pas
    faire échapper ``~/.ssh`` à l'escalade.
    """
    if home is not None:
        home_path = _posix(home)
    else:
        home_path = _posix(posixpath.expanduser("~"))
        if os.name == "posix":
            home_path = os.path.realpath(home_path)
    home_path = home_path.rstrip("/")
    return [
        *SYSTEM_SENSITIVE_ROOTS,
        *[f"{home_path}/{sub}" for sub in HOME_SENSITIVE_SUBPATHS],
    ]


def is_sensitive_path(path: str | Path, home: str | Path | None = None) -> bool:
    """``True`` si ``path`` est exactement, ou est sous, un préfixe sensible.

    Les séquences ``..`` sont normalisées : ``"/etc/../tmp/x"`` devient
    ``"/tmp/x"`` et n'est donc pas considéré sensible.
    """
    normalized = _posix(path)
    for root in sensitive_roots(home):
        if normalized == root or normalized.startswith(root + "/"):
            return True
    return False
