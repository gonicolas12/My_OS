"""Tests des outils processus (:mod:`tools.processes`).

Provider **factice** injecté → déterministe, sans toucher aux vrais processus.
On couvre : tri par mémoire/CPU, limites, validation du PID, PID protégés,
permission refusée (root requis), disparition du PID.
"""

# Le faux provider respecte la signature du Protocol même quand un paramètre
# (pid) n'est pas inspecté par le stub.
# pylint: disable=missing-function-docstring,unused-argument
from __future__ import annotations

import os

import pytest

from tools.processes import (
    KillProcess,
    ListProcesses,
    ProcessInfo,
    PROCESS_TOOLS,
)


class _FakeProvider:
    """Provider scriptable : liste figée + comportement de kill paramétrable."""

    def __init__(self, processes: list[ProcessInfo] | None = None) -> None:
        self._processes = processes or []
        self.killed: list[int] = []
        self.exists_default = True
        self.kill_error: Exception | None = None

    def list_processes(self) -> list[ProcessInfo]:
        return list(self._processes)

    def pid_exists(self, pid: int) -> bool:
        return self.exists_default

    def kill(self, pid: int) -> None:
        if self.kill_error is not None:
            raise self.kill_error
        self.killed.append(pid)


def _procs() -> list[ProcessInfo]:
    return [
        ProcessInfo(
            pid=10,
            name="firefox",
            username="alice",
            memory_percent=30.0,
            cpu_percent=5.0,
        ),
        ProcessInfo(
            pid=20,
            name="python",
            username="alice",
            memory_percent=10.0,
            cpu_percent=80.0,
        ),
        ProcessInfo(
            pid=30, name="bash", username="alice", memory_percent=1.0, cpu_percent=0.0
        ),
    ]


def test_list_processes_est_niveau_0() -> None:
    assert ListProcesses().risk_level == 0


def test_list_processes_trie_par_memoire_par_defaut() -> None:
    result = ListProcesses(_FakeProvider(_procs())).run({})
    assert result.success is True
    lignes = result.output.splitlines()
    # 1re ligne = en-tête ; ensuite firefox (30%) avant python (10%) avant bash.
    assert "mémoire" in lignes[0] or "memory" in lignes[0]
    assert lignes[1].split()[0] == "10"  # firefox, plus gros conso mémoire
    assert lignes[2].split()[0] == "20"
    assert lignes[3].split()[0] == "30"


def test_list_processes_trie_par_cpu() -> None:
    result = ListProcesses(_FakeProvider(_procs())).run({"sort_by": "cpu"})
    lignes = result.output.splitlines()
    assert lignes[1].split()[0] == "20"  # python, 80% CPU en tête


def test_list_processes_sort_by_invalide_retombe_sur_memoire() -> None:
    result = ListProcesses(_FakeProvider(_procs())).run({"sort_by": "magie"})
    assert result.output.splitlines()[1].split()[0] == "10"


def test_list_processes_respecte_la_limite() -> None:
    result = ListProcesses(_FakeProvider(_procs())).run({"limit": 1})
    # en-tête + 1 ligne
    assert len(result.output.splitlines()) == 2


def test_list_processes_limite_invalide_utilise_le_defaut() -> None:
    result = ListProcesses(_FakeProvider(_procs())).run({"limit": "abc"})
    assert result.success is True  # ne plante pas, prend le défaut


def test_kill_process_est_niveau_2() -> None:
    assert KillProcess().risk_level == 2


def test_kill_process_termine_le_pid() -> None:
    provider = _FakeProvider()
    result = KillProcess(provider).run({"pid": 4242})
    assert result.success is True
    assert provider.killed == [4242]


def test_kill_process_accepte_un_pid_en_chaine() -> None:
    provider = _FakeProvider()
    result = KillProcess(provider).run({"pid": "4242"})
    assert result.success is True
    assert provider.killed == [4242]


@pytest.mark.parametrize("bad", [None, "abc", "", 1.5, True])
def test_kill_process_pid_invalide_echoue(bad: object) -> None:
    result = KillProcess(_FakeProvider()).run({"pid": bad})
    assert result.success is False
    assert "pid" in result.output.lower()


@pytest.mark.parametrize("protege", [0, 1, -1])
def test_kill_process_refuse_les_pid_proteges(protege: int) -> None:
    result = KillProcess(_FakeProvider()).run({"pid": protege})
    assert result.success is False
    assert "protégé" in result.output


def test_kill_process_refuse_de_se_tuer_lui_meme() -> None:
    result = KillProcess(_FakeProvider()).run({"pid": os.getpid()})
    assert result.success is False
    assert "protégé" in result.output


def test_kill_process_pid_inexistant_echoue() -> None:
    provider = _FakeProvider()
    provider.exists_default = False
    result = KillProcess(provider).run({"pid": 9999})
    assert result.success is False
    assert "aucun processus" in result.output


def test_kill_process_permission_refusee_suggere_root() -> None:
    provider = _FakeProvider()
    provider.kill_error = PermissionError("denied")
    result = KillProcess(provider).run({"pid": 4242})
    assert result.success is False
    assert "permission refusée" in result.output
    assert "administrateur" in result.output


def test_kill_process_pid_disparu_pendant_l_envoi() -> None:
    provider = _FakeProvider()
    provider.kill_error = ProcessLookupError("gone")
    result = KillProcess(provider).run({"pid": 4242})
    assert result.success is False
    assert "disparu" in result.output


def test_kill_process_n_eleve_jamais_par_lui_meme() -> None:
    # Contrat jalon 3 : kill_process ne déclare pas requires_elevation.
    assert KillProcess().requires_elevation({"pid": 4242}) is False


def test_registre_process_tools_complet() -> None:
    assert set(PROCESS_TOOLS) == {"list_processes", "kill_process"}
