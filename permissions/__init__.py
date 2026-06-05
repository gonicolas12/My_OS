"""Moteur de permissions — cœur de sûreté du projet.

C'est *ici* que se décide ce qui s'exécute et ce qui demande confirmation. La
sûreté ne dépend pas de la fiabilité du LLM (cf. docs/SECURITY.md §1) :

* :mod:`permissions.risk_levels` : source de vérité statique outil → niveau.
* :mod:`permissions.blocklist` : niveau 3, jamais franchissable.
* :mod:`permissions.session_grants` : mémorisation des autorisations.
* :mod:`permissions.policy_engine` : point de passage unique.
* :mod:`permissions.confirmation` : payload pour le popup.
* :mod:`permissions.audit_log` : journal SQLite.
"""
