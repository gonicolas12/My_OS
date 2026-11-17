"""Avis du bureau du premier démarrage de l'ISO (:mod:`ui.setup_notice`).

``myos-user-firstrun`` les montre tant que les dépendances de My_OS s'installent en
tâche de fond (le script lui-même est testé dans ``tests/test_packaging``). Ici :
langue (celle du popup), textes et raccourci, appel au serveur de notifications
(remplacé par un faux) et ligne de commande.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.config import DEFAULT_HOTKEY, Config
from core.i18n import set_language
from ui import setup_notice

_Call = tuple[str, int, str, str, str, int]


class _Server:
    """Faux serveur de notifications : garde chaque appel, rend l'identifiant 42."""

    def __init__(self) -> None:
        self.calls: list[_Call] = []

    def __call__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, app: str, replaces: int, icon: str, summary: str, body: str, expire: int
    ) -> int:
        self.calls.append((app, replaces, icon, summary, body, expire))
        return 42


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> _Server:
    fake = _Server()
    monkeypatch.setattr(setup_notice, "_dbus_notify", fake)
    return fake


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Config]:
    """Popup sans choix mémorisé, config par défaut, système en anglais."""
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "C.UTF-8")
    config = Config()
    monkeypatch.setattr(setup_notice, "load_config", lambda: config)
    return config_home / "my_os" / "popup.conf", config


def _popup_conf(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


# ---- langue ------------------------------------------------------------------------


def test_langue_choisie_dans_le_popup(settings: tuple[Path, Config]) -> None:
    conf, _ = settings
    # Fichier écrit par QSettings (le popup y garde aussi sa géométrie).
    _popup_conf(conf, "[General]\ngeometry=@ByteArray(\\x1\\xd9%)\nlanguage=fr\n")
    assert setup_notice.popup_language() == "fr"


@pytest.mark.parametrize(
    "text",
    [None, "[General]\ngeometry=@Rect(0 0 960 680)\n", "language=fr\n", b"\xff\xfe"],
)
def test_sans_choix_lisible_du_popup(
    settings: tuple[Path, Config], text: str | bytes | None
) -> None:
    # Pas de fichier, pas de choix, fichier sans section, ou octets qui ne sont pas de
    # l'UTF-8 (UnicodeDecodeError).
    conf, _ = settings
    if text is not None:
        _popup_conf(conf, text)
    assert setup_notice.popup_language() is None


@pytest.mark.parametrize(
    ("popup", "configured", "lang", "expected"),
    [
        ("fr", "en", "C.UTF-8", "My_OS n'est pas encore prêt"),  # choix du popup
        ("de", "en", "fr_FR.UTF-8", "My_OS is not ready yet"),  # choix inconnu : config
        (None, "auto", "fr_FR.UTF-8", "My_OS n'est pas encore prêt"),  # système
        (None, "auto", "C.UTF-8", "My_OS is not ready yet"),  # live : anglais
    ],
)
def test_langue_du_popup_sinon_config_sinon_systeme(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    settings: tuple[Path, Config],
    server: _Server,
    monkeypatch: pytest.MonkeyPatch,
    popup: str | None,
    configured: str,
    lang: str,
    expected: str,
) -> None:
    conf, config = settings
    if popup is not None:
        _popup_conf(conf, f"[General]\nlanguage={popup}\n")
    config.language = configured
    monkeypatch.setenv("LANG", lang)
    setup_notice.notify(setup_notice.WAITING)
    assert server.calls[0][3] == expected


# ---- textes ------------------------------------------------------------------------


def test_textes_avec_le_raccourci_lisible() -> None:
    set_language("fr")
    title, body = setup_notice.notice_text(setup_notice.WAITING, DEFAULT_HOTKEY)
    assert title == "My_OS n'est pas encore prêt"
    # Hors ligne comme derrière un portail captif : Internet « ne répond pas ».
    assert "Internet ne répond pas pour l'instant" in body
    assert "l'assistant (Ctrl+Alt+Espace)" in body
    set_language("en")
    title, body = setup_notice.notice_text(setup_notice.READY, "<ctrl>+<shift>+m")
    assert title == "My_OS is ready"
    assert body.startswith("Its components are installed: Ctrl+Shift+M opens")


# ---- envoi -------------------------------------------------------------------------


@pytest.mark.usefixtures("settings")
def test_avis_en_attente_reste_affiche(server: _Server) -> None:
    assert setup_notice.notify(setup_notice.WAITING) == 42
    [(app, replaces, icon, summary, body, expire)] = server.calls
    assert (app, replaces, icon) == ("My_OS", 0, "network-offline")
    assert summary == "My_OS is not ready yet"
    assert "the assistant (Ctrl+Alt+Space)" in body
    assert expire == 0  # jamais d'expiration : il reste jusqu'à « prêt »


@pytest.mark.usefixtures("settings")
def test_avis_pret_remplace_l_avis_en_attente(server: _Server) -> None:
    assert setup_notice.notify(setup_notice.READY, 42) == 42
    [(_, replaces, icon, summary, _, expire)] = server.calls
    assert (replaces, icon, summary) == (42, "dialog-information", "My_OS is ready")
    assert expire == -1  # délai habituel du serveur


@pytest.mark.usefixtures("settings")
@pytest.mark.parametrize("error", [ImportError("No module named 'dbus'"), OSError()])
def test_sans_serveur_de_notifications_rien_n_echoue(
    caplog: pytest.LogCaptureFixture, error: Exception
) -> None:

    def unreachable(*_args: object) -> int:
        raise error

    with caplog.at_level(logging.WARNING, logger="myos.setup"):
        assert setup_notice.notify(setup_notice.WAITING, sender=unreachable) is None
    assert "Avis du bureau « waiting » non affiché" in caplog.text


def test_config_illisible_rien_n_echoue(
    monkeypatch: pytest.MonkeyPatch,
    server: _Server,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> Config:
        raise ValueError("config.yaml : YAML invalide")

    monkeypatch.setattr(setup_notice, "load_config", broken)
    with caplog.at_level(logging.WARNING, logger="myos.setup"):
        assert setup_notice.notify(setup_notice.READY) is None
    assert "YAML invalide" in caplog.text
    assert server.calls == []


def test_raccourci_inattendu_rien_n_echoue(
    settings: tuple[Path, Config], server: _Server
) -> None:
    _, config = settings
    config.hotkey = 42  # type: ignore[assignment]  # config.yaml mal écrit
    assert setup_notice.notify(setup_notice.WAITING) is None
    assert server.calls == []


# ---- ligne de commande -------------------------------------------------------------


@pytest.mark.usefixtures("settings")
def test_ligne_de_commande(server: _Server, capsys: pytest.CaptureFixture[str]) -> None:
    assert setup_notice.main(["waiting"]) == 0
    assert capsys.readouterr().out == "42\n"  # identifiant, pour le remplacer
    assert setup_notice.main(["ready", "42"]) == 0
    assert setup_notice.main(["ready"]) == 0
    assert setup_notice.main(["ready", "x7"]) == 0  # identifiant illisible : ignoré
    assert setup_notice.main(["ready", "²"]) == 0  # chiffre pour isdigit, pas pour int
    assert [call[1] for call in server.calls] == [0, 42, 0, 0, 0]


@pytest.mark.parametrize("args", [[], ["oups"], ["ready", "1", "2"]])
def test_ligne_de_commande_invalide(
    server: _Server, capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    assert setup_notice.main(args) == 2
    assert "usage" in capsys.readouterr().err
    assert server.calls == []
