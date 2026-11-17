"""Avis du bureau pendant l'installation du premier démarrage de l'ISO.

Sur l'ISO, ``myos-user-firstrun`` installe les dépendances PyPI de My_OS (``pynput``,
``ollama``) en tâche de fond, puis démarre le daemon (cf. packaging/README.md). Tant
qu'elles manquent, ``myosd`` ne tourne pas : le raccourci global, qu'il écoute, n'ouvre
rien, et le popup ne peut rien montrer. Seul un avis du bureau (notification) peut alors
dire à l'utilisateur ce qui se passe. Le script en montre deux :

* ``waiting`` : My_OS n'est pas encore prêt, faute de connexion à Internet ;
  l'installation reprendra d'elle-même. L'avis reste affiché (aucun délai d'expiration)
  jusqu'à ce que l'utilisateur le ferme ou que ``ready`` le remplace ;
* ``ready`` : l'installation est faite, le raccourci ouvre l'assistant. Il remplace
  l'avis ``waiting`` dont l'identifiant est donné.

Ligne de commande, lancée par le script avec le Python du système ::

    python -m ui.setup_notice waiting       # écrit l'identifiant de l'avis
    python -m ui.setup_notice ready [ID]    # remplace l'avis ID

Langue : celle du popup, soit son choix mémorisé (``~/.config/my_os/popup.conf``,
cf. :mod:`ui.popup`), sinon ``language`` de la config, sinon la langue du système
(:func:`core.i18n.resolve_language`). Le raccourci est celui de la config, écrit comme
dans le popup (``Ctrl+Alt+Espace``).

L'avis passe par le serveur de notifications du bureau (D-Bus,
``org.freedesktop.Notifications`` ; xfce4-notifyd sur l'ISO, démarré à la demande).
Sans lui, rien ne s'affiche et rien n'échoue : la cause va sur la sortie d'erreur, soit
le journal du script (``firstrun.log``).
"""

from __future__ import annotations

import configparser
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from core.config import load_config
from core.i18n import N_, resolve_language, set_language, tr
from core.logger import get_logger
from ui.window_chrome import hotkey_label

_log = get_logger("myos.setup")

WAITING = "waiting"
READY = "ready"
EVENTS = (WAITING, READY)

APP_NAME = "My_OS"
_SUMMARIES = {
    WAITING: N_("My_OS n'est pas encore prêt"),
    READY: N_("My_OS est prêt"),
}
_BODIES = {
    # Montré hors ligne comme réseau annoncé mais pip en échec (portail captif, PyPI
    # injoignable) : Internet « ne répond pas », sans accuser la connexion seule.
    WAITING: N_(
        "Il lui reste des composants à télécharger, mais Internet ne répond pas pour "
        "l'instant (pas de connexion, ou une page de connexion à valider ?). Dès qu'il "
        "répondra, l'installation reprendra d'elle-même et l'assistant ({hotkey}) "
        "sera prêt peu après."
    ),
    READY: N_(
        "Ses composants sont installés : {hotkey} ouvre l'assistant. Le modèle local "
        "peut demander encore quelques minutes ; l'assistant vous dira s'il n'est pas "
        "prêt."
    ),
}
# Icônes du thème du bureau (noms freedesktop).
_ICONS = {WAITING: "network-offline", READY: "dialog-information"}
# Délai d'expiration en ms : 0 = jamais (l'avis reste jusqu'à son remplacement),
# -1 = celui du serveur de notifications.
_EXPIRE_MS = {WAITING: 0, READY: -1}

# Envoi d'un avis : (application, avis remplacé ou 0, icône, titre, texte, expiration
# en ms) → identifiant de l'avis affiché.
Sender = Callable[[str, int, str, str, str, int], int]


def popup_language(environ: Mapping[str, str] | None = None) -> str | None:
    """Langue choisie dans le popup (menu de sa barre de titre), si mémorisée.

    Le popup la garde avec sa géométrie dans ``QSettings("my_os", "popup")``, soit sous
    Linux le fichier INI ``$XDG_CONFIG_HOME/my_os/popup.conf`` (défaut ``~/.config``),
    clé ``language`` du groupe ``General``. Le fichier est une donnée : seule une valeur
    reconnue par :func:`core.i18n.resolve_language` comptera.
    """
    env = os.environ if environ is None else environ
    config_home = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read(Path(config_home) / "my_os" / "popup.conf", encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return None
    return parser.get("General", "language", fallback=None)


def notice_text(event: str, hotkey: str) -> tuple[str, str]:
    """Titre et texte de l'avis ``event`` dans la langue courante ; ``hotkey`` est le
    raccourci au format de la config (``<ctrl>+<alt>+<space>``)."""
    return tr(_SUMMARIES[event]), tr(_BODIES[event], hotkey=hotkey_label(hotkey))


def _dbus_notify(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    app: str, replaces: int, icon: str, summary: str, body: str, expire_ms: int
) -> int:
    """``Notify`` du serveur de notifications du bureau (bus de session)."""
    import dbus  # pylint: disable=import-outside-toplevel

    proxy = dbus.SessionBus().get_object(
        "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
    )
    server = dbus.Interface(proxy, "org.freedesktop.Notifications")
    return int(
        server.Notify(
            app,
            dbus.UInt32(replaces),
            icon,
            summary,
            body,
            dbus.Array([], signature="s"),
            dbus.Dictionary({"urgency": dbus.Byte(1)}, signature="sv"),
            dbus.Int32(expire_ms),
        )
    )


def notify(
    event: str, replaces: int = 0, *, sender: Sender | None = None
) -> int | None:
    """Affiche l'avis ``event`` (remplace l'avis ``replaces`` s'il est donné).

    Renvoie l'identifiant de l'avis, ou ``None`` s'il n'a pas pu être affiché : jamais
    d'exception, le script d'installation ne doit pas en dépendre.
    """
    try:
        config = load_config()
        set_language(resolve_language(popup_language(), config.language))
        summary, body = notice_text(event, config.hotkey)
        send = sender or _dbus_notify
        return send(APP_NAME, replaces, _ICONS[event], summary, body, _EXPIRE_MS[event])
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # Config illisible, raccourci inattendu, dbus absent, bus de session ou serveur
        # injoignable : l'avis est perdu, pas l'installation.
        _log.warning("Avis du bureau « %s » non affiché : %s", event, exc)
        return None


def main(argv: Sequence[str] | None = None) -> int:
    """``waiting`` ou ``ready [ID]`` : affiche l'avis et écrit son identifiant."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in EVENTS or len(args) > 2:
        print("usage : python -m ui.setup_notice waiting | ready [ID]", file=sys.stderr)
        return 2
    # isdecimal, pas isdigit : « ² » est un chiffre pour isdigit, mais int() le refuse.
    replaces = int(args[1]) if len(args) == 2 and args[1].isdecimal() else 0
    notice_id = notify(args[0], replaces)
    if notice_id:
        print(notice_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
