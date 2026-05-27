# 🔌 Contrats d'interface — My_OS

> Ce document fige les **interfaces entre composants** pour que l'implémentation reste cohérente d'une session à l'autre. Ce sont des contrats de départ raisonnables ; ils peuvent évoluer, mais tout changement doit être répercuté ici **et** dans le code, jamais l'un sans l'autre.

---

## 1 · Protocole IPC (daemon ↔ popup)

Communication sur une **socket Unix locale** (`/run/user/<uid>/myos.sock` ou équivalent). Messages encodés en JSON, un message par ligne (`\n` comme délimiteur) ou préfixés par leur longueur (à décider à l'implémentation — documenter le choix retenu).

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

---

## 2 · Classe de base des outils (`tools/base_tool.py`)

Tout outil hérite de `BaseTool`. Contrat minimal :

```python
class BaseTool:
    name: str                    # identifiant unique, ex. "move_file"
    description: str             # description pour le LLM
    risk_level: int              # 0..3, OBLIGATOIRE — sinon l'outil n'est pas chargé
    parameters: dict             # schéma JSON des arguments attendus

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

Boucle de traitement d'une requête :

```
1. Reçoit user_message (via ipc_server)
2. Construit le contexte : le contenu lu est étiqueté comme DONNÉE non fiable
3. Appelle models.cloud_router.generate(...)
4. Pour chaque tool_request émis par le modèle :
     a. decision = policy_engine.evaluate(tool, args, grants)
     b. si "blocked" → refuse, journalise, informe le LLM
     c. si "confirm" → envoie confirmation_needed au popup, attend la réponse
     d. si "auto" (ou confirmé) → audit_log.log(...) puis tool.run(args)
     e. renvoie le ToolResult au modèle pour la suite du raisonnement
5. Stream les tokens vers le popup, puis "done"
```

---

## 7 · Règle d'or

Quand un contrat ci-dessus est insuffisant pour coder, **étendre ce document d'abord**, puis implémenter. Ne jamais laisser deux modules diverger sur un format de message ou une signature.
