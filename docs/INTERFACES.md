# 🔌 Contrats d'interface — My_OS

> Ce document fige les **interfaces entre composants** pour que l'implémentation reste cohérente d'une session à l'autre. Ce sont des contrats de départ raisonnables ; ils peuvent évoluer, mais tout changement doit être répercuté ici **et** dans le code, jamais l'un sans l'autre.

---

## 1 · Protocole IPC (daemon ↔ popup)

Communication sur une **socket Unix locale**. Le daemon est le **serveur** (crée et écoute la socket), le popup est le **client**.

**Choix de cadrage retenu (jalon 1) :** JSON encodé en UTF-8, **un message par ligne**, `\n` comme délimiteur (pas de préfixe de longueur). Un message ne contient donc jamais de `\n` littéral non échappé.

**Chemin de la socket :** `$XDG_RUNTIME_DIR/myos.sock` si la variable est définie (cas normal en session systemd utilisateur), sinon repli sur `/run/user/<uid>/myos.sock`. Le chemin est résolu par `core/config.py` et partagé entre daemon et popup.

### Message popup → daemon
```json
{
  "type": "user_message",
  "id": "uuid-de-la-requête",
  "content": "range mes Téléchargements par type",
  "use_cloud": false
}
```

### Messages daemon → popup (streaming)
```json
{ "type": "token", "id": "uuid", "text": "fragment de réponse" }
{ "type": "tool_request", "id": "uuid", "tool": "move_file", "args": {...}, "risk_level": 1, "summary": "Déplacer 3 fichiers vers Images/" }
{ "type": "confirmation_needed", "id": "uuid", "request_id": "uuid-action", "tool": "delete_file", "args": {...}, "risk_level": 2, "summary": "..." }
{ "type": "done", "id": "uuid" }
{ "type": "error", "id": "uuid", "message": "..." }
```

### Réponse de confirmation popup → daemon
```json
{
  "type": "confirmation_response",
  "request_id": "uuid-action",
  "decision": "approve_once",   // approve_once | approve_scope | deny
  "scope": "this_folder"        // optionnel : this_file | this_folder | session
}
```

### Messages de contrôle daemon → popup
Le raccourci global est capté par le daemon ; c'est lui qui ordonne au popup (résident, caché) de s'afficher. Ce message de contrôle n'est pas lié à une requête, donc sans champ `id`.
```json
{ "type": "show" }   // le popup s'affiche centré, au-dessus de tout, et prend le focus
```
La fermeture (Échap) est gérée localement par le popup (il se cache) ; aucun message n'est requis vers le daemon.

### Note jalon 1 (socle)
Tant que ni modèle ni outils ne sont branchés, le daemon répond à un `user_message` par un `token` d'accusé de réception puis un `done`. C'est un **stub temporaire** du jalon 1, remplacé par l'orchestrateur réel au jalon 2.

---

## 2 · Classe de base des outils (`tools/base_tool.py`)

Tout outil hérite de `BaseTool`. Contrat minimal :

```python
class BaseTool:
    name: str                    # identifiant unique, ex. "move_file"
    description: str             # description pour le LLM
    risk_level: int              # 0..3, OBLIGATOIRE — sinon l'outil n'est pas chargé
    parameters: dict             # schéma JSON des arguments attendus

    def normalize_args(self, args: dict) -> dict:
        """Normalise les arguments AVANT évaluation des permissions.
        Appelé par l'orchestrator en amont de policy_engine.evaluate : la forme
        normalisée est donc vue par la blocklist, l'escalade, les grants ET run.
        Par défaut : identité. Les outils fichiers expansent ~ ici, pour qu'un
        chemin comme ~/.ssh soit bien détecté sensible (sinon il échapperait à
        l'escalade)."""
        return args

    def escalate(self, args: dict) -> int:
        """Renvoie le risk_level effectif selon les arguments.
        Ne peut QUE renvoyer >= self.risk_level (jamais en dessous).
        Par défaut : retourne self.risk_level."""
        return self.risk_level

    def run(self, args: dict) -> "ToolResult":
        """Exécute l'action. N'est appelé QUE si policy_engine a validé.
        Ne fait AUCUNE vérification de permission lui-même (séparation des responsabilités)."""
        ...
```

```python
@dataclass
class ToolResult:
    success: bool
    output: str                  # résultat lisible (résumé pour le LLM/l'UI)
    reversible: bool = False     # l'action peut-elle être annulée ?
    undo_data: dict | None = None  # info nécessaire pour annuler, si reversible
```

**Règle.** `run()` ne contrôle jamais les permissions — c'est le rôle exclusif du `policy_engine`. Un outil suppose qu'il a déjà été autorisé quand `run()` est appelé.

---

## 3 · Moteur de permissions (`permissions/policy_engine.py`)

```python
@dataclass
class Decision:
    action: str                  # "auto" | "confirm" | "blocked"
    risk_level: int
    summary: str                 # description lisible de l'action
    requires_elevation: bool     # nécessite polkit ?

def evaluate(tool: BaseTool, args: dict, grants: "SessionGrants") -> Decision:
    """Décide du sort d'une action.
    Ordre impératif :
      1. blocklist.is_blocked(tool, args) → si oui, action="blocked"
      2. level = tool.escalate(args)
      3. si grants couvre déjà cette action → action="auto"
      4. sinon level 0 → "auto" ; level 1/2 → "confirm" ; level 3 → "blocked"
    """
```

Le `policy_engine` est le **point de passage unique**. Aucune action n'atteint `tool.run()` sans une `Decision` avec `action in ("auto",)` ou une confirmation utilisateur explicite.

---

## 4 · Journal d'audit (`permissions/audit_log.py`)

Table SQLite `audit` dans `data/audit.db` :

| Colonne | Type | Description |
|---------|------|-------------|
| `id` | INTEGER PK | auto-incrément |
| `timestamp` | TEXT (ISO 8601) | date/heure de l'action |
| `tool` | TEXT | nom de l'outil |
| `args` | TEXT (JSON) | arguments (jamais de secret) |
| `risk_level` | INTEGER | niveau effectif après escalade |
| `decision` | TEXT | auto / approved / denied / blocked |
| `success` | INTEGER | 0/1, résultat d'exécution |
| `reversible` | INTEGER | 0/1 |

```python
def log(tool: str, args: dict, risk_level: int, decision: str,
        success: bool, reversible: bool) -> None: ...
```

**Règle.** L'entrée d'audit est écrite **avant** l'exécution pour les actions destructrices (trace même en cas de crash pendant l'action), puis mise à jour avec le résultat.

---

## 5 · Routeur de modèles (`models/cloud_router.py`)

```python
def generate(messages: list[dict], use_cloud: bool, stream: bool = True):
    """Aiguille vers le backend local (Qwen/Ollama) ou cloud (Claude).
    - use_cloud=False (défaut) → local_llm
    - use_cloud=True → vérifie qu'une clé existe (secrets.get_api_key()),
      sinon lève une erreur claire. Journalise l'envoi cloud.
    Renvoie un itérateur de fragments si stream=True."""
```

```python
# models/secrets.py
def get_api_key() -> str | None: ...   # via keyring, jamais depuis un fichier
def set_api_key(key: str) -> None: ...
def has_api_key() -> bool: ...
```

---

## 6 · Daemon (`daemon/orchestrator.py`)

Boucle agentique de traitement d'une requête :

```
1. Reçoit user_message (via ipc_server) ; initialise l'historique [user]
2. BOUCLE (jusqu'à réponse finale sans outil, ou MAX_STEPS) :
   a. plan = model.respond(historique, on_token)  → narration streamée au popup
   b. si plan.tool_calls est vide → réponse finale, on sort de la boucle
   c. pour chaque tool_call :
        - decision = policy_engine.evaluate(tool, normalize_args(args), grants)
        - "blocked" → journalise, message ; pas d'exécution
        - "confirm" → confirmation_needed au popup, attend la réponse
        - "auto"/confirmé → audit_log puis tool.run(args)
        - le résultat (succès/échec/blocage/refus) est réinjecté dans
          l'historique comme message role=tool (DONNÉE non fiable)
   d. reboucle : le modèle observe les résultats et décide de la suite
3. Stream les tokens vers le popup, puis "done"
```

Garde-fou : `MAX_STEPS` borne le nombre d'itérations (anti-boucle infinie).
Le contenu réinjecté est une **donnée**, jamais une instruction (SECURITY §2.2).

---

## 6.5 · Modèle injectable côté orchestrator (jalon 2)

Au jalon 2, l'orchestrator dépend d'un **modèle** par injection (testable
avec un mock). Le streaming `generate()` du §5 reste prévu pour la suite ;
le contrat minimal utilisé pour décider quels outils appeler est :

```python
@dataclass
class ToolCall:
    tool: str          # nom déclaré par l'outil (cf. BaseTool.name)
    args: dict         # arguments à passer à tool.run()

@dataclass
class Plan:
    narration: str            # texte du tour (peut être vide)
    tool_calls: list[ToolCall]  # actions de ce tour ; vide = réponse finale

TokenCallback = Callable[[str], None]

# Historique conversationnel (Message) :
#   {"role": "user", "content": str}
#   {"role": "assistant", "content": str, "tool_calls": list[ToolCall]}
#   {"role": "tool", "tool": str, "content": str}   # résultat réinjecté
Message = dict

class Model(Protocol):
    def respond(self, messages: list[Message], on_token: TokenCallback | None = None) -> Plan: ...
```

**Boucle agentique.** `respond` reçoit l'historique complet et renvoie le
`Plan` d'**un tour**. Un `Plan` sans `tool_calls` est une réponse finale (fin de
boucle). Sinon, l'orchestrator exécute les outils, réinjecte leurs résultats
(messages `role=tool`) et rappelle `respond` — jusqu'à `MAX_STEPS` (§6).

**Streaming.** Si `on_token` est fourni, le modèle l'appelle pour chaque
fragment de texte au fil de la génération (le popup l'affiche en direct).
`Plan.narration` reste le texte complet du tour (audit / tests). Un modèle qui
ignore `on_token` reste valide : repli qui envoie `Plan.narration` en une fois.
Le daemon stream chaque fragment au popup via un message `token` (cf. §1).

L'orchestrator dépend aussi d'un fournisseur de confirmation injectable
(pour découpler l'attente bloquante du transport IPC, et permettre les tests) :

```python
class ConfirmationProvider(Protocol):
    def ask(self, payload: dict) -> ConfirmationResponse: ...
```

En production, l'implémentation envoie `payload` (un `confirmation_needed`
construit par `permissions.confirmation.build_confirmation_needed`) sur la
socket, attend le `confirmation_response` côté daemon et le parse avec
`parse_confirmation_response`. En tests, on injecte un stub.

## 7 · Règle d'or

Quand un contrat ci-dessus est insuffisant pour coder, **étendre ce document d'abord**, puis implémenter. Ne jamais laisser deux modules diverger sur un format de message ou une signature.
