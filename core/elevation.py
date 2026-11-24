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
  pacman, y compris sous Windows ;
* un **refus de polkit** (fenêtre d'authentification fermée, autorisation
  refusée) est rendu dans la langue de My_OS : ``pkexec`` l'écrit en anglais, en
  dur. Seul le texte change : le code de retour reste celui de ``pkexec``, et la
  commande n'a pas été lancée ;
* une commande peut être **annulée** (``cancel``) : son processus est tué dès que
  l'événement est posé. Sert au contrôle de l'ordinateur, dont l'arrêt (Échap,
  bouton ■) coupe une frappe déjà lancée.

La sortie d'une commande renvoyée ici est une **donnée** non fiable comme tout
contenu lu (cf. docs/SECURITY.md §2.2) : l'appelant la résume pour le LLM, il ne
l'exécute jamais.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from core.i18n import N_, tr

# Binaire d'élévation polkit. ``pkexec`` exécute la commande en root après
# authentification via l'agent polkit de la session — ponctuel, par action.
PKEXEC = "pkexec"

# Délai par défaut (s) avant d'abandonner une commande système.
DEFAULT_TIMEOUT_S = 120.0

# Intervalle (s) auquel une commande annulable regarde si on l'a annulée : c'est le
# retard maximal entre l'annulation et l'arrêt du processus.
CANCEL_POLL_S = 0.01
# Code de retour d'une commande annulée (tuée, ou jamais lancée).
CANCELLED_RETURNCODE = -1
_CANCELLED = N_("commande annulée : interrompue avant la fin")

# Refus de polkit : (code de retour de pkexec, ligne qu'il écrit sur stderr, message
# de My_OS). pkexec écrit ces lignes en anglais, sans gettext (polkit 128,
# src/programs/pkexec.c) : sans ce remplacement, le popup affichait « Request
# dismissed » en français comme en anglais. Une ligne n'est reconnue qu'avec son code,
# car 127 sert aussi à « commande introuvable » (pkexec et run_command ci-dessous).
_POLKIT_REFUSALS: tuple[tuple[int, str, str], ...] = (
    (
        126,  # fenêtre d'authentification fermée (Annuler, Échap)
        "Error executing command as another user: Request dismissed",
        N_(
            "demande d'autorisation fermée sans réponse : la commande n'a pas été "
            "lancée"
        ),
    ),
    (
        127,  # polkit refuse (pkexec ajoute « This incident has been reported. »)
        "Error executing command as another user: Not authorized",
        N_("autorisation refusée : la commande n'a pas été lancée"),
    ),
)


@dataclass
class CommandResult:
    """Résultat d'une commande système.

    ``ok`` est vrai si le code de retour est 0. ``stdout``/``stderr`` sont du
    texte (décodé UTF-8, erreurs remplacées) — des **données** à résumer, pas
    des instructions. ``cancelled`` : la commande a été annulée (``cancel`` de
    :func:`run_command`) avant de finir, ou n'a pas été lancée.
    """

    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False

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


def _detached_runner(
    argv: list[str], timeout: float
) -> subprocess.CompletedProcess[str]:
    """Lanceur sans tube, pour une commande qui laisse un processus derrière elle
    (application graphique lancée) : hérités par ce processus, des tubes de capture
    resteraient ouverts et ``subprocess.run`` attendrait leur fermeture jusqu'au
    délai (vu en VM avec ``gio launch``). Entrées et sorties : ``/dev/null``."""
    completed = subprocess.run(
        argv,
        shell=False,  # jamais de shell : pas d'interprétation d'entrée (SECURITY §7)
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
    return subprocess.CompletedProcess(argv, completed.returncode, "", "")


def _cancellable_runner(
    argv: list[str], timeout: float, *, cancel: threading.Event
) -> subprocess.CompletedProcess[str]:
    """Lanceur annulable : comme :func:`_default_runner`, mais le processus est tué
    dès que ``cancel`` est posé (au plus :data:`CANCEL_POLL_S` après). Une commande
    tuée renvoie un code non nul : :func:`run_command` la reconnaît à ``cancel``."""
    deadline = time.monotonic() + timeout
    with subprocess.Popen(
        argv,
        shell=False,  # jamais de shell : pas d'interprétation d'entrée (SECURITY §7)
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=CANCEL_POLL_S)
            except subprocess.TimeoutExpired:
                if cancel.is_set() or time.monotonic() >= deadline:
                    process.kill()
                    stdout, stderr = process.communicate()
                    if not cancel.is_set():
                        raise subprocess.TimeoutExpired(argv, timeout) from None
                    return subprocess.CompletedProcess(
                        argv, process.returncode or CANCELLED_RETURNCODE, stdout, stderr
                    )
                continue
            return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _cancelled_result(stdout: str = "") -> CommandResult:
    return CommandResult(
        returncode=CANCELLED_RETURNCODE,
        stdout=stdout,
        stderr=tr(_CANCELLED),
        cancelled=True,
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


def _polkit_refusal(returncode: int, stderr: str) -> str | None:
    """Message, dans la langue de My_OS, d'un refus de polkit écrit par ``pkexec``.

    Reconnu au code de retour **et** à la ligne anglaise exacte de ``pkexec`` ;
    ``None`` pour toute autre sortie, laissée telle quelle.
    """
    lines = {line.strip() for line in stderr.splitlines()}
    for code, english, message in _POLKIT_REFUSALS:
        if returncode == code and english in lines:
            return tr(message)
    return None


def run_command(
    argv: list[str],
    *,
    elevate: bool = False,
    timeout: float = DEFAULT_TIMEOUT_S,
    runner: Runner | None = None,
    detach_output: bool = False,
    cancel: threading.Event | None = None,
) -> CommandResult:
    """Exécute ``argv`` (sans shell), avec élévation polkit ponctuelle si demandé.

    :param argv: commande déjà découpée et **validée par l'appelant** (les noms
        de paquets, PID, etc. doivent être vérifiés en amont — ce module ne
        connaît pas la sémantique de la commande).
    :param elevate: si vrai, préfixe ``pkexec`` → root ponctuel, par action,
        après authentification polkit. Le daemon reste utilisateur.
    :param timeout: délai max en secondes.
    :param runner: lanceur injectable (tests). Défaut : :func:`subprocess.run`.
    :param detach_output: pas de capture de la sortie (``/dev/null``), pour une
        commande qui lance un processus durable (application graphique) : la
        sortie n'est pas lue et seul le code de retour compte.
    :param cancel: événement d'annulation. Déjà posé : rien n'est lancé. Posé
        pendant la commande : le processus est tué (au plus :data:`CANCEL_POLL_S`
        après). Dans les deux cas, le résultat a ``cancelled=True``. Avec un
        ``runner`` injecté, seule la vérification avant et après le lancement a lieu.
    :returns: :class:`CommandResult` (code, stdout, stderr, annulation).

    En cas de timeout ou d'absence du binaire (``pkexec`` ou la commande
    introuvable), renvoie un :class:`CommandResult` d'échec lisible plutôt que
    de propager l'exception — l'orchestrator réinjecte ce message comme donnée.
    Avec ``elevate``, un refus de polkit (codes 126 et 127 de ``pkexec``) a pour
    ``stderr`` un message dans la langue de My_OS, au lieu de l'anglais de
    ``pkexec`` ; le code de retour ne change pas.
    """
    final_argv = build_argv(argv, elevate=elevate)
    if cancel is not None and cancel.is_set():
        return _cancelled_result()  # annulée avant même d'être lancée
    if runner is not None:
        run = runner
    elif detach_output:
        run = _detached_runner
    elif cancel is not None:
        run = partial(_cancellable_runner, cancel=cancel)
    else:
        run = _default_runner
    try:
        completed = run(final_argv, timeout=timeout)
    except FileNotFoundError as exc:
        return CommandResult(
            returncode=127,
            stdout="",
            stderr=tr("commande introuvable : {error}", error=exc),
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            returncode=124,
            stdout="",
            stderr=tr(
                "délai dépassé ({seconds}s) : {command}",
                seconds=f"{timeout:.0f}",
                command=" ".join(final_argv),
            ),
        )
    returncode = int(completed.returncode)
    if cancel is not None and cancel.is_set() and returncode != 0:
        # Tuée par l'annulation (ou en échec au même moment : l'annulation prime).
        return _cancelled_result(completed.stdout or "")
    stderr = completed.stderr or ""
    if elevate:
        # Seul pkexec écrit ces refus : sans élévation, la sortie reste telle quelle.
        stderr = _polkit_refusal(returncode, stderr) or stderr
    return CommandResult(
        returncode=returncode,
        stdout=completed.stdout or "",
        stderr=stderr,
    )
