"""Outils de réglages système du jalon 3 — luminosité, audio, réseau.

Quatre outils, tous **niveau 1** et **sans élévation** (les services système
appliquent leur propre politique polkit pour la session active de l'utilisateur ;
aucun n'a besoin que My_OS passe root) :

* ``set_brightness`` — luminosité de l'écran (0–100 %) ;
* ``set_volume`` — volume du son (0–100 %) ;
* ``set_mute`` — coupe / rétablit le son ;
* ``set_wifi`` — active / désactive le Wi-Fi.

**Transport.** La logique d'outil (validation, bornage) est découplée du
transport via un :class:`SettingsBackend` **injectable** : les tests passent un
faux backend déterministe, sans dépendre de D-Bus ni du matériel. Le backend de
production :class:`_SystemBackend` (import paresseux de ``dbus``) utilise :

* la luminosité via **D-Bus** ``systemd-logind`` (``Session.SetBrightness``) ;
* le Wi-Fi via **D-Bus** ``NetworkManager`` (propriété ``WirelessEnabled``) ;
* le volume/mute via ``pactl`` (PipeWire/PulseAudio) lancé sans shell par
  :func:`core.elevation.run_command` — l'interface D-Bus audio n'étant pas
  exposée de façon fiable et portable, on utilise l'outil standard, en argv
  liste validée (cf. SECURITY §7). Choix documenté, transport interne au
  backend.

Aucune vérification de permission ici (rôle exclusif du ``policy_engine``).
"""

from __future__ import annotations

from typing import Protocol

from tools.base_tool import BaseTool, ToolResult


def _err(message: str) -> ToolResult:
    """Construit un ToolResult d'échec lisible."""
    return ToolResult(success=False, output=message, reversible=False)


def _coerce_percent(value: object) -> int | None:
    """Convertit un pourcentage (int/float/chaîne) en entier **borné à 0–100**.

    Renvoie ``None`` si la valeur n'est pas numériquement interprétable. Les
    valeurs hors borne sont ramenées dans [0, 100] plutôt que rejetées (un
    « monte le son à 200 » devient 100).
    """
    if isinstance(value, bool):  # bool est un int : on l'exclut explicitement
        return None
    if isinstance(value, (int, float)):
        number = int(value)
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        number = int(value.strip())
    else:
        return None
    return max(0, min(100, number))


def _coerce_bool(value: object) -> bool | None:
    """Convertit un booléen tolérant (bool, 0/1, 'true'/'false', 'on'/'off')."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "on", "yes", "oui", "actif", "active"):
            return True
        if low in ("false", "0", "off", "no", "non", "inactif", "desactive"):
            return False
    return None


class SettingsBackend(Protocol):
    """Transport abstrait des réglages système (D-Bus / pactl en production)."""

    def set_brightness(self, percent: int) -> None:
        """Règle la luminosité (``percent`` déjà borné à 0–100)."""

    def set_volume(self, percent: int) -> None:
        """Règle le volume (``percent`` déjà borné à 0–100)."""

    def set_mute(self, muted: bool) -> None:
        """Coupe (``True``) ou rétablit (``False``) le son."""

    def set_wifi(self, enabled: bool) -> None:
        """Active (``True``) ou désactive (``False``) le Wi-Fi."""


class _SystemBackend:
    """Backend de production. Imports paresseux : rien n'est chargé tant qu'on
    n'appelle pas une méthode (le module reste importable sous Windows/tests)."""

    def set_brightness(self, percent: int) -> None:
        """Règle la luminosité via D-Bus logind (``Session.SetBrightness``)."""
        import os  # pylint: disable=import-outside-toplevel

        import dbus  # pylint: disable=import-outside-toplevel

        backlight_root = "/sys/class/backlight"
        devices = (
            sorted(os.listdir(backlight_root)) if os.path.isdir(backlight_root) else []
        )
        if not devices:
            raise RuntimeError("aucun périphérique de rétroéclairage détecté")
        device = devices[0]
        with open(
            f"{backlight_root}/{device}/max_brightness", encoding="ascii"
        ) as handle:
            maximum = int(handle.read().strip())
        value = max(0, min(maximum, round(percent / 100 * maximum)))
        bus = dbus.SystemBus()
        session = bus.get_object(
            "org.freedesktop.login1", "/org/freedesktop/login1/session/auto"
        )
        iface = dbus.Interface(session, "org.freedesktop.login1.Session")
        iface.SetBrightness("backlight", device, dbus.UInt32(value))

    def set_volume(self, percent: int) -> None:
        """Règle le volume via ``pactl`` (sans shell, argv liste)."""
        from core.elevation import run_command  # pylint: disable=import-outside-toplevel

        result = run_command(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"]
        )
        if not result.ok:
            raise RuntimeError(
                result.stderr.strip() or "échec de pactl set-sink-volume"
            )

    def set_mute(self, muted: bool) -> None:
        """Coupe/rétablit le son via ``pactl`` (sans shell, argv liste)."""
        from core.elevation import run_command  # pylint: disable=import-outside-toplevel

        result = run_command(
            ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0"]
        )
        if not result.ok:
            raise RuntimeError(result.stderr.strip() or "échec de pactl set-sink-mute")

    def set_wifi(self, enabled: bool) -> None:
        """Active/désactive le Wi-Fi via D-Bus NetworkManager (``WirelessEnabled``)."""
        import dbus  # pylint: disable=import-outside-toplevel

        bus = dbus.SystemBus()
        manager = bus.get_object(
            "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
        )
        props = dbus.Interface(manager, "org.freedesktop.DBus.Properties")
        props.Set(
            "org.freedesktop.NetworkManager",
            "WirelessEnabled",
            dbus.Boolean(enabled),
        )


class _SettingTool(BaseTool):  # pylint: disable=abstract-method
    """Base commune (abstraite) : porte un ``backend`` injectable.

    ``run`` est volontairement non surchargée ici — chaque sous-classe concrète
    l'implémente ; cette classe n'est jamais instanciée directement.
    """

    risk_level = 1  # surchargé par chaque sous-classe ; présent pour BaseTool

    def __init__(self, backend: SettingsBackend | None = None) -> None:
        self._backend: SettingsBackend = backend or _SystemBackend()


class SetBrightness(_SettingTool):
    """Règle la luminosité de l'écran (0–100 %)."""

    name = "set_brightness"
    description = "Règle la luminosité de l'écran en pourcentage (0 à 100)."
    risk_level = 1
    parameters = {"percent": "int — niveau de luminosité voulu, 0 à 100"}

    def run(self, args: dict) -> ToolResult:
        percent = _coerce_percent(args.get("percent"))
        if percent is None:
            return _err("set_brightness : 'percent' doit être un nombre (0 à 100)")
        try:
            self._backend.set_brightness(percent)
        except (OSError, RuntimeError, ValueError) as exc:
            return _err(f"set_brightness : {exc}")
        return ToolResult(
            success=True, output=f"Luminosité réglée à {percent} %", reversible=False
        )


class SetVolume(_SettingTool):
    """Règle le volume du son (0–100 %)."""

    name = "set_volume"
    description = "Règle le volume du son en pourcentage (0 à 100)."
    risk_level = 1
    parameters = {"percent": "int — niveau de volume voulu, 0 à 100"}

    def run(self, args: dict) -> ToolResult:
        percent = _coerce_percent(args.get("percent"))
        if percent is None:
            return _err("set_volume : 'percent' doit être un nombre (0 à 100)")
        try:
            self._backend.set_volume(percent)
        except (OSError, RuntimeError, ValueError) as exc:
            return _err(f"set_volume : {exc}")
        return ToolResult(
            success=True, output=f"Volume réglé à {percent} %", reversible=False
        )


class SetMute(_SettingTool):
    """Coupe ou rétablit le son."""

    name = "set_mute"
    description = "Coupe (true) ou rétablit (false) le son."
    risk_level = 1
    parameters = {"muted": "bool — true pour couper le son, false pour le rétablir"}

    def run(self, args: dict) -> ToolResult:
        muted = _coerce_bool(args.get("muted"))
        if muted is None:
            return _err("set_mute : 'muted' doit être un booléen (true/false)")
        try:
            self._backend.set_mute(muted)
        except (OSError, RuntimeError, ValueError) as exc:
            return _err(f"set_mute : {exc}")
        etat = "coupé" if muted else "rétabli"
        return ToolResult(success=True, output=f"Son {etat}", reversible=False)


class SetWifi(_SettingTool):
    """Active ou désactive le Wi-Fi."""

    name = "set_wifi"
    description = "Active (true) ou désactive (false) le Wi-Fi."
    risk_level = 1
    parameters = {"enabled": "bool — true pour activer, false pour désactiver"}

    def run(self, args: dict) -> ToolResult:
        enabled = _coerce_bool(args.get("enabled"))
        if enabled is None:
            return _err("set_wifi : 'enabled' doit être un booléen (true/false)")
        try:
            self._backend.set_wifi(enabled)
        except (OSError, RuntimeError, ValueError) as exc:
            return _err(f"set_wifi : {exc}")
        etat = "activé" if enabled else "désactivé"
        return ToolResult(success=True, output=f"Wi-Fi {etat}", reversible=False)


# Registre des outils de réglages — fusionné dans le daemon (cf. daemon/myosd.py).
SETTINGS_TOOLS: dict[str, BaseTool] = {
    tool.name: tool
    for tool in (
        SetBrightness(),
        SetVolume(),
        SetMute(),
        SetWifi(),
    )
}
