"""Bureau X11 : fenêtres (EWMH), capture d'écran et injection clavier/souris.

* **Lecture** des fenêtres par ``python-xlib`` (dépendance de ``pynput``) :
  ``_NET_CLIENT_LIST_STACKING``, ``_NET_ACTIVE_WINDOW``, titre, classe, PID,
  position avec le cadre (``_NET_FRAME_EXTENTS``), état réduit.
* **Capture** de l'écran par Pillow (``ImageGrab``, XCB).
* **Injection** par ``xdotool`` (XTEST), lancé sans shell via
  :func:`core.elevation.run_command`, le point d'exécution unique des commandes
  système. ``xdotool`` tape l'Unicode quelle que soit la disposition du clavier
  (« é », « € », « » »), là où ``pynput`` se trompe sur les caractères absents de
  la disposition (vu en VM : « » » tapé « « »).

Chaque injection passe d'abord par un **garde** (:class:`InputGuard`) : une fois le
contrôle arrêté (Échap, bouton ■), plus aucun événement n'est envoyé, quoi que
fasse le modèle. C'est la garantie de l'arrêt, au plus bas niveau.

X11 seulement : sous Wayland, ni XTEST ni capture globale (le compositeur les
interdit). Les imports lourds (Xlib, Pillow) sont paresseux : le module se charge
sous Windows et dans les tests.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Protocol

from core.elevation import CommandResult, run_command
from core.i18n import tr
from core.logger import get_logger
from core.session import SESSION_WAYLAND, detect_session_type
from tools.computer.screen import Rect, Window

_log = get_logger("myosd.computer.x11")

# Délai entre deux caractères tapés (ms) : assez pour que chaque application suive.
TYPE_DELAY_MS = 12
# Délai entre deux clics ou touches répétés (ms).
REPEAT_DELAY_MS = 80
# Délai max d'une commande xdotool (s) : une fenêtre qui ne s'active pas ne bloque pas.
_XDOTOOL_TIMEOUT_S = 10.0
# Boutons de souris X11.
MOUSE_BUTTONS = {"left": 1, "middle": 2, "right": 3}
SCROLL_BUTTONS = {"up": 4, "down": 5, "left": 6, "right": 7}

# Types de fenêtres EWMH qui ne sont pas des fenêtres d'application.
_SKIPPED_TYPES = ("_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK")


class DesktopError(RuntimeError):
    """Échec d'une opération sur le bureau (message lisible, renvoyé au modèle)."""


class ControlStopped(DesktopError):
    """Le contrôle a été arrêté (Échap, bouton ■) ou n'est pas accordé : rien n'est
    injecté."""


class InputGuard(Protocol):
    """Garde consulté avant chaque événement injecté (cf. daemon.control)."""

    def check(self) -> None:
        """Lève une exception si le contrôle n'est pas (ou plus) autorisé."""

    def expect_escape(self, count: int = 1) -> None:
        """Prévient que ``count`` touches Échap vont être injectées (ce ne sont pas
        des demandes d'arrêt)."""


def unavailable_reason(environ: dict[str, str] | None = None) -> str | None:
    """Pourquoi le bureau X11 est inaccessible (``None`` s'il l'est)."""
    env = os.environ if environ is None else environ
    if detect_session_type(env) == SESSION_WAYLAND:
        return tr(
            "le contrôle de l'écran demande une session X11 : sous Wayland "
            "(expérimental), le compositeur interdit la capture et l'injection"
        )
    if not env.get("DISPLAY"):
        return tr("aucun écran X11 accessible (variable DISPLAY absente)")
    return None


# Économiseurs d'écran / verrous joignables sur le bus de session (service, objet,
# interface) : Xfce d'abord (celui de l'ISO), puis les interfaces courantes.
_SCREENSAVERS = (
    ("org.xfce.ScreenSaver", "/org/xfce/ScreenSaver", "org.xfce.ScreenSaver"),
    (
        "org.freedesktop.ScreenSaver",
        "/org/freedesktop/ScreenSaver",
        "org.freedesktop.ScreenSaver",
    ),
    ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver"),
    ("org.mate.ScreenSaver", "/org/mate/ScreenSaver", "org.mate.ScreenSaver"),
    (
        "org.cinnamon.ScreenSaver",
        "/org/cinnamon/ScreenSaver",
        "org.cinnamon.ScreenSaver",
    ),
)


def screen_locked() -> bool:
    """``True`` si l'écran est verrouillé (ou l'économiseur actif).

    Interroge les économiseurs connus sur le bus de session (``GetActive``). Sans
    ``dbus-python`` ni économiseur joignable, renvoie ``False`` : l'écran de
    verrouillage, lui, reste protégé par la liste de blocage des fenêtres.
    """
    try:
        import dbus  # pylint: disable=import-outside-toplevel
    except ImportError:
        return False
    try:
        bus = dbus.SessionBus()
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return False
    for service, path, interface in _SCREENSAVERS:
        try:
            proxy = bus.get_object(service, path, introspect=False)
            if bool(proxy.GetActive(dbus_interface=interface, timeout=0.5)):
                return True
        except Exception:  # noqa: BLE001, S112  # pylint: disable=broad-exception-caught
            continue
    return False


class X11Desktop:  # pylint: disable=too-many-public-methods
    """Accès au bureau X11. Une connexion Xlib par opération (sûr entre threads)."""

    def __init__(
        self,
        *,
        guard: InputGuard | None = None,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self._guard = guard
        self._runner = runner

    def set_guard(self, guard: InputGuard | None) -> None:
        """Branche le garde de la session de contrôle (``None`` : aucun événement)."""
        self._guard = guard

    # ---- lecture -------------------------------------------------------------
    @staticmethod
    def _open() -> Any:
        try:
            from Xlib import display  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise DesktopError(
                tr("python-xlib est absent (dépendance de pynput)")
            ) from exc
        try:
            return display.Display()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise DesktopError(
                tr("connexion au serveur X impossible : {error}", error=exc)
            ) from exc

    def screen_rect(self) -> Rect:
        """Taille de l'écran (bureau virtuel X11 complet)."""
        disp = self._open()
        try:
            screen = disp.screen()
            return Rect(0, 0, screen.width_in_pixels, screen.height_in_pixels)
        finally:
            disp.close()

    def windows(self) -> list[Window]:
        """Fenêtres d'application du bureau courant, de la plus haute à la plus basse.

        Le bureau et les panneaux (docks) sont exclus ; les fenêtres réduites sont
        gardées (marquées), pour pouvoir les rouvrir.
        """
        disp = self._open()
        try:
            return _list_windows(disp, include_docks=False)
        finally:
            disp.close()

    def docks(self) -> list[Window]:
        """Panneaux du bureau (barre des tâches, dock de lanceurs)."""
        disp = self._open()
        try:
            return _list_windows(disp, include_docks=True, only_docks=True)
        finally:
            disp.close()

    def window_at(self, x: int, y: int) -> Window | None:
        """Fenêtre visible la plus haute sous le point ``(x, y)`` (panneaux compris)."""
        disp = self._open()
        try:
            for window in _list_windows(disp, include_docks=True):
                if not window.minimized and window.rect.contains(x, y):
                    return window
            return None
        finally:
            disp.close()

    def active_window(self) -> Window | None:
        """Fenêtre qui a le focus (``None`` : le bureau)."""
        for window in self.windows():
            if window.active:
                return window
        return None

    @staticmethod
    def capture() -> Any:
        """Capture de tout l'écran (image Pillow RGB)."""
        try:
            from PIL import ImageGrab  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise DesktopError(tr("Pillow est absent (paquet python-pillow)")) from exc
        try:
            return ImageGrab.grab(xdisplay=None).convert("RGB")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise DesktopError(
                tr("capture d'écran impossible : {error}", error=exc)
            ) from exc

    # ---- injection -----------------------------------------------------------
    def check_guard(self) -> None:
        """Lève :class:`ControlStopped` si aucune action n'est permise en ce moment."""
        if self._guard is None:
            raise ControlStopped("aucune session de contrôle active : action refusée")
        self._guard.check()

    def _xdotool(self, *args: str) -> CommandResult:
        self.check_guard()
        result = run_command(
            ["xdotool", *args], timeout=_XDOTOOL_TIMEOUT_S, runner=self._runner
        )
        if not result.ok:
            detail = result.stderr.strip() or f"code {result.returncode}"
            raise DesktopError(
                tr(
                    "xdotool {command} a échoué : {detail}",
                    command=args[0],
                    detail=detail,
                )
            )
        return result

    def click(self, x: int, y: int, *, button: str = "left", count: int = 1) -> None:
        """Déplace le pointeur en ``(x, y)`` et clique ``count`` fois."""
        number = MOUSE_BUTTONS.get(button)
        if number is None:
            raise DesktopError(tr("bouton de souris inconnu : {button}", button=button))
        self._xdotool(
            "mousemove",
            "--sync",
            str(x),
            str(y),
            "click",
            "--repeat",
            str(max(1, count)),
            "--delay",
            str(REPEAT_DELAY_MS),
            str(number),
        )

    def scroll(self, x: int, y: int, direction: str, amount: int) -> None:
        """Fait défiler (molette) sous le point ``(x, y)``."""
        number = SCROLL_BUTTONS.get(direction)
        if number is None:
            raise DesktopError(
                tr(
                    "direction de défilement inconnue : {direction}",
                    direction=direction,
                )
            )
        self._xdotool(
            "mousemove",
            "--sync",
            str(x),
            str(y),
            "click",
            "--repeat",
            str(max(1, amount)),
            "--delay",
            "40",
            str(number),
        )

    def type_text(self, text: str) -> None:
        """Tape ``text`` (Unicode, retours à la ligne = Entrée) dans la fenêtre active."""
        self._xdotool(
            "type", "--clearmodifiers", "--delay", str(TYPE_DELAY_MS), "--", text
        )

    def key(self, combo: str, *, repeat: int = 1) -> None:
        """Appuie sur une combinaison xdotool déjà validée (ex. ``ctrl+s``).

        Un Échap injecté est annoncé au garde avant l'envoi : l'écoute de la touche
        d'arrêt le reconnaît et ne coupe pas le contrôle.
        """
        self.check_guard()
        if "Escape" in combo.split("+") and self._guard is not None:
            self._guard.expect_escape(max(1, repeat))
        self._xdotool(
            "key",
            "--clearmodifiers",
            "--repeat",
            str(max(1, repeat)),
            "--delay",
            str(REPEAT_DELAY_MS),
            combo,
        )

    def activate(self, window_id: int) -> None:
        """Donne le focus à une fenêtre (la rouvre si elle est réduite)."""
        self._xdotool("windowactivate", "--sync", str(window_id))


def _atom_names(disp: Any, atoms: list[int]) -> set[str]:
    names = set()
    for atom in atoms:
        try:
            names.add(disp.get_atom_name(atom))
        except Exception:  # noqa: BLE001, S112  # pylint: disable=broad-exception-caught
            continue
    return names


def _prop(disp: Any, window: Any, name: str) -> list[Any] | None:
    try:
        prop = window.get_full_property(disp.intern_atom(name), 0)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return None
    return list(prop.value) if prop is not None else None


def _title(disp: Any, window: Any) -> str:
    try:
        prop = window.get_full_property(
            disp.intern_atom("_NET_WM_NAME"), disp.intern_atom("UTF8_STRING")
        )
        if prop is not None and prop.value:
            value = prop.value
            return (
                value.decode("utf-8", "replace")
                if isinstance(value, bytes)
                else str(value)
            )
        name = window.get_wm_name()
        return name.decode("latin-1") if isinstance(name, bytes) else (name or "")
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return ""


def _rects(disp: Any, window: Any) -> tuple[Rect, Rect]:
    """(cadre avec la barre de titre, contenu de l'application) en coordonnées d'écran."""
    root = disp.screen().root
    geometry = window.get_geometry()
    origin = root.translate_coords(window, 0, 0)
    left = right = top = bottom = 0
    extents = _prop(disp, window, "_NET_FRAME_EXTENTS")
    if extents and len(extents) == 4:
        left, right, top, bottom = (int(v) for v in extents)
    content = Rect(
        int(origin.x), int(origin.y), int(geometry.width), int(geometry.height)
    )
    frame = Rect(
        content.x - left,
        content.y - top,
        content.width + left + right,
        content.height + top + bottom,
    )
    return frame, content


def _list_windows(
    disp: Any, *, include_docks: bool, only_docks: bool = False
) -> list[Window]:
    """Fenêtres gérées du bureau courant, de la plus haute à la plus basse."""
    root = disp.screen().root
    stacking = _prop(disp, root, "_NET_CLIENT_LIST_STACKING") or []
    active_ids = _prop(disp, root, "_NET_ACTIVE_WINDOW") or [0]
    current = (_prop(disp, root, "_NET_CURRENT_DESKTOP") or [0])[0]
    result: list[Window] = []
    for window_id in reversed(stacking):
        try:
            window = disp.create_resource_object("window", window_id)
            types = _atom_names(disp, _prop(disp, window, "_NET_WM_WINDOW_TYPE") or [])
            is_dock = "_NET_WM_WINDOW_TYPE_DOCK" in types
            if (only_docks and not is_dock) or (is_dock and not include_docks):
                continue
            if "_NET_WM_WINDOW_TYPE_DESKTOP" in types:
                continue
            desktop = (_prop(disp, window, "_NET_WM_DESKTOP") or [current])[0]
            if desktop not in (current, 0xFFFFFFFF):
                continue
            states = _atom_names(disp, _prop(disp, window, "_NET_WM_STATE") or [])
            wm_class = window.get_wm_class() or ("", "")
            pid = (_prop(disp, window, "_NET_WM_PID") or [None])[0]
            frame, content = _rects(disp, window)
            result.append(
                Window(
                    id=int(window_id),
                    title=_title(disp, window),
                    app=str(wm_class[1] or ""),
                    instance=str(wm_class[0] or ""),
                    pid=int(pid) if pid else None,
                    rect=frame,
                    active=int(window_id) == int(active_ids[0]),
                    minimized="_NET_WM_STATE_HIDDEN" in states,
                    content=content,
                )
            )
        except Exception:  # noqa: BLE001, S112  # pylint: disable=broad-exception-caught
            continue  # fenêtre détruite entre-temps
    return result
