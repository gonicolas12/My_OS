"""Doubles de test du contrôle de l'ordinateur : un bureau scripté, sans X11.

:class:`FakeBackend` implémente le contrat ``ComputerBackend`` : fenêtres et éléments
fixés par le test, actions enregistrées (``actions``), garde simulé (``guard_ok``).
Partagé par les tests des outils, des permissions et du daemon.
"""

# pylint: disable=missing-function-docstring,too-many-instance-attributes
from __future__ import annotations

from dataclasses import replace

from tools.computer.apps import AppEntry
from tools.computer.screen import Element, Observation, Rect, Window
from tools.computer.tools import ScreenContext, build_computer_tools
from tools.computer.x11 import ControlStopped

SCREEN = Rect(0, 0, 1920, 1080)
POPUP_PID = 4242
STOP_RECT = Rect(990, 950, 30, 30)
EDITOR_RECT = Rect(600, 300, 700, 500)


def make_window(  # pylint: disable=too-many-arguments
    window_id: int = 1,
    title: str = "Sans titre 1 - Mousepad",
    *,
    app: str = "Mousepad",
    instance: str = "mousepad",
    pid: int | None = 500,
    rect: Rect = EDITOR_RECT,
    active: bool = True,
    minimized: bool = False,
) -> Window:
    return Window(
        id=window_id,
        title=title,
        app=app,
        instance=instance,
        pid=pid,
        rect=rect,
        active=active,
        minimized=minimized,
        content=Rect(rect.x, rect.y + 30, rect.width, rect.height - 30),
    )


def make_element(
    role: str,
    name: str,
    rect: Rect,
    *,
    source: str = "a11y",
    states: tuple[str, ...] = (),
    value: str = "",
) -> Element:
    return Element(
        role=role,
        name=name,
        rect=rect,
        source=source,
        states=frozenset(states),
        value=value,
    )


class FakeBackend:
    """Bureau scripté : ``observe`` renvoie les fenêtres et éléments fixés."""

    def __init__(
        self,
        windows: list[Window],
        elements: list[Element] | None = None,
        *,
        focused: Element | None = None,
        locked: bool = False,
        apps: dict[str, AppEntry] | None = None,
    ) -> None:
        self.windows_list = list(windows)
        self.elements = list(elements or [])
        self.focused = focused
        self.locked = locked
        self.apps = apps or {}
        self.actions: list[tuple] = []
        self.guard_ok = True
        self.observations = 0
        self.exclusions: list[Rect] = []
        self.prepared = 0
        self.reason: str | None = None
        # Fenêtre ouverte par launch() (open_app) ; None : rien n'apparaît.
        self.new_window_on_launch: Window | None = None
        # Éléments accessibles relus juste avant un clic ; None : ceux de l'écran.
        self.accessible: list[Element] | None = None
        self.guard: object | None = None
        self._last: Observation | None = None

    # ---- état ------------------------------------------------------------------
    def unavailable_reason(self) -> str | None:
        return self.reason

    def observe(self) -> Observation:
        if not self.guard_ok:
            raise ControlStopped("arrêté")
        self.observations += 1
        active = self.active_window()
        self._last = Observation(
            screen=SCREEN,
            windows=list(self.windows_list),
            active=active,
            elements=list(self.elements),
            image=b"jpeg",
            image_size=(1280, 720),
        )
        return self._last

    @property
    def last(self) -> Observation | None:
        return self._last

    def window_at(self, x: int, y: int) -> Window | None:
        for window in self.windows_list:
            if not window.minimized and window.rect.contains(x, y):
                return window
        return None

    def windows(self) -> list[Window]:
        return list(self.windows_list)

    def active_window(self) -> Window | None:
        return next((w for w in self.windows_list if w.active), None)

    def screen_locked(self) -> bool:
        return self.locked

    def reset(self) -> None:
        self._last = None

    def focused_element(self) -> Element | None:
        return self.focused

    def accessible_at(self, x: int, y: int) -> list[Element]:
        source = self.elements if self.accessible is None else self.accessible
        return [e for e in source if e.source == "a11y" and e.rect.contains(x, y)]

    def set_exclusions(self, rects: list[Rect]) -> None:
        self.exclusions = list(rects)

    def prepare(self) -> None:
        self.prepared += 1

    def set_guard(self, guard: object | None) -> None:
        self.guard = guard

    # ---- actions ---------------------------------------------------------------
    def _act(self, *action: object) -> None:
        if not self.guard_ok:
            raise ControlStopped("arrêté")
        self.actions.append(action)

    def click(self, x: int, y: int, *, button: str = "left", count: int = 1) -> None:
        self._act("click", x, y, button, count)

    def type_text(self, text: str) -> None:
        self._act("type", text)

    def key(self, combo: str, *, repeat: int = 1) -> None:
        self._act("key", combo, repeat)

    def scroll(self, x: int, y: int, direction: str, amount: int) -> None:
        self._act("scroll", x, y, direction, amount)

    def activate(self, window_id: int) -> None:
        self._act("activate", window_id)
        self.windows_list = [
            replace(w, active=w.id == window_id) for w in self.windows_list
        ]

    def find_app(self, name: str) -> AppEntry | None:
        return self.apps.get(name.strip().lower())

    def app_names(self) -> list[str]:
        return sorted(entry.name for entry in self.apps.values())

    def launch(self, app: AppEntry) -> None:
        self._act("launch", app.app_id)
        if self.new_window_on_launch is not None:
            self.windows_list = [
                replace(w, active=False) for w in self.windows_list
            ] + [self.new_window_on_launch]


def screen_tools(backend: FakeBackend) -> dict:
    """Outils de l'écran branchés sur ``backend`` (popup = POPUP_PID, ■ = STOP_RECT)."""
    context = ScreenContext(
        backend=backend,
        myos_pids=lambda: {POPUP_PID},
        stop_rect=lambda: STOP_RECT,
        sleep=lambda _seconds: None,
    )
    return build_computer_tools(context)
