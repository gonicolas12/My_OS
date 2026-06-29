"""Backend cloud (Claude via l'API Anthropic) + routeur de sélection par requête.

Décision de contrat (cf. docs/INTERFACES.md §5.2) : le backend cloud implémente le
**même protocole** :class:`daemon.orchestrator.Model` que le local
(``respond(messages, on_token) -> Plan``), en s'appuyant sur le **tool use
Anthropic**. L'IA cloud pilote donc les outils par la *même* boucle agentique et le
*même* ``policy_engine`` que le local — un seul chemin de code, un seul choke point
de sécurité. Le LLM cloud n'a **aucun droit supplémentaire** par rapport au local.

Sécurité (cf. docs/SECURITY.md menaces 2 & 4) :

* la clé API n'est **jamais** en dur ni en clair : elle vient de
  :mod:`models.secrets` (trousseau OS) ; ``anthropic`` est importé **paresseusement** ;
* le cloud est **opt-in par requête** : c'est l'``Orchestrator`` qui choisit ce
  backend selon ``use_cloud`` (cf. INTERFACES §5.3), jamais le modèle ;
* le contenu lu reste une **donnée** non fiable : les schémas d'outils et le system
  prompt sont partagés avec le local, qui rappelle déjà l'invariant.

Le schéma d'outils envoyé à Claude est **dérivé** de ``ALL_TOOLS_SCHEMA`` (format
Ollama) pour éviter une 3ᵉ copie de la liste d'outils, et le system prompt réutilise
:data:`models.local_llm.BASE_SYSTEM_PROMPT` (+ une note « cloud » honnête).
"""

from __future__ import annotations

import hashlib
from typing import Any

from daemon.orchestrator import Model, Plan, TokenCallback, ToolCall
from models import secrets
from models.local_llm import ALL_TOOLS_SCHEMA, BASE_SYSTEM_PROMPT, machine_context

# Modèle Claude par défaut (récent, bon rapport vitesse/intelligence). Configurable
# via core.config.ModelConfig.cloud_model.
DEFAULT_CLOUD_MODEL = "claude-sonnet-4-6"

# Plafond de tokens de sortie par tour. Volontairement modéré : un assistant au
# raccourci enchaîne des réponses concises et des appels d'outils, pas des pavés.
DEFAULT_MAX_TOKENS = 4096

# Note de backend « cloud » ajoutée au prompt de base : honnête sur le fait que la
# conversation quitte la machine, et rappelle que les permissions restent locales.
_CLOUD_NOTE = (
    "\n\nPour cette requête, tu fonctionnes en mode CLOUD : la conversation est "
    "envoyée à l'API Anthropic (Claude), un service distant. Tu restes soumis au "
    "moteur de permissions LOCAL — tu n'as aucun droit supplémentaire, et toute "
    "action sensible passe par une confirmation de l'utilisateur, exactement comme "
    "en local."
)


def _to_anthropic_tools(schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convertit le schéma d'outils Ollama (``function``) au format Anthropic.

    Ollama : ``{"type": "function", "function": {name, description, parameters}}``.
    Anthropic : ``{name, description, input_schema}``. On dérive le second du
    premier pour garder **une seule** source de vérité des outils.
    """
    tools: list[dict[str, Any]] = []
    for entry in schema:
        function = entry.get("function", entry)
        tools.append(
            {
                "name": function["name"],
                "description": function.get("description", ""),
                "input_schema": function.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            }
        )
    return tools


def _to_anthropic(messages: list[dict]) -> list[dict]:
    """Traduit l'historique générique de l'orchestrator au format Messages Anthropic.

    Historique générique (cf. INTERFACES §6.5) :
      - ``{"role": "user", "content": str}``
      - ``{"role": "assistant", "content": str, "tool_calls": list[ToolCall]}``
      - ``{"role": "tool", "tool": str, "content": str}``

    Format Anthropic : le ``system`` est passé à part ; un appel d'outil est un bloc
    ``tool_use`` (avec un ``id``) dans un message ``assistant`` ; son résultat est un
    bloc ``tool_result`` (référant le **même** ``tool_use_id``) dans un message
    ``user``. Les ``ToolCall`` génériques n'ont pas d'``id`` : on en **synthétise** de
    façon déterministe et on apparie chaque résultat d'outil au ``tool_use`` qui
    précède, dans l'ordre (l'orchestrator émet un message ``tool`` par ``tool_call``,
    dans l'ordre — cf. INTERFACES §6).
    """
    out: list[dict] = []
    pending_ids: list[str] = []  # file des tool_use en attente de leur résultat
    counter = 0
    for message in messages:
        role = message.get("role")
        if role == "user":
            out.append({"role": "user", "content": str(message.get("content", ""))})
        elif role == "assistant":
            blocks: list[dict] = []
            text = str(message.get("content", "") or "")
            if text.strip():
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                counter += 1
                tool_use_id = f"toolu_{counter}"
                pending_ids.append(tool_use_id)
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": call.tool,
                        "input": dict(call.args or {}),
                    }
                )
            if blocks:  # un tour assistant vide (ni texte ni outil) n'est pas envoyé
                out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            tool_use_id = pending_ids.pop(0) if pending_ids else f"toolu_{counter}"
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": str(message.get("content", "")),
                        }
                    ],
                }
            )
    return out


def _make_anthropic_client(api_key: str | None) -> Any:
    """Instancie un client Anthropic. Import paresseux : ``anthropic`` n'est requis
    qu'ici, pas pour importer le module (tests, mode stub)."""
    import anthropic  # pylint: disable=import-outside-toplevel

    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


class ClaudeClient:
    """Implémentation :class:`daemon.orchestrator.Model` via l'API Anthropic.

    Le client réel est injectable (``client=``) pour les tests : aucun appel réseau
    n'est fait à l'import ni en test. En production, il est construit paresseusement
    à partir de la clé du trousseau.
    """

    name = "claude"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_CLOUD_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = client if client is not None else _make_anthropic_client(api_key)
        # Prompt de base partagé + note « cloud » honnête + contexte machine réel.
        self._system_prompt = BASE_SYSTEM_PROMPT + _CLOUD_NOTE + machine_context()
        self._tools = _to_anthropic_tools(ALL_TOOLS_SCHEMA)

    def respond(
        self, messages: list[dict], on_token: TokenCallback | None = None
    ) -> Plan:
        """Soumet l'historique à Claude en streaming et le convertit en :class:`Plan`.

        Le texte est diffusé fragment par fragment via ``on_token`` ; les blocs
        ``tool_use`` du message final deviennent des :class:`ToolCall` que
        l'orchestrator route vers le ``policy_engine`` puis les outils réels.
        """
        narration_parts: list[str] = []
        with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system_prompt,
            tools=self._tools,
            messages=_to_anthropic(messages),
        ) as stream:
            for fragment in stream.text_stream:
                narration_parts.append(fragment)
                if on_token is not None:
                    on_token(fragment)
            final = stream.get_final_message()

        tool_calls = [
            ToolCall(tool=block.name, args=dict(getattr(block, "input", {}) or {}))
            for block in final.content
            if getattr(block, "type", None) == "tool_use"
        ]
        return Plan(narration="".join(narration_parts).strip(), tool_calls=tool_calls)


class CloudUnavailable(RuntimeError):
    """Levée quand le cloud est demandé mais qu'aucune clé API n'est configurée."""


class CloudRouter:
    """Sélecteur du backend cloud, **par requête** (cf. INTERFACES §5.3).

    Ne construit le :class:`ClaudeClient` que lorsqu'une clé existe, et le met en
    cache. Si la clé change (l'utilisateur la corrige via le popup), le client est
    reconstruit (comparaison par empreinte, pour ne pas dupliquer le secret en RAM).
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_CLOUD_MODEL,
        client_factory: Any | None = None,
    ) -> None:
        self._model_name = model
        # Fabrique injectable pour les tests : () -> Model. En production, None →
        # construction d'un ClaudeClient réel à partir de la clé du trousseau.
        self._client_factory = client_factory
        self._cloud: Model | None = None
        self._fingerprint: str | None = None

    def is_available(self) -> bool:
        """``True`` si une clé API est configurée (donc le cloud est utilisable)."""
        return secrets.has_api_key()

    def get_cloud_model(self) -> Model:
        """Renvoie le :class:`Model` cloud, en le construisant/cachant au besoin.

        Lève :class:`CloudUnavailable` si aucune clé n'est configurée — l'appelant
        (orchestrator) replie alors proprement sur le local.
        """
        key = secrets.get_api_key()
        if not key:
            raise CloudUnavailable("aucune clé API configurée (cf. models.secrets)")
        fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
        if self._cloud is None or fingerprint != self._fingerprint:
            self._cloud = (
                self._client_factory()
                if self._client_factory is not None
                else ClaudeClient(api_key=key, model=self._model_name)
            )
            self._fingerprint = fingerprint
        return self._cloud
