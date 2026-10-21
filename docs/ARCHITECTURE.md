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
│ Couche 2 — Outils + moteur de permissions       │  outils système + garde + audit
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
| `daemon/hotkey_listener.py` | capture du raccourci global — X11 via `pynput` (nominal), Wayland via le portal `GlobalShortcuts` (jalon 5, expérimental) ; backend choisi selon la session (cf. `INTERFACES.md §9`) |
| `daemon/ipc_server.py` | serveur socket Unix, protocole de messages |
| `daemon/orchestrator.py` | logique requête → modèle → outils → réponse |

**Flux IPC.** Le popup et le daemon sont deux processus. Le popup envoie le message utilisateur sur la socket ; le daemon répond en streaming. La socket est locale (pas de réseau) — cf. `SECURITY.md §5`.

---

## 3 · Couche 2 — Outils + moteur de permissions

La couche qui fait de My_OS un *système* et non un chatbot. Elle expose des **outils** au modèle — par le *tool calling* natif d'Ollama et de l'API Anthropic, sans serveur MCP — et **filtre chaque action** par le moteur de permissions.

### 3.1 · Les outils (`tools/`)
Chaque outil est une capacité concrète et déterministe (≠ « le modèle clique »). Tous héritent de `tools/base_tool.py` et déclarent un `risk_level`.

| Fichier | Outils | Niveau de base |
|---------|--------|----------------|
| `tools/files.py` | lire, lister | 0 |
|  | écrire, déplacer, créer | 1 (→2 si chemin sensible) |
|  | supprimer (fichiers seulement, jamais un dossier) | 2 |
| `tools/packages.py` | rechercher (`pacman -Ss`, mots-clés) | 0 |
|  | installer (`pacman -S`, élévation polkit) | 1 |
|  | désinstaller, mettre à jour (`pacman -Rns` / `-Syu`, élévation polkit) | 2 |
| `tools/system_settings.py` | luminosité, Wi-Fi (D-Bus) ; volume, sourdine (`pactl`) | 1 |
| `tools/processes.py` | lister | 0 |
|  | tuer un processus (droits de l'utilisateur) | 2 |

Les outils fichiers résolvent le **chemin réel** dans `normalize_args` (`~`, chemin relatif, `..`, `//`, liens symboliques) **avant** toute décision : blocklist, escalade, grants et audit voient tous le fichier réellement visé (cf. `SECURITY.md §4`, règle 5).

### 3.2 · Le moteur de permissions (`permissions/`)
Voir `SECURITY.md §4` pour les règles. Modules :

| Fichier | Rôle |
|---------|------|
| `risk_levels.py` | table statique outil → niveau (source de vérité) |
| `policy_engine.py` | décide auto / confirm / bloqué ; applique l'escalade ; les grants ne couvrent que le niveau 1 |
| `blocklist.py` | niveau 3, vérifié en premier |
| `session_grants.py` | mémorise « ce dossier / cette session » (jamais pour le niveau 2) |
| `confirmation.py` | construit la demande de confirmation pour l'UI, avec les portées autorisées (`allowed_scopes`) |
| `audit_log.py` | écrit chaque action dans `~/.local/share/my_os/audit.db` (SQLite) |

**Invariant.** Aucune action système ne s'exécute sans passer par `policy_engine.py`. C'est le point de passage unique — un « choke point » de sécurité.

---

## 4 · Couche 3 — Les modèles

### 4.1 · Modèle local (`models/local_llm.py`)
Qwen via Ollama. Backend par défaut. Repris quasi tel quel de My_AI. Confidentialité totale : rien ne sort de la machine.

Deux réglages viennent aussi de My_AI. Ollama est joint sur `127.0.0.1` : un « localhost » configuré y est ramené, car il passerait d'abord par l'IPv6 (`::1`), où Ollama n'écoute pas. Le contexte du modèle (cache K/V) est en 8 bits, deux fois moins de mémoire. C'est un réglage du serveur Ollama, pas du daemon : l'ISO le pose par un drop-in systemd du service `ollama` (`packaging/archiso/airootfs/etc/systemd/system/ollama.service.d/10-myos.conf`), à copier à la main sur une Arch existante (`INSTALLATION.md §2.3`).

La fenêtre de contexte (`model.num_ctx`, 16 384 tokens par défaut) est la même à chaque requête : Ollama recharge le modèle quand elle change. Le client n'envoie que la part de l'historique qui y tient. Chaque résultat d'outil est plafonné au quart de la fenêtre ; au-delà du budget, les plus vieux tours partent d'un bloc, jusqu'à la moitié du budget, pour que le début de l'envoi reste stable et qu'Ollama reprenne ce qu'il a déjà lu. Ollama ne coupe donc plus jamais le début du prompt, où sont les règles et les outils.

Le daemon ne choisit pas où tourne le modèle : Ollama prend la carte graphique dédiée quand il en trouve une, sinon le processeur. L'ISO embarque pour cela le backend Vulkan d'Ollama et les pilotes Vulkan libres (AMD, Intel, NVIDIA) ; une installation depuis les sources ajoute le backend de sa carte (`INSTALLATION.md §2.3`).

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
| `ui/popup.py` | fenêtre PySide6 : client IPC, routage des messages du daemon vers la bonne réponse, états de la fenêtre (agrandie, plein écran) et mémoire de sa géométrie |
| `ui/chat_view.py` | conversation : bulle de l'utilisateur, réponse en streaming, cartes d'outils et de code, notices, écran d'accueil, défilement collé en bas |
| `ui/composer.py` | zone de saisie multiligne, bascule « ☁ Cloud », clé API, bouton d'envoi |
| `ui/window_chrome.py` | barre de titre (rappel « Échap pour fermer », boutons) et poignées de redimensionnement d'une fenêtre sans cadre système |
| `ui/markdown_render.py` | découpage texte / blocs de code et mise en forme du markdown (`QTextDocument`, sans HTML brut ni image) |
| `ui/confirm_dialog.py` | dialogue de confirmation d'action |
| `ui/icons.py` | icônes et logo dessinés au `QPainter` (aucun fichier image, pas de QtSvg) |
| `ui/styles.py` | jetons de design et feuille QSS, charte noir/orange (gabarit de l'interface de My_AI) |
| `ui/wayland_layer.py` | présentation Wayland du popup (surface `layer-shell`), repli X11 — jalon 5 (cf. `INTERFACES.md §9`) |

**Choix technique.** `QTextBrowser` plutôt que `QWebEngineView` : le popup doit s'ouvrir **instantanément** au raccourci ; un moteur Chromium embarqué serait trop lourd à charger, et resterait résident toute la session. Le moteur de texte de Qt n'exécute aucun script : c'est aussi ce qui garantit qu'un contenu lu ne peut rien faire dans le processus qui répond aux confirmations (cf. `SECURITY.md §2.2`).

**Fenêtre.** Le popup est *frameless* (look uniforme, sans décoration du gestionnaire de fenêtres) : sa barre de titre et ses poignées de bord délèguent déplacement et redimensionnement au gestionnaire de fenêtres (`startSystemMove` / `startSystemResize`), avec un repli manuel. Double-clic sur la barre : agrandir ; `F11` : plein écran. Taille, position et état sont mémorisés dans `~/.config/my_os/popup.conf`, et réappliqués à chaque ouverture (xfwm4 efface l'état d'une fenêtre masquée).

**Port Wayland (jalon 5 — expérimental).** X11 reste le chemin **nominal**,
pleinement supporté. Sous Wayland, le centrage et l'always-on-top côté client Qt
n'ont pas d'effet : le popup est posé en **surface overlay `layer-shell`** (plugin
système `layer-shell-qt`, activé via `QT_WAYLAND_SHELL_INTEGRATION`) et le raccourci
global passe par le **portal `org.freedesktop.portal.GlobalShortcuts`**. Le backend
(X11 vs Wayland) est sélectionné au démarrage selon le type de session
(`core/session.py`) — contrats et limites (support dépendant du compositeur) détaillés
dans `INTERFACES.md §9`. Repli propre + diagnostic si le compositeur n'a pas
`layer-shell` / le portal.

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
   └─▶ l'outil normalise ses arguments (chemin réel : ~, relatif, liens)
   └─▶ permissions/policy_engine évalue le risque
        ├─ niveau 0 → exécute
        ├─ niveau 1/2 → ui/confirm_dialog demande confirmation
        │               (au-dessus du popup, « Refuser » par défaut)
        └─ niveau 3 → refuse
   └─▶ permissions/audit_log journalise la décision + le résultat

5. tools/ exécute l'action validée (déplacer les fichiers)

6. daemon renvoie la réponse en streaming
   └─▶ ui/popup affiche progressivement
```

---

## 7 · Persistance

| Donnée | Stockage |
|--------|----------|
| Journal d'audit | `~/.local/share/my_os/audit.db` (SQLite) |
| Configuration | `config.yaml` (versionné) + `config.local.yaml` (surcharges locales, non versionné) |
| Taille, position et état du popup | `~/.config/my_os/popup.conf` (`QSettings`, aucune donnée sensible) |
| Clé API / secrets | trousseau OS (`keyring`) — **pas** sur disque en clair |
| Grants de session | en mémoire (réinitialisés à chaque session) |
| Dépendances PyPI (ISO) | venv `~/.local/share/my_os/venv` (créé au 1er run, journal `firstrun.log`) |
| Modèle local | stockage d'Ollama (`/var/lib/ollama`) |
| Bases pacman (ISO live) | resynchronisées au boot depuis l'instantané figé du build |

---

## 8 · Ce qui est réutilisé de My_AI

| Composant My_AI | Réutilisation dans My_OS |
|-----------------|--------------------------|
| `local_llm.py` (Ollama/Qwen) | quasi tel quel |
| `mcp_client.py` (pont outils) | inspiration de la couche outils (en v1 : appel direct par *tool calling*, sans MCP) |
| logique confirmation avant suppression | généralisée en moteur de permissions |
| flux d'approbation de l'extension VS Code | repris pour les grants de session |
| rendu markdown / streaming chat | logique reprise, nouvelle vue Qt |
| interface React (jetons de design, fil de messages, composer) | charte transposée en Qt : mêmes gris, arrondis et tailles, accent orange de My_OS |
| SQLite (base de connaissances) | réutilisé pour le journal d'audit |

L'effort **neuf** se concentre sur : le daemon (couche 1), le moteur de permissions (couche 2), et la vue Qt (couche 4).

---

## 9 · Décisions d'architecture notables

- **Daemon + popup en deux processus** plutôt qu'un seul : isole l'UI du cœur, et permet au popup de planter/redémarrer sans tuer le daemon.
- **Choke point de sécurité unique** (`policy_engine.py`) : un seul endroit à auditer pour garantir qu'aucune action n'échappe au contrôle.
- **Outils déterministes d'abord, GUI en dernier recours** (voir roadmap) : la fiabilité prime, le contrôle par vision n'arrive que quand aucune commande directe n'existe.
- **Local par défaut, cloud opt-in** : inverse de la norme, par choix de confidentialité.
