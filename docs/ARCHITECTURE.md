# 🏗️ Architecture de My_OS

> Structure technique détaillée. Ce document explique *comment* My_OS est organisé et *pourquoi*. Il complète le `README.md` (vue d'ensemble) et `SECURITY.md` (modèle de menaces).

---

## 1 · Vue en couches

My_OS s'organise en cinq couches, construites de bas en haut. Chaque couche ne dépend que de celles en dessous.

```
┌─────────────────────────────────────────────────┐
│ Couche 4 — Popup Qt (PySide6)                   │  interface invoquée au raccourci
├─────────────────────────────────────────────────┤
│ Couche 3a — Modèle local │ 3b — Routeur cloud   │  Qwen/Ollama (défaut) · Claude (opt-in)
├─────────────────────────────────────────────────┤
│ Couche 2 — Pont MCP + moteur de permissions     │  outils système + garde + audit
├─────────────────────────────────────────────────┤
│ Couche 1 — Daemon myosd                         │  service résident, raccourci, IPC
├─────────────────────────────────────────────────┤
│ Couche 0 — Base Arch Linux                      │  kernel, drivers, paquets
└─────────────────────────────────────────────────┘
```

---

## 2 · Couche 1 — Le daemon `myosd`

Le cœur résident du système. Un service `systemd` **utilisateur** qui démarre à l'ouverture de session et reste actif.

**Responsabilités :**
- Écouter le raccourci clavier global et déclencher l'affichage du popup.
- Exposer une socket Unix locale (IPC) pour communiquer avec le popup.
- Orchestrer le cycle d'une requête : recevoir le message → appeler le modèle → exécuter les outils validés → renvoyer la réponse.

**Modules :**
| Fichier | Rôle |
|---------|------|
| `daemon/myosd.py` | point d'entrée, boucle de vie du service |
| `daemon/hotkey_listener.py` | capture du raccourci global (X11 via `pynput`) |
| `daemon/ipc_server.py` | serveur socket Unix, protocole de messages |
| `daemon/orchestrator.py` | logique requête → modèle → outils → réponse |

**Flux IPC.** Le popup et le daemon sont deux processus. Le popup envoie le message utilisateur sur la socket ; le daemon répond en streaming. La socket est locale (pas de réseau) — cf. `SECURITY.md §5`.

---

## 3 · Couche 2 — Pont MCP + moteur de permissions

La couche qui fait de My_OS un *système* et non un chatbot. Elle expose des **outils** au modèle et **filtre chaque action** par le moteur de permissions.

### 3.1 · Les outils (`tools/`)
Chaque outil est une capacité concrète et déterministe (≠ « le modèle clique »). Tous héritent de `tools/base_tool.py` et déclarent un `risk_level`.

| Fichier | Outils | Niveau de base |
|---------|--------|----------------|
| `tools/files.py` | lire, lister | 0 |
|  | écrire, déplacer, créer | 1 (→2 si chemin sensible) |
|  | supprimer | 2 |
| `tools/packages.py` | installer, mettre à jour (`pacman`) | 1-2 |
| `tools/system_settings.py` | luminosité, audio, réseau (D-Bus) | 1 |
| `tools/processes.py` | lister | 0 |
|  | tuer un processus | 2 |

### 3.2 · Le moteur de permissions (`permissions/`)
Voir `SECURITY.md §4` pour les règles. Modules :

| Fichier | Rôle |
|---------|------|
| `risk_levels.py` | table statique outil → niveau (source de vérité) |
| `policy_engine.py` | décide auto / confirm / bloqué ; applique l'escalade |
| `blocklist.py` | niveau 3, vérifié en premier |
| `session_grants.py` | mémorise « une fois / ce dossier / cette session » |
| `confirmation.py` | construit la demande de confirmation pour l'UI |
| `audit_log.py` | écrit chaque action dans `data/audit.db` (SQLite) |

**Invariant.** Aucune action système ne s'exécute sans passer par `policy_engine.py`. C'est le point de passage unique — un « choke point » de sécurité.

---

## 4 · Couche 3 — Les modèles

### 4.1 · Modèle local (`models/local_llm.py`)
Qwen via Ollama. Backend par défaut. Repris quasi tel quel de My_AI. Confidentialité totale : rien ne sort de la machine.

### 4.2 · Routeur cloud (`models/cloud_router.py`)
Aiguille une requête vers le backend local ou cloud (Claude via la lib `anthropic`). Le cloud est **opt-in**, activé par requête. Le routeur :
- vérifie qu'une clé API est présente (via `models/secrets.py`) ;
- signale visuellement le mode cloud ;
- journalise les envois.

### 4.3 · Secrets (`models/secrets.py`)
Stockage/lecture de la clé API via `keyring` (trousseau OS). Jamais en clair. Cf. `SECURITY.md §3` menace 4.

---

## 5 · Couche 4 — Le popup Qt (`ui/`)

Interface invoquée au raccourci. Nouvelle vue en PySide6, mais la **logique** de chat est reprise de My_AI.

| Fichier | Rôle |
|---------|------|
| `ui/popup.py` | fenêtre PySide6 : centrée, sans bordure, au-dessus de tout |
| `ui/chat_view.py` | affichage de la conversation |
| `ui/markdown_render.py` | rendu markdown via `QTextBrowser` (léger) |
| `ui/streaming.py` | affichage progressif des réponses |
| `ui/confirm_dialog.py` | dialogue de confirmation d'action |
| `ui/styles.py` | thème sombre/orange (esprit My_AI) |

**Choix technique.** `QTextBrowser` plutôt que `QWebEngineView` : le popup doit s'ouvrir **instantanément** au raccourci ; un moteur Chromium embarqué serait trop lourd à charger.

**Note Wayland (plus tard).** Sous Wayland, le centrage et l'always-on-top passent par `layer-shell` ; le raccourci global par le portal `GlobalShortcuts`. Géré au jalon 5 (port Wayland).

---

## 6 · Cycle de vie d'une requête (bout en bout)

```
1. Utilisateur appuie sur le raccourci
   └─▶ daemon/hotkey_listener détecte, demande l'affichage du popup

2. Utilisateur tape « range mes Téléchargements par type » + Entrée
   └─▶ ui/popup envoie le message via la socket IPC

3. daemon/orchestrator reçoit
   └─▶ models/ (local ou cloud) génère une réponse + appels d'outils

4. Pour chaque appel d'outil :
   └─▶ permissions/policy_engine évalue le risque
        ├─ niveau 0 → exécute
        ├─ niveau 1/2 → ui/confirm_dialog demande confirmation
        └─ niveau 3 → refuse
   └─▶ permissions/audit_log journalise la décision + le résultat

5. tools/ exécute l'action validée (déplacer les fichiers)

6. daemon renvoie la réponse en streaming
   └─▶ ui/streaming affiche progressivement
```

---

## 7 · Persistance

| Donnée | Stockage |
|--------|----------|
| Journal d'audit | `data/audit.db` (SQLite) |
| Configuration | `config.yaml` |
| Clé API / secrets | trousseau OS (`keyring`) — **pas** sur disque en clair |
| Grants de session | en mémoire (réinitialisés à chaque session) |

---

## 8 · Ce qui est réutilisé de My_AI

| Composant My_AI | Réutilisation dans My_OS |
|-----------------|--------------------------|
| `local_llm.py` (Ollama/Qwen) | quasi tel quel |
| `mcp_client.py` (pont outils) | base du pont de la couche 2 |
| logique confirmation avant suppression | généralisée en moteur de permissions |
| flux d'approbation de l'extension VS Code | repris pour les grants de session |
| rendu markdown / streaming chat | logique reprise, nouvelle vue Qt |
| SQLite (base de connaissances) | réutilisé pour le journal d'audit |

L'effort **neuf** se concentre sur : le daemon (couche 1), le moteur de permissions (couche 2), et la vue Qt (couche 4).

---

## 9 · Décisions d'architecture notables

- **Daemon + popup en deux processus** plutôt qu'un seul : isole l'UI du cœur, et permet au popup de planter/redémarrer sans tuer le daemon.
- **Choke point de sécurité unique** (`policy_engine.py`) : un seul endroit à auditer pour garantir qu'aucune action n'échappe au contrôle.
- **Outils déterministes d'abord, GUI en dernier recours** (voir roadmap) : la fiabilité prime, le contrôle par vision n'arrive que quand aucune commande directe n'existe.
- **Local par défaut, cloud opt-in** : inverse de la norme, par choix de confidentialité.
