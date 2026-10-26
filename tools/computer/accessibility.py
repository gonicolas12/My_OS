"""Lecture de l'arbre d'accessibilité AT-SPI : les éléments visibles d'une fenêtre.

C'est la source **principale** de l'observation : sans vision ni OCR, l'arbre
d'accessibilité donne pour chaque élément son rôle (bouton, champ, menu…), son
nom, ses états (focus, coché, inactif) et sa position exacte à l'écran. GTK (donc
toutes les applications Xfce), Qt, LibreOffice et Firefox l'exposent ; une zone
que l'application n'expose pas (ex. la vue des fichiers de Thunar) est complétée
par l'OCR (cf. :mod:`tools.computer.ocr`).

``gi.repository.Atspi`` (PyGObject, paquets ``python-gobject`` + ``at-spi2-core``)
est importé **paresseusement** : le module reste importable sous Windows et dans
les tests, où l'on injecte de faux nœuds (:class:`AccessibleNode`).

Le parcours est **borné** (nombre de nœuds, durée, profondeur) : une application
lente ou une page web de milliers de nœuds ne bloque jamais l'observation.
Libatspi n'est pas sûr entre threads : un verrou sérialise les lectures.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from core.logger import get_logger
from tools.computer.screen import Element, Rect, Window

_log = get_logger("myosd.computer.a11y")

# Rôle AT-SPI (nom anglais renvoyé par get_role_name) → rôle normalisé.
_ROLE_MAP: dict[str, str] = {
    "push button": "button",
    "button": "button",
    "toggle button": "toggle",
    "check box": "checkbox",
    "radio button": "radio",
    "menu": "menu",
    "menu item": "menu_item",
    "check menu item": "menu_item",
    "radio menu item": "menu_item",
    "combo box": "combo",
    "text": "text_field",
    "entry": "text_field",
    "password text": "password",
    "spin button": "spin",
    "document text": "document",
    "terminal": "terminal",
    "page tab": "tab",
    "link": "link",
    "list item": "list_item",
    "table cell": "cell",
    "tree item": "tree_item",
    "icon": "icon",
    "slider": "slider",
    "label": "label",
    "static": "label",
    "heading": "heading",
    "status bar": "status",
}

# Rôles sans intérêt quand ils n'ont pas de nom (séparateurs, cellules de structure) ;
# les autres sont gardés sans nom : champs (leur contenu compte), boutons-icônes.
_STRUCTURE_ROLES = frozenset({"cell", "menu_item", "list_item", "tree_item"})
# Rôles « texte » gardés seulement s'ils ont un nom court (contexte : messages,
# titres de dialogue) ; ils ne sont pas cliquables en soi.
_TEXT_ROLES = frozenset({"label", "heading", "status"})
_TEXT_NAME_MAX = 200
# Fenêtres de premier niveau d'une application (frame GTK, filler Qt, dialogues).
_TOPLEVEL_ROLES = frozenset(
    {
        "frame",
        "dialog",
        "window",
        "alert",
        "file chooser",
        "color chooser",
        "font chooser",
        "filler",
    }
)
# Fenêtres transitoires (menus contextuels) parcourues en plus de la fenêtre active.
_POPUP_ROLES = frozenset({"window", "menu", "popup menu"})
# Éléments de menu : un menu ouvert déborde souvent de sa fenêtre.
_MENU_ROLES = frozenset({"menu", "menu item", "check menu item", "radio menu item"})

# Bornes du parcours.
MAX_NODES = 3000
MAX_SECONDS = 2.0
MAX_DEPTH = 40
MAX_CHILDREN = 400
MAX_ELEMENTS = 220
_VALUE_READ_MAX = 400  # caractères lus au plus dans un champ / document

# Variables passées aux applications lancées par My_OS (open_app) pour qu'elles
# exposent leur arbre : Chromium et Electron ne lisent l'état de l'accessibilité
# qu'à leur démarrage (Qt et Firefox suivent aussi org.a11y.Status, cf.
# enable_accessibility).
ACCESSIBILITY_ENV: dict[str, str] = {
    "GNOME_ACCESSIBILITY": "1",
    "ACCESSIBILITY_ENABLED": "1",
    "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
}


def enable_accessibility() -> bool:
    """Active l'accessibilité de la session (``org.a11y.Status.IsEnabled``).

    Les applications Qt et Firefox suivent ce drapeau du bus d'accessibilité et
    exposent alors leur arbre ; GTK l'expose toujours. Sans dbus-python ni bus
    d'accessibilité, renvoie ``False`` sans lever : l'OCR prend le relais.
    """
    try:
        import dbus  # pylint: disable=import-outside-toplevel
    except ImportError:
        return False
    try:
        bus = dbus.SessionBus()
        proxy = bus.get_object("org.a11y.Bus", "/org/a11y/bus", introspect=False)
        props = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
        # variant_level=1 : sans introspection, dbus-python enverrait un booléen
        # simple (signature ssb), que le bus refuse (méthode inconnue).
        props.Set("org.a11y.Status", "IsEnabled", dbus.Boolean(True, variant_level=1))
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        _log.info("Accessibilité de la session non activée : %s", exc)
        return False
    return True


class AccessibleNode(Protocol):
    """Sous-ensemble d'``Atspi.Accessible`` utilisé ici (faux nœuds en test)."""

    def get_role_name(self) -> str:
        """Rôle AT-SPI en anglais (« push button »…)."""

    def get_name(self) -> str:
        """Nom accessible."""

    def get_child_count(self) -> int:
        """Nombre d'enfants."""

    def get_child_at_index(self, index: int) -> Any:
        """Enfant ``index``."""


@dataclass
class NodeInfo:
    """Propriétés d'un nœud lues en une fois (indépendantes de libatspi)."""

    role: str  # rôle AT-SPI brut
    name: str
    states: frozenset[str]  # showing, visible, focused, checked, selected,
    # expanded, sensitive, editable, active
    rect: Rect | None
    value: str = ""


class AtspiAdapter(Protocol):
    """Accès aux nœuds : production (libatspi) ou faux arbre (tests)."""

    def applications(self) -> list[tuple[Any, int | None, str]]:
        """Applications du bureau : ``(nœud, pid, nom)``."""

    def info(self, node: Any) -> NodeInfo:
        """Lit rôle, nom, états, position (et valeur des champs) d'un nœud."""

    def children(self, node: Any) -> Iterable[Any]:
        """Enfants d'un nœud (bornés)."""

    def refresh(self, app: Any) -> None:
        """Vide le cache de l'application (états et noms à jour)."""


def _valid_rect(rect: Rect | None, bounds: Rect) -> Rect | None:
    """Position utilisable : non vide, à l'écran ; coupée aux bornes données."""
    if rect is None or rect.width <= 0 or rect.height <= 0:
        return None
    if rect.x < -10000 or rect.y < -10000:  # GTK : hors écran = INT_MIN
        return None
    clipped = rect.intersection(bounds)
    if clipped is None or clipped.area < 0.2 * rect.area:
        return None
    return clipped


def _element_from(info: NodeInfo, rect: Rect) -> Element | None:
    """Élément pour le modèle, ou ``None`` si le nœud n'en vaut pas un."""
    role = _ROLE_MAP.get(info.role)
    if role is None:
        return None
    name = " ".join(info.name.split())
    if role == "text_field" and "editable" not in info.states:
        # Texte en lecture seule : un libellé si court, sinon un document.
        role = "label" if len(info.value or name) <= _TEXT_NAME_MAX else "document"
        name = name or info.value
    if role in _TEXT_ROLES:
        if not name or len(name) > _TEXT_NAME_MAX:
            return None
    elif not name and role in _STRUCTURE_ROLES:
        return None  # séparateurs, cellules de structure
    states = {
        s for s in ("focused", "checked", "selected", "expanded") if s in info.states
    }
    if "sensitive" not in info.states and role not in _TEXT_ROLES:
        states.add("disabled")
    if role == "password":
        states.add("password")
    value = (
        info.value
        if role in ("text_field", "spin", "combo", "document", "terminal")
        else ""
    )
    return Element(
        role=role,
        name=name,
        rect=rect,
        source="a11y",
        states=frozenset(states),
        value="" if role == "password" else value,
    )


def _reading_order(elements: list[Element]) -> list[Element]:
    """Ordre de lecture : de haut en bas par bandes de 12 px, puis de gauche à droite."""
    return sorted(elements, key=lambda e: (e.rect.y // 12, e.rect.x, e.rect.y))


def _dedupe(elements: list[Element]) -> list[Element]:
    """Retire les doublons : même nom au même endroit (ex. bouton et son libellé)."""
    kept: list[Element] = []
    for element in elements:
        duplicate = False
        for other in kept:
            same_name = element.name and element.name == other.name
            if same_name and (
                element.rect.overlap_ratio(other.rect) > 0.6
                or other.rect.overlap_ratio(element.rect) > 0.6
            ):
                duplicate = True
                # Le rôle interactif l'emporte sur un libellé.
                if other.role in _TEXT_ROLES and element.role not in _TEXT_ROLES:
                    kept[kept.index(other)] = element
                break
        if not duplicate:
            kept.append(element)
    return kept


class AccessibilityReader:
    """Lit les éléments visibles de la fenêtre active via AT-SPI."""

    def __init__(
        self,
        adapter: AtspiAdapter | None = None,
        *,
        max_nodes: int = MAX_NODES,
        max_seconds: float = MAX_SECONDS,
    ) -> None:
        self._adapter = adapter
        self._max_nodes = max_nodes
        self._max_seconds = max_seconds
        self._lock = threading.Lock()

    def _get_adapter(self) -> AtspiAdapter | None:
        if self._adapter is None:
            try:
                self._adapter = _LibAtspiAdapter()
            except (ImportError, ValueError, RuntimeError) as exc:
                _log.warning("Accessibilité AT-SPI indisponible : %s", exc)
                return None
        return self._adapter

    def elements(self, window: Window, screen: Rect) -> list[Element] | None:
        """Éléments visibles de ``window`` (ordre de lecture).

        ``None`` si l'application n'est pas dans l'arbre d'accessibilité (rien
        d'exposé : l'OCR prend le relais) ; liste vide si elle n'expose rien de
        visible.
        """
        with self._lock:
            adapter = self._get_adapter()
            if adapter is None or window.pid is None:
                return None
            try:
                return self._read(adapter, window, screen)
            except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                _log.warning("Lecture AT-SPI impossible (%s) : %s", window.app, exc)
                return None

    def focused(self, window: Window, screen: Rect) -> Element | None:
        """Élément qui a le focus clavier dans ``window`` (``None`` si inconnu)."""
        elements = self.elements(window, screen) or []
        for element in elements:
            if "focused" in element.states:
                return element
        return None

    def _read(
        self, adapter: AtspiAdapter, window: Window, screen: Rect
    ) -> list[Element] | None:
        apps = [app for app in adapter.applications() if app[1] == window.pid]
        if not apps:
            return None
        # Les éléments sont gardés dans la fenêtre (une liste défilée expose aussi
        # ses lignes masquées), sauf les menus ouverts, qui en débordent souvent.
        bounds = window.rect.intersection(screen) or screen
        roots: list[Any] = []
        for app, _pid, _name in apps:
            adapter.refresh(app)
            roots.extend(self._toplevels(adapter, app, window))
        if not roots:
            return []
        deadline = time.monotonic() + self._max_seconds
        found: list[Element] = []
        visited = 0
        stack: list[tuple[Any, int]] = [(root, 0) for root in reversed(roots)]
        while stack and visited < self._max_nodes and len(found) < MAX_ELEMENTS:
            if time.monotonic() > deadline:
                _log.info("Parcours AT-SPI interrompu (durée) : %d nœuds", visited)
                break
            node, depth = stack.pop()
            visited += 1
            try:
                info = adapter.info(node)
            except Exception:  # noqa: BLE001, S112  # pylint: disable=broad-exception-caught
                continue
            showing = "showing" in info.states and "visible" in info.states
            if depth > 0 and not showing:
                continue
            in_menu = info.role in _MENU_ROLES
            rect = _valid_rect(info.rect, screen if in_menu else bounds)
            if showing and rect is not None:
                element = _element_from(info, rect)
                if element is not None:
                    found.append(element)
            if depth >= MAX_DEPTH:
                continue
            try:
                kids = list(adapter.children(node))
            except Exception:  # noqa: BLE001, S112  # pylint: disable=broad-exception-caught
                continue
            stack.extend((kid, depth + 1) for kid in reversed(kids))
        return _dedupe(_reading_order(found))

    @staticmethod
    def _toplevels(adapter: AtspiAdapter, app: Any, window: Window) -> list[Any]:
        """Fenêtre de l'application qui correspond à ``window`` (+ menus ouverts)."""
        best_score, best = 0, None
        popups: list[Any] = []
        for child in adapter.children(app):
            try:
                info = adapter.info(child)
            except Exception:  # noqa: BLE001, S112  # pylint: disable=broad-exception-caught
                continue
            if info.role not in _TOPLEVEL_ROLES and info.role not in _POPUP_ROLES:
                continue
            score = 0
            if "active" in info.states:
                score += 3
            if info.name and info.name == window.title:
                score += 2
            if info.rect is not None and info.rect.overlap_ratio(window.rect) > 0.5:
                score += 1
            if score > best_score:
                best_score, best = score, child
            elif (
                not score
                and info.role in _POPUP_ROLES
                and "showing" in info.states
                and info.rect is not None
                and info.rect.area > 0
            ):
                popups.append(child)
        return ([best] if best is not None else []) + popups


def _state_names(state_set: Any, atspi: Any) -> frozenset[str]:
    names = (
        "showing",
        "visible",
        "focused",
        "checked",
        "selected",
        "expanded",
        "sensitive",
        "editable",
        "active",
    )
    return frozenset(
        name
        for name in names
        if state_set.contains(getattr(atspi.StateType, name.upper()))
    )


class _LibAtspiAdapter:
    """Accès réel à l'arbre AT-SPI via libatspi (PyGObject)."""

    def __init__(self) -> None:
        import gi  # pylint: disable=import-outside-toplevel

        gi.require_version("Atspi", "2.0")
        from gi.repository import (  # pylint: disable=import-outside-toplevel
            Atspi,
            GLib,
        )

        self._atspi = Atspi
        self._context = GLib.MainContext.default()
        Atspi.init()
        # Une application figée ne bloque pas plus d'une seconde par appel.
        Atspi.set_timeout(1000, 15000)

    def _drain(self) -> None:
        """Traite les signaux D-Bus en attente (mises à jour du cache de libatspi)."""
        for _ in range(200):
            if not self._context.pending():
                break
            self._context.iteration(False)

    def applications(self) -> list[tuple[Any, int | None, str]]:
        self._drain()
        desktop = self._atspi.get_desktop(0)
        apps = []
        for index in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(index)
            if app is None:
                continue
            try:
                apps.append((app, app.get_process_id(), app.get_name() or ""))
            except Exception:  # noqa: BLE001, S112  # pylint: disable=broad-exception-caught
                continue
        return apps

    def refresh(self, app: Any) -> None:
        try:
            app.clear_cache()
        except Exception:  # noqa: BLE001, S110  # pylint: disable=broad-exception-caught
            pass

    def info(self, node: Any) -> NodeInfo:
        atspi = self._atspi
        role = node.get_role_name() or ""
        name = node.get_name() or ""
        states = _state_names(node.get_state_set(), atspi)
        rect = None
        # Méthodes d'interface appelées par leur classe : sur l'objet, PyGObject
        # résoudrait par exemple ``get_text`` vers Accessible.get_text (obsolète).
        if node.get_component_iface() is not None:
            ext = atspi.Component.get_extents(node, atspi.CoordType.SCREEN)
            rect = Rect(ext.x, ext.y, ext.width, ext.height)
        value = ""
        if role in (
            "text",
            "entry",
            "spin button",
            "combo box",
            "document text",
            "terminal",
        ):
            value = self._text_of(node)
        return NodeInfo(role=role, name=name, states=states, rect=rect, value=value)

    def _text_of(self, node: Any) -> str:
        if node.get_text_iface() is None:
            return ""
        try:
            count = self._atspi.Text.get_character_count(node)
            return self._atspi.Text.get_text(node, 0, min(count, _VALUE_READ_MAX)) or ""
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            return ""

    @staticmethod
    def children(node: Any) -> Iterable[Any]:
        count = min(node.get_child_count(), MAX_CHILDREN)
        for index in range(count):
            child = node.get_child_at_index(index)
            if child is not None:
                yield child
