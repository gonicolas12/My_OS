"""Outils du contrôle de l'ordinateur exposés au modèle.

Sept outils, tous de **niveau 1** et tous soumis à la session de contrôle accordée
par l'utilisateur pour la requête (cf. :data:`permissions.risk_levels.COMPUTER_TOOLS`) :

* ``observe_screen`` — fenêtres + éléments numérotés de la fenêtre active ;
* ``click`` — clic sur un élément de la dernière observation (par son numéro) ;
* ``type_text`` — saisie au clavier (dans un élément, ou là où est le focus) ;
* ``press_keys`` — touche ou combinaison (« ctrl+s », « Entrée ») ;
* ``scroll`` — défilement ;
* ``open_app`` — lance une application installée par son nom ;
* ``focus_window`` — passe à une autre fenêtre.

Chaque action renvoie la **nouvelle observation** : le modèle voit l'effet de son
action sans un tour de plus (un tour du modèle local coûte des secondes).

**Cible réelle.** ``normalize_args`` résout ce que l'action vise vraiment (fenêtre
sous le point, application, élément, champ focalisé), depuis l'état de l'écran et
jamais depuis ce que dit le modèle (une clé ``target`` fournie par lui est jetée).
La liste de blocage, l'escalade, la confirmation et l'audit jugent cette cible —
comme le chemin réel des outils fichiers. Juste avant d'agir, ``run`` revérifie que
la cible n'a pas changé (une fenêtre surgie entre-temps fait échouer l'action).

Aucune vérification de permission ici (rôle exclusif du ``policy_engine``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

from permissions.risk_levels import (
    ACTIVATION_KEYS,
    DESTRUCTIVE_COMBOS,
    SYSTEM_SETTINGS_APPS,
    TERMINAL_APPS,
    app_keys,
    is_destructive_label,
)
from tools.base_tool import BaseTool, ToolResult
from tools.computer.backend import ComputerBackend
from tools.computer.keys import MAX_REPEAT, combo_parts, parse_combo
from tools.computer.screen import (
    Element,
    Rect,
    Window,
    describe_window,
    format_observation,
)
from tools.computer.x11 import ControlStopped, DesktopError

# Attente après une action, le temps que l'interface se mette à jour (s).
SETTLE_CLICK = 0.7
SETTLE_TYPE = 0.4
SETTLE_KEYS = 0.7
SETTLE_SCROLL = 0.5
SETTLE_FOCUS = 0.6
APP_WINDOW_TIMEOUT = 12.0
_APP_POLL = 0.3
# Texte tapé au plus par appel, et par morceau : la fenêtre et le garde sont revérifiés
# entre deux morceaux, donc un arrêt (Échap) coupe la frappe en 0,2 s au plus.
MAX_TEXT = 2000
_CHUNK = 16
_DIRECTIONS = {
    "up": "up",
    "haut": "up",
    "down": "down",
    "bas": "down",
    "left": "left",
    "gauche": "left",
    "right": "right",
    "droite": "right",
}
_BUTTONS = {
    "left": "left",
    "gauche": "left",
    "right": "right",
    "droit": "right",
    "middle": "middle",
}
NO_OBSERVATION = (
    "Aucune observation de l'écran en cours : appelez d'abord observe_screen, puis "
    "utilisez les numéros de la DERNIÈRE observation."
)
STOPPED = "Contrôle arrêté par l'utilisateur : aucune action n'a été faite."
SCREEN_CHANGED = (
    "L'écran a changé depuis l'observation (une autre fenêtre est maintenant "
    "visée) : action annulée. Observez à nouveau l'écran."
)
ELEMENT_MOVED = (
    "L'élément visé n'est plus à cet endroit (l'écran a changé depuis "
    "l'observation) : action annulée. Observez à nouveau l'écran."
)
# Caractères tapés : retour à la ligne et tabulation, jamais d'autre caractère de
# contrôle (un Échap tapé dans le texte couperait le contrôle, par exemple).
_TYPABLE_CONTROLS = frozenset("\n\t")


@dataclass
class ScreenContext:
    """Ce que les outils partagent : le bureau et ce qui appartient à My_OS.

    ``myos_pids`` et ``stop_rect`` viennent de la session de contrôle du daemon :
    processus du popup (ses fenêtres ne sont jamais des cibles) et bouton d'arrêt.
    ``sleep`` est injectable (tests sans attente).
    """

    backend: ComputerBackend
    myos_pids: Callable[[], set[int]] = field(default=set)
    stop_rect: Callable[[], Rect | None] = field(default=lambda: None)
    sleep: Callable[[float], None] = time.sleep


def _err(message: str) -> ToolResult:
    return ToolResult(success=False, output=message, reversible=False)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip().strip("[]w").strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "oui", "yes")
    return bool(value)


class _ScreenTool(BaseTool):  # pylint: disable=abstract-method
    """Base commune : contexte partagé, résolution de cible, observation finale."""

    risk_level = 1
    # Sous-classes : True si un libellé destructeur (« Supprimer ») escalade.
    _label_escalates: ClassVar[bool] = False

    def __init__(self, context: ScreenContext) -> None:
        self._ctx = context

    @property
    def _backend(self) -> ComputerBackend:
        return self._ctx.backend

    # ---- cible ---------------------------------------------------------------
    def _target(
        self,
        window: Window | None,
        *,
        point: tuple[int, int] | None = None,
        element: Element | None = None,
    ) -> dict:
        """Cible réelle d'une action, telle que la jugent les permissions."""
        target: dict = {
            "window": describe_window(window) if window else "bureau",
            "window_id": window.id if window else 0,
            "app": window.app if window else "",
            "instance": window.instance if window else "",
            "pid": window.pid if window else None,
            "myos": bool(window and window.pid in self._ctx.myos_pids()),
            "locked": self._backend.screen_locked(),
        }
        if element is not None:
            target["role"] = element.role
            target["name"] = element.name
            target["source"] = element.source
        if point is not None:
            target["x"], target["y"] = point
            stop = self._ctx.stop_rect()
            target["stop_button"] = bool(stop and stop.contains(*point))
        return target

    def _element(self, number: object) -> Element | None:
        last = self._backend.last
        index = _as_int(number)
        if last is None or index is None:
            return None
        return last.element(index)

    def _point_target(self, element: Element) -> dict:
        point = element.rect.center
        return self._target(
            self._backend.window_at(*point), point=point, element=element
        )

    def _focus_target(self) -> dict:
        """Cible d'une saisie au clavier : la fenêtre active et son champ focalisé."""
        window = self._backend.active_window()
        focused = self._backend.focused_element() if window else None
        return self._target(window, element=focused)

    def escalate(self, args: dict) -> int:
        """Niveau 2 pour un terminal, les paramètres du système, ou (clic) une
        action irréversible annoncée par le libellé visé."""
        target = args.get("target")
        if not isinstance(target, dict):
            return self.risk_level
        keys = app_keys(str(target.get("app", "")), str(target.get("instance", "")))
        if keys & TERMINAL_APPS or target.get("role") == "terminal":
            return 2
        if keys & SYSTEM_SETTINGS_APPS:
            return 2
        if self._label_escalates and is_destructive_label(str(target.get("name", ""))):
            return 2
        return self.risk_level

    # ---- exécution -------------------------------------------------------------
    def _still(self, target: dict, window: Window | None) -> str | None:
        """Message d'erreur si la fenêtre visée n'est plus celle qui a été jugée."""
        expected = target.get("window_id", 0)
        current = window.id if window else 0
        if expected != current:
            return SCREEN_CHANGED
        return None

    def _moved(self, target: dict) -> str | None:
        """Message d'erreur si l'élément accessible visé n'est plus sous le point.

        Relu juste avant le clic : une confirmation a pu laisser à l'application le
        temps de changer (liste qui défile, dialogue qui se remplace), et le clic
        tomberait alors sur autre chose que ce que l'utilisateur a approuvé. Un
        texte lu par OCR n'est pas relu (trop lent) : sa fenêtre l'est toujours.
        """
        if target.get("source") != "a11y" or not target.get("name"):
            return None
        point = int(target["x"]), int(target["y"])
        found = self._backend.accessible_at(*point)
        same = any(
            e.name == target["name"] and e.role == target.get("role") for e in found
        )
        return None if same else ELEMENT_MOVED

    def _refocus(self, target: dict) -> None:
        """Rend le focus à la fenêtre visée s'il l'a perdu (une confirmation vient de
        s'afficher par-dessus, par exemple). La fenêtre doit toujours exister."""
        expected = int(target.get("window_id", 0) or 0)
        current = self._backend.active_window()
        if not expected or (current is not None and current.id == expected):
            return
        if any(w.id == expected for w in self._backend.windows()):
            self._backend.activate(expected)
            self._ctx.sleep(0.3)

    def _observed(self, summary: str, settle: float) -> ToolResult:
        """Attend que l'interface se mette à jour, puis renvoie la nouvelle observation."""
        self._ctx.sleep(settle)
        try:
            observation = self._backend.observe()
        except DesktopError as exc:
            return ToolResult(
                success=True, output=f"{summary}\n(Observation impossible : {exc}.)"
            )
        return ToolResult(
            success=True,
            output=f"{summary}\n\n{format_observation(observation)}",
            image=observation.image,
        )

    def _guarded(self, action: Callable[[], ToolResult]) -> ToolResult:
        try:
            return action()
        except ControlStopped:
            return _err(STOPPED)
        except DesktopError as exc:
            return _err(f"{self.name} : {exc}")


class ObserveScreen(_ScreenTool):
    """Observe l'écran : fenêtres et éléments numérotés de la fenêtre active."""

    name = "observe_screen"
    description = (
        "Regarde l'écran : fenêtres ouvertes et éléments visibles de la fenêtre "
        "active, chacun avec un numéro."
    )
    risk_level = 1
    parameters: ClassVar[dict] = {}

    def normalize_args(self, args: dict) -> dict:
        """Aucun argument."""
        return {}

    def run(self, args: dict) -> ToolResult:
        def act() -> ToolResult:
            observation = self._backend.observe()
            return ToolResult(
                success=True,
                output=format_observation(observation),
                image=observation.image,
            )

        return self._guarded(act)


class Click(_ScreenTool):
    """Clique sur un élément de la dernière observation."""

    name = "click"
    description = "Clique sur un élément de la dernière observation, par son numéro."
    risk_level = 1
    _label_escalates = True
    parameters: ClassVar[dict] = {
        "element": "int — numéro de l'élément dans la dernière observation",
        "double": "bool — double-clic (ouvrir un fichier, une icône)",
        "button": "str — left (défaut) ou right (menu contextuel)",
    }

    def normalize_args(self, args: dict) -> dict:
        """Résout l'élément en cible réelle (fenêtre sous le point, libellé)."""
        clean = {
            "element": _as_int(args.get("element")),
            "double": _as_bool(args.get("double", False)),
            "button": _BUTTONS.get(str(args.get("button", "left")).lower(), "left"),
        }
        element = self._element(clean["element"])
        if element is not None:
            clean["target"] = self._point_target(element)
        return clean

    def run(self, args: dict) -> ToolResult:
        target = args.get("target")
        if self._backend.last is None:
            return _err(NO_OBSERVATION)
        if not isinstance(target, dict):
            return _err(
                f"click : numéro d'élément inconnu ({args.get('element')}). "
                "Utilisez un numéro de la dernière observation."
            )

        def act() -> ToolResult:
            x, y = int(target["x"]), int(target["y"])
            problem = self._still(target, self._backend.window_at(x, y))
            problem = problem or self._moved(target)
            if problem:
                return _err(problem)
            count = 2 if args.get("double") else 1
            self._backend.click(
                x, y, button=str(args.get("button", "left")), count=count
            )
            what = "Double-clic" if count == 2 else "Clic"
            if args.get("button") == "right":
                what = "Clic droit"
            name = target.get("name") or target.get("role", "")
            return self._observed(
                f"{what} sur [{args['element']}] « {name} ».", SETTLE_CLICK
            )

        return self._guarded(act)


class TypeText(_ScreenTool):
    """Tape un texte au clavier."""

    name = "type_text"
    description = (
        "Tape un texte au clavier dans la fenêtre active (ou dans un élément)."
    )
    risk_level = 1
    parameters: ClassVar[dict] = {
        "text": "str — texte à taper",
        "element": "int — (optionnel) champ où taper, numéro de la dernière observation",
        "enter": "bool — appuyer sur Entrée après le texte",
    }

    def normalize_args(self, args: dict) -> dict:
        """Texte exact + cible réelle (champ visé, ou champ qui a le focus)."""
        text = args.get("text", "")
        clean: dict = {
            "text": text if isinstance(text, str) else str(text),
            "enter": _as_bool(args.get("enter", False)),
        }
        number = _as_int(args.get("element"))
        if number is not None:
            clean["element"] = number
            element = self._element(number)
            if element is not None:
                clean["target"] = self._point_target(element)
            return clean
        clean["target"] = self._focus_target()
        return clean

    def run(self, args: dict) -> ToolResult:
        text = str(args.get("text", ""))
        if not text and not args.get("enter"):
            return _err("type_text : texte vide.")
        if any(ord(c) < 32 and c not in _TYPABLE_CONTROLS for c in text):
            return _err(
                "type_text : caractère de contrôle refusé (seuls le retour à la ligne "
                "et la tabulation se tapent ; pour une touche, utilisez press_keys)."
            )
        if len(text) > MAX_TEXT:
            return _err(
                f"type_text : texte trop long ({len(text)} > {MAX_TEXT} caractères)."
            )
        target = args.get("target")
        if not isinstance(target, dict):
            if self._backend.last is None:
                return _err(NO_OBSERVATION)
            return _err(
                f"type_text : numéro d'élément inconnu ({args.get('element')})."
            )

        def act() -> ToolResult:
            if "x" in target:  # champ désigné : clic pour y mettre le focus
                x, y = int(target["x"]), int(target["y"])
                problem = self._still(target, self._backend.window_at(x, y))
                problem = problem or self._moved(target)
                if problem:
                    return _err(problem)
                self._backend.click(x, y)
                self._ctx.sleep(0.25)
            else:
                self._refocus(target)
            for start in range(0, len(text), _CHUNK):
                problem = self._still(target, self._backend.active_window())
                if problem:
                    return _err(problem)
                self._backend.type_text(text[start : start + _CHUNK])
            if args.get("enter"):
                self._backend.key("Return")
            suffix = " puis Entrée" if args.get("enter") else ""
            return self._observed(
                f"Texte tapé ({len(text)} caractères){suffix}.", SETTLE_TYPE
            )

        return self._guarded(act)


class PressKeys(_ScreenTool):
    """Appuie sur une touche ou une combinaison."""

    name = "press_keys"
    description = "Appuie sur une touche ou une combinaison (« ctrl+s », « Entrée »)."
    risk_level = 1
    parameters: ClassVar[dict] = {
        "keys": "str — combinaison, ex. ctrl+s, alt+F4, Entrée, Échap, Tab",
        "repeat": "int — nombre d'appuis (défaut 1)",
    }

    def normalize_args(self, args: dict) -> dict:
        """Combinaison canonique (vue par la liste de blocage) + cible réelle."""
        raw = args.get("keys", "")
        try:
            keys = parse_combo(str(raw))
            clean: dict = {"keys": keys}
        except ValueError as exc:
            clean = {"keys": str(raw), "invalid": str(exc)}
        repeat = _as_int(args.get("repeat", 1)) or 1
        clean["repeat"] = max(1, min(repeat, MAX_REPEAT))
        clean["target"] = self._focus_target()
        return clean

    def escalate(self, args: dict) -> int:
        """En plus de la cible : Maj+Suppr (suppression définitive), et Entrée ou
        Espace sur un élément focalisé au libellé destructeur, comme son clic."""
        level = super().escalate(args)
        keys = str(args.get("keys", ""))
        if keys in DESTRUCTIVE_COMBOS:
            return max(level, 2)
        _modifiers, key = combo_parts(keys)
        target = args.get("target") if isinstance(args.get("target"), dict) else {}
        if key in ACTIVATION_KEYS and is_destructive_label(str(target.get("name", ""))):
            return max(level, 2)
        return level

    def run(self, args: dict) -> ToolResult:
        if args.get("invalid"):
            return _err(f"press_keys : {args['invalid']}.")
        target = args.get("target") or {}

        def act() -> ToolResult:
            self._refocus(target)
            problem = self._still(target, self._backend.active_window())
            if problem:
                return _err(problem)
            self._backend.key(str(args["keys"]), repeat=int(args.get("repeat", 1)))
            times = f" ×{args['repeat']}" if int(args.get("repeat", 1)) > 1 else ""
            return self._observed(f"Touches « {args['keys']} »{times}.", SETTLE_KEYS)

        return self._guarded(act)


class Scroll(_ScreenTool):
    """Fait défiler la fenêtre active (ou un élément)."""

    name = "scroll"
    description = (
        "Fait défiler la fenêtre active (ou un élément) vers le haut ou le bas."
    )
    risk_level = 1
    parameters: ClassVar[dict] = {
        "direction": "str — up, down, left ou right",
        "amount": "int — crans de molette (défaut 3)",
        "element": "int — (optionnel) élément sous lequel défiler",
    }

    def normalize_args(self, args: dict) -> dict:
        """Direction, crans, point de défilement réel."""
        direction = _DIRECTIONS.get(str(args.get("direction", "down")).strip().lower())
        amount = _as_int(args.get("amount", 3)) or 3
        clean: dict = {
            "direction": direction or str(args.get("direction")),
            "amount": max(1, min(amount, 15)),
        }
        number = _as_int(args.get("element"))
        element = self._element(number) if number is not None else None
        if number is not None:
            clean["element"] = number
        if element is not None:
            clean["target"] = self._point_target(element)
        else:
            window = self._backend.active_window()
            if window is not None:
                area = window.content or window.rect
                clean["target"] = self._target(window, point=area.center)
        return clean

    def run(self, args: dict) -> ToolResult:
        if args.get("direction") not in ("up", "down", "left", "right"):
            return _err("scroll : direction attendue up, down, left ou right.")
        target = args.get("target")
        if not isinstance(target, dict) or "x" not in target:
            return _err("scroll : rien à faire défiler (aucune fenêtre active).")

        def act() -> ToolResult:
            x, y = int(target["x"]), int(target["y"])
            problem = self._still(target, self._backend.window_at(x, y))
            if problem:
                return _err(problem)
            self._backend.scroll(x, y, str(args["direction"]), int(args["amount"]))
            return self._observed(
                f"Défilement {args['direction']} ×{args['amount']}.", SETTLE_SCROLL
            )

        return self._guarded(act)


class FocusWindow(_ScreenTool):
    """Passe à une autre fenêtre ouverte."""

    name = "focus_window"
    description = (
        "Passe à une fenêtre ouverte, par son numéro [wN] de la dernière observation."
    )
    risk_level = 1
    parameters: ClassVar[dict] = {"window": "int — numéro N de la fenêtre [wN]"}

    def normalize_args(self, args: dict) -> dict:
        """Fenêtre visée, depuis la dernière observation."""
        number = _as_int(args.get("window"))
        clean: dict = {"window": number}
        last = self._backend.last
        window = (
            last.window(number) if last is not None and number is not None else None
        )
        if window is not None:
            clean["target"] = self._target(window)
        return clean

    def run(self, args: dict) -> ToolResult:
        target = args.get("target")
        if self._backend.last is None:
            return _err(NO_OBSERVATION)
        if not isinstance(target, dict):
            return _err(f"focus_window : fenêtre inconnue (w{args.get('window')}).")

        def act() -> ToolResult:
            self._backend.activate(int(target["window_id"]))
            return self._observed(
                f"Fenêtre {target['window']} au premier plan.", SETTLE_FOCUS
            )

        return self._guarded(act)


class OpenApp(_ScreenTool):
    """Lance une application installée par son nom."""

    name = "open_app"
    description = (
        "Ouvre une application installée par son nom (« Firefox », « Mousepad »)."
    )
    risk_level = 1
    parameters: ClassVar[dict] = {"name": "str — nom de l'application"}

    def normalize_args(self, args: dict) -> dict:
        """Application réellement lancée (fichier .desktop trouvé)."""
        name = str(args.get("name", "")).strip()
        clean: dict = {"name": name}
        entry = self._backend.find_app(name) if name else None
        if entry is not None:
            clean["target"] = {
                "window": entry.name,
                "app": entry.app_id,
                "instance": entry.executable,
                "desktop_file": entry.path,
                "locked": self._backend.screen_locked(),
            }
        return clean

    def run(self, args: dict) -> ToolResult:
        name = str(args.get("name", ""))
        entry = self._backend.find_app(name) if name else None
        target = args.get("target")
        if entry is None or not isinstance(target, dict):
            known = ", ".join(self._backend.app_names())
            return _err(
                f"open_app : aucune application « {name} ». Installées : {known}."
            )
        if entry.path != target.get("desktop_file"):
            return _err("open_app : le catalogue d'applications a changé, réessayez.")

        def act() -> ToolResult:
            before = {w.id for w in self._backend.windows()}
            self._backend.launch(entry)
            waited = 0.0
            new: Window | None = None
            while waited < APP_WINDOW_TIMEOUT and new is None:
                self._ctx.sleep(_APP_POLL)
                waited += _APP_POLL
                new = next(
                    (w for w in self._backend.windows() if w.id not in before), None
                )
            if new is None:
                return self._observed(
                    f"{entry.name} lancé, mais aucune nouvelle fenêtre n'est apparue "
                    f"en {APP_WINDOW_TIMEOUT:.0f} s (déjà ouvert ?).",
                    0.0,
                )
            if not new.active:
                self._backend.activate(new.id)
            return self._observed(f"{entry.name} ouvert.", SETTLE_FOCUS)

        return self._guarded(act)


def build_computer_tools(context: ScreenContext) -> dict[str, BaseTool]:
    """Registre des outils de l'écran, partageant le même contexte."""
    tools = (
        ObserveScreen(context),
        Click(context),
        TypeText(context),
        PressKeys(context),
        Scroll(context),
        OpenApp(context),
        FocusWindow(context),
    )
    return {tool.name: tool for tool in tools}
