"""Capture du raccourci clavier global — X11 (``pynput``) et Wayland (portal).

Le daemon est seul à écouter le raccourci. Quand la combinaison est pressée, il
déclenche ``on_activate`` (le daemon ordonne alors au popup de s'afficher).

**Sélection de backend (jalon 5, cf. docs/INTERFACES.md §9).** Le contrat public
:class:`HotkeyListener` est **inchangé** ; en interne, la façade choisit selon le
type de session :

* X11 (chemin **nominal**) → :class:`_X11Backend` (``pynput.keyboard.GlobalHotKeys``) ;
* Wayland (**expérimental**) → :class:`_PortalBackend`, qui passe par le portal XDG
  ``org.freedesktop.portal.GlobalShortcuts`` (D-Bus).

Toutes les dépendances lourdes (``pynput``, ``dbus``, GLib) sont importées
**paresseusement** dans ``start()`` : instancier un backend n'importe rien, donc la
sélection est testable sans X ni Wayland réels.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol

from core.logger import get_logger
from core.session import SESSION_WAYLAND, detect_session_type

_log = get_logger("myosd.hotkey")

# Identifiant logique du raccourci côté portal (My_OS n'en déclare qu'un).
_SHORTCUT_ID = "activate"


class _HotkeyBackend(Protocol):
    """Contrat interne partagé par les backends X11 et Wayland."""

    def start(self) -> None:
        """Démarre l'écoute du raccourci (en arrière-plan)."""

    def stop(self) -> None:
        """Arrête l'écoute du raccourci."""


class _X11Backend:
    """Backend X11 (nominal) : ``pynput.keyboard.GlobalHotKeys``."""

    def __init__(self, hotkey: str, on_activate: Callable[[], None]) -> None:
        self._hotkey = hotkey
        self._on_activate = on_activate
        self._listener = None

    def start(self) -> None:
        """Démarre l'écoute (thread géré par pynput).

        ``pynput`` est importé ici (et non au chargement) pour éviter une erreur
        d'import sur un environnement sans serveur X.
        """
        from pynput import keyboard

        self._listener = keyboard.GlobalHotKeys({self._hotkey: self._on_activate})
        self._listener.start()
        _log.info("Raccourci global X11 (pynput) : %s", self._hotkey)

    def stop(self) -> None:
        """Arrête l'écoute du raccourci."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


class PortalTransport(Protocol):
    """Transport D-Bus du portal ``GlobalShortcuts`` (injectable pour les tests).

    Le portal est asynchrone et exige une boucle d'événements ; cette abstraction
    isole toute la mécanique D-Bus (même esprit que ``SettingsBackend`` du jalon 3).
    """

    def listen(
        self, hotkey: str, shortcut_id: str, on_activate: Callable[[], None]
    ) -> None:
        """Crée la session portal, lie le raccourci, puis **bloque** sur la boucle
        d'événements en appelant ``on_activate`` à chaque activation. Rend la main
        quand :meth:`stop` est invoqué."""

    def stop(self) -> None:
        """Arrête la boucle d'événements (débloque :meth:`listen`)."""


class _PortalBackend:
    """Backend Wayland (expérimental) : portal ``GlobalShortcuts``.

    Délègue le D-Bus à un :class:`PortalTransport` injectable et fait tourner sa
    boucle bloquante dans un thread. Toute erreur du portal est capturée et
    journalisée — elle ne tue jamais le daemon — et le raccourci reste simplement
    inactif (X11 demeure le chemin nominal).
    """

    def __init__(
        self,
        hotkey: str,
        on_activate: Callable[[], None],
        *,
        transport: PortalTransport | None = None,
    ) -> None:
        self._hotkey = hotkey
        self._on_activate = on_activate
        self._transport = transport
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Lance l'écoute du portal dans un thread dédié."""
        if self._transport is None:
            self._transport = _DBusPortalTransport()
        self._thread = threading.Thread(
            target=self._run, name="hotkey-portal", daemon=True
        )
        self._thread.start()
        _log.info(
            "Raccourci global Wayland (portal GlobalShortcuts) demandé : %s "
            "(expérimental — support selon le compositeur)",
            self._hotkey,
        )

    def _run(self) -> None:
        assert self._transport is not None
        try:
            self._transport.listen(self._hotkey, _SHORTCUT_ID, self._on_activate)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Aucune défaillance du portal ne doit faire tomber le daemon.
            _log.warning(
                "Portal GlobalShortcuts indisponible (%s) : raccourci Wayland "
                "inactif. Vérifiez xdg-desktop-portal + un backend (GNOME/KDE), "
                "ou liez la combinaison via votre compositeur. X11 reste nominal.",
                exc,
            )

    def stop(self) -> None:
        """Arrête la boucle du portal et attend la fin du thread."""
        if self._transport is not None:
            try:
                self._transport.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


def _select_backend(
    hotkey: str, on_activate: Callable[[], None], session_type: str
) -> _HotkeyBackend:
    """Choisit le backend selon le type de session (cf. docs/INTERFACES.md §9).

    Wayland → portal ; tout le reste (dont X11, le défaut) → pynput. L'instanciation
    n'importe aucune dépendance lourde (imports paresseux dans ``start()``).
    """
    if session_type == SESSION_WAYLAND:
        return _PortalBackend(hotkey, on_activate)
    return _X11Backend(hotkey, on_activate)


class HotkeyListener:
    """Écoute une combinaison globale et appelle ``on_activate`` à chaque appui.

    Contrat public **inchangé** : ``HotkeyListener(hotkey, on_activate)``. Les deux
    kwargs optionnels n'existent que pour les tests :

    * ``session_type`` force le type de session (sinon :func:`detect_session_type`) ;
    * ``backend`` court-circuite la sélection (injection directe d'un faux backend).
    """

    def __init__(
        self,
        hotkey: str,
        on_activate: Callable[[], None],
        *,
        session_type: str | None = None,
        backend: _HotkeyBackend | None = None,
    ) -> None:
        self._hotkey = hotkey
        self._on_activate = on_activate
        self._session_type = session_type
        self._backend = backend

    def start(self) -> None:
        """Résout le backend (selon la session) puis démarre l'écoute."""
        if self._backend is None:
            session = self._session_type or detect_session_type()
            self._backend = _select_backend(self._hotkey, self._on_activate, session)
        self._backend.start()

    def stop(self) -> None:
        """Arrête l'écoute du raccourci (sans effet si jamais démarrée)."""
        if self._backend is not None:
            self._backend.stop()


class _DBusPortalTransport:
    """Transport de production : portal ``GlobalShortcuts`` via ``dbus-python`` + GLib.

    **Expérimental et non couvert par les tests** : nécessite un bus de session et un
    compositeur Wayland implémentant le portal. ``dbus`` et ``gi``/GLib sont importés
    **paresseusement** ; le module reste importable sans eux (Windows, CI, X11).

    Flux portal (XDG) : ``CreateSession`` → réponse via objet ``Request`` →
    ``BindShortcuts`` → réponse → signal ``Activated``. Le ``preferred_trigger`` est
    *best-effort* : le compositeur peut l'ignorer et laisser l'utilisateur lier la
    combinaison lui-même.
    """

    _PORTAL_BUS = "org.freedesktop.portal.Desktop"
    _PORTAL_PATH = "/org/freedesktop/portal/desktop"
    _SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
    _REQUEST_IFACE = "org.freedesktop.portal.Request"

    def __init__(self) -> None:
        self._loop = None

    def listen(  # pylint: disable=too-many-locals
        self, hotkey: str, shortcut_id: str, on_activate: Callable[[], None]
    ) -> None:
        """Implémente le contrat :meth:`PortalTransport.listen` (bloquant)."""
        import dbus  # pylint: disable=import-outside-toplevel
        import dbus.mainloop.glib  # pylint: disable=import-outside-toplevel
        from gi.repository import GLib  # pylint: disable=import-outside-toplevel

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        portal = bus.get_object(self._PORTAL_BUS, self._PORTAL_PATH)
        shortcuts = dbus.Interface(portal, self._SHORTCUTS_IFACE)

        # Le raccourci activé est signalé globalement : on filtre par identifiant.
        def _on_activated(_session, activated_id, _timestamp, _options) -> None:
            if str(activated_id) == shortcut_id:
                on_activate()

        bus.add_signal_receiver(
            _on_activated,
            signal_name="Activated",
            dbus_interface=self._SHORTCUTS_IFACE,
        )

        # Chemin de la Request renvoyée par le portal (format XDG déterministe).
        sender = bus.get_unique_name()[1:].replace(".", "_")

        def _request_path(token: str) -> str:
            return f"{self._PORTAL_PATH}/request/{sender}/{token}"

        def _bind(session_handle: str) -> None:
            token = "myos_bind"
            entry = (
                dbus.String(shortcut_id),
                dbus.Dictionary(
                    {
                        "description": dbus.String("Ouvrir l'assistant My_OS"),
                        "preferred_trigger": dbus.String(_hotkey_to_trigger(hotkey)),
                    },
                    signature="sv",
                ),
            )
            bus.add_signal_receiver(
                lambda code, _results: _log.info(
                    "BindShortcuts : réponse portal %s", code
                ),
                signal_name="Response",
                dbus_interface=self._REQUEST_IFACE,
                path=_request_path(token),
            )
            shortcuts.BindShortcuts(
                dbus.ObjectPath(session_handle),
                dbus.Array([entry], signature="(sa{sv})"),
                "",
                dbus.Dictionary({"handle_token": dbus.String(token)}, signature="sv"),
            )

        def _on_session(code: int, results: dict) -> None:
            if code != 0:
                _log.warning("CreateSession refusée par le portal (code %s)", code)
                return
            _bind(str(results["session_handle"]))

        create_token = "myos_create"
        bus.add_signal_receiver(
            _on_session,
            signal_name="Response",
            dbus_interface=self._REQUEST_IFACE,
            path=_request_path(create_token),
        )
        shortcuts.CreateSession(
            dbus.Dictionary(
                {
                    "handle_token": dbus.String(create_token),
                    "session_handle_token": dbus.String("myos_session"),
                },
                signature="sv",
            )
        )

        self._loop = GLib.MainLoop()
        self._loop.run()

    def stop(self) -> None:
        """Arrête la boucle GLib (débloque :meth:`listen`)."""
        if self._loop is not None:
            self._loop.quit()
            self._loop = None


def _hotkey_to_trigger(hotkey: str) -> str:
    """Convertit un raccourci pynput en *trigger* portal best-effort.

    Ex. ``"<ctrl>+<alt>+<space>"`` → ``"CTRL+ALT+space"``. Le format XDG attend des
    modificateurs en majuscules ; le compositeur reste libre d'ignorer ce trigger.
    """
    mods = {
        "ctrl": "CTRL",
        "alt": "ALT",
        "shift": "SHIFT",
        "cmd": "LOGO",
        "super": "LOGO",
    }
    out: list[str] = []
    for raw in hotkey.replace("<", "").replace(">", "").split("+"):
        part = raw.strip()
        if part:
            out.append(mods.get(part.lower(), part))
    return "+".join(out)
