"""Tests de la sélection de backend du raccourci global (jalon 5).

Aucun X ni Wayland réel : on teste la *sélection* de backend (pure), la délégation
de la façade et la mécanique du backend portal via un transport injecté. Les
backends de production (pynput, dbus/GLib) ne sont jamais démarrés ici (leurs
imports restent paresseux).
"""

# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

from collections.abc import Callable

from core.session import SESSION_WAYLAND, SESSION_X11
from daemon import hotkey_listener as hk
from daemon.hotkey_listener import (
    HotkeyListener,
    _PortalBackend,
    _select_backend,
    _SHORTCUT_ID,
    _X11Backend,
    _hotkey_to_trigger,
)


def _noop() -> None:
    pass


# --- Sélection de backend (pure, sans démarrage) -------------------------------


def test_select_backend_x11() -> None:
    backend = _select_backend("<ctrl>+<alt>+<space>", _noop, SESSION_X11)
    assert isinstance(backend, _X11Backend)


def test_select_backend_wayland() -> None:
    backend = _select_backend("<ctrl>+<alt>+<space>", _noop, SESSION_WAYLAND)
    assert isinstance(backend, _PortalBackend)


def test_select_backend_inconnu_retombe_sur_x11() -> None:
    # Toute valeur non-Wayland (dont une session inattendue) → X11 nominal.
    backend = _select_backend("<ctrl>+<alt>+<space>", _noop, "tty")
    assert isinstance(backend, _X11Backend)


# --- Façade : délégation et auto-sélection ------------------------------------


class _FakeBackend:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


def test_facade_delegue_au_backend_injecte() -> None:
    fake = _FakeBackend()
    listener = HotkeyListener("<ctrl>+<alt>+<space>", _noop, backend=fake)

    listener.start()
    listener.stop()

    assert (fake.started, fake.stopped) == (1, 1)


def test_facade_stop_sans_start_ne_plante_pas() -> None:
    # Aucun backend injecté et jamais démarré : stop() est un no-op silencieux.
    HotkeyListener("<ctrl>+<alt>+<space>", _noop).stop()


def test_facade_auto_selectionne_selon_la_session(monkeypatch) -> None:  # noqa: ANN001
    # On remplace _select_backend pour vérifier l'aiguillage sans démarrer de
    # backend réel (pynput/dbus ne sont pas importés).
    captured: dict = {}
    fake = _FakeBackend()

    def _fake_select(
        hotkey: str, _on_activate: Callable[[], None], session_type: str
    ) -> _FakeBackend:
        captured["session"] = session_type
        captured["hotkey"] = hotkey
        return fake

    monkeypatch.setattr(hk, "_select_backend", _fake_select)
    listener = HotkeyListener("<ctrl>+<alt>+m", _noop, session_type=SESSION_WAYLAND)

    listener.start()

    assert captured == {"session": SESSION_WAYLAND, "hotkey": "<ctrl>+<alt>+m"}
    assert fake.started == 1


# --- Backend portal : mécanique via transport injecté --------------------------


class _FakeTransport:
    """Faux PortalTransport : enregistre l'appel et simule deux activations."""

    def __init__(self) -> None:
        self.listen_args: tuple[str, str] | None = None
        self.stopped = False

    def listen(
        self, hotkey: str, shortcut_id: str, on_activate: Callable[[], None]
    ) -> None:
        self.listen_args = (hotkey, shortcut_id)
        on_activate()
        on_activate()

    def stop(self) -> None:
        self.stopped = True


def test_portal_backend_relaie_les_activations() -> None:
    activations: list[int] = []
    transport = _FakeTransport()
    backend = _PortalBackend(
        "<ctrl>+<alt>+<space>",
        lambda: activations.append(1),
        transport=transport,
    )

    backend.start()
    backend.stop()  # joint le thread → garantit la fin de listen()

    assert transport.listen_args == ("<ctrl>+<alt>+<space>", _SHORTCUT_ID)
    assert activations == [1, 1]
    assert transport.stopped is True


def test_portal_backend_n_explose_pas_si_listen_leve() -> None:
    class _Boom:
        def listen(self, *_args: object) -> None:
            raise RuntimeError("pas de portal")

        def stop(self) -> None:
            pass

    backend = _PortalBackend("<ctrl>+<alt>+<space>", _noop, transport=_Boom())
    # _run capture l'exception et journalise : aucun crash ne remonte.
    backend.start()
    backend.stop()


# --- Conversion du trigger best-effort ----------------------------------------


def test_hotkey_to_trigger_modificateurs() -> None:
    assert _hotkey_to_trigger("<ctrl>+<alt>+<space>") == "CTRL+ALT+space"


def test_hotkey_to_trigger_super_devient_logo() -> None:
    assert _hotkey_to_trigger("<super>+m") == "LOGO+m"
