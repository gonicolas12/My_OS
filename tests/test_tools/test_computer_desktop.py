"""Bureau du contrôle de l'ordinateur : combinaisons de touches, catalogue des
applications, injection X11 (xdotool simulé) et backend d'observation."""

# pylint: disable=missing-function-docstring,too-many-instance-attributes
# pylint: disable=redefined-outer-name,use-implicit-booleaness-not-comparison
# pylint: disable=unbalanced-tuple-unpacking
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_tools.computer_fakes import make_element, make_window
from tools.computer import backend as backend_module
from tools.computer.apps import AppCatalog, AppEntry, application_dirs
from tools.computer.backend import LOCKED_NOTE, DesktopBackend, scope_unit
from tools.computer.keys import combo_parts, parse_combo
from tools.computer.screen import Rect
from tools.computer.x11 import (
    ControlStopped,
    DesktopError,
    X11Desktop,
    unavailable_reason,
)

# ---- combinaisons de touches ---------------------------------------------------


@pytest.mark.parametrize(
    ("written", "canonical"),
    [
        ("ctrl+s", "ctrl+s"),
        ("Ctrl+S", "ctrl+s"),
        ("Contrôle + Maj + T", "ctrl+shift+t"),
        ("shift+ctrl+t", "ctrl+shift+t"),
        ("Entrée", "Return"),
        ("Échap", "Escape"),
        ("alt+F4", "alt+F4"),
        ("f11", "F11"),
        ("Retour arrière", "BackSpace"),
        ("Suppr", "Delete"),
        ("ctrl++", "ctrl+plus"),
        ("ctrl+-", "ctrl+minus"),
        ("Windows", "super"),
        ("A", "A"),
        ("Page suivante", "Next"),
        ("flèche haut", "Up"),
    ],
)
def test_combinaison_canonique(written: str, canonical: str) -> None:
    assert parse_combo(written) == canonical


@pytest.mark.parametrize("bad", ["", "   ", "ctrl+a+b", "ctrl+machin", "F99"])
def test_combinaison_illisible(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_combo(bad)


def test_parties_d_une_combinaison() -> None:
    assert combo_parts("ctrl+shift+Delete") == (frozenset({"ctrl", "shift"}), "Delete")
    assert combo_parts("Return") == (frozenset(), "Return")


# ---- catalogue des applications --------------------------------------------------


def _desktop_file(folder: Path, stem: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.desktop").write_text(
        "[Desktop Entry]\nType=Application\n" + body, encoding="utf-8"
    )


@pytest.fixture
def catalog(tmp_path: Path) -> AppCatalog:
    system = tmp_path / "usr"
    user = tmp_path / "home"
    _desktop_file(
        system,
        "org.xfce.mousepad",
        "Name=Mousepad\nGenericName=Text Editor\nGenericName[fr]=Éditeur de texte\n"
        "Keywords=text;editor;\nExec=mousepad %U\n",
    )
    _desktop_file(
        system,
        "firefox",
        "Name=Firefox\nGenericName=Web Browser\nExec=/usr/lib/firefox/firefox %u\n",
    )
    _desktop_file(system, "cache", "Name=Caché\nExec=cache\nNoDisplay=true\n")
    _desktop_file(system, "lien", "Type=Link\nName=Lien\nURL=https://example.org\n")
    _desktop_file(user, "firefox", "Name=Firefox (perso)\nExec=firefox-perso\n")
    return AppCatalog([user, system])


def test_catalogue_ignore_cachees_et_liens(catalog: AppCatalog) -> None:
    names = {entry.name for entry in catalog.entries()}
    assert names == {"Mousepad", "Firefox (perso)"}


def test_dossier_utilisateur_prioritaire(catalog: AppCatalog) -> None:
    assert catalog.find("firefox").executable == "firefox-perso"


@pytest.mark.parametrize(
    "query", ["Mousepad", "mousepad", "éditeur de texte", "EDITEUR DE TEXTE", "editor"]
)
def test_recherche_par_nom_generique_ou_mot_cle(
    catalog: AppCatalog, query: str
) -> None:
    entry = catalog.find(query)
    assert entry is not None and entry.app_id == "org.xfce.mousepad"


def test_recherche_sans_resultat(catalog: AppCatalog) -> None:
    assert catalog.find("photoshop") is None
    assert catalog.find("") is None


def test_entree_qui_ouvre_un_terminal(tmp_path: Path) -> None:
    _desktop_file(
        tmp_path,
        "xfce4-terminal-emulator",
        "Name=Terminal Emulator\nExec=exo-open --launch TerminalEmulator\n"
        "Categories=Utility;X-XFCE;\n",
    )
    _desktop_file(tmp_path, "htop", "Name=Htop\nExec=htop\nTerminal=true\n")
    _desktop_file(tmp_path, "notes", "Name=Notes\nExec=notes\nCategories=Office;\n")
    catalog = AppCatalog([tmp_path])
    emulator = catalog.find("Terminal Emulator")
    assert emulator.categories == ("Utility", "X-XFCE")
    assert emulator.command == "exo-open --launch TerminalEmulator"
    assert emulator.opens_terminal
    assert catalog.find("htop").opens_terminal
    assert not catalog.find("notes").opens_terminal


def test_dossiers_xdg(tmp_path: Path) -> None:
    dirs = application_dirs({"HOME": str(tmp_path), "XDG_DATA_DIRS": "/opt/a:/opt/b"})
    assert dirs[0] == tmp_path / ".local" / "share" / "applications"
    assert Path("/opt/a/applications") in dirs and Path("/opt/b/applications") in dirs


# ---- injection X11 (xdotool simulé) ------------------------------------------------


class _Guard:
    def __init__(self) -> None:
        self.allowed = True
        self.escapes: list[int] = []

    def check(self) -> None:
        if not self.allowed:
            raise ControlStopped("arrêté")

    def expect_escape(self, count: int = 1) -> None:
        self.escapes.append(count)


def _desktop(returncode: int = 0) -> tuple[X11Desktop, list, _Guard]:
    calls: list = []

    def runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
        _ = timeout
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, returncode, "", "erreur X")

    guard = _Guard()
    return X11Desktop(guard=guard, runner=runner), calls, guard


def test_session_x11_requise() -> None:
    assert "Wayland" in unavailable_reason({"XDG_SESSION_TYPE": "wayland"})
    assert "DISPLAY" in unavailable_reason({"XDG_SESSION_TYPE": "x11"})
    assert unavailable_reason({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}) is None


def test_clic_xdotool() -> None:
    desktop, calls, _ = _desktop()
    desktop.click(740, 415, count=2)
    assert calls == [
        [
            "xdotool",
            "mousemove",
            "--sync",
            "740",
            "415",
            "click",
            "--repeat",
            "2",
            "--delay",
            "80",
            "1",
        ]
    ]


def test_saisie_xdotool_protegee_par_double_tiret() -> None:
    desktop, calls, _ = _desktop()
    desktop.type_text("--help é")
    assert calls[0][:2] == ["xdotool", "type"]
    assert calls[0][-2:] == ["--", "--help é"]


def test_echap_injecte_annonce_au_garde() -> None:
    desktop, calls, guard = _desktop()
    desktop.key("Escape", repeat=3)
    desktop.key("ctrl+s")
    assert guard.escapes == [3]
    assert calls[0][-1] == "Escape" and calls[1][-1] == "ctrl+s"


def test_rien_n_est_injecte_sans_garde_ou_apres_l_arret() -> None:
    desktop, calls, guard = _desktop()
    guard.allowed = False
    with pytest.raises(ControlStopped):
        desktop.click(1, 1)
    with pytest.raises(ControlStopped):
        desktop.key("Escape")
    assert guard.escapes == []
    desktop.set_guard(None)
    with pytest.raises(ControlStopped):
        desktop.type_text("x")
    assert calls == []


def test_echec_de_xdotool_signale() -> None:
    desktop, _, _ = _desktop(returncode=1)
    with pytest.raises(DesktopError, match="xdotool windowactivate"):
        desktop.activate(42)


def test_bouton_et_direction_inconnus() -> None:
    desktop, calls, _ = _desktop()
    with pytest.raises(DesktopError):
        desktop.click(1, 1, button="quatre")
    with pytest.raises(DesktopError):
        desktop.scroll(1, 1, "diagonale", 3)
    assert calls == []


# ---- backend d'observation ----------------------------------------------------------


class _Desktop:
    """Double de X11Desktop pour DesktopBackend.observe."""

    def __init__(self, windows: list, *, capture_fails: bool = True) -> None:
        self._windows = windows
        self.capture_fails = capture_fails
        self.guard_ok = True
        self.guard = None

    def check_guard(self) -> None:
        if not self.guard_ok:
            raise ControlStopped("arrêté")

    @staticmethod
    def screen_rect() -> Rect:
        return Rect(0, 0, 1920, 1080)

    def windows(self) -> list:
        return list(self._windows)

    @staticmethod
    def docks() -> list:
        return []

    def window_at(self, x: int, y: int):
        return next((w for w in self._windows if w.rect.contains(x, y)), None)

    def active_window(self):
        return next((w for w in self._windows if w.active), None)

    def capture(self):
        if self.capture_fails:
            raise DesktopError("pas d'écran")
        image_module = pytest.importorskip("PIL.Image")
        return image_module.new("RGB", (1920, 1080), "white")

    def set_guard(self, guard) -> None:
        self.guard = guard


class _A11y:
    def __init__(self, elements: list | None) -> None:
        self._elements = elements

    def elements(self, _window, _screen):
        return self._elements

    def focused(self, _window, _screen):
        return next((e for e in self._elements or [] if "focused" in e.states), None)


class _Ocr:
    def __init__(self, lines: list) -> None:
        self.lines = lines

    @staticmethod
    def available() -> bool:
        return True

    def read(self, _image, _region) -> list:
        return self.lines


def _backend(
    windows: list,
    elements: list | None,
    *,
    lines: list | None = None,
    capture_fails: bool = True,
):
    desktop = _Desktop(windows, capture_fails=capture_fails)
    backend = DesktopBackend(
        desktop=desktop,  # type: ignore[arg-type]
        accessibility=_A11y(elements),  # type: ignore[arg-type]
        ocr=_Ocr(lines or []),  # type: ignore[arg-type]
        apps=AppCatalog([]),
    )
    return backend, desktop


def test_observation_fenetre_active_en_tete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_module, "screen_locked", lambda: False)
    thunar = make_window(2, "myos - Thunar", app="Thunar", active=False)
    editor = make_window()
    button = make_element("button", "Enregistrer", Rect(700, 400, 80, 30))
    backend, _ = _backend([thunar, editor], [button])
    observation = backend.observe()
    assert observation.windows[0] is editor and observation.active is editor
    assert observation.elements == [button]
    assert backend.last is observation
    assert any("Capture d'écran impossible" in note for note in observation.notes)


def test_observation_exige_la_session_de_controle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend_module, "screen_locked", lambda: False)
    backend, desktop = _backend([make_window()], [])
    desktop.guard_ok = False
    with pytest.raises(ControlStopped):
        backend.observe()


def test_ecran_verrouille_rien_n_est_lu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend_module, "screen_locked", lambda: True)
    backend, desktop = _backend(
        [make_window()], [make_element("button", "x", Rect(0, 0, 1, 1))]
    )
    desktop.capture_fails = False
    observation = backend.observe()
    assert observation.elements == [] and observation.active is None
    assert LOCKED_NOTE in observation.notes
    assert observation.image is None


def test_ocr_complete_l_accessibilite_hors_barre_d_arret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PIL.Image")
    monkeypatch.setattr(backend_module, "screen_locked", lambda: False)
    button = make_element("button", "Enregistrer", Rect(700, 400, 80, 30))
    lines = [
        ("Desktop", Rect(850, 450, 60, 14)),
        ("Cliquez ici", Rect(1000, 760, 70, 14)),
    ]
    backend, _ = _backend([make_window()], [button], lines=lines, capture_fails=False)
    backend.set_exclusions([Rect(900, 740, 400, 60)])
    observation = backend.observe()
    assert [e.name for e in observation.elements] == ["Enregistrer", "Desktop"]
    assert observation.image is not None and observation.image_size == (1280, 720)


def test_elements_accessibles_sous_un_point() -> None:
    inside = make_element("button", "OK", Rect(700, 400, 80, 30))
    elsewhere = make_element("button", "Non", Rect(900, 400, 80, 30))
    backend, _ = _backend([make_window()], [inside, elsewhere])
    assert backend.accessible_at(710, 410) == [inside]
    assert backend.accessible_at(10, 10) == []


def _launch_calls(monkeypatch: pytest.MonkeyPatch, systemd: bool) -> list:
    calls: list = []

    def runner(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
        _ = timeout
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(DesktopBackend, "_systemd_user", staticmethod(lambda: systemd))
    desktop = _Desktop([make_window()])
    desktop.guard = object()
    backend = DesktopBackend(desktop=desktop, runner=runner, apps=AppCatalog([]))  # type: ignore[arg-type]
    entry = AppEntry(
        app_id="org.xfce.mousepad",
        name="Mousepad",
        path="/usr/share/applications/org.xfce.mousepad.desktop",
    )
    backend.launch(entry)
    return calls


def test_lancement_dans_son_propre_scope_systemd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (argv,) = _launch_calls(monkeypatch, systemd=True)
    assert argv[:5] == ["systemd-run", "--user", "--scope", "--quiet", "--collect"]
    assert argv[5].startswith("--unit=app-myos-org.xfce.mousepad-")
    assert argv[-3:] == [
        "gio",
        "launch",
        "/usr/share/applications/org.xfce.mousepad.desktop",
    ]
    assert "ACCESSIBILITY_ENABLED=1" in argv and argv[6] == "env"


def test_lancement_sans_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    (argv,) = _launch_calls(monkeypatch, systemd=False)
    assert argv[0] == "env" and argv[-2:] == [
        "launch",
        "/usr/share/applications/org.xfce.mousepad.desktop",
    ]


def test_nom_de_scope_assaini() -> None:
    unit = scope_unit("com.exemple/app étrange")
    assert unit.startswith("app-myos-com.exemple_app__trange-") and unit.endswith(
        ".scope"
    )
    assert scope_unit("x") != scope_unit("x")  # suffixe aléatoire
