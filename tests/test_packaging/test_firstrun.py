"""Script du 1er démarrage de l'ISO (``myos-user-firstrun``) : venv et dépendances,
puis téléchargement du modèle local en tâche de fond, avec de nouveaux essais espacés.

Le vrai script tourne sous ``sh``, avec un HOME temporaire et des commandes factices en
tête du PATH : ``python`` (crée le venv et son ``pip``), ``df`` (espace libre choisi),
``ollama`` (échoue N fois puis réussit), ``nm-online`` et ``sleep`` (note le délai sans
attendre). Le fichier d'état qu'il écrit est relu par :mod:`models.model_pull`, le
lecteur du daemon : c'est le contrat entre les deux. Linux seulement (``sh``,
``/proc``) : ignoré sous Windows, lancé dans le labo et dans la VM.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from models import model_pull

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "packaging"
    / "archiso"
    / "airootfs"
    / "usr"
    / "local"
    / "bin"
    / "myos-user-firstrun"
)
MODEL = "qwen3.5:4b"

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/stat").exists() or shutil.which("sh") is None,
    reason="script du 1er démarrage de l'ISO : Linux seulement (sh, /proc)",
)

# Commandes factices, pilotées par des fichiers de $FAKE_DIR.
_FAKES = {
    # python -m venv --system-site-packages DIR : un venv dont pip réussit, sauf
    # si $FAKE_DIR/pip-fails existe.
    "python": """#!/bin/sh
[ "$1 $2 $3" = "-m venv --system-site-packages" ] || exit 2
mkdir -p "$4/bin"
printf '#!/bin/sh\\nexit 0\\n' >"$4/bin/python"
printf '#!/bin/sh\\n[ -f "%s/pip-fails" ] && exit 1\\nexit 0\\n' "$FAKE_DIR" >"$4/bin/pip"
chmod +x "$4/bin/python" "$4/bin/pip"
""",
    "df": """#!/bin/sh
echo "Filesystem 1048576-blocks Used Available Capacity Mounted on"
echo "cowspace 16000 1000 $(cat "$FAKE_DIR/free") 7% /var/lib/ollama"
""",
    # ollama show : le modèle est-il là ? ollama pull : échoue $FAKE_DIR/fails fois.
    # Au 1er essai, garde l'état noté et le PID de l'appelant ; tant que
    # $FAKE_DIR/hold existe (10 s au plus), le téléchargement « dure ».
    "ollama": """#!/bin/sh
d="$FAKE_DIR"
case "$1" in
show)
    [ -f "$d/present" ]
    exit
    ;;
pull)
    n=$(($(cat "$d/pulls" 2>/dev/null || echo 0) + 1))
    echo "$n" >"$d/pulls"
    if [ "$n" -eq 1 ]; then
        cp "$HOME/.local/share/my_os/model-pull.status" "$d/status-during-pull"
        echo "$PPID" >"$d/pid-during-pull"
    fi
    i=0
    while [ -f "$d/hold" ] && [ "$i" -lt 200 ]; do
        PATH=/usr/bin:/bin sleep 0.05
        i=$((i + 1))
    done
    if [ "$n" -gt "$(cat "$d/fails")" ]; then
        touch "$d/present"
        echo "success"
        exit 0
    fi
    echo "Error: pull model manifest: net/http: TLS handshake timeout" >&2
    exit 1
    ;;
esac
exit 2
""",
    "nm-online": """#!/bin/sh
[ ! -f "$FAKE_DIR/offline" ]
""",
    # Note le délai au lieu d'attendre ; avec $FAKE_DIR/manual, l'utilisateur tire le
    # modèle à la main pendant l'attente.
    "sleep": """#!/bin/sh
echo "$1" >>"$FAKE_DIR/sleeps"
if [ -f "$FAKE_DIR/manual" ]; then touch "$FAKE_DIR/present"; fi
""",
}


class _Firstrun:
    """Le script dans un HOME temporaire, avec ses commandes factices."""

    def __init__(self, tmp_path: Path, *, fails: int = 0, free_mib: int = 7000) -> None:
        self.home = tmp_path / "home"
        self.fake = tmp_path / "fake"
        bin_dir = tmp_path / "bin"
        for folder in (self.home, self.fake, bin_dir):
            folder.mkdir()
        for name, body in _FAKES.items():
            path = bin_dir / name
            path.write_text(body, encoding="utf-8", newline="\n")
            path.chmod(0o755)
        (self.fake / "fails").write_text(str(fails), encoding="utf-8")
        (self.fake / "free").write_text(str(free_mib), encoding="utf-8")
        self.base = self.home / ".local" / "share" / "my_os"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_DIR": str(self.fake),
            "LANG": "C.UTF-8",
        }

    def flag(self, name: str) -> None:
        (self.fake / name).touch()

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(_SCRIPT)],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def status(self) -> str:
        try:
            return (self.base / "model-pull.status").read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def wait_end(self) -> str:
        """État final de la tâche de fond (noté après sa dernière ligne de journal)."""
        _wait(lambda: self.status().split()[:1] in (["done"], ["failed"]))
        return self.status()

    def log(self) -> str:
        return (self.base / "model-pull.log").read_text(encoding="utf-8")

    def words(self, name: str) -> list[str]:
        path = self.fake / name
        return path.read_text(encoding="utf-8").split() if path.exists() else []


def _wait(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("délai dépassé")
        time.sleep(0.05)


@pytest.fixture
def firstrun_dir(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], None]:
    """Fait lire au daemon l'état écrit par le script (dossier donné)."""
    return lambda folder: monkeypatch.setattr(model_pull, "FIRSTRUN_DIR", folder)


def test_premier_essai_reussi(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    result = firstrun.run()
    assert result.returncode == 0, result.stderr
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pulls") == ["1"]
    assert firstrun.words("sleeps") == []
    assert (firstrun.base / ".firstrun-done").exists()
    assert "essai 1 sur 5 : ollama pull qwen3.5:4b" in firstrun.log()


def test_nouveaux_essais_espaces_puis_reussite(
    tmp_path: Path, firstrun_dir: Callable[[Path], None]
) -> None:
    # Registre injoignable aux deux premiers essais (vu en VM : délai TLS dépassé).
    firstrun = _Firstrun(tmp_path, fails=2)
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pulls") == ["3"]
    assert firstrun.words("sleeps") == ["30", "60"]
    log = firstrun.log()
    assert "TLS handshake timeout" in log  # la cause de chaque échec reste lisible
    assert "échec ; nouvel essai dans 30 s" in log
    assert "essai 3 sur 5" in log
    assert "modèle qwen3.5:4b téléchargé" in log
    # Pendant le téléchargement, l'état désigne la tâche de fond elle-même.
    state, model, pid = firstrun.words("status-during-pull")
    assert (state, model) == ("pulling", MODEL)
    assert [pid] == firstrun.words("pid-during-pull")
    firstrun_dir(firstrun.base)
    assert model_pull.pull_state(MODEL) == model_pull.UNKNOWN  # fini : rien à dire


def test_cinq_essais_puis_abandon(
    tmp_path: Path, firstrun_dir: Callable[[Path], None]
) -> None:
    firstrun = _Firstrun(tmp_path, fails=99)
    assert firstrun.run().returncode == 0  # la session n'attend jamais le modèle
    assert firstrun.wait_end() == f"failed {MODEL}\n"
    assert firstrun.words("pulls") == ["5"]
    assert firstrun.words("sleeps") == ["30", "60", "120", "240"]
    assert "non téléchargé après 5 essais. Lancez : ollama pull qwen3.5:4b" in (
        firstrun.log()
    )
    firstrun_dir(firstrun.base)
    assert model_pull.pull_state(MODEL) == model_pull.FAILED


def test_pendant_le_telechargement_le_daemon_le_sait(
    tmp_path: Path, firstrun_dir: Callable[[Path], None]
) -> None:
    firstrun = _Firstrun(tmp_path)
    firstrun.flag("hold")  # le téléchargement « dure » jusqu'au retrait du fichier
    assert firstrun.run().returncode == 0  # le script rend la main tout de suite
    _wait(lambda: (firstrun.fake / "status-during-pull").exists())
    firstrun_dir(firstrun.base)
    assert model_pull.pull_state(MODEL) == model_pull.PULLING
    assert model_pull.pull_state("qwen3.5:9b") == model_pull.UNKNOWN
    (firstrun.fake / "hold").unlink()
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert model_pull.pull_state(MODEL) == model_pull.UNKNOWN


def test_modele_tire_a_la_main_pendant_l_attente(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path, fails=99)
    firstrun.flag("manual")
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pulls") == ["1"]  # plus aucun essai : il est là
    assert "modèle qwen3.5:4b déjà présent" in firstrun.log()


def test_reseau_pas_annonce_essai_quand_meme(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    firstrun.flag("offline")
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert "pas de connexion annoncée par NetworkManager" in firstrun.log()


def test_pas_assez_de_place_aucun_essai(
    tmp_path: Path, firstrun_dir: Callable[[Path], None]
) -> None:
    firstrun = _Firstrun(tmp_path, free_mib=3000)
    assert firstrun.run().returncode == 0
    assert firstrun.status() == f"no-space {MODEL}\n"
    assert "3000 Mio libres, 4096 requis" in firstrun.log()
    assert firstrun.words("pulls") == []
    firstrun_dir(firstrun.base)
    assert model_pull.pull_state(MODEL) == model_pull.NO_SPACE


def test_echec_de_pip_ni_marqueur_ni_telechargement(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    firstrun.flag("pip-fails")
    result = firstrun.run()
    assert result.returncode == 1
    assert "échec de pip" in result.stderr
    assert not (
        firstrun.base / ".firstrun-done"
    ).exists()  # retenté à la session suivante
    assert firstrun.status() == ""
    assert firstrun.words("pulls") == []


def test_deuxieme_session_ne_refait_rien(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    assert firstrun.run().returncode == 0
    firstrun.wait_end()
    assert firstrun.run().returncode == 0
    time.sleep(0.3)
    assert firstrun.words("pulls") == ["1"]
