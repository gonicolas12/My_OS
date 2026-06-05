"""Exécution de commandes système — point d'élévation **unique** du jalon 3.

Ce module est le *seul* endroit où My_OS lance un sous-processus système pour
piloter la machine (pacman, etc.). Il est volontairement minuscule et
auditable, dans le même esprit que le « choke point » du
:mod:`permissions.policy_engine` :

* jamais de ``shell=True`` (cf. CLAUDE.md / SECURITY checklist §7) : on exige
  une ``list[str]`` d'arguments déjà découpés et validés par l'appelant ;
* l'élévation de privilège est **ponctuelle, par action, via polkit** : quand
  ``elevate=True``, la commande est préfixée par ``pkexec``. C'est l'agent
  polkit de la session (Xfce/X11) qui demande le mot de passe et accorde root
  *uniquement* pour ce processus. Le daemon, lui, reste utilisateur — il
  n'hérite jamais de root (cf. docs/SECURITY.md menace 3) ;
* le ``runner`` est **injectable** : en production il pointe sur
  :func:`subprocess.run` ; en test on injecte un stub, ce qui permet de
  vérifier l'argv construit (et la présence de ``pkexec``) sans polkit ni
  pacman, y compris sous Windows.

La sortie d'une commande renvoyée ici est une **donnée** non fiable comme tout
contenu lu (cf. docs/SECURITY.md §2.2) : l'appelant la résume pour le LLM, il ne
l'exécute jamais.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

# Binaire d'élévation polkit. ``pkexec`` exécute la commande en root après
# authentification via l'agent polkit de la session — ponctuel, par action.
PKEXEC = "pkexec"

# Délai par défaut (s) avant d'abandonner une commande système.
DEFAULT_TIMEOUT_S = 120.0


@dataclass
class CommandResult:
    """Résultat d'une commande système.

    ``ok`` est vrai si le code de retour est 0. ``stdout``/``stderr`` sont du
    texte (décodé UTF-8, erreurs remplacées) — des **données** à résumer, pas
    des instructions.
    """

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """``True`` si la commande s'est terminée avec un code de retour nul."""
        return self.returncode == 0


# Signature d'un lanceur de processus (sous-ensemble de ``subprocess.run`` qu'on
# utilise). Injectable pour les tests : reçoit l'argv final et renvoie un objet
# exposant ``returncode``, ``stdout``, ``stderr``.
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _default_runner(
    argv: list[str], timeout: float
) -> subprocess.CompletedProcess[str]:
    """Lanceur de production : ``subprocess.run`` sans shell, sortie capturée."""
    return subprocess.run(
        argv,
        shell=False,  # jamais de shell : pas d'interprétation d'entrée (SECURITY §7)
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def build_argv(argv: list[str], *, elevate: bool) -> list[str]:
    """Construit l'argv final, préfixé par ``pkexec`` si ``elevate``.

    Validé strictement : ``argv`` doit être une liste **non vide** de chaînes
    **non vides**. Toute autre forme lève :class:`ValueError` — on refuse en
    particulier qu'une chaîne unique soit passée (qui serait, sous un shell,
    une porte d'injection).
    """
    if not isinstance(argv, list) or not argv:
        raise ValueError("argv doit être une liste non vide d'arguments")
    if not all(isinstance(part, str) and part for part in argv):
        raise ValueError("argv ne doit contenir que des chaînes non vides")
    return [PKEXEC, *argv] if elevate else list(argv)


def run_command(
    argv: list[str],
    *,
    elevate: bool = False,
    timeout: float = DEFAULT_TIMEOUT_S,
    runner: Runner | None = None,
) -> CommandResult:
    """Exécute ``argv`` (sans shell), avec élévation polkit ponctuelle si demandé.

    :param argv: commande déjà découpée et **validée par l'appelant** (les noms
        de paquets, PID, etc. doivent être vérifiés en amont — ce module ne
        connaît pas la sémantique de la commande).
    :param elevate: si vrai, préfixe ``pkexec`` → root ponctuel, par action,
        après authentification polkit. Le daemon reste utilisateur.
    :param timeout: délai max en secondes.
    :param runner: lanceur injectable (tests). Défaut : :func:`subprocess.run`.
    :returns: :class:`CommandResult` (code, stdout, stderr).

    En cas de timeout ou d'absence du binaire (``pkexec`` ou la commande
    introuvable), renvoie un :class:`CommandResult` d'échec lisible plutôt que
    de propager l'exception — l'orchestrator réinjecte ce message comme donnée.
    """
    final_argv = build_argv(argv, elevate=elevate)
    run = runner if runner is not None else _default_runner
    try:
        completed = run(final_argv, timeout=timeout)
    except FileNotFoundError as exc:
        return CommandResult(
            returncode=127, stdout="", stderr=f"commande introuvable : {exc}"
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            returncode=124,
            stdout="",
            stderr=f"délai dépassé ({timeout:.0f}s) : {' '.join(final_argv)}",
        )
    return CommandResult(
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
