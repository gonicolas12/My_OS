"""Modèle local absent : pourquoi, et la commande qui l'installe.

Sur l'ISO, ``myos-user-firstrun`` télécharge le modèle local en tâche de fond à la
première session, en plusieurs essais espacés (cf. packaging/README.md), et note où il
en est dans ``~/.local/share/my_os/model-pull.status``, une ligne :

* ``pending MODÈLE`` : à télécharger (noté juste avant que la tâche ne se lance) ;
* ``pulling MODÈLE PID`` : la tâche de fond (PID) tourne encore : un essai en cours,
  ou l'attente avant le suivant ;
* ``done MODÈLE`` : téléchargé ;
* ``failed MODÈLE`` : tous les essais ont échoué ;
* ``no-space MODÈLE`` : pas tenté, faute d'espace libre.

Une session suivante relance le téléchargement d'un modèle ``failed``, ``no-space``,
``pending`` ou ``pulling`` dont la tâche a disparu. Ici, ``pending`` (un instant) est un
état inconnu.

Quand Ollama répond que le modèle configuré n'existe pas (HTTP 404), le client local
lève :class:`ModelNotInstalled` (:func:`model_not_installed`), dont le message, choisi
d'après cet état, dit au popup pourquoi le modèle manque (il se télécharge encore, le
téléchargement a échoué, la place manque, ou rien n'est connu : installation hors ISO)
et donne chaque fois la commande ``ollama pull``.

Le fichier d'état est une DONNÉE (cf. SECURITY §2.2) : seuls un mot connu, le nom du
modèle (comparé à celui de la config) et un PID entier en sont tirés ; rien n'y est
exécuté ni affiché tel quel.
"""

from __future__ import annotations

from pathlib import Path

from core.i18n import N_, tr
from core.logger import get_logger
from daemon.orchestrator import ModelUnavailable

_log = get_logger("myosd.model")

# Dossier où myos-user-firstrun écrit son journal et l'état du téléchargement : le
# même chemin que le script (~/.local/share/my_os, XDG_DATA_HOME n'y change rien).
FIRSTRUN_DIR = Path.home() / ".local" / "share" / "my_os"
STATUS_FILE = "model-pull.status"
# Processus du système : la tâche de fond du téléchargement tourne-t-elle encore ?
_PROC = Path("/proc")
# Une tâche de fond lancée par un script garde la ligne de commande de celui-ci.
_FIRSTRUN = b"myos-user-firstrun"

PULLING = "pulling"
FAILED = "failed"
NO_SPACE = "no-space"
UNKNOWN = ""

# Message du popup selon l'état du téléchargement (le popup le préfixe d'« Erreur : ») ;
# chacun donne la commande, seule sur sa ligne pour la copier.
_MESSAGES = {
    # La tâche de fond tourne : un essai en cours, ou l'attente avant le suivant.
    PULLING: N_(
        "le modèle local {model} n'est pas encore prêt : il se télécharge en "
        "arrière-plan, ou attend un nouvel essai. Réessayez dans quelques minutes. "
        "Pour suivre le téléchargement ou le relancer, lancez dans un terminal :\n"
        "ollama pull {model}"
    ),
    # Essais épuisés, mais aussi tâche interrompue, disque plein… : aucune cause
    # supposée, le journal la donne.
    FAILED: N_(
        "le modèle local {model} n'a pas pu être téléchargé (essais en échec ou "
        "interrompus, détails dans ~/.local/share/my_os/model-pull.log). Nouvel essai "
        "à la prochaine ouverture de session ; pour le télécharger tout de suite, "
        "lancez dans un terminal :\nollama pull {model}"
    ),
    NO_SPACE: N_(
        "le modèle local {model} n'a pas été téléchargé, faute d'espace libre (en "
        "session live, cet espace est pris sur la mémoire vive ; détails dans "
        "~/.local/share/my_os/model-pull.log). Une fois la place disponible, lancez "
        "dans un terminal :\nollama pull {model}"
    ),
    UNKNOWN: N_(
        "le modèle local {model} n'est pas installé. Pour l'installer (réseau "
        "requis), lancez dans un terminal :\nollama pull {model}"
    ),
}


class ModelNotInstalled(ModelUnavailable):
    """Le modèle local n'est pas (encore) dans Ollama ; le message dit quoi faire."""


def is_model_missing(exc: BaseException) -> bool:
    """``True`` pour l'erreur d'Ollama « modèle introuvable » (HTTP 404).

    ``ollama.ResponseError`` porte le code HTTP dans ``status_code`` ; pour un appel
    de ``/api/chat``, 404 signifie que le modèle demandé n'est pas installé (même
    pendant son téléchargement, tant qu'il n'est pas complet).
    """
    return getattr(exc, "status_code", None) == 404


def _firstrun_running(pid: str) -> bool:
    """La tâche de fond ``pid`` de myos-user-firstrun tourne-t-elle encore ?

    Le processus doit exister ET être ce script : après un redémarrage, le PID noté
    peut désigner un tout autre processus.
    """
    if not (pid.isascii() and pid.isdigit()):
        return False
    try:
        return _FIRSTRUN in (_PROC / pid / "cmdline").read_bytes()
    except OSError:
        return False


def pull_state(model: str) -> str:
    """État du téléchargement de ``model`` au premier démarrage de l'ISO.

    :data:`PULLING` si la tâche de fond tourne encore, :data:`FAILED` si elle a renoncé
    ou a été interrompue, :data:`NO_SPACE` si elle n'a pas été lancée faute de place,
    :data:`UNKNOWN` sinon (pas d'ISO, autre modèle, fichier illisible ou téléchargement
    terminé puis modèle retiré).
    """
    try:
        fields = (FIRSTRUN_DIR / STATUS_FILE).read_text(encoding="utf-8").split()
    except (OSError, UnicodeDecodeError):
        return UNKNOWN
    if len(fields) < 2 or fields[1] != model:
        return UNKNOWN
    state = fields[0]
    if state == PULLING:
        pid = fields[2] if len(fields) > 2 else ""
        return PULLING if _firstrun_running(pid) else FAILED
    return state if state in (FAILED, NO_SPACE) else UNKNOWN


def model_not_installed(model: str) -> ModelNotInstalled:
    """L'erreur à lever quand Ollama n'a pas ``model`` : message du popup dans la
    langue de la requête, avec la commande ; la cause va au journal du daemon."""
    state = pull_state(model)
    _log.warning(
        "Modèle local %s absent d'Ollama (téléchargement du 1er démarrage : %s)",
        model,
        state or "état inconnu",
    )
    return ModelNotInstalled(tr(_MESSAGES[state], model=model))
