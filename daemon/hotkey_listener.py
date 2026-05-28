"""Capture du raccourci clavier global (X11 via ``pynput``).

Le daemon est seul à écouter le raccourci. Quand la combinaison est pressée, il
déclenche un callback (le daemon ordonne alors au popup de s'afficher). Le port
Wayland (portal ``GlobalShortcuts``) est prévu pour le jalon 5.
"""

from __future__ import annotations

from collections.abc import Callable

from core.logger import get_logger

_log = get_logger("myosd.hotkey")


class HotkeyListener:
    """Écoute une combinaison globale et appelle ``on_activate`` à chaque appui."""

    def __init__(self, hotkey: str, on_activate: Callable[[], None]) -> None:
        self._hotkey = hotkey
        self._on_activate = on_activate
        self._listener = None

    def start(self) -> None:
        """Démarre l'écoute en arrière-plan (thread géré par pynput).

        ``pynput`` est importé ici (et non au chargement du module) pour éviter
        une erreur d'import sur un environnement sans serveur X.
        """
        from pynput import keyboard

        self._listener = keyboard.GlobalHotKeys({self._hotkey: self._on_activate})
        self._listener.start()
        _log.info("Écoute du raccourci global : %s", self._hotkey)

    def stop(self) -> None:
        """Arrête l'écoute du raccourci."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
