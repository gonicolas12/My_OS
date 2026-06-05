"""Rendu de la conversation en markdown pour ``QTextBrowser``.

``QTextBrowser.setMarkdown`` rend nativement le markdown (gras, italique,
listes, ``code``…). Ce module se contente d'assembler la liste de messages en
un document markdown unique ; c'est le QTextBrowser qui fait le rendu visuel
(léger, sans moteur web — cf. docs/ARCHITECTURE.md §5).

Sécurité : le texte de l'assistant peut contenir des données non fiables (ex.
contenu d'un fichier lu). QTextBrowser n'exécute aucun script et les liens ne
s'ouvrent pas tout seuls (``setOpenExternalLinks(False)``) — le risque se limite
au rendu visuel, conforme au principe « contenu lu = donnée » (SECURITY §2.2).
"""

from __future__ import annotations

from collections.abc import Sequence

# Un message = {"role": "user" | "assistant", "text": str}
Message = dict[str, str]


def conversation_to_markdown(messages: Sequence[Message]) -> str:
    """Assemble les messages en un document markdown unique.

    Les messages utilisateur sont préfixés et en gras ; les messages assistant
    sont rendus tels quels (markdown du modèle). Les messages vides (réponse en
    cours, pas encore de token) sont ignorés.
    """
    blocks: list[str] = []
    for message in messages:
        text = message.get("text", "")
        if message.get("role") == "user":
            blocks.append(f"**Vous :** {text}")
        elif text:
            blocks.append(text)
    return "\n\n".join(blocks)
