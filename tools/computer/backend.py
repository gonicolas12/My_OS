"""Backend du contrôle de l'ordinateur : observer l'écran, agir sur le bureau.

:class:`ComputerBackend` est le contrat utilisé par les outils
(:mod:`tools.computer.tools`) ; les tests injectent un faux backend, sans X11.
:class:`DesktopBackend` est l'implémentation de production :

* **observer** = fenêtres X11 + éléments de la fenêtre active lus dans l'arbre
  d'accessibilité, complétés par l'OCR pour les zones que l'application n'expose
  pas ; capture annotée (numéros dessinés) pour un modèle qui voit les images ;
* **agir** = injection XTEST (``xdotool``) derrière le garde de la session de
  contrôle, et lancement d'applications par leur fichier ``.desktop``.

Le backend ne décide d'aucune permission : les outils l'appellent seulement après
le ``policy_engine`` (cf. CLAUDE.md, invariant du point de passage unique).
"""

from __future__ import annotations

import os
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from core.elevation import run_command
from core.logger import get_logger
from tools.computer.accessibility import (
    ACCESSIBILITY_ENV,
    AccessibilityReader,
    enable_accessibility,
)
from tools.computer.apps import AppCatalog, AppEntry
from tools.computer.marks import annotate
from tools.computer.ocr import TesseractOCR, ocr_elements
from tools.computer.screen import Element, Observation, Rect, Window
from tools.computer.x11 import (
    DesktopError,
    InputGuard,
    X11Desktop,
    screen_locked,
    unavailable_reason,
)

_log = get_logger("myosd.computer")

# Lignes lues par OCR gardées au plus par observation (une page de texte n'inonde
# pas le contexte du modèle).
MAX_OCR_ELEMENTS = 60
OCR_MODES = ("auto", "off")
LOCKED_NOTE = (
    "L'écran est VERROUILLÉ : My_OS ne lit rien et n'agit pas sur l'écran de "
    "verrouillage (il attend le mot de passe de l'utilisateur)."
)


class ComputerBackend(Protocol):  # pylint: disable=too-many-public-methods
    """Contrat entre les outils de contrôle et le bureau réel (ou un faux en test)."""

    def unavailable_reason(self) -> str | None:
        """Pourquoi le contrôle est impossible ici (``None`` s'il est possible)."""

    def observe(self) -> Observation:
        """Nouvelle observation de l'écran (devient la dernière connue)."""

    @property
    def last(self) -> Observation | None:
        """Dernière observation (celle dont le modèle connaît les numéros)."""

    def window_at(self, x: int, y: int) -> Window | None:
        """Fenêtre sous un point de l'écran."""

    def windows(self) -> list[Window]:
        """Fenêtres d'application ouvertes (la plus haute d'abord)."""

    def active_window(self) -> Window | None:
        """Fenêtre qui a le focus clavier (``None`` : le bureau)."""

    def screen_locked(self) -> bool:
        """``True`` si l'écran est verrouillé."""

    def reset(self) -> None:
        """Oublie la dernière observation (nouvelle session : numéros périmés)."""

    def focused_element(self) -> Element | None:
        """Élément qui a le focus clavier dans la fenêtre active."""

    def accessible_at(self, x: int, y: int) -> list[Element]:
        """Éléments accessibles qui couvrent le point, lus à l'instant."""

    def set_exclusions(self, rects: list[Rect]) -> None:
        """Zones de l'indicateur de My_OS (barre d'arrêt), jamais lues comme
        contenu de l'écran."""

    def prepare(self) -> None:
        """Début d'une session : applications invitées à exposer leur arbre
        d'accessibilité."""

    def click(self, x: int, y: int, *, button: str = "left", count: int = 1) -> None:
        """Clique en ``(x, y)``."""

    def type_text(self, text: str) -> None:
        """Tape un texte dans la fenêtre active."""

    def key(self, combo: str, *, repeat: int = 1) -> None:
        """Appuie sur une combinaison de touches (syntaxe xdotool validée)."""

    def scroll(self, x: int, y: int, direction: str, amount: int) -> None:
        """Fait défiler sous ``(x, y)``."""

    def activate(self, window_id: int) -> None:
        """Donne le focus à une fenêtre."""

    def find_app(self, name: str) -> AppEntry | None:
        """Application installée qui correspond à ``name``."""

    def app_names(self) -> list[str]:
        """Quelques noms d'applications installées (aide au modèle)."""

    def launch(self, app: AppEntry) -> None:
        """Lance une application par son fichier ``.desktop``."""

    def set_guard(self, guard: InputGuard | None) -> None:
        """Branche le garde de la session de contrôle (``None`` : aucune action)."""


class DesktopBackend:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Backend de production (X11 + AT-SPI + Tesseract + Pillow)."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        desktop: X11Desktop | None = None,
        accessibility: AccessibilityReader | None = None,
        ocr: TesseractOCR | None = None,
        apps: AppCatalog | None = None,
        ocr_mode: str = "auto",
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self._desktop = desktop or X11Desktop(runner=runner)
        self._a11y = accessibility or AccessibilityReader()
        self._ocr = ocr or TesseractOCR(runner=runner)
        self._apps = apps or AppCatalog()
        self._ocr_mode = ocr_mode if ocr_mode in OCR_MODES else "auto"
        self._runner = runner
        self._last: Observation | None = None
        self._exclusions: list[Rect] = []

    # ---- état ----------------------------------------------------------------
    @staticmethod
    def unavailable_reason() -> str | None:
        """Pourquoi le contrôle est impossible (Wayland, pas d'écran X11)."""
        return unavailable_reason()

    @property
    def last(self) -> Observation | None:
        """Dernière observation."""
        return self._last

    def set_guard(self, guard: InputGuard | None) -> None:
        """Branche (ou retire) le garde de la session de contrôle."""
        self._desktop.set_guard(guard)

    # ---- observation -----------------------------------------------------------
    def observe(self) -> Observation:
        """Fenêtres + éléments de la fenêtre active + capture annotée.

        Comme une action, une lecture de l'écran exige la session de contrôle
        (garde) : sans elle, rien n'est lu ni capturé.
        """
        self._desktop.check_guard()
        started = time.monotonic()
        screen = self._desktop.screen_rect()
        windows = self._desktop.windows()
        active = next((w for w in windows if w.active), None)
        # La fenêtre active en tête de liste : [w1] est toujours celle qu'on voit.
        if active is not None:
            windows = [active, *[w for w in windows if w is not active]]
        notes: list[str] = []
        if screen_locked():
            # Rien n'est lu ni capturé : l'écran de verrouillage attend un mot de passe.
            notes.append(LOCKED_NOTE)
            observation = Observation(
                screen=screen, windows=windows, active=None, elements=[], notes=notes
            )
            self._last = observation
            return observation
        image = None
        try:
            image = self._desktop.capture()
        except DesktopError as exc:
            notes.append(f"(Capture d'écran impossible : {exc}.)")
        elements, accessible = self._accessible_elements(active, screen)
        if active is not None:
            region = (active.content or active.rect).intersection(screen) or screen
        else:
            region = screen  # bureau : icônes et panneaux
        if image is not None and self._ocr_mode == "auto" and self._ocr.available():
            read = ocr_elements(
                self._ocr.read(image, region), elements, excluded=self._exclusions
            )
            elements = _reading_order(elements + read[:MAX_OCR_ELEMENTS])
        elif accessible is None and active is not None:
            notes.append(
                "(Cette application n'expose pas d'éléments accessibles et l'OCR "
                "est indisponible.)"
            )
        observation = Observation(
            screen=screen,
            windows=windows,
            active=active,
            elements=elements,
            notes=notes,
        )
        if image is not None:
            observation.image, observation.image_size = annotate(image, elements)
        self._last = observation
        _log.info(
            "Observation : %d fenêtre(s), %d élément(s) en %.2f s",
            len(windows),
            len(elements),
            time.monotonic() - started,
        )
        return observation

    def _accessible_elements(
        self, active: Window | None, screen: Rect
    ) -> tuple[list[Element], list[Element] | None]:
        """Éléments accessibles de la fenêtre active ; sur le bureau, ceux des panneaux.

        Renvoie ``(éléments, lus)`` où ``lus`` vaut ``None`` si l'application de la
        fenêtre active n'expose rien à l'accessibilité.
        """
        if active is not None:
            if active.minimized:
                return [], []
            accessible = self._a11y.elements(active, screen)
            return list(accessible or []), accessible
        elements: list[Element] = []
        for dock in self._desktop.docks():
            elements += self._a11y.elements(dock, screen) or []
        return elements, elements

    def window_at(self, x: int, y: int) -> Window | None:
        """Fenêtre sous le point ``(x, y)``."""
        return self._desktop.window_at(x, y)

    def windows(self) -> list[Window]:
        """Fenêtres d'application ouvertes."""
        return self._desktop.windows()

    def active_window(self) -> Window | None:
        """Fenêtre active (``None`` : le bureau)."""
        return self._desktop.active_window()

    @staticmethod
    def screen_locked() -> bool:
        """``True`` si l'écran est verrouillé."""
        return screen_locked()

    def reset(self) -> None:
        """Oublie la dernière observation."""
        self._last = None

    def focused_element(self) -> Element | None:
        """Élément qui a le focus dans la fenêtre active (lecture AT-SPI)."""
        window = self._desktop.active_window()
        if window is None:
            return None
        return self._a11y.focused(window, self._desktop.screen_rect())

    def accessible_at(self, x: int, y: int) -> list[Element]:
        """Éléments accessibles de la fenêtre sous ``(x, y)`` qui couvrent ce point."""
        window = self._desktop.window_at(x, y)
        if window is None:
            return []
        elements = self._a11y.elements(window, self._desktop.screen_rect()) or []
        return [e for e in elements if e.rect.contains(x, y)]

    def set_exclusions(self, rects: list[Rect]) -> None:
        """Zones de l'indicateur (barre d'arrêt) : l'OCR n'y lit rien."""
        self._exclusions = list(rects)

    @staticmethod
    def prepare() -> None:
        """Active l'accessibilité de la session (applications Qt, Firefox…)."""
        enable_accessibility()

    @staticmethod
    def _systemd_user() -> bool:
        """``True`` si un gestionnaire systemd utilisateur tourne (socket privée)."""
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        return bool(runtime) and Path(runtime, "systemd", "private").exists()

    # ---- actions ---------------------------------------------------------------
    def click(self, x: int, y: int, *, button: str = "left", count: int = 1) -> None:
        """Clic (garde de session vérifié avant l'événement)."""
        self._desktop.click(x, y, button=button, count=count)

    def type_text(self, text: str) -> None:
        """Saisie au clavier (garde vérifié)."""
        self._desktop.type_text(text)

    def key(self, combo: str, *, repeat: int = 1) -> None:
        """Combinaison de touches (garde vérifié)."""
        self._desktop.key(combo, repeat=repeat)

    def scroll(self, x: int, y: int, direction: str, amount: int) -> None:
        """Défilement (garde vérifié)."""
        self._desktop.scroll(x, y, direction, amount)

    def activate(self, window_id: int) -> None:
        """Focus sur une fenêtre (garde vérifié)."""
        self._desktop.activate(window_id)

    def find_app(self, name: str) -> AppEntry | None:
        """Application installée la plus proche de ``name``."""
        found = self._apps.find(name)
        if found is None:
            self._apps.reload()  # installée depuis le premier chargement ?
            found = self._apps.find(name)
        return found

    def app_names(self) -> list[str]:
        """Noms d'applications installées."""
        return self._apps.suggestions()

    def launch(self, app: AppEntry) -> None:
        """Lance l'application comme le menu (``gio launch``), accessibilité activée
        (variables :data:`ACCESSIBILITY_ENV`, passées par ``env`` sans shell).

        Sous systemd, l'application part dans son propre scope transitoire
        (``systemd-run --user --scope``) : lancée depuis le service ``myosd``, elle
        resterait sinon dans son groupe de contrôle et serait tuée à chaque arrêt ou
        redémarrage du daemon (vérifié en VM avec GLib 2.90).
        """
        self._desktop.check_guard()
        variables = [f"{key}={value}" for key, value in ACCESSIBILITY_ENV.items()]
        argv = ["env", *variables, "gio", "launch", app.path]
        if self._systemd_user():
            argv = [
                "systemd-run",
                "--user",
                "--scope",
                "--quiet",
                "--collect",
                f"--unit={scope_unit(app.app_id)}",
                *argv,
            ]
        # Sans tube : l'application lancée hériterait de ceux de la capture et
        # run_command attendrait sa fermeture (cf. core.elevation).
        result = run_command(
            argv, timeout=15.0, runner=self._runner, detach_output=True
        )
        if not result.ok:
            detail = result.stderr.strip() or f"code {result.returncode}"
            raise DesktopError(f"lancement de {app.name} impossible : {detail}")


def _reading_order(elements: list[Element]) -> list[Element]:
    return sorted(elements, key=lambda e: (e.rect.y // 12, e.rect.x, e.rect.y))


def scope_unit(app_id: str) -> str:
    """Nom du scope systemd d'une application lancée par My_OS.

    Convention des lanceurs de bureau : ``app-<lanceur>-<application>-<aléa>.scope``
    (caractères hors ``[A-Za-z0-9_.]`` remplacés par ``_``).
    """
    safe = re.sub(r"[^A-Za-z0-9_.]", "_", app_id)[:64] or "app"
    return f"app-myos-{safe}-{secrets.token_hex(4)}.scope"
