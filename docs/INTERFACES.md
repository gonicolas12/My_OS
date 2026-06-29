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
{ "type": "confirmation_needed", "id": "uuid", "request_id": "uuid-action", "tool": "delete_file", "args": {...}, "risk_level": 2, "summary": "...", "requires_elevation": false }
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

### Message de contrôle popup → daemon (reset)
L'orchestrator garde la conversation **en mémoire entre les requêtes** (cf. §6).
Pour repartir d'une conversation vierge, le popup envoie un message de contrôle
``reset`` (déclenché par le raccourci `Ctrl+L`). Il n'est lié à aucune requête,
donc sans champ `id`. Le popup vide aussi son affichage, pour rester cohérent
avec le daemon.
```json
{ "type": "reset" }   // le daemon oublie l'historique conversationnel courant
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

    def requires_elevation(self, args: dict) -> bool:
        """True si l'action nécessite root (polkit). ORTHOGONAL au risk_level :
        pacman -S est niveau 1 mais exige root ; delete_file est niveau 2 sans
        root. Lu par le policy_engine pour Decision.requires_elevation ;
        l'élévation réelle est faite à l'exécution par core.elevation.run_command.
        Par défaut : False."""
        return False

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
      2. level = max(tool.escalate(args), tool.risk_level)
      3. requires_elevation = (level == 2) or tool.requires_elevation(args)
      4. si grants couvre déjà cette action → action="auto"
      5. sinon level 0 → "auto" ; level 1/2 → "confirm" ; level 3 → "blocked"
    """
```

Le `policy_engine` est le **point de passage unique**. Aucune action n'atteint `tool.run()` sans une `Decision` avec `action in ("auto",)` ou une confirmation utilisateur explicite.

**Élévation (`requires_elevation`).** Ce drapeau est **orthogonal au niveau de risque** : il vaut vrai si l'action est de niveau 2 (sensible) *ou* si l'outil déclare avoir besoin de root via `tool.requires_elevation(args)`. Exemple : `install_package` (pacman `-S`) est de **niveau 1** (confirmation simple) mais `requires_elevation=True` (root requis). À l'inverse `delete_file` est niveau 2 sans élévation réelle. Le drapeau est purement consultatif (UI + payload `confirmation_needed`) ; **l'élévation effective** est réalisée à l'exécution par l'outil via `core.elevation.run_command(..., elevate=True)` (cf. §8), jamais par le daemon lui-même qui reste utilisateur.

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

## 5 · Routeur de modèles (`models/cloud_router.py`) — jalon 4

### 5.1 · Secrets (`models/secrets.py`)

```python
# models/secrets.py — clé API cloud via le trousseau OS (keyring), JAMAIS un fichier.
SERVICE_NAME = "my_os"
API_KEY_NAME = "anthropic_api_key"

def get_api_key() -> str | None: ...   # None si absente OU si le trousseau échoue
def set_api_key(key: str) -> None: ...  # ValueError si clé vide
def has_api_key() -> bool: ...
def delete_api_key() -> None: ...        # idempotent
```

La clé n'apparaît **jamais** dans `config.yaml`, les logs, ni l'audit
(cf. SECURITY menace 4). Le **popup** écrit la clé directement dans le trousseau
via `set_api_key` ; le **daemon** la lit via `get_api_key`. Le secret ne transite
donc **pas** par l'IPC (pas de message IPC pour la clé). Toute erreur de trousseau
(backend absent/verrouillé) est traitée comme « pas de clé » → repli local, jamais
de crash.

### 5.2 · Backend cloud = un `Model` (tool use Anthropic)

**Décision (résolution de la divergence de contrat).** Le backend Claude
implémente le **même protocole `Model`** que le local (cf. §6.5 :
`respond(messages, on_token) -> Plan`), avec le **tool use Anthropic**. L'IA cloud
pilote donc les outils par la *même* boucle agentique et le *même*
`policy_engine` — un seul chemin de code, un seul choke point de sécurité. Le LLM
cloud n'a **aucun droit supplémentaire** par rapport au local.

> L'ancienne signature `generate(messages, use_cloud, stream) -> itérateur` est
> **remplacée** par ce contrat (un `Model` + un sélecteur), pour ne pas dupliquer
> la boucle agentique ni contourner les permissions.

```python
class ClaudeClient:                       # implémente le protocole Model (§6.5)
    name = "claude"
    def __init__(self, *, api_key: str | None = None,
                 model: str = "claude-sonnet-4-6",
                 max_tokens: int = 4096,
                 client=None): ...         # `anthropic` importé paresseusement
    def respond(self, messages, on_token=None) -> Plan: ...  # streaming via on_token

class CloudUnavailable(RuntimeError): ...  # cloud demandé mais pas de clé

class CloudRouter:                         # sélecteur de backend cloud (lazy)
    def __init__(self, *, model: str = "claude-sonnet-4-6",
                 client_factory=None): ...  # client_factory injectable (tests)
    def is_available(self) -> bool: ...     # = secrets.has_api_key()
    def get_cloud_model(self) -> Model: ... # construit/cache le ClaudeClient ;
                                            # CloudUnavailable si pas de clé
```

### 5.3 · Routage **par requête** (côté orchestrator)

Le cloud est **opt-in, par requête** : le message IPC `user_message` porte
`use_cloud` (§1). L'`Orchestrator` reçoit un `cloud_router: ModelRouter | None`
injecté (optionnel ; absent → toujours local). À chaque `user_message` :

```
1. use_cloud=False (défaut)                 → modèle local.
2. use_cloud=True et clé présente           → modèle cloud + indicateur + log.
3. use_cloud=True mais pas de clé / pas de
   routeur                                  → REPLI LOCAL + notice claire au popup
                                              (jamais de cloud silencieux).
```

Le backend est **résolu une seule fois par `user_message`** (jamais changé en
plein milieu de la boucle agentique). L'historique conversationnel **partagé**
(§6) est envoyé tel quel au backend choisi — le même historique peut donc partir
tantôt au local, tantôt au cloud (c'est voulu).

### 5.4 · Journalisation des envois cloud

Chaque envoi cloud est journalisé via un **logger dédié `myosd.cloud`**
(horodatage, nom du modèle, **nombre de messages et de caractères** transmis) —
**jamais** la clé, **jamais** le contenu. Le canal est le logger (journald sous
systemd), distinct du **journal d'audit** (`data/audit.db`), réservé aux actions
d'outils et décisions de permission (§4). La minimisation des données envoyées au
cloud est traitée en §6 et SECURITY menace 2.

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

**Mémoire conversationnelle.** L'historique (user/assistant/tool) est **conservé
entre les requêtes**, en mémoire côté daemon (l'`Orchestrator`), et non
reconstruit par le popup — le cœur de confiance possède l'état (cohérent avec
les grants/audit). Chaque `user_message` est *ajouté* à cet historique ; le
modèle « se souvient » donc des tours précédents. L'historique est **borné**
(`MAX_HISTORY_MESSAGES`, élagage à une frontière de tour pour préserver les
paires assistant↔tool) afin de limiter le contexte et — au jalon 4 — le volume
envoyé au cloud. Le message de contrôle `reset` (§1) le vide. Rien n'est
persisté sur disque : la mémoire disparaît à l'arrêt du daemon (cf. ARCHITECTURE §7).

**Mémoire et cloud (jalon 4).** Quand `use_cloud=True`, c'est **tout l'historique
persistant courant** (borné par `MAX_HISTORY_MESSAGES`) qui part au backend cloud,
pas seulement le dernier message — conséquence directe de la mémoire partagée. Choix
de minimisation retenu (cf. SECURITY menace 2) : on **conserve** l'historique partagé
(cohérence local/cloud voulue), mais l'envoi est **visible** (indicateur popup +
notice in-transcript) et **journalisé** (taille transmise, §5.4), et l'on **conseille
un `reset` (Ctrl+L) avant de passer au cloud** pour repartir d'un contexte minimal.
Aucun cap distinct n'est appliqué en mode cloud (il casserait la cohérence de
l'historique partagé) ; la borne `MAX_HISTORY_MESSAGES` s'applique aux deux backends.

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

**Backends implémentant `Model`.** Le protocole `Model` est implémenté par le
local (`models.local_llm.OllamaClient`, Ollama) **et** par le cloud
(`models.cloud_router.ClaudeClient`, tool use Anthropic). L'orchestrator dépend de
`Model` (pas d'un backend concret) ; il sélectionne le backend **par requête**
selon `use_cloud` (cf. §5.3), sans rien changer à la boucle agentique ni à la
mémoire conversationnelle.

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

## 8 · Exécution système & élévation (`core/elevation.py`) — jalon 3

Point **unique** d'exécution de sous-processus système (pacman, etc.), dans
l'esprit du choke point du `policy_engine`. Jamais de `shell=True` ; l'argv est
une `list[str]` déjà découpée et validée par l'outil appelant.

```python
@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    @property
    def ok(self) -> bool: ...   # returncode == 0

def run_command(
    argv: list[str], *,
    elevate: bool = False,         # True → préfixe "pkexec" (root ponctuel via polkit)
    timeout: float = 120.0,
    runner: Runner | None = None,  # injectable pour les tests (défaut: subprocess.run)
) -> CommandResult: ...
```

**Élévation polkit.** Quand `elevate=True`, la commande est préfixée par
`pkexec` : l'agent polkit de la session (Xfce/X11) demande le mot de passe et
accorde root **uniquement à ce processus**, pour **cette action**. Le daemon
reste utilisateur (cf. SECURITY menace 3). Aucun fichier `.policy` dédié n'est
requis : `pkexec` utilise la policy par défaut `org.freedesktop.policykit.exec`.

**Robustesse.** En cas de `pkexec`/binaire introuvable (`FileNotFoundError`) ou
de timeout, `run_command` renvoie un `CommandResult` d'échec lisible (codes 127
/ 124) au lieu de lever — l'orchestrator réinjecte ce texte comme **donnée**.

**Contrat outils système.** Un outil qui pilote le système :
- construit son argv en **liste** (jamais de concaténation shell) ;
- **valide ses entrées** avant (noms de paquets via regex, PID entier, etc.) ;
- déclare `requires_elevation(args)` cohérent avec son usage de `elevate=True` ;
- ne vérifie aucune permission lui-même (rôle du `policy_engine`).

---

## 7 · Règle d'or

Quand un contrat ci-dessus est insuffisant pour coder, **étendre ce document d'abord**, puis implémenter. Ne jamais laisser deux modules diverger sur un format de message ou une signature.
