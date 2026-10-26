"""Client Ollama pour un modèle local (Qwen 3.5 par défaut).

Implémente le protocole :class:`daemon.orchestrator.Model` en s'appuyant sur
le mécanisme de **tool calling** d'Ollama : le modèle reçoit un schéma JSON
des outils fichiers et renvoie des appels structurés (``tool_calls``), que
l'orchestrator route ensuite vers le ``policy_engine`` puis vers les outils
réels.

Pour l'activer côté VM :

.. code-block:: bash

   ollama pull qwen3.5:4b
   # puis démarrer myosd avec OllamaClient injecté à la place de StubRuleModel

Sécurité (cf. SECURITY §2.2) : le contenu de fichiers lu par le LLM via
``read_file`` est une **donnée** ; le prompt système le rappelle pour
limiter l'injection de prompt indirecte. Les permissions restent appliquées
côté orchestrator quelle que soit la « décision » du modèle.

**Langue.** Un prompt système par langue (:mod:`models.prompts`), et les
descriptions des outils traduites (:func:`tools_schema`) : le modèle reçoit ceux de
la langue de la requête (:func:`core.i18n.current_language`). Le prompt français est
le prompt d'origine, inchangé.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.i18n import N_, SUPPORTED_LANGUAGES, current_language, tr, translate
from daemon.orchestrator import Plan, TokenCallback, ToolCall
from models.prompts import system_prompt

if TYPE_CHECKING:
    import ollama


# Adresse d'Ollama quand ni la config ni OLLAMA_HOST n'en donnent une.
DEFAULT_HOST = "http://127.0.0.1:11434"

# « localhost » en position d'hôte, avec ou sans schéma ni identifiants
# (localhost, localhost:11434, http://localhost:11434, http://u:p@localhost…).
_LOCALHOST = re.compile(
    r"^((?:[a-z][a-z0-9+.-]*://)?(?:[^@/]*@)?)localhost(?=[:/]|$)", re.IGNORECASE
)


def _ollama_host(host: str | None) -> str:
    """Adresse d'Ollama : ``host`` (config), sinon ``OLLAMA_HOST``, sinon
    :data:`DEFAULT_HOST`, dans le même ordre que la lib ``ollama``.

    Un hôte « localhost » devient 127.0.0.1 : Ollama n'écoute qu'en IPv4 par
    défaut, et « localhost » se résout d'abord en ::1, soit une connexion refusée
    avant chaque requête. Sous Linux, refusée aussitôt, elle coûte moins d'1 ms
    (mesuré en VM) ; sous Windows, ≈ 2 s par requête (mesuré dans My_AI). Toute
    autre adresse (Ollama sur une autre machine, IPv6 explicite) reste telle quelle.
    """
    host = (host or "").strip() or os.environ.get("OLLAMA_HOST", "").strip()
    if not host:
        return DEFAULT_HOST
    return _LOCALHOST.sub(r"\g<1>127.0.0.1", host, count=1)


def _make_ollama_client(host: str | None) -> Any:
    """Instancie un client Ollama. Import paresseux : la dépendance ``ollama``
    n'est requise qu'ici, pas pour importer le module (mode stub / tests)."""
    import ollama

    return ollama.Client(host=_ollama_host(host))


def _to_ollama(messages: list[dict], *, with_image: bool = False) -> list[dict]:
    """Traduit l'historique générique de l'orchestrator au format chat d'Ollama.

    Les ``tool_calls`` de l'assistant **doivent** être reconstruits : sans la
    correspondance assistant(tool_calls) → tool(result), le template de chat de
    Qwen devient incohérent et le modèle génère des appels malformés (erreur de
    parsing côté Ollama). On renvoie donc :

    - ``user`` : ``{"role": "user", "content": ...}`` ;
    - ``assistant`` : contenu + ``tool_calls`` au format Ollama si présents ;
    - ``tool`` : ``{"role": "tool", "content": <résultat>, "tool_name": <outil>}``,
      avec la capture d'écran du résultat (``images``) si ``with_image`` : le
      gabarit de Qwen 3.5 la place dans la réponse de l'outil (vérifié avec
      Ollama 0.40.6 : +922 tokens, texte de l'image lu par le modèle).
    """
    out: list[dict] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content", ""))
        if role == "assistant":
            entry: dict = {"role": "assistant", "content": content}
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                entry["tool_calls"] = [
                    {"function": {"name": call.tool, "arguments": call.args}}
                    for call in tool_calls
                ]
            out.append(entry)
        elif role == "tool":
            entry = {
                "role": "tool",
                "content": content,
                "tool_name": str(message.get("tool", "")),
            }
            image = message.get("image")
            if with_image and isinstance(image, bytes):
                entry["images"] = [image]  # encodé en base64 par la lib ollama
            out.append(entry)
        elif role == "user":
            out.append({"role": "user", "content": content})
    return out


DEFAULT_MODEL = "qwen3.5:4b"

# Fenêtre de contexte demandée à Ollama, la même à chaque requête (il recharge le
# modèle quand elle change). Celle d'Ollama par défaut, 4 096 tokens, déborde vite :
# le prompt système et les schémas d'outils en prennent déjà ~2 430.
DEFAULT_NUM_CTX = 16384

# Prompt lu par lots de 256 tokens. qwen3.5 étant hybride, llama.cpp ne reprend un
# prompt qu'à un point de sauvegarde posé un lot avant la fin du précédent : une
# nouvelle conversation relit ~250 tokens du prompt système, au lieu de ~1 000 avec
# les lots de 1 024 qu'Ollama prend dès 8 192 tokens de fenêtre (médiane 10 s au lieu
# de 30 s sans GPU, VM 8 cœurs). Mesuré en alternance : un long prompt ne se lit pas
# plus lentement.
_NUM_BATCH = 256

# Durée pendant laquelle Ollama garde le modèle chargé après une requête. Son défaut,
# 5 min, le décharge entre deux usages espacés : la requête suivante relit alors tout
# le prompt système et les outils (~2 430 tokens, près d'une minute sans GPU).
DEFAULT_KEEP_ALIVE = "1h"

# Taille estimée sans tokenizer : ~3,5 caractères par token mesurés sur le prompt
# de My_OS (français, schémas JSON) ; 3 garde une marge.
_CHARS_PER_TOKEN = 3
# Balises du gabarit de chat autour de chaque message (rôle, fin de tour).
_MESSAGE_OVERHEAD = 8
# Remplace un ancien résultat d'outil de la requête en cours qui ne tient plus.
_ELIDED_RESULT = N_(
    "[résultat déjà exploité, retiré pour tenir dans la fenêtre de contexte]"
)

# Capture d'écran (outils de l'écran) envoyée au modèle local : ~1 000 tokens pour
# 1280×720 (un token par carré de 32 px) ; mesuré +922 avec Ollama 0.40.6.
IMAGE_TOKENS = 1100
VISION_MODES = ("auto", "on", "off")
# Vision « auto » : la capture n'est jointe que si le modèle tourne assez vite pour
# qu'elle coûte peu, c'est-à-dire sur une vraie carte graphique. Mesuré en labo
# (28 tâches, 6 écrans de l'ISO) : la liste numérotée seule trouve le bon élément
# 27 fois sur 28 en 2,6 s par étape, avec la capture 28 sur 28 mais 10,8 s sur
# l'iGPU Intel Arc ; sur processeur, bien plus. Le critère est la vitesse de
# GÉNÉRATION (jamais en cache) : ~10 tokens/s sur processeur ou iGPU, 40 à 150 sur
# une carte dédiée. La vitesse de lecture du prompt ne convient pas : avec le cache,
# Ollama 0.40 compte tout le prompt mais ne chronomètre que la part relue (vu en
# labo : jusqu'à ~2 600 tokens/s « mesurés » sur l'iGPU).
VISION_MIN_GENERATION_RATE = 30.0  # tokens/s
# Mesure retenue seulement sur assez de tokens générés (sinon, surtout du temps fixe).
_RATE_MIN_TOKENS = 16

# Réponse vide alors que le modèle a généré au moins ces tokens : Ollama n'a pas su
# la lire (appel d'outil mal formé). Une seule nouvelle demande, avec cette note.
_UNREADABLE_MIN_TOKENS = 24
_UNREADABLE_NOTE = N_(
    "[Note de My_OS : ta réponse précédente n'a pas pu être lue (appel d'outil mal "
    "formé, souvent un texte trop long). Recommence ; un long texte se tape en "
    "plusieurs appels type_text plus courts.]"
)


# Rappel de langue d'une demande, dans la langue de cette demande (traduit) : cf.
# with_language_reminders.
_LANGUAGE_REMINDER = N_("(Réponds en français.)")


class UnreadableResponse(RuntimeError):
    """Le modèle a répondu, mais Ollama n'a pu en extraire ni texte ni outil."""


def with_language_reminders(messages: list[dict]) -> list[dict]:
    """Conversation qui a changé de langue : chaque demande rappelle la sienne.

    Un petit modèle poursuit dans la langue des tours précédents même quand le prompt
    système demande l'autre : mesuré avec qwen3.5:4b, après une demande en français,
    3 réponses en français sur 25 à une demande en anglais, et une règle de plus dans
    le prompt n'y change presque rien (1 sur 15). Avec le rappel en fin de chaque
    demande : 25 sur 25 dans chaque sens.

    Le rappel n'est ajouté que si plusieurs langues se côtoient (une conversation
    dans une seule langue est envoyée telle quelle), et à chaque demande dans SA
    langue : ce qui est renvoyé d'une requête à l'autre ne change pas, Ollama
    reprend ce qu'il a déjà lu. L'historique du daemon n'est pas modifié.
    """
    users = [m for m in messages if m.get("role") == "user"]
    if len({m["language"] for m in users if m.get("language")}) < 2:
        return messages
    reminded = []
    for message in messages:
        if message.get("role") == "user" and message.get("language"):
            note = translate(_LANGUAGE_REMINDER, str(message["language"]))
            message = {**message, "content": f"{message.get('content', '')}\n\n{note}"}
        reminded.append(message)
    return reminded


def _estimate_tokens(text: str) -> int:
    """Nombre de tokens d'un texte, estimé par excès (cf. ``_CHARS_PER_TOKEN``)."""
    return len(text) // _CHARS_PER_TOKEN + 1


def _message_tokens(message: dict, *, with_image: bool = False) -> int:
    """Tokens estimés d'un message de l'historique, appels d'outils compris
    (et sa capture d'écran si elle est envoyée)."""
    tokens = _estimate_tokens(str(message.get("content", ""))) + _MESSAGE_OVERHEAD
    for call in message.get("tool_calls") or []:
        tokens += _estimate_tokens(
            call.tool + json.dumps(call.args, ensure_ascii=False)
        )
    if with_image and message.get("image"):
        tokens += IMAGE_TOKENS
    return tokens


def _cap_tool_output(message: dict, max_chars: int) -> dict:
    """Le message tel qu'envoyé : un résultat d'outil garde au plus ``max_chars``
    caractères, et la coupure est signalée au modèle."""
    content = str(message.get("content", ""))
    if message.get("role") != "tool" or len(content) <= max_chars:
        return message
    note = tr(
        "\n[... résultat tronqué pour la fenêtre de contexte : {kept} caractères "
        "sur {total} ...]",
        kept=max_chars,
        total=len(content),
    )
    return {**message, "content": content[:max_chars] + note}


# Note de backend « local » ajoutée au prompt de base pour Ollama : rappelle que
# rien ne sort de la machine (le pendant cloud vit dans models.cloud_router).
_LOCAL_NOTES = {
    "fr": (
        "\n\nTu tournes en LOCAL (modèle Qwen via Ollama) : les données de "
        "l'utilisateur restent sur sa machine, rien n'est envoyé à l'extérieur."
    ),
    "en": (
        "\n\nYou run LOCALLY (Qwen model via Ollama): the user's data stays on "
        "their machine, nothing is sent outside."
    ),
}


# Schémas JSON Schema des outils fichiers du jalon 2. Conformes au format
# tool-calling utilisé par Ollama (chat.tools).
FILE_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": N_("Lit le contenu d'un fichier texte."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": N_("Chemin absolu du fichier"),
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": N_("Liste le contenu d'un répertoire."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": N_("Chemin absolu du répertoire"),
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": N_("Écrit (ou écrase) un fichier avec un contenu donné."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": N_("Chemin absolu")},
                    "content": {"type": "string", "description": N_("Contenu UTF-8")},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": N_("Crée un fichier vide (échoue s'il existe déjà)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": N_("Chemin absolu")}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file",
            "description": N_(
                "Déplace un fichier ou un dossier d'une source vers une destination."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {
                        "type": "string",
                        "description": N_("Chemin absolu source"),
                    },
                    "dst": {
                        "type": "string",
                        "description": N_("Chemin absolu destination"),
                    },
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": N_("Supprime un fichier (refuse les répertoires)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": N_("Chemin absolu du fichier"),
                    }
                },
                "required": ["path"],
            },
        },
    },
]


# Schémas des outils processus du jalon 3.
PROCESS_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": N_(
                "Liste les processus en cours, triés par mémoire (défaut) ou CPU. "
                "Utile pour voir ce qui consomme les ressources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {
                        "type": "string",
                        "enum": ["memory", "cpu"],
                        "description": N_("Critère de tri (défaut : memory)"),
                    },
                    "limit": {
                        "type": "integer",
                        "description": N_("Nombre de processus à afficher (défaut 10)"),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kill_process",
            "description": N_("Termine un processus identifié par son PID."),
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "integer",
                        "description": N_("Identifiant du processus à terminer"),
                    }
                },
                "required": ["pid"],
            },
        },
    },
]


# Schémas des outils paquets (pacman) du jalon 3.
PACKAGE_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_package",
            "description": N_(
                "Recherche des paquets dans les dépôts officiels. Les descriptions "
                "des paquets sont en anglais : cherche avec des mots-clés EN "
                "ANGLAIS (ex. « video player » pour un lecteur vidéo)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": N_(
                            "1 à 5 mots-clés en anglais, sans accents, séparés "
                            "par des espaces (ex. 'video player')"
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_package",
            "description": N_("Installe un paquet depuis les dépôts officiels."),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": N_("Nom exact du paquet à installer"),
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_package",
            "description": N_(
                "Désinstalle un paquet (et ses dépendances devenues orphelines)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": N_("Nom exact du paquet à désinstaller"),
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_system",
            "description": N_(
                "Met à jour la liste des paquets et tout le système installé."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# Schémas des outils de réglages système (D-Bus / pactl) du jalon 3.
SETTINGS_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "set_brightness",
            "description": N_(
                "Règle la luminosité de l'écran en pourcentage (0 à 100)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "integer",
                        "description": N_("Niveau de luminosité voulu, 0 à 100"),
                    }
                },
                "required": ["percent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": N_("Règle le volume du son en pourcentage (0 à 100)."),
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "integer",
                        "description": N_("Niveau de volume voulu, 0 à 100"),
                    }
                },
                "required": ["percent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_mute",
            "description": N_("Coupe (true) ou rétablit (false) le son."),
            "parameters": {
                "type": "object",
                "properties": {
                    "muted": {
                        "type": "boolean",
                        "description": N_(
                            "true pour couper le son, false pour le rétablir"
                        ),
                    }
                },
                "required": ["muted"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_wifi",
            "description": N_("Active (true) ou désactive (false) le Wi-Fi."),
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": N_("true pour activer, false pour désactiver"),
                    }
                },
                "required": ["enabled"],
            },
        },
    },
]


def _function(
    name: str, description: str, properties: dict, required: list[str]
) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_ELEMENT = {
    "type": "integer",
    "description": N_("Numéro [N] de la dernière observation"),
}

# Schémas des outils du contrôle de l'ordinateur (descriptions courtes : ils sont
# relus à chaque chargement du modèle).
COMPUTER_TOOLS_SCHEMA: list[dict[str, Any]] = [
    _function(
        "observe_screen",
        N_(
            "Regarde l'écran : fenêtres [wN] et éléments visibles de la fenêtre "
            "active, numérotés [N]."
        ),
        {},
        [],
    ),
    _function(
        "click",
        N_("Clique sur un élément de la dernière observation."),
        {
            "element": _ELEMENT,
            "double": {
                "type": "boolean",
                "description": N_("Double-clic (ouvrir un fichier, une icône)"),
            },
            "button": {
                "type": "string",
                "enum": ["left", "right"],
                "description": N_("right : menu contextuel"),
            },
        },
        ["element"],
    ),
    _function(
        "type_text",
        N_("Tape un texte au clavier, dans l'élément donné ou là où est le curseur."),
        {
            "text": {"type": "string", "description": N_("Texte à taper")},
            "element": _ELEMENT,
            "enter": {"type": "boolean", "description": N_("Appuyer sur Entrée après")},
        },
        ["text"],
    ),
    _function(
        "press_keys",
        N_(
            "Appuie sur une touche ou une combinaison : « ctrl+s », « Entrée », "
            "« Échap », « alt+F4 », « Tab »."
        ),
        {
            "keys": {"type": "string", "description": N_("Combinaison")},
            "repeat": {
                "type": "integer",
                "description": N_("Nombre d'appuis (défaut 1)"),
            },
        },
        ["keys"],
    ),
    _function(
        "scroll",
        N_("Fait défiler la fenêtre active (ou un élément)."),
        {
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "description": N_("Crans (défaut 3)")},
            "element": _ELEMENT,
        },
        ["direction"],
    ),
    _function(
        "open_app",
        N_(
            "Ouvre une application installée par son nom (« Firefox », « Mousepad », "
            "« gestionnaire de fichiers ») ; déjà ouverte, passe à sa fenêtre."
        ),
        {"name": {"type": "string", "description": N_("Nom de l'application")}},
        ["name"],
    ),
    _function(
        "focus_window",
        N_("Passe à une fenêtre ouverte [wN] de la dernière observation."),
        {"window": {"type": "integer", "description": N_("Numéro N de [wN]")}},
        ["window"],
    ),
]


# Outils sans le contrôle de l'écran (ComputerConfig.enabled = false).
BASE_TOOLS_SCHEMA: list[dict[str, Any]] = [
    *FILE_TOOLS_SCHEMA,
    *PROCESS_TOOLS_SCHEMA,
    *PACKAGE_TOOLS_SCHEMA,
    *SETTINGS_TOOLS_SCHEMA,
]

# Schéma complet envoyé au modèle : tous les outils câblés dans le daemon.
# (La duplication outil Python ↔ schéma JSON ↔ table risk_levels est connue ;
# centraliser est un chantier à part — cf. plan jalon 3.)
ALL_TOOLS_SCHEMA: list[dict[str, Any]] = [*BASE_TOOLS_SCHEMA, *COMPUTER_TOOLS_SCHEMA]


def _localized(value: Any, language: str) -> Any:
    """Copie d'un schéma d'outils dont chaque ``description`` est traduite."""
    if isinstance(value, dict):
        return {
            key: (
                translate(item, language)
                if key == "description" and isinstance(item, str)
                else _localized(item, language)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_localized(item, language) for item in value]
    return value


def tools_schema(language: str, *, screen_control: bool = True) -> list[dict[str, Any]]:
    """Schéma des outils envoyé au modèle, descriptions dans ``language``.

    Mêmes noms et paramètres dans toutes les langues : seules les descriptions
    changent. Sans contrôle de l'ordinateur, les outils de l'écran sont retirés.
    """
    schema = ALL_TOOLS_SCHEMA if screen_control else BASE_TOOLS_SCHEMA
    return _localized(schema, language)


@dataclass(frozen=True)
class _Prompt:
    """Ce qui est envoyé au modèle local dans une langue, et la place qu'il laisse."""

    system: str
    tools: list[dict[str, Any]]
    budget: int  # tokens laissés à l'historique
    tool_max_chars: int  # longueur max d'un résultat d'outil envoyé


class OllamaClient:  # pylint: disable=too-many-instance-attributes
    """Implémentation :class:`daemon.orchestrator.Model` via Ollama HTTP local.

    Un prompt (système + outils) par langue, préparé une fois : celui de la langue
    de la requête est envoyé (cf. :func:`core.i18n.current_language`).
    """

    name = "ollama"

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        model: str = DEFAULT_MODEL,
        host: str | None = None,
        client: ollama.Client | None = None,
        think: bool = False,
        num_ctx: int = DEFAULT_NUM_CTX,
        keep_alive: str | int = DEFAULT_KEEP_ALIVE,
        *,
        screen_control: bool = True,
        vision: str = "auto",
    ) -> None:
        self._model = model
        self._think = think
        self._keep_alive = keep_alive
        self._client = client if client is not None else _make_ollama_client(host)
        # Prompt de chaque langue, une fois : base (+ section de l'écran si ses
        # outils sont actifs) + note « local » + contexte réel de la machine.
        self._prompts = {
            language: self._prompt(language, num_ctx, screen_control)
            for language in SUPPORTED_LANGUAGES
        }
        self._options = {"num_ctx": num_ctx, "num_batch": _NUM_BATCH}
        # Capture d'écran jointe au modèle (cf. VISION_MIN_GENERATION_RATE).
        self._vision = vision if vision in VISION_MODES else "auto"
        self._generation_rate: float | None = None  # tokens/s générés (Ollama)
        # Premier message de l'historique encore envoyé (cf. _fit_to_window).
        self._first_sent: dict | None = None

    @staticmethod
    def _prompt(language: str, num_ctx: int, screen_control: bool) -> _Prompt:
        """Prompt et outils d'une langue, et la part de la fenêtre qu'ils laissent."""
        system = system_prompt(
            language, screen_control=screen_control, backend_note=_LOCAL_NOTES[language]
        )
        tools = tools_schema(language, screen_control=screen_control)
        # Part de la fenêtre laissée à l'historique : ni le prompt système et les
        # outils, envoyés à chaque requête, ni la place gardée pour la réponse.
        fixed = _estimate_tokens(system) + _estimate_tokens(
            json.dumps(tools, ensure_ascii=False)
        )
        answer = min(max(num_ctx // 8, 512), 2048)
        budget = max(num_ctx - fixed - answer, 0)
        # Un résultat d'outil : au plus un quart de la fenêtre (comme les documents
        # dans My_AI), et jamais plus de la moitié du budget.
        tool_max_chars = min(num_ctx // 4, budget // 2) * _CHARS_PER_TOKEN
        return _Prompt(system, tools, budget, tool_max_chars)

    def wants_images(self) -> bool:
        """``True`` si la capture d'écran est jointe au modèle en ce moment.

        ``on`` / ``off`` : toujours / jamais. ``auto`` : seulement si la dernière
        vitesse de génération mesurée par Ollama atteint
        :data:`VISION_MIN_GENERATION_RATE` (carte graphique) ; tant que rien n'est
        mesuré, pas d'image.
        """
        if self._vision != "auto":
            return self._vision == "on"
        return (
            self._generation_rate is not None
            and self._generation_rate >= VISION_MIN_GENERATION_RATE
        )

    def _record_rate(self, chunk: Any) -> None:
        """Vitesse de génération, depuis le dernier fragment d'Ollama."""
        count = getattr(chunk, "eval_count", None)
        duration = getattr(chunk, "eval_duration", None)
        if not isinstance(count, int) or not isinstance(duration, int) or duration <= 0:
            return
        if count >= _RATE_MIN_TOKENS:
            self._generation_rate = count / (duration / 1e9)

    def _fit_to_window(
        self, messages: list[dict], prompt: _Prompt, *, with_image: bool = False
    ) -> list[dict]:
        """La part de l'historique envoyée au modèle : celle qui tient dans sa fenêtre.

        Sans elle, Ollama coupe lui-même le DÉBUT d'un prompt trop long (prompt
        système, règles et outils compris) et la réponse s'arrête net quand la
        fenêtre est pleine. Ici :

        - chaque résultat d'outil est plafonné, coupure signalée au modèle ;
        - au-delà du budget, les plus anciens tours partent en entier, jusqu'à la
          moitié du budget : le début de ce qui est envoyé reste ensuite le même
          d'une requête à l'autre, et Ollama reprend ce qu'il a déjà lu au lieu de
          tout relire ;
        - si la requête en cours dépasse seule le budget, ses plus anciens
          résultats d'outils sont remplacés par une mention (le dernier reste) ;
        - une capture d'écran jointe compte pour :data:`IMAGE_TOKENS`.

        L'historique du daemon n'est jamais modifié : seul l'envoi est ajusté.
        """
        sent = [_cap_tool_output(m, prompt.tool_max_chars) for m in messages]
        sizes = [_message_tokens(m, with_image=with_image) for m in sent]
        turns = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        current = turns[-1] if turns else 0
        start = next((i for i, m in enumerate(messages) if m is self._first_sent), 0)
        if sum(sizes[start:]) > prompt.budget:
            for index in (i for i in turns if start < i <= current):
                start = index
                if sum(sizes[start:]) <= prompt.budget // 2:
                    break
        self._first_sent = messages[start] if start < len(messages) else None

        kept, total = sent[start:], sum(sizes[start:])
        tools = [i for i, m in enumerate(kept) if m.get("role") == "tool"]
        for i in tools[:-1]:
            if total <= prompt.budget:
                break
            elided = {**kept[i], "content": tr(_ELIDED_RESULT)}
            elided.pop("image", None)
            total -= _message_tokens(kept[i], with_image=with_image) - _message_tokens(
                elided
            )
            kept[i] = elided
        return kept

    def respond(
        self, messages: list[dict], on_token: TokenCallback | None = None
    ) -> Plan:
        """Soumet l'historique au modèle en streaming et le convertit en :class:`Plan`.

        Le system prompt est préfixé à chaque appel ; l'historique générique
        (user/assistant/tool), ajusté à la fenêtre de contexte, est traduit au
        format Ollama. Le texte est diffusé fragment par fragment via ``on_token`` ;
        les appels d'outils sont accumulés (Ollama les fournit généralement dans le
        dernier fragment).

        Réponse illisible : si le modèle a généré du texte mais qu'Ollama ne renvoie
        ni texte ni appel d'outil (appel mal formé, vu en VM avec un ``type_text``
        de ~800 tokens), la demande est refaite une fois avec une note qui le
        signale ; une seconde réponse illisible lève :class:`UnreadableResponse`
        (l'orchestrator l'affiche en erreur, jamais un silence).

        Prompt, outils et notes sont ceux de la langue de la requête.
        """
        prompt = self._prompts[current_language()]
        with_image = self.wants_images()
        kept = self._fit_to_window(messages, prompt, with_image=with_image)
        sent = [
            {"role": "system", "content": prompt.system},
            *_to_ollama(with_language_reminders(kept), with_image=with_image),
        ]
        plan, generated = self._stream(sent, prompt.tools, on_token)
        if plan.narration or plan.tool_calls or generated < _UNREADABLE_MIN_TOKENS:
            return plan
        retry, generated = self._stream(
            [*sent, {"role": "user", "content": tr(_UNREADABLE_NOTE)}],
            prompt.tools,
            on_token,
        )
        if retry.narration or retry.tool_calls or generated < _UNREADABLE_MIN_TOKENS:
            return retry
        raise UnreadableResponse(
            tr(
                "réponse illisible (appel d'outil mal formé, deux fois de suite) : "
                "reformulez ou découpez la demande"
            )
        )

    def _stream(
        self,
        sent: list[dict],
        tools: list[dict[str, Any]],
        on_token: TokenCallback | None,
    ) -> tuple[Plan, int]:
        """Un appel au modèle : ``(plan, nombre de tokens générés)``."""
        stream = self._client.chat(
            model=self._model,
            messages=sent,
            tools=tools,
            stream=True,
            think=self._think,
            options=self._options,
            keep_alive=self._keep_alive,
        )
        narration_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        generated = 0
        for chunk in stream:
            message = chunk.message
            fragment = message.content or ""
            if fragment:
                narration_parts.append(fragment)
                if on_token is not None:
                    on_token(fragment)
            for call in message.tool_calls or []:
                tool_calls.append(
                    ToolCall(
                        tool=call.function.name,
                        args=dict(call.function.arguments or {}),
                    )
                )
            if getattr(chunk, "done", False):
                self._record_rate(chunk)
                count = getattr(chunk, "eval_count", None)
                generated = count if isinstance(count, int) else 0
        plan = Plan(narration="".join(narration_parts).strip(), tool_calls=tool_calls)
        return plan, generated
