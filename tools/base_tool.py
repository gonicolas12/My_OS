"""Classe de base de tous les outils + résultat d'exécution.

Contrat (cf. docs/INTERFACES.md §2) :

* tout outil hérite de :class:`BaseTool` et **doit** déclarer un ``risk_level``
  entier dans ``{0, 1, 2, 3}`` ; un outil sans niveau valide est refusé au
  chargement (``TypeError`` à la définition de la sous-classe) ;
* ``escalate(args)`` peut **uniquement augmenter** le risque selon les
  arguments — jamais le diminuer ; le ``policy_engine`` réapplique cette
  garantie en défense en profondeur ;
* ``run(args)`` exécute l'action ; il **n'est appelé** qu'après validation par
  le ``policy_engine`` — l'outil ne fait aucun contrôle de permission lui-même
  (séparation des responsabilités).
"""

from __future__ import annotations

from dataclasses import dataclass

VALID_RISK_LEVELS = (0, 1, 2, 3)


@dataclass
class ToolResult:
    """Résultat retourné par :meth:`BaseTool.run`.

    ``reversible`` et ``undo_data`` documentent une éventuelle annulation
    ultérieure ; au jalon 2 ils ne sont pas encore exploités par l'orchestrator.
    """

    success: bool
    output: str
    reversible: bool = False
    undo_data: dict | None = None


class BaseTool:
    """Classe de base abstraite des outils.

    Sous-classes : surchargent au minimum :attr:`name`, :attr:`description`,
    :attr:`risk_level` et :meth:`run`. Si l'outil escalade son risque selon
    ses arguments, surcharger :meth:`escalate`. Si certains chemins sont
    impactés (pour les grants), surcharger :meth:`affected_paths`.
    """

    name: str = ""
    description: str = ""
    parameters: dict = {}
    # risk_level est volontairement absent ici : c'est aux sous-classes de le
    # déclarer. Le hook ``__init_subclass__`` ci-dessous refuse celles qui ne
    # le font pas, conformément à l'invariant de sécurité.
    risk_level: int

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        declared = cls.__dict__.get("risk_level")
        if not isinstance(declared, int) or declared not in VALID_RISK_LEVELS:
            raise TypeError(
                f"Outil {cls.__name__!r} : risk_level doit être déclaré dans "
                f"{VALID_RISK_LEVELS} (obtenu : {declared!r}). "
                f"Cf. docs/SECURITY.md §4."
            )

    # pylint: disable-next=unused-argument
    def escalate(self, args: dict) -> int:
        """Renvoie le risque effectif selon ``args``.

        Contrat : **doit** renvoyer une valeur ``>= self.risk_level``. Le
        ``policy_engine`` réapplique un ``max`` en défense en profondeur, mais
        c'est l'outil qui décrit sa logique d'escalade ici.
        """
        return self.risk_level

    # pylint: disable-next=unused-argument
    def requires_elevation(self, args: dict) -> bool:
        """``True`` si l'action nécessite une élévation de privilège (polkit).

        **Orthogonal au ``risk_level``** : un outil peut être en niveau 1
        (confirmation simple) tout en exigeant root — typiquement
        ``pacman -S`` (installer = niveau 1, mais root requis). À l'inverse,
        une suppression de fichier utilisateur est niveau 2 sans aucun root.

        Le ``policy_engine`` lit cette déclaration pour renseigner
        ``Decision.requires_elevation`` (cf. docs/INTERFACES.md §3) ; l'élévation
        réelle est faite à l'exécution par :func:`core.elevation.run_command`
        avec ``elevate=True``. Par défaut : ``False``.
        """
        return False

    # pylint: disable-next=unused-argument
    def affected_paths(self, args: dict) -> list[str]:
        """Chemins concernés par l'action, utilisés pour matcher les grants.

        Par défaut : aucun (l'outil n'est pas paramétré par un chemin). Les
        outils fichiers surchargeront pour renvoyer ``[args["path"]]`` ou
        l'équivalent.
        """
        return []

    def normalize_args(self, args: dict) -> dict:
        """Renvoie des arguments normalisés AVANT évaluation des permissions.

        Appelé par l'orchestrator en amont de ``policy_engine.evaluate`` : la
        forme normalisée est donc celle vue par la blocklist, l'escalade, les
        grants ET ``run``. Par défaut : identité.

        Les outils fichiers surchargent pour expanser ``~`` afin que la
        détection de chemin sensible voie le chemin réel (sinon ``~/.ssh``
        échapperait à l'escalade — cf. docs/SECURITY.md §4).
        """
        return args

    def run(self, args: dict) -> ToolResult:
        """Exécute l'action. Implémentation obligatoire dans les sous-classes."""
        raise NotImplementedError
