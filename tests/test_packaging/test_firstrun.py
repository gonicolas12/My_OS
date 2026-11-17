"""Script du 1er démarrage de l'ISO (``myos-user-firstrun``) : venv tout de suite, puis
en tâche de fond les dépendances (nouveaux essais tant que le réseau manque, avis du
bureau), myosd et le modèle local (nouveaux essais espacés) ; aux sessions suivantes,
nouveau téléchargement d'un modèle qui n'a pas abouti.

Le vrai script tourne sous ``sh``, avec un HOME temporaire et des commandes factices en
tête du PATH : ``python`` (crée le venv et son ``pip`` ; affiche les avis du bureau),
``pip`` (échoue N fois puis réussit), ``df`` (espace libre choisi), ``ollama`` (échoue
N fois puis réussit), ``nm-online`` (répond sans attendre), ``systemctl``, ``mv``
(peut rater une écriture) et ``sleep`` (note le délai sans attendre). Chaque commande
factice note son passage dans ``$FAKE_DIR/events`` : l'ordre des étapes se vérifie. Le
fichier d'état qu'il écrit est relu par :mod:`models.model_pull`, le lecteur du
daemon : c'est le contrat entre les deux. Linux seulement (``sh``, ``/proc``) : ignoré
sous Windows, lancé dans le labo et dans la VM.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import os
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
MYOSD_STARTED = "systemctl --user start myosd.service (marqueur)"

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/stat").exists() or shutil.which("sh") is None,
    reason="script du 1er démarrage de l'ISO : Linux seulement (sh, /proc)",
)

# Commandes factices, pilotées par des fichiers de $FAKE_DIR.
_FAKES = {
    # python -P -m venv --clear --system-site-packages DIR : un venv dont le pip est la
    # commande factice _pip. python -P -m ui.setup_notice ÉVÉNEMENT [ID] : l'avis du
    # bureau, noté ; écrit l'identifiant 7, sauf si l'envoi échoue
    # ($FAKE_DIR/notify-fails). Sans -P (dossier courant en tête des imports) : refusé.
    "python": """#!/bin/sh
d="$FAKE_DIR"
case "$*" in
"-P -m venv --clear --system-site-packages "*)
    echo "venv" >>"$d/events"
    rm -rf "$6"
    mkdir -p "$6/bin"
    printf '#!/bin/sh\\nexit 0\\n' >"$6/bin/python"
    cp "$(dirname "$0")/_pip" "$6/bin/pip"
    chmod +x "$6/bin/python" "$6/bin/pip"
    ;;
"-P -m ui.setup_notice "*)
    shift 3
    echo "notice $*" >>"$d/events"
    echo "$*" >>"$d/notices"
    if [ -f "$d/notify-fails" ]; then
        echo "pas de bus de session" >&2
        exit 1
    fi
    echo 7
    ;;
*)
    echo "python factice : arguments inattendus : $*" >&2
    exit 2
    ;;
esac
""",
    # pip install --quiet --upgrade pip : réussit. pip install --quiet pynput ollama :
    # échoue $FAKE_DIR/pip-fails fois, puis réussit ; tant que $FAKE_DIR/pip-hold
    # existe (10 s au plus), l'installation « dure ».
    "_pip": """#!/bin/sh
d="$FAKE_DIR"
[ "$*" = "install --quiet --upgrade pip" ] && exit 0
[ "$*" = "install --quiet pynput ollama" ] || exit 2
n=$(($(cat "$d/pip-installs" 2>/dev/null || echo 0) + 1))
echo "$n" >"$d/pip-installs"
echo "pip $n" >>"$d/events"
i=0
while [ -f "$d/pip-hold" ] && [ "$i" -lt 200 ]; do
    PATH=/usr/bin:/bin sleep 0.05
    i=$((i + 1))
done
[ "$n" -gt "$(cat "$d/pip-fails" 2>/dev/null || echo 0)" ] && exit 0
echo "ERROR: Could not find a version that satisfies the requirement pynput" >&2
exit 1
""",
    # Espace libre choisi ; avec $FAKE_DIR/df-fails, df échoue (dossier absent).
    "df": """#!/bin/sh
if [ -f "$FAKE_DIR/df-fails" ]; then
    echo "df: /var/lib/ollama: No such file or directory" >&2
    exit 1
fi
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
    echo "ollama pull $n" >>"$d/events"
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
    # Répond sans attendre : hors ligne si $FAKE_DIR/offline existe, ou pour les
    # $FAKE_DIR/offline-for prochains appels (la connexion arrive ensuite).
    "nm-online": """#!/bin/sh
d="$FAKE_DIR"
echo "nm-online $*" >>"$d/events"
[ -f "$d/offline" ] && exit 1
n=$(cat "$d/offline-for" 2>/dev/null || echo 0)
if [ "$n" -gt 0 ]; then
    echo $((n - 1)) >"$d/offline-for"
    exit 1
fi
exit 0
""",
    # Note la commande, si le marqueur des dépendances était posé et l'état du modèle à
    # ce moment ; avec $FAKE_DIR/systemctl-fails, le démarrage échoue.
    "systemctl": """#!/bin/sh
d="$FAKE_DIR"
marker=sans-marqueur
[ -f "$HOME/.local/share/my_os/.firstrun-done" ] && marker=marqueur
echo "systemctl $* ($marker)" >>"$d/events"
cp "$HOME/.local/share/my_os/model-pull.status" "$d/status-at-myosd-start" 2>/dev/null
if [ -f "$d/systemctl-fails" ]; then
    echo "Failed to start myosd.service: Unit myosd.service not found." >&2
    exit 1
fi
exit 0
""",
    # Le vrai mv, sauf au N-ième appel ($FAKE_DIR/mv-fail-at) : écriture ratée (cowspace
    # plein en live).
    "mv": """#!/bin/sh
d="$FAKE_DIR"
n=$(($(cat "$d/mv-calls" 2>/dev/null || echo 0) + 1))
echo "$n" >"$d/mv-calls"
if [ "$n" = "$(cat "$d/mv-fail-at" 2>/dev/null)" ]; then
    echo "mv: écriture impossible : plus de place" >&2
    exit 1
fi
PATH=/usr/bin:/bin exec mv "$@"
""",
    # Note le délai au lieu d'attendre ; avec $FAKE_DIR/manual, l'utilisateur tire le
    # modèle à la main pendant l'attente.
    "sleep": """#!/bin/sh
echo "$1" >>"$FAKE_DIR/sleeps"
echo "sleep $1" >>"$FAKE_DIR/events"
if [ -f "$FAKE_DIR/manual" ]; then touch "$FAKE_DIR/present"; fi
""",
}


class _Firstrun:
    """Le script dans un HOME temporaire, avec ses commandes factices."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        fails: int = 0,
        free_mib: int = 7000,
        pip_fails: int = 0,
    ) -> None:
        self.home = tmp_path / "home"
        self.fake = tmp_path / "fake"
        self.bin = tmp_path / "bin"
        for folder in (self.home, self.fake, self.bin):
            folder.mkdir()
        for name, body in _FAKES.items():
            path = self.bin / name
            path.write_text(body, encoding="utf-8", newline="\n")
            path.chmod(0o755)
        self.set("fails", fails)
        self.set("free", free_mib)
        self.set("pip-fails", pip_fails)
        self.base = self.home / ".local" / "share" / "my_os"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "FAKE_DIR": str(self.fake),
            "LANG": "C.UTF-8",
        }

    def set(self, name: str, value: int) -> None:
        (self.fake / name).write_text(str(value), encoding="utf-8")

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
        """État final du modèle (noté après la dernière ligne de son journal)."""
        _wait(lambda: self.status().split()[:1] in (["done"], ["failed"], ["no-space"]))
        return self.status()

    def wait_notice(self, line: str) -> None:
        """Attend l'avis ``line`` : « prêt » part après le lancement du modèle, qui
        peut finir avant lui."""
        _wait(lambda: line in self.lines("notices"))

    def log(self) -> str:
        """Journal du téléchargement du modèle."""
        return (self.base / "model-pull.log").read_text(encoding="utf-8")

    def setup_log(self) -> str:
        """Journal du 1er démarrage (dépendances, myosd, nouveaux essais du modèle)."""
        try:
            return (self.base / "firstrun.log").read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def words(self, name: str) -> list[str]:
        path = self.fake / name
        return path.read_text(encoding="utf-8").split() if path.exists() else []

    def lines(self, name: str) -> list[str]:
        path = self.fake / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def events(self) -> list[str]:
        return self.lines("events")

    def later_session(self, status: str) -> None:
        """Une session suivante : dépendances installées, ``status`` du modèle."""
        self.base.mkdir(parents=True)
        (self.base / ".firstrun-done").touch()
        (self.base / "model-pull.status").write_text(status + "\n", encoding="utf-8")


def _wait(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("délai dépassé")
        time.sleep(0.05)


def _in_order(events: list[str], *expected: str) -> bool:
    """``expected`` apparaît dans ``events`` dans cet ordre (d'autres entre deux)."""
    remaining = iter(events)
    return all(item in remaining for item in expected)


@pytest.fixture
def firstrun_dir(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], None]:
    """Fait lire au daemon l'état écrit par le script (dossier donné)."""
    return lambda folder: monkeypatch.setattr(model_pull, "FIRSTRUN_DIR", folder)


# ---- 1re session : dépendances, myosd, modèle --------------------------------------


def test_premier_essai_reussi(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    result = firstrun.run()
    assert result.returncode == 0, result.stderr
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pip-installs") == ["1"]
    assert firstrun.words("pulls") == ["1"]
    assert firstrun.words("sleeps") == []
    assert (firstrun.base / ".firstrun-done").exists()
    # myosd démarre une fois les dépendances là, puis le modèle se télécharge.
    assert _in_order(firstrun.events(), "venv", "pip 1", MYOSD_STARTED, "ollama pull 1")
    # Le modèle est noté « à télécharger » avant le marqueur : une session fermée juste
    # après les dépendances le retentera à la suivante.
    assert firstrun.lines("status-at-myosd-start") == [f"pending {MODEL}"]
    assert firstrun.lines("notices") == []  # tout va bien : aucun avis
    setup_log = firstrun.setup_log()
    assert "essai 1 : pip install pynput ollama" in setup_log
    assert "dépendances installées ; démarrage de myosd" in setup_log
    assert "essai 1 sur 5 : ollama pull qwen3.5:4b" in firstrun.log()


def test_la_session_n_attend_pas_les_dependances(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    firstrun.flag("pip-hold")  # pip « dure » jusqu'au retrait du fichier
    started = time.monotonic()
    result = firstrun.run()
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - started < 5  # le script rend la main tout de suite
    # Le venv est là sans attendre : le popup, lancé par l'autostart, s'en sert.
    assert (firstrun.base / "venv" / "bin" / "python").exists()
    _wait(lambda: firstrun.words("pip-installs") == ["1"])
    assert not (firstrun.base / ".firstrun-done").exists()
    assert MYOSD_STARTED not in firstrun.events()  # pas de daemon sans ses modules
    assert firstrun.status() == ""
    (firstrun.fake / "pip-hold").unlink()
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert MYOSD_STARTED in firstrun.events()


def test_pip_en_echec_nouveaux_essais_espaces(tmp_path: Path) -> None:
    # Réseau annoncé, mais PyPI injoignable aux deux premiers essais.
    firstrun = _Firstrun(tmp_path, pip_fails=2)
    result = firstrun.run()
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert firstrun.wait_end() == f"done {MODEL}\n"
    firstrun.wait_notice("ready 7")
    assert firstrun.words("pip-installs") == ["3"]
    assert firstrun.words("sleeps") == ["30", "60"]
    # Dès le 1er échec, un avis le dit ; « prêt » le remplace une fois myosd démarré.
    assert _in_order(
        firstrun.events(),
        "pip 1",
        "notice waiting",
        "sleep 30",
        "pip 2",
        "sleep 60",
        "pip 3",
        MYOSD_STARTED,
        "notice ready 7",
    )
    assert firstrun.lines("notices") == ["waiting", "ready 7"]
    setup_log = firstrun.setup_log()
    assert "Could not find a version" in setup_log  # la cause de chaque échec
    assert "échec ; nouvel essai dans 30 s" in setup_log
    assert "essai 3 : pip install pynput ollama" in setup_log
    assert "avis « My_OS n'est pas encore prêt » : 7" in setup_log
    assert not (firstrun.base / "setup-notice").exists()  # avis remplacé


def test_sans_reseau_avis_puis_reprise_des_la_connexion(tmp_path: Path) -> None:
    # NetworkManager n'annonce rien aux deux premières attentes (pas de Wi-Fi choisi),
    # puis la connexion arrive. Le vrai nm-online attend son délai (30 s, puis 5 min)
    # et rend la main dès qu'une connexion arrive ; le faux répond aussitôt, et le
    # script attend alors le reste du délai : jamais deux essais de pip à la suite.
    firstrun = _Firstrun(tmp_path, pip_fails=2)
    firstrun.set("offline-for", 2)
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    firstrun.wait_notice("ready 7")
    # L'avis précède le 1er essai ; hors ligne, pas de délai de plus après un échec.
    events = firstrun.events()
    assert _in_order(
        events,
        "nm-online -q -t 30",
        "notice waiting",
        "pip 1",
        "nm-online -q -t 300",
        "pip 2",
        "nm-online -q -t 300",
        "pip 3",
        MYOSD_STARTED,
        "notice ready 7",
    )
    # Le reste du délai : 30 s puis 300 s, moins le temps (une seconde au plus, sous
    # charge) passé dans le faux nm-online ; l'avis vient après cette attente.
    first_sleep = next(i for i, event in enumerate(events) if event.startswith("sleep"))
    assert first_sleep < events.index("notice waiting")
    slept = [int(value) for value in firstrun.words("sleeps")]
    assert len(slept) == 2
    assert 28 <= slept[0] <= 30
    assert 298 <= slept[1] <= 300
    assert firstrun.lines("notices") == ["waiting", "ready 7"]
    assert "nouvel essai dès que NetworkManager annonce une connexion" in (
        firstrun.setup_log()
    )


def test_essais_toutes_les_5_min_tant_que_la_session_dure(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path, pip_fails=6)
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    firstrun.wait_notice("ready 7")
    assert firstrun.words("sleeps") == ["30", "60", "120", "240", "300", "300"]
    assert firstrun.words("pip-installs") == ["7"]
    assert firstrun.lines("notices") == ["waiting", "ready 7"]  # un seul avis


def test_avis_impossible_l_installation_continue(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path, pip_fails=1)
    firstrun.flag("notify-fails")  # pas de serveur de notifications
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    firstrun.wait_notice("ready")  # sans identifiant à remplacer
    assert MYOSD_STARTED in firstrun.events()
    assert firstrun.lines("notices") == ["waiting", "ready"]
    setup_log = firstrun.setup_log()
    assert "avis « My_OS n'est pas encore prêt » : non affiché" in setup_log
    assert "pas de bus de session" in setup_log


def test_myosd_ne_demarre_pas_pas_d_avis_pret(tmp_path: Path) -> None:
    # « Prêt » promettrait un raccourci qui n'ouvre rien : l'avis d'attente reste.
    firstrun = _Firstrun(tmp_path, pip_fails=1)
    firstrun.flag("systemctl-fails")
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"  # le modèle, lui, arrive
    _wait(lambda: "myosd n'a pas démarré" in firstrun.setup_log())
    time.sleep(0.3)
    assert firstrun.lines("notices") == ["waiting"]
    assert (firstrun.base / "setup-notice").exists()


@pytest.mark.skipif(shutil.which("flock") is None, reason="flock (util-linux) absent")
def test_une_seule_installation_a_la_fois(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    firstrun.flag("pip-hold")
    assert firstrun.run().returncode == 0
    _wait(lambda: firstrun.words("pip-installs") == ["1"])
    # Une autre session s'ouvre pendant que la 1re tâche installe encore (elle a
    # survécu à sa session ; sur l'ISO, LightDM l'arrête, vu en VM) : la nouvelle ne
    # réinstalle rien, mais remontre l'avis, disparu avec l'ancien bureau ; la 1re
    # tâche le remplacera par « prêt ».
    assert firstrun.run().returncode == 0
    _wait(lambda: "installation déjà en cours" in firstrun.setup_log())
    firstrun.wait_notice("waiting")
    _wait(lambda: (firstrun.base / "setup-notice").exists())  # son identifiant
    (firstrun.fake / "pip-hold").unlink()
    assert firstrun.wait_end() == f"done {MODEL}\n"
    firstrun.wait_notice("ready 7")
    assert firstrun.words("pip-installs") == ["1"]
    assert firstrun.words("pulls") == ["1"]
    assert firstrun.events().count(MYOSD_STARTED) == 1


def test_avis_perime_d_une_tache_interrompue_ignore(tmp_path: Path) -> None:
    # Avis noté par une tâche tuée lors d'une session précédente : pas de « prêt »
    # surprise quand tout va bien cette fois.
    firstrun = _Firstrun(tmp_path)
    firstrun.base.mkdir(parents=True)
    (firstrun.base / "setup-notice").write_text("42\n", encoding="utf-8")
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    time.sleep(0.3)
    assert firstrun.lines("notices") == []
    assert not (firstrun.base / "setup-notice").exists()


def test_venv_sans_pip_recree(tmp_path: Path) -> None:
    # Création du venv interrompue : python est là, pas pip. Relancé seul, venv ne
    # recréerait pas bin/pip (vérifié) : il est refait de zéro.
    firstrun = _Firstrun(tmp_path)
    python = firstrun.base / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.events().count("venv") == 1
    assert firstrun.words("pip-installs") == ["1"]


def test_venv_complet_garde(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    venv_bin = firstrun.base / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shutil.copy(firstrun.bin / "_pip", venv_bin / "pip")
    for name in ("python", "pip"):
        (venv_bin / name).chmod(0o755)
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert "venv" not in firstrun.events()


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


def test_ecriture_ratee_le_telechargement_continue(tmp_path: Path) -> None:
    # Cowspace plein en live : l'état « pulling » ne s'écrit pas. La tâche de fond ne
    # s'arrête pas pour autant (set +e) et note son état final.
    firstrun = _Firstrun(tmp_path)
    firstrun.set("mv-fail-at", 2)  # 1 : « pending » avant le marqueur ; 2 : « pulling »
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pulls") == ["1"]
    assert firstrun.lines("status-during-pull") == [f"pending {MODEL}"]


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
    # NetworkManager n'annonce jamais rien (réseau géré autrement ?) : tout est essayé.
    firstrun = _Firstrun(tmp_path)
    firstrun.flag("offline")
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    firstrun.wait_notice("ready 7")
    assert "pas de connexion annoncée par NetworkManager" in firstrun.setup_log()
    assert "pas de connexion annoncée par NetworkManager" in firstrun.log()
    assert firstrun.lines("notices") == ["waiting", "ready 7"]


def test_pas_assez_de_place_aucun_essai(
    tmp_path: Path, firstrun_dir: Callable[[Path], None]
) -> None:
    firstrun = _Firstrun(tmp_path, free_mib=3000)
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"no-space {MODEL}\n"
    assert "3000 Mio libres, 4096 requis" in firstrun.log()
    assert firstrun.words("pulls") == []
    assert MYOSD_STARTED in firstrun.events()  # le cloud reste possible
    firstrun_dir(firstrun.base)
    assert model_pull.pull_state(MODEL) == model_pull.NO_SPACE


def test_place_inconnue_essai_quand_meme(tmp_path: Path) -> None:
    # df en échec (dossier d'Ollama absent…) : pas de « no-space » sans preuve.
    firstrun = _Firstrun(tmp_path, free_mib=3000)
    firstrun.flag("df-fails")
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pulls") == ["1"]
    assert "place libre inconnue pour /var/lib/ollama" in firstrun.setup_log()


def test_modele_deja_present_sa_place_ne_compte_pas(tmp_path: Path) -> None:
    # Vu en VM : le modèle déjà dans Ollama prend la place que la garde exige ; sans
    # vérifier d'abord sa présence, l'état passait à « no-space » à tort.
    firstrun = _Firstrun(tmp_path, free_mib=3000)
    firstrun.flag("present")
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pulls") == []
    assert "modèle qwen3.5:4b déjà présent" in firstrun.log()


# ---- sessions suivantes --------------------------------------------------------------


def test_deuxieme_session_ne_refait_rien(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    assert firstrun.run().returncode == 0
    firstrun.wait_end()
    assert firstrun.run().returncode == 0
    time.sleep(0.3)
    assert firstrun.words("pulls") == ["1"]
    assert firstrun.words("pip-installs") == ["1"]
    assert firstrun.events().count("venv") == 1
    assert firstrun.events().count(MYOSD_STARTED) == 1  # ~/.xprofile s'en charge


def test_session_suivante_retente_un_modele_en_echec(
    tmp_path: Path, firstrun_dir: Callable[[Path], None]
) -> None:
    firstrun = _Firstrun(tmp_path, fails=99)
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"failed {MODEL}\n"
    firstrun.set("fails", 5)  # le réseau est revenu : l'essai suivant passe
    result = firstrun.run()
    assert result.returncode == 0, result.stderr
    # « failed » est encore là tant que la tâche de fond n'a pas démarré.
    _wait(lambda: firstrun.words("pulls") == ["6"])
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pip-installs") == ["1"]  # les dépendances sont là
    assert "non téléchargé à une session précédente (failed) : nouvel essai" in (
        firstrun.setup_log()
    )
    assert "essai 1 sur 5" in firstrun.log()  # 5 nouveaux essais au besoin
    firstrun_dir(firstrun.base)
    assert model_pull.pull_state(MODEL) == model_pull.UNKNOWN


def test_session_suivante_modele_tire_a_la_main_entre_temps(tmp_path: Path) -> None:
    # Après l'échec, l'utilisateur a suivi le conseil du popup (ollama pull) ; en live,
    # la place libre est prise par le modèle lui-même.
    firstrun = _Firstrun(tmp_path, free_mib=3000)
    firstrun.flag("present")
    firstrun.later_session(f"failed {MODEL}")
    assert firstrun.run().returncode == 0
    _wait(lambda: firstrun.status() == f"done {MODEL}\n")
    assert firstrun.words("pulls") == []
    assert "(failed) : nouvel essai" in firstrun.setup_log()
    assert "modèle qwen3.5:4b déjà présent" in firstrun.log()


def test_session_suivante_retente_faute_de_place(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path, free_mib=3000)
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"no-space {MODEL}\n"
    firstrun.set("free", 7000)  # de la place a été libérée
    assert firstrun.run().returncode == 0
    _wait(lambda: firstrun.words("pulls") == ["1"])  # « no-space » encore noté avant
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert "(no-space) : nouvel essai" in firstrun.setup_log()


@pytest.mark.parametrize(
    ("status", "cause"),
    [
        (f"pulling {MODEL} 999999999", "pulling"),  # PID disparu (redémarrage)
        (f"pulling {MODEL} self", "pulling"),  # PID vivant, mais pas ce script
        (f"pulling {MODEL} abc", "pulling"),  # PID illisible
        (f"pending {MODEL}", "pending"),  # session fermée juste après les dépendances
    ],
)
def test_session_suivante_reprend_un_telechargement_interrompu(
    tmp_path: Path, status: str, cause: str
) -> None:
    # La session (ou la machine) s'est arrêtée avant la fin du téléchargement, voire
    # avant son lancement.
    status = status.replace("self", str(os.getpid()))  # pytest : pas myos-user-firstrun
    firstrun = _Firstrun(tmp_path)
    firstrun.later_session(status)
    assert firstrun.run().returncode == 0
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pulls") == ["1"]
    assert f"({cause}) : nouvel essai" in firstrun.setup_log()
    assert firstrun.words("pip-installs") == []


def test_session_suivante_laisse_un_telechargement_en_cours(tmp_path: Path) -> None:
    firstrun = _Firstrun(tmp_path)
    firstrun.flag("hold")
    assert firstrun.run().returncode == 0
    _wait(lambda: (firstrun.fake / "status-during-pull").exists())
    # Nouvelle session pendant que la tâche de fond télécharge encore.
    assert firstrun.run().returncode == 0
    time.sleep(0.3)
    (firstrun.fake / "hold").unlink()
    assert firstrun.wait_end() == f"done {MODEL}\n"
    assert firstrun.words("pulls") == ["1"]
    assert "nouvel essai" not in firstrun.setup_log()


@pytest.mark.parametrize(
    "status",
    [f"done {MODEL}", "failed qwen3.5:9b", "inconnu qwen3.5:4b", "", "*  ?"],
)
def test_session_suivante_rien_a_retenter(tmp_path: Path, status: str) -> None:
    # Modèle téléchargé (retiré ensuite : choix de l'utilisateur), autre modèle, état
    # inconnu ou fichier vide : aucun téléchargement.
    firstrun = _Firstrun(tmp_path)
    firstrun.later_session(status)
    assert firstrun.run().returncode == 0
    time.sleep(0.3)
    assert firstrun.words("pulls") == []
    assert firstrun.status() == status + "\n"
    assert firstrun.setup_log() == ""
