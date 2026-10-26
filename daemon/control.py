"""Session de contrôle de l'ordinateur : indicateur visible, arrêt garanti.

Le modèle ne voit l'écran et n'agit sur la souris et le clavier que pendant une
**session de contrôle**, accordée par l'utilisateur pour une requête (cf.
``Orchestrator``). La session :

* ne démarre que si l'**indicateur** est affiché : le daemon demande au popup
  d'allumer la lueur orange des bords de l'écran et la barre d'arrêt
  (``control_state``), et attend son accusé (``control_ready``). Pas de popup, pas
  d'indicateur : pas de contrôle ;
* s'arrête **à tout moment** : touche Échap (écoute globale, sans capture du
  clavier), bouton ■ de la barre (``stop_control``), popup déconnecté. Dès l'arrêt,
  le garde (:meth:`check`) refuse tout nouvel événement : plus rien n'est injecté,
  quoi que fasse le modèle ;
* se termine avec la requête (``control_state`` inactif : la lueur s'éteint).

Une seule session à la fois (un seul écran, un seul indicateur). L'écoute d'Échap
passe par ``pynput`` (X11), importé paresseusement ; elle ignore les Échap que le
modèle injecte lui-même (fermer un menu n'arrête pas le contrôle).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from core.i18n import N_, tr
from core.logger import get_logger
from tools.computer.backend import ComputerBackend
from tools.computer.screen import Rect
from tools.computer.x11 import ControlStopped

_log = get_logger("myosd.control")

# Délai max d'affichage de l'indicateur par le popup (s).
READY_TIMEOUT_S = 5.0
# Échap injectés par le modèle : chacun est attendu par l'écoute, au plus ce délai
# (plus l'écart entre deux appuis répétés). Un Échap qui n'est pas attendu, ou qui
# arrive une fois les attendus reçus, vient de l'utilisateur : il arrête tout.
_INJECTED_ESCAPE_S = 0.6
_INJECTED_REPEAT_S = 0.1

STOP_REASONS = ("finished", "user", "error")
_ALREADY_RUNNING = N_("un contrôle de l'ordinateur est déjà en cours")


def _rect(value: object) -> Rect | None:
    """Rectangle ``[x, y, largeur, hauteur]`` envoyé par le popup, s'il est valide."""
    if (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        and value[2] > 0
        and value[3] > 0
    ):
        return Rect(*value)
    return None


class StopKeyListener(Protocol):
    """Écoute globale de la touche d'arrêt (Échap)."""

    def start(self) -> None:
        """Commence l'écoute (en arrière-plan)."""

    def stop(self) -> None:
        """Arrête l'écoute."""


class _PynputEscape:
    """Écoute d'Échap sous X11 (``pynput``, extension RECORD : rien n'est capturé,
    la touche arrive aussi à l'application)."""

    def __init__(self, on_escape: Callable[[], None]) -> None:
        self._on_escape = on_escape
        self._listener = None

    def start(self) -> None:
        """Lance l'écoute."""
        from pynput import keyboard  # pylint: disable=import-outside-toplevel

        def on_press(key: object) -> None:
            if key == keyboard.Key.esc:
                self._on_escape()

        self._listener = keyboard.Listener(on_press=on_press)
        self._listener.start()

    def stop(self) -> None:
        """Arrête l'écoute."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


class ControlSession:  # pylint: disable=too-many-instance-attributes
    """État du contrôle de l'ordinateur (garde des injections, indicateur, arrêt)."""

    def __init__(
        self,
        send: Callable[[dict], bool],
        backend: ComputerBackend,
        *,
        ready_timeout: float = READY_TIMEOUT_S,
        listener_factory: Callable[[Callable[[], None]], StopKeyListener] | None = None,
    ) -> None:
        self._send = send
        self._backend = backend
        self._ready_timeout = ready_timeout
        self._listener_factory = listener_factory or _PynputEscape
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._active = False
        self._cancelled = False
        self._request_id: str | None = None
        self._stop_rect: Rect | None = None
        self._popup_pid: int | None = None
        self._listener: StopKeyListener | None = None
        # Échéances des Échap injectés par le modèle, pas encore reçus par l'écoute.
        self._expected_escapes: list[float] = []

    # ---- ce que voient les outils ----------------------------------------------
    @property
    def backend(self) -> ComputerBackend:
        """Bureau piloté pendant la session."""
        return self._backend

    def myos_pids(self) -> set[int]:
        """Processus de l'interface de My_OS : leurs fenêtres ne sont jamais visées."""
        return {self._popup_pid} if self._popup_pid else set()

    def stop_rect(self) -> Rect | None:
        """Bouton ■ de la barre d'arrêt (jamais cliqué par le modèle)."""
        return self._stop_rect

    def set_popup_pid(self, pid: int | None) -> None:
        """Processus du popup connecté (``SO_PEERCRED`` de la socket IPC)."""
        self._popup_pid = pid

    # ---- garde (InputGuard) ------------------------------------------------------
    def check(self) -> None:
        """Lève :class:`ControlStopped` si aucune action n'est permise."""
        if not self._active or self._cancelled:
            raise ControlStopped("contrôle arrêté ou non accordé")

    def expect_escape(self, count: int = 1) -> None:
        """Le modèle va injecter ``count`` Échap : ce ne sont pas des demandes d'arrêt."""
        now = time.monotonic()
        with self._lock:
            self._expected_escapes += [
                now + _INJECTED_ESCAPE_S + index * _INJECTED_REPEAT_S
                for index in range(max(1, count))
            ]

    # ---- cycle de vie ------------------------------------------------------------
    @property
    def active(self) -> bool:
        """``True`` pendant une session (indicateur affiché, pas d'arrêt)."""
        return self._active and not self._cancelled

    @property
    def cancelled(self) -> bool:
        """``True`` si l'utilisateur a arrêté la session en cours."""
        return self._cancelled

    def unavailable_reason(self) -> str | None:
        """Pourquoi une session ne peut pas commencer (``None`` si elle le peut)."""
        reason = self._backend.unavailable_reason()
        if reason:
            return reason
        if self._active:
            return tr(_ALREADY_RUNNING)
        return None

    def begin(self, request_id: str) -> str | None:
        """Ouvre une session pour ``request_id`` ; renvoie un message d'échec ou ``None``.

        Le popup doit afficher l'indicateur et l'accuser (:meth:`on_ready`) dans le
        délai : sinon la session n'ouvre pas.
        """
        with self._lock:
            if self._active:
                return tr(_ALREADY_RUNNING)
            self._ready.clear()
            self._cancelled = False
            self._stop_rect = None
            self._request_id = request_id
        if not self._send({"type": "control_state", "id": request_id, "active": True}):
            return tr(
                "le popup n'est pas connecté : l'indicateur de contrôle ne peut pas "
                "s'afficher"
            )
        if not self._ready.wait(self._ready_timeout):
            self._send(
                {
                    "type": "control_state",
                    "id": request_id,
                    "active": False,
                    "reason": "error",
                }
            )
            return tr("l'indicateur de contrôle ne s'est pas affiché : contrôle annulé")
        with self._lock:
            self._active = True
            self._expected_escapes = []
        self._backend.reset()
        self._backend.prepare()
        self._backend.set_guard(self)
        self._start_listener()
        _log.info("Contrôle de l'ordinateur accordé (requête %s)", request_id)
        return None

    def on_ready(self, message: dict) -> None:
        """Accusé du popup : l'indicateur est affiché (``control_ready``).

        ``stop_rect`` (bouton ■) n'est jamais cliqué par le modèle ; ``bar_rect``
        (toute la barre) n'est jamais lu comme contenu de l'écran.
        """
        if str(message.get("id", "")) != self._request_id:
            return
        self._stop_rect = _rect(message.get("stop_rect"))
        bar = _rect(message.get("bar_rect"))
        self._backend.set_exclusions([r for r in (bar, self._stop_rect) if r])
        self._ready.set()

    def cancel(self, reason: str = "user") -> None:
        """Arrêt immédiat (Échap, bouton ■, popup perdu) : plus aucune injection."""
        with self._lock:
            if not self._active or self._cancelled:
                return
            self._cancelled = True
            request_id = self._request_id
        self._backend.set_guard(None)
        self._backend.set_exclusions([])
        _log.info("Contrôle de l'ordinateur arrêté (%s)", reason)
        self._send(
            {
                "type": "control_state",
                "id": request_id,
                "active": False,
                "reason": reason,
            }
        )

    def end(self, reason: str = "finished") -> None:
        """Fin de la requête : ferme la session (idempotent)."""
        with self._lock:
            was_running = self._active and not self._cancelled
            self._active = False
            request_id = self._request_id
        self._backend.set_guard(None)
        self._backend.set_exclusions([])
        self._stop_listener()
        if was_running:
            _log.info("Contrôle de l'ordinateur terminé (%s)", reason)
            self._send(
                {
                    "type": "control_state",
                    "id": request_id,
                    "active": False,
                    "reason": reason,
                }
            )

    def popup_lost(self) -> None:
        """Le popup s'est déconnecté : sans indicateur, le contrôle s'arrête."""
        self._popup_pid = None
        self.cancel("error")

    # ---- touche Échap --------------------------------------------------------------
    def _on_escape(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._expected_escapes = [t for t in self._expected_escapes if t > now]
            injected = bool(self._expected_escapes)
            if injected:
                self._expected_escapes.pop(0)  # Échap injecté par le modèle lui-même
        if not injected:
            self.cancel("user")

    def _start_listener(self) -> None:
        try:
            listener = self._listener_factory(self._on_escape)
            listener.start()
            self._listener = listener
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # Le bouton ■ reste disponible ; l'échec est journalisé, jamais masqué.
            _log.warning("Écoute de la touche Échap impossible : %s", exc)

    def _stop_listener(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001, S110  # pylint: disable=broad-exception-caught
                pass
