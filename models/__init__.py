"""Couche 3 — modèles (local et cloud).

Au jalon 2 : un modèle stub à base de règles permet de tester la chaîne
orchestrator → outils → permissions → audit sans dépendre d'Ollama.

Au jalon 7-8 : :mod:`models.local_llm` ajoute le vrai client Ollama/Qwen
derrière le même protocole :class:`daemon.orchestrator.Model`, et le stub
reste disponible comme repli (config ``models.backend: stub`` par exemple).
"""
