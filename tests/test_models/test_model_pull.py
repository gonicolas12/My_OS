"""Modèle local absent : état du téléchargement du 1er démarrage et message du popup.

Le fichier d'état est écrit par ``myos-user-firstrun`` (ISO) ; le script lui-même est
testé dans ``tests/test_packaging/test_firstrun.py``, avec ce même lecteur.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.i18n import use_language
from daemon.orchestrator import ModelUnavailable
from models import model_pull
from models.model_pull import (
    FAILED,
    NO_SPACE,
    PULLING,
    UNKNOWN,
    ModelNotInstalled,
    is_model_missing,
    model_not_installed,
    pull_state,
)

MODEL = "qwen3.5:4b"
COMMAND = "\nollama pull qwen3.5:4b"


@pytest.fixture
def firstrun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Dossier de myos-user-firstrun et /proc factices ; renvoie le dossier."""
    folder = tmp_path / "my_os"
    folder.mkdir()
    monkeypatch.setattr(model_pull, "FIRSTRUN_DIR", folder)
    monkeypatch.setattr(model_pull, "_PROC", tmp_path / "proc")
    return folder


def _status(folder: Path, line: str) -> None:
    (folder / "model-pull.status").write_text(line + "\n", encoding="utf-8")


def _process(tmp_path: Path, pid: int, cmdline: bytes) -> None:
    (tmp_path / "proc" / str(pid)).mkdir(parents=True)
    (tmp_path / "proc" / str(pid) / "cmdline").write_bytes(cmdline)


_FIRSTRUN_CMDLINE = b"/bin/sh\0/usr/local/bin/myos-user-firstrun\0"


# ---- erreur d'Ollama « modèle introuvable » ----------------------------------------


class _HttpError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def test_seul_un_404_signale_un_modele_absent() -> None:
    assert is_model_missing(_HttpError(404))
    assert not is_model_missing(_HttpError(500))
    assert not is_model_missing(ConnectionError("Ollama injoignable"))


def test_erreur_reelle_de_la_lib_ollama() -> None:
    ollama = pytest.importorskip("ollama")
    error = ollama.ResponseError(
        '{"error":"model \\"qwen3.5:4b\\" not found, try pulling it first"}', 404
    )
    assert is_model_missing(error)
    assert not is_model_missing(ollama.ResponseError("server busy", 503))


# ---- état du téléchargement ---------------------------------------------------------


def test_sans_fichier_d_etat_rien_n_est_connu(firstrun: Path) -> None:
    assert pull_state(MODEL) == UNKNOWN


def test_telechargement_en_cours(firstrun: Path, tmp_path: Path) -> None:
    _process(tmp_path, 4242, _FIRSTRUN_CMDLINE)
    _status(firstrun, f"pulling {MODEL} 4242")
    assert pull_state(MODEL) == PULLING


@pytest.mark.parametrize(
    "line",
    [
        f"pulling {MODEL} 4243",  # tâche de fond terminée sans écrire (tuée)
        f"pulling {MODEL} 4244",  # PID repris par un autre processus
        f"pulling {MODEL}",  # sans PID
        f"pulling {MODEL} ../4242",  # jamais un chemin
        f"failed {MODEL}",
    ],
)
def test_telechargement_abandonne_ou_interrompu(
    firstrun: Path, tmp_path: Path, line: str
) -> None:
    _process(tmp_path, 4242, _FIRSTRUN_CMDLINE)
    _process(tmp_path, 4244, b"/usr/bin/firefox\0")
    _status(firstrun, line)
    assert pull_state(MODEL) == FAILED


def test_pas_assez_de_place(firstrun: Path) -> None:
    _status(firstrun, f"no-space {MODEL}")
    assert pull_state(MODEL) == NO_SPACE


@pytest.mark.parametrize(
    "line",
    [
        f"done {MODEL}",  # téléchargé, puis retiré depuis
        f"pending {MODEL}",  # noté un instant, juste avant que la tâche se lance
        "failed qwen3.5:9b",  # un autre modèle que celui de la config
        "no-space",
        "",
        "n'importe quoi",
        f"rm {MODEL} -rf /",
    ],
)
def test_etat_sans_rapport_avec_le_modele(firstrun: Path, line: str) -> None:
    _status(firstrun, line)
    assert pull_state(MODEL) == UNKNOWN


def test_fichier_illisible(firstrun: Path) -> None:
    (firstrun / "model-pull.status").write_bytes(b"\xff\xfe failed")
    assert pull_state(MODEL) == UNKNOWN


# ---- message du popup ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # La tâche tourne : un essai en cours, ou l'attente avant le suivant.
        (f"pulling {MODEL} 4242", "il se télécharge en arrière-plan, ou attend un"),
        # Pas de cause supposée (tâche tuée, disque plein…), et le prochain essai.
        (f"failed {MODEL}", "essais en échec ou interrompus"),
        (f"failed {MODEL}", "Nouvel essai à la prochaine ouverture de session"),
        (f"no-space {MODEL}", "faute d'espace libre"),
        (None, "n'est pas installé"),
    ],
)
def test_message_selon_l_etat_avec_la_commande(
    firstrun: Path, tmp_path: Path, line: str | None, expected: str
) -> None:
    _process(tmp_path, 4242, _FIRSTRUN_CMDLINE)
    if line is not None:
        _status(firstrun, line)
    error = model_not_installed(MODEL)
    assert isinstance(error, ModelNotInstalled)
    assert isinstance(error, ModelUnavailable)
    message = str(error)
    assert expected in message
    # La commande, seule sur la dernière ligne : à copier telle quelle.
    assert message.endswith(COMMAND)


def test_message_en_anglais(firstrun: Path) -> None:
    _status(firstrun, f"failed {MODEL}")
    with use_language("en"):
        message = str(model_not_installed(MODEL))
    assert message.startswith("the local model qwen3.5:4b could not be downloaded")
    assert message.endswith(COMMAND)


def test_le_message_suit_le_modele_de_la_config(firstrun: Path) -> None:
    _status(firstrun, f"failed {MODEL}")
    message = str(model_not_installed("qwen3.5:9b"))
    assert "n'est pas installé" in message  # l'état noté concerne un autre modèle
    assert message.endswith("\nollama pull qwen3.5:9b")


def test_la_cause_va_au_journal_du_daemon(
    firstrun: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _status(firstrun, f"no-space {MODEL}")
    with caplog.at_level(logging.WARNING, logger="myosd.model"):
        model_not_installed(MODEL)
    assert "qwen3.5:4b" in caplog.text
    assert "no-space" in caplog.text
