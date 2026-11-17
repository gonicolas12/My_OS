"""Tests de la sélection de backend du raccourci global (jalon 5).

Aucun X ni Wayland réel, sauf un test : on teste la *sélection* de backend (pure), la
délégation de la façade, la mécanique du backend portal via un transport injecté, et
l'arrêt du backend X11 sur une écoute factice. Seul le dernier test démarre le vrai
``pynput``, sur un serveur X virtuel (Xvfb) qu'il arrête : ignoré là où Xvfb ou
``pynput`` manquent (Windows).
"""

# pylint: disable=missing-function-docstring,protected-access
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable

import pytest

from core.session import SESSION_WAYLAND, SESSION_X11
from daemon import hotkey_listener as hk
from daemon.hotkey_listener import (
    _SHORTCUT_ID,
    HotkeyListener,
    _hotkey_to_trigger,
    _PortalBackend,
    _select_backend,
    _X11Backend,
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


def test_facade_auto_selectionne_selon_la_session(monkeypatch) -> None:
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


# --- Backend X11 : arrêt, y compris quand le serveur X a disparu --------------
# Fin de session : le thread de pynput meurt avec la connexion au serveur X, et son
# stop() lève alors (ConnectionClosedError, vu en VM après une reconnexion rapide).
# L'arrêt du daemon, que ~/.xprofile redémarre pour la nouvelle session, ne doit pas
# s'interrompre pour autant.

_CLOSED = ConnectionError("Display connection closed by server")


class _PynputListener(threading.Thread):
    """Écoute ``pynput`` factice : un vrai thread, en cours ou déjà terminé."""

    def __init__(self, *, running: bool, stop_error: Exception | None = None) -> None:
        super().__init__(daemon=True)
        self._listening = running
        self._stop_error = stop_error
        self._released = threading.Event()
        self.stop_calls = 0

    def run(self) -> None:
        if self._listening:
            self._released.wait(5.0)

    def stop(self) -> None:
        self.stop_calls += 1
        self._released.set()
        if self._stop_error is not None:
            raise self._stop_error


def _x11_with(listener: _PynputListener) -> _X11Backend:
    listener.start()
    backend = _X11Backend("<ctrl>+<alt>+<space>", _noop)
    backend._listener = listener
    return backend


def test_x11_stop_arrete_l_ecoute_en_cours() -> None:
    listener = _PynputListener(running=True)
    backend = _x11_with(listener)

    backend.stop()
    backend.stop()  # 2e arrêt : sans effet

    listener.join(2.0)
    assert listener.stop_calls == 1
    assert not listener.is_alive()


def test_x11_stop_ecoute_morte_avec_le_serveur_x() -> None:
    listener = _PynputListener(running=False, stop_error=_CLOSED)
    backend = _x11_with(listener)
    listener.join(2.0)

    backend.stop()  # ne lève pas

    assert listener.stop_calls == 0  # plus rien à arrêter


def test_x11_stop_ne_leve_jamais() -> None:
    # Connexion fermée entre le contrôle du thread et son arrêt : erreur journalisée.
    listener = _PynputListener(running=True, stop_error=_CLOSED)
    backend = _x11_with(listener)

    backend.stop()

    assert listener.stop_calls == 1
    backend.stop()  # écoute oubliée : sans effet
    assert listener.stop_calls == 1


def _xvfb() -> tuple[subprocess.Popen[bytes], str]:
    """Serveur X virtuel prêt (une connexion réussit), sur un numéro sans serveur.

    Numéro explicite plutôt que ``-displayfd`` : sous WSL, ``/tmp/.X11-unix`` est en
    lecture seule (WSLg) et ``-displayfd`` y refuse tous les numéros, alors qu'un
    numéro donné écoute sur son socket abstrait.
    """
    import Xlib.display  # pylint: disable=import-outside-toplevel  # dépendance de pynput

    for number in range(150, 200):
        if os.path.exists(f"/tmp/.X11-unix/X{number}") or os.path.exists(
            f"/tmp/.X{number}-lock"
        ):
            continue
        display = f":{number}"
        server = subprocess.Popen(
            ["Xvfb", display, "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10.0
        while server.poll() is None and time.monotonic() < deadline:
            try:
                Xlib.display.Display(display).close()
            except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                time.sleep(0.1)
                continue
            return server, display
        server.kill()
        server.wait(5)
    pytest.skip("aucun Xvfb n'a démarré")


@pytest.mark.skipif(
    shutil.which("Xvfb") is None or importlib.util.find_spec("pynput") is None,
    reason="vrai pynput sur Xvfb : Linux avec Xvfb et pynput seulement",
)
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_x11_vrai_pynput_serveur_x_ferme(monkeypatch: pytest.MonkeyPatch) -> None:
    server, display = _xvfb()
    try:
        monkeypatch.setenv("DISPLAY", display)
        monkeypatch.delenv("XAUTHORITY", raising=False)
        backend = _X11Backend("<ctrl>+<alt>+<space>", _noop)
        backend.start()
        listener = backend._listener
        assert listener is not None
        # Connectée au serveur X (le wait() de pynput n'a pas de délai : attendu à part).
        ready = threading.Event()
        threading.Thread(
            target=lambda: (listener.wait(), ready.set()), daemon=True
        ).start()
        assert ready.wait(10.0)
    finally:
        server.terminate()
        server.wait(5)
    # La connexion fermée tue le thread de pynput (son join() attendrait le délai :
    # celui de threading.Thread rend la main dès la fin du thread).
    threading.Thread.join(listener, 5.0)
    assert not listener.is_alive()

    started = time.monotonic()
    backend.stop()  # ne lève pas
    assert time.monotonic() - started < 1.0
    assert backend._listener is None


# --- Conversion du trigger best-effort ----------------------------------------


def test_hotkey_to_trigger_modificateurs() -> None:
    assert _hotkey_to_trigger("<ctrl>+<alt>+<space>") == "CTRL+ALT+space"


def test_hotkey_to_trigger_super_devient_logo() -> None:
    assert _hotkey_to_trigger("<super>+m") == "LOGO+m"
