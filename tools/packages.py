"""Outils paquets du jalon 3 — ``pacman`` enveloppé.

Quatre outils, sur le patron de :mod:`tools.files` :

* ``search_package`` — **niveau 0**, pas de root : ``pacman -Ss`` (lecture) ;
* ``install_package`` — **niveau 1** mais ``requires_elevation`` (root via
  polkit) : ``pacman -S`` ;
* ``update_system`` — **niveau 2** + élévation : ``pacman -Syu`` ;
* ``remove_package`` — **niveau 2** + élévation : ``pacman -Rns``. La blocklist
  refuse en amont les paquets critiques (cf. :mod:`permissions.blocklist`).

Sécurité (cf. CLAUDE.md / SECURITY §7) :

* **jamais de shell** : toute commande passe par :func:`core.elevation.run_command`
  qui exige une ``list[str]`` ;
* **validation stricte** des noms de paquets / requêtes via une regex avant de
  construire l'argv : un nom invalide (espace, ``;``, ``-`` en tête…) est rejeté
  *avant* exécution, ce qui ferme l'injection d'arguments pacman (``-`` initial)
  comme l'injection shell ;
* l'élévation est **ponctuelle, par action** : ``run_command(..., elevate=True)``
  préfixe ``pkexec``. Le daemon reste utilisateur.

Aucune vérification de permission ici (rôle exclusif du ``policy_engine``). La
sortie de pacman est une **donnée** non fiable (SECURITY §2.2).
"""

from __future__ import annotations

import re

from core.elevation import Runner, run_command
from tools.base_tool import BaseTool, ToolResult

# Nom de paquet pacman : minuscule/chiffre en tête, puis lettres, chiffres et
# « @ . _ + - ». Interdit le « - » initial (sinon pacman le lit comme un flag),
# les espaces et tout métacaractère. Borne de longueur défensive.
_PACKAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9@._+-]{0,99}$")

# Requête de recherche : un peu plus permissive (majuscules tolérées car
# ``pacman -Ss`` matche un motif), mais toujours sans « - » initial ni espace.
_SEARCH_QUERY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._+-]{0,99}$")

# Tronque la sortie pacman réinjectée pour ne pas noyer le modèle / l'UI.
_MAX_OUTPUT_CHARS = 4000


def _err(message: str) -> ToolResult:
    """Construit un ToolResult d'échec lisible."""
    return ToolResult(success=False, output=message, reversible=False)


def _clip(text: str) -> str:
    """Tronque proprement une sortie de commande trop longue."""
    text = text.strip()
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + "\n[... sortie tronquée ...]"


def _format_result(success_prefix: str, result: object) -> ToolResult:
    """Construit le ToolResult depuis un :class:`core.elevation.CommandResult`.

    ``result`` est typé ``object`` pour éviter une dépendance d'import circulaire
    inutile ; il expose ``ok``, ``stdout`` et ``stderr``.
    """
    ok = bool(getattr(result, "ok", False))
    stdout = str(getattr(result, "stdout", ""))
    stderr = str(getattr(result, "stderr", ""))
    if ok:
        body = _clip(stdout) or "(aucune sortie)"
        return ToolResult(
            success=True, output=f"{success_prefix}\n{body}", reversible=False
        )
    detail = _clip(stderr) or _clip(stdout) or "échec sans message"
    return _err(f"{success_prefix} : échec\n{detail}")


class _PacmanTool(BaseTool):  # pylint: disable=abstract-method
    """Base commune (abstraite) : porte un ``runner`` injectable vers ``run_command``.

    ``run`` est volontairement non surchargée ici — chaque sous-classe concrète
    l'implémente ; cette classe n'est jamais instanciée directement.
    """

    risk_level = 0  # surchargé par chaque sous-classe ; présent pour BaseTool

    def __init__(self, runner: Runner | None = None) -> None:
        self._runner = runner


class SearchPackage(_PacmanTool):
    """Recherche un paquet dans les dépôts (``pacman -Ss``). Lecture, sans root."""

    name = "search_package"
    description = "Recherche un paquet disponible dans les dépôts officiels."
    risk_level = 0
    parameters = {"query": "str — terme à rechercher (nom de paquet ou motif)"}

    def run(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not _SEARCH_QUERY_RE.match(query):
            return _err(
                "search_package : requête invalide (lettres, chiffres et "
                "« @._+- » uniquement, sans tiret initial ni espace)"
            )
        result = run_command(["pacman", "-Ss", query], runner=self._runner)
        # pacman -Ss renvoie un code != 0 quand il n'y a aucun résultat : ce
        # n'est pas une erreur d'exécution, on le présente comme « rien trouvé ».
        if not result.ok and not result.stderr.strip():
            return ToolResult(
                success=True,
                output=f"Aucun paquet ne correspond à « {query} ».",
                reversible=False,
            )
        return _format_result(f"Résultats pour « {query} » :", result)


class InstallPackage(_PacmanTool):
    """Installe un paquet (``pacman -S``). Niveau 1, mais root requis (polkit)."""

    name = "install_package"
    description = "Installe un paquet depuis les dépôts officiels."
    risk_level = 1
    parameters = {"name": "str — nom exact du paquet à installer"}

    def requires_elevation(self, args: dict) -> bool:
        return True

    def run(self, args: dict) -> ToolResult:
        name = args.get("name")
        if not isinstance(name, str) or not _PACKAGE_NAME_RE.match(name):
            return _err(
                "install_package : nom de paquet invalide (minuscules, chiffres "
                "et « @._+- », sans tiret initial)"
            )
        result = run_command(
            ["pacman", "-S", "--noconfirm", name], elevate=True, runner=self._runner
        )
        return _format_result(f"Installation de « {name} »", result)


class RemovePackage(_PacmanTool):
    """Désinstalle un paquet et ses dépendances orphelines (``pacman -Rns``)."""

    name = "remove_package"
    description = "Désinstalle un paquet (et ses dépendances devenues orphelines)."
    risk_level = 2
    parameters = {"name": "str — nom exact du paquet à désinstaller"}

    def requires_elevation(self, args: dict) -> bool:
        return True

    def run(self, args: dict) -> ToolResult:
        name = args.get("name")
        if not isinstance(name, str) or not _PACKAGE_NAME_RE.match(name):
            return _err(
                "remove_package : nom de paquet invalide (minuscules, chiffres "
                "et « @._+- », sans tiret initial)"
            )
        result = run_command(
            ["pacman", "-Rns", "--noconfirm", name], elevate=True, runner=self._runner
        )
        return _format_result(f"Désinstallation de « {name} »", result)


class UpdateSystem(_PacmanTool):
    """Met à jour tout le système (``pacman -Syu``). Niveau 2 + élévation."""

    name = "update_system"
    description = "Met à jour la liste des paquets et tout le système installé."
    risk_level = 2
    parameters: dict = {}

    def requires_elevation(self, args: dict) -> bool:
        return True

    def run(self, args: dict) -> ToolResult:
        result = run_command(
            ["pacman", "-Syu", "--noconfirm"], elevate=True, runner=self._runner
        )
        return _format_result("Mise à jour du système", result)


# Registre des outils paquets — fusionné dans le daemon (cf. daemon/myosd.py).
PACKAGE_TOOLS: dict[str, BaseTool] = {
    tool.name: tool
    for tool in (
        SearchPackage(),
        InstallPackage(),
        RemovePackage(),
        UpdateSystem(),
    )
}
