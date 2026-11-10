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

Le schéma d'outils envoyé à Claude est **dérivé** de celui du local
(:func:`models.local_llm.tools_schema`, format Ollama) pour éviter une 3ᵉ copie de la
liste d'outils, et le system prompt réutilise
:data:`models.prompts.BASE_SYSTEM_PROMPTS` (+ une note « cloud » honnête). Les deux
suivent la langue de la requête, comme en local.

**Réflexion** (Claude Sonnet 5.5, cf. INTERFACES §5.2). Le modèle réfléchit par défaut
(réflexion adaptative). Dans une boucle d'outils, chaque tour repart tel que l'API l'a
renvoyé (``Plan.raw_content`` : blocs de réflexion signés, vrais identifiants des appels
d'outils). Ces blocs sont liés à la conversation qui les a produits : toute modification
d'un tour antérieur les invalide (erreur 400 sur les comptes récents). Or l'historique
de My_OS change entre deux requêtes (langue, élagage) et pendant le contrôle de l'écran
(seule la dernière capture est gardée). D'où deux règles : seule la réflexion de la
requête en cours repart (retirer les blocs du début de l'historique est permis), et
l'API écarte un bloc devenu invalide au lieu d'échouer (``drop_block``, en-tête bêta).
Une réponse coupée (longueur maximale) ou refusée n'exécute aucun outil.
"""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Any

from core.i18n import N_, SUPPORTED_LANGUAGES, current_language, tr
from core.logger import get_logger
from daemon.orchestrator import Model, Plan, TokenCallback, ToolCall
from models import secrets
from models.local_llm import tools_schema, with_language_reminders
from models.prompts import system_prompt

# Modèle Claude par défaut (récent, bon rapport vitesse/intelligence). Configurable
# via core.config.ModelConfig.cloud_model.
DEFAULT_CLOUD_MODEL = "claude-sonnet-5-5"

# Plafond de tokens de sortie par tour. La réflexion du modèle en fait partie, même
# quand son texte n'est pas renvoyé : 16 000 lui laissent la place avant la réponse et
# les appels d'outils (un plafond atteint coupe le tour, cf. _LENGTH_STOPS).
DEFAULT_MAX_TOKENS = 16000

# Réglages de la réflexion (comparés avec une vraie clé avant d'en changer le défaut) :
# "adaptive", défaut de l'API sur ce modèle, ou "between_tools", le plus bas (pas de
# réflexion préalable, notes brèves entre deux outils ; effort high au plus).
THINKING_MODES = ("adaptive", "between_tools")
# Niveaux d'effort ; None = défaut de l'API (high sur Claude Sonnet 5.5).
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
_BETWEEN_TOOLS_MAX_EFFORT = ("low", "medium", "high")
# En-tête bêta qui permet de demander d'écarter un bloc de réflexion invalidé par une
# modification de l'historique au lieu d'échouer (réflexion adaptative seulement).
THINKING_BINDING_BETA = "thinking-binding-controls-2026-08-01"
_THINKING_TYPES = frozenset({"thinking", "redacted_thinking"})
# Arrêts qui coupent le tour avant sa fin : le plan est incomplet, rien ne s'exécute.
_LENGTH_STOPS = frozenset({"max_tokens", "model_context_window_exceeded"})

_cloud_log = get_logger("myosd.cloud")

_CUT_NOTICE = N_(
    "⚠ Réponse coupée : Claude a atteint la longueur maximale d'une réponse. Aucune "
    "action de ce tour n'a été lancée ; reformulez la demande ou découpez-la."
)
_REFUSAL_NOTICE = N_(
    "⚠ Claude a refusé de poursuivre cette demande (règles d'usage d'Anthropic). "
    "Aucune action de ce tour n'a été lancée."
)

# Note de backend « cloud » ajoutée au prompt de base : honnête sur le fait que la
# conversation quitte la machine, et rappelle que les permissions restent locales.
_CLOUD_NOTES = {
    "fr": (
        "\n\nPour cette requête, tu fonctionnes en mode CLOUD : la conversation est "
        "envoyée à l'API Anthropic (Claude), un service distant. Tu restes soumis au "
        "moteur de permissions LOCAL — tu n'as aucun droit supplémentaire, et toute "
        "action sensible passe par une confirmation de l'utilisateur, exactement "
        "comme en local."
    ),
    "en": (
        "\n\nFor this request, you run in CLOUD mode: the conversation is sent to "
        "the Anthropic API (Claude), a remote service. You remain subject to the "
        "LOCAL permission engine — you have no extra rights, and every sensitive "
        "action goes through a confirmation by the user, exactly as in local mode."
    ),
}


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


def _field(block: Any, name: str) -> Any:
    """Champ d'un bloc de contenu, objet du SDK ou dict."""
    return block.get(name) if isinstance(block, dict) else getattr(block, name, None)


def _raw_blocks(message: dict, *, current_request: bool) -> list[Any] | None:
    """Tour assistant tel que l'API l'a renvoyé, s'il correspond encore au message.

    Dans la requête en cours (la boucle d'outils), le contenu brut repart tel quel,
    blocs de réflexion signés compris. Pour les requêtes précédentes, seuls le texte et
    les appels d'outils (avec leurs vrais identifiants) repartent : retirer la réflexion
    du début de l'historique est permis, alors qu'un historique modifié entre deux
    requêtes (langue, élagage) invaliderait ces blocs. ``None`` : pas de contenu brut,
    ou des appels d'outils qui ne correspondent plus (conversion générique).
    """
    raw = message.get("raw_content")
    if not isinstance(raw, list) or not raw:
        return None
    uses = [
        _field(block, "name") for block in raw if _field(block, "type") == "tool_use"
    ]
    if uses != [call.tool for call in message.get("tool_calls") or []]:
        return None
    if current_request:
        return list(raw)
    kept = [block for block in raw if _field(block, "type") not in _THINKING_TYPES]
    return kept or None


def _to_anthropic(messages: list[dict]) -> list[dict]:
    """Traduit l'historique générique de l'orchestrator au format Messages Anthropic.

    Historique générique (cf. INTERFACES §6.5) :
      - ``{"role": "user", "content": str}``
      - ``{"role": "assistant", "content": str, "tool_calls": list[ToolCall]}``,
        plus ``raw_content`` pour un tour produit par le cloud
      - ``{"role": "tool", "tool": str, "content": str}``

    Format Anthropic : le ``system`` est passé à part ; un appel d'outil est un bloc
    ``tool_use`` (avec un ``id``) dans un message ``assistant`` ; son résultat est un
    bloc ``tool_result`` (référant le **même** ``tool_use_id``) dans un message
    ``user``. Un tour produit par le cloud repart tel que l'API l'a renvoyé (cf.
    :func:`_raw_blocks`). Les ``ToolCall`` génériques (tours du local) n'ont pas
    d'``id`` : on en **synthétise** de façon déterministe. Chaque résultat d'outil est
    apparié au ``tool_use`` qui précède, dans l'ordre (l'orchestrator émet un message
    ``tool`` par ``tool_call``, dans l'ordre — cf. INTERFACES §6).
    """
    out: list[dict] = []
    pending_ids: list[str] = []  # file des tool_use en attente de leur résultat
    counter = 0
    # La requête en cours commence au dernier message de l'utilisateur.
    last_user = max(
        (i for i, message in enumerate(messages) if message.get("role") == "user"),
        default=-1,
    )
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "user":
            out.append({"role": "user", "content": str(message.get("content", ""))})
        elif role == "assistant":
            raw = _raw_blocks(message, current_request=index > last_user)
            if raw is not None:
                out.append({"role": "assistant", "content": raw})
                pending_ids.extend(
                    str(_field(block, "id"))
                    for block in raw
                    if _field(block, "type") == "tool_use"
                )
                continue
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
                            "content": _tool_content(message),
                        }
                    ],
                }
            )
    return out


def _tool_content(message: dict) -> str | list[dict]:
    """Contenu d'un résultat d'outil : le texte, et la capture d'écran s'il y en a une.

    Seule la dernière capture est gardée dans l'historique (cf. orchestrator) :
    au plus une image part par requête.
    """
    text = str(message.get("content", ""))
    image = message.get("image")
    if not isinstance(image, bytes):
        return text
    return [
        {"type": "text", "text": text},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(image).decode("ascii"),
            },
        },
    ]


def _make_anthropic_client(api_key: str | None) -> Any:
    """Instancie un client Anthropic. Import paresseux : ``anthropic`` n'est requis
    qu'ici, pas pour importer le module (tests, mode stub)."""
    import anthropic  # pylint: disable=import-outside-toplevel

    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _log_response(
    final: Any, model: str, first_word: float | None, total: float
) -> None:
    """Journalise une réponse du cloud : arrêt, durées, tokens facturés (sans contenu).

    Complète l'envoi tracé par l'orchestrator (cf. INTERFACES §5.4) ; sert aussi à
    mesurer les réglages de la réflexion (« premier mot » = premier texte affiché).
    """
    usage = getattr(final, "usage", None)

    def count(name: str) -> int:
        return int(getattr(usage, name, None) or 0)

    dropped = sum(
        1
        for change in getattr(final, "input_transformations", None) or []
        if _field(change, "type") == "thinking_dropped"
    )
    details = getattr(final, "stop_details", None)
    _cloud_log.info(
        "Réponse CLOUD (%s) : arrêt %s%s, premier mot %s, durée %.1f s ; tokens : "
        "entrée %d (cache lu %d, écrit %d), sortie %d ; blocs de réflexion écartés %d",
        model,
        getattr(final, "stop_reason", None),
        f" ({_field(details, 'category')})" if details is not None else "",
        f"{first_word:.1f} s" if first_word is not None else "aucun",
        total,
        count("input_tokens"),
        count("cache_read_input_tokens"),
        count("cache_creation_input_tokens"),
        count("output_tokens"),
        dropped,
    )


class ClaudeClient:  # pylint: disable=too-many-instance-attributes
    """Implémentation :class:`daemon.orchestrator.Model` via l'API Anthropic.

    Le client réel est injectable (``client=``) pour les tests : aucun appel réseau
    n'est fait à l'import ni en test. En production, il est construit paresseusement
    à partir de la clé du trousseau.

    ``thinking`` et ``effort`` règlent la réflexion du modèle (cf. :data:`THINKING_MODES`,
    :data:`EFFORT_LEVELS`) ; par défaut, ceux de l'API. Jamais de ``temperature``,
    ``top_p`` ni ``top_k`` : refusés par ce modèle (erreur 400).
    """

    name = "claude"

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_CLOUD_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any | None = None,
        screen_control: bool = True,
        thinking: str = "adaptive",
        effort: str | None = None,
    ) -> None:
        if thinking not in THINKING_MODES:
            raise ValueError(f"réflexion inconnue : {thinking!r}")
        if effort is not None and effort not in EFFORT_LEVELS:
            raise ValueError(f"effort inconnu : {effort!r}")
        if thinking == "between_tools" and effort not in (
            None,
            *_BETWEEN_TOOLS_MAX_EFFORT,
        ):
            raise ValueError("between_tools n'accepte qu'un effort high ou moins")
        self._model = model
        self._max_tokens = max_tokens
        self._thinking = thinking
        self._effort = effort
        self._client = client if client is not None else _make_anthropic_client(api_key)
        # Par langue : prompt de base partagé (+ section écran) + note « cloud »
        # honnête + contexte machine réel, et outils traduits. Les captures d'écran
        # partent avec les résultats d'outils.
        self._system_prompts = {
            language: system_prompt(
                language,
                screen_control=screen_control,
                backend_note=_CLOUD_NOTES[language],
            )
            for language in SUPPORTED_LANGUAGES
        }
        self._tools_by_language = {
            language: _to_anthropic_tools(
                tools_schema(language, screen_control=screen_control)
            )
            for language in SUPPORTED_LANGUAGES
        }

    @property
    def _tools(self) -> list[dict[str, Any]]:
        """Outils envoyés à Claude dans la langue courante."""
        return self._tools_by_language[current_language()]

    def _request(self, messages: list[dict]) -> dict[str, Any]:
        """Paramètres de l'appel (jamais de paramètre d'échantillonnage)."""
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": self._system_prompts[current_language()],
            "tools": self._tools,
            "messages": _to_anthropic(with_language_reminders(messages)),
        }
        if self._thinking == "between_tools":
            # Aucun autre champ n'est accepté ici : pas de drop_block possible.
            request["thinking"] = {"type": "between_tools"}
        else:
            # Réflexion adaptative (le défaut de l'API) ; un bloc invalidé par une
            # modification de l'historique est écarté au lieu de faire échouer.
            request["thinking"] = {
                "type": "adaptive",
                "block_binding": {"prefix_mismatch_behavior": "drop_block"},
            }
            request["betas"] = [THINKING_BINDING_BETA]
        if self._effort is not None:
            request["output_config"] = {"effort": self._effort}
        return request

    def respond(
        self, messages: list[dict], on_token: TokenCallback | None = None
    ) -> Plan:
        """Soumet l'historique à Claude en streaming et le convertit en :class:`Plan`.

        Le texte est diffusé fragment par fragment via ``on_token`` ; les blocs
        ``tool_use`` du message final deviennent des :class:`ToolCall` que
        l'orchestrator route vers le ``policy_engine`` puis les outils réels, et le
        contenu brut (``Plan.raw_content``) repart tel quel au tour suivant. Une
        réponse coupée ou refusée ne donne aucun appel d'outil (``Plan.interrupted``).
        Prompt et outils sont ceux de la langue de la requête.
        """
        started = time.monotonic()
        first_word: float | None = None
        narration_parts: list[str] = []
        with self._client.beta.messages.stream(**self._request(messages)) as stream:
            for fragment in stream.text_stream:
                if first_word is None:
                    first_word = time.monotonic() - started
                narration_parts.append(fragment)
                if on_token is not None:
                    on_token(fragment)
            final = stream.get_final_message()
        _log_response(final, self._model, first_word, time.monotonic() - started)

        narration = "".join(narration_parts).strip()
        stop = getattr(final, "stop_reason", None)
        if stop in _LENGTH_STOPS:
            return Plan(narration=narration, interrupted=tr(_CUT_NOTICE))
        if stop == "refusal":
            return Plan(narration=narration, interrupted=tr(_REFUSAL_NOTICE))
        content = list(getattr(final, "content", None) or [])
        tool_calls = [
            ToolCall(tool=block.name, args=dict(getattr(block, "input", {}) or {}))
            for block in content
            if getattr(block, "type", None) == "tool_use"
        ]
        return Plan(narration=narration, tool_calls=tool_calls, raw_content=content)


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
        screen_control: bool = True,
    ) -> None:
        self._model_name = model
        self._screen_control = screen_control
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
                else ClaudeClient(
                    api_key=key,
                    model=self._model_name,
                    screen_control=self._screen_control,
                )
            )
            self._fingerprint = fingerprint
        return self._cloud
