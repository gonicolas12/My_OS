# 🖥️ My_OS — Un système d'exploitation assisté par IA, local et sécurisé

**Local-first · Sécurisé par conception · Open source (MIT)**

> Une distribution Linux (base Arch) dans laquelle une IA est intégrée *au cœur du
> système*. Un raccourci clavier global ouvre un assistant qui peut lire et organiser
> vos fichiers, installer des logiciels, ajuster les paramètres de la machine et
> piloter votre PC en langage naturel — avec une **confirmation systématique** pour
> toute action à risque. Le modèle tourne **localement** (Qwen via Ollama) ; un modèle
> cloud (Claude) peut être activé **en option, par requête**. Vos données restent chez
> vous par défaut.

My_OS est la suite logique de [My_AI](https://github.com/gonicolas12/My_AI) : on passe
d'une *application* qui parle au PC à un *système* dans lequel l'IA est résidente et
profondément intégrée.

> **État du projet : v1 complète.** Les cinq jalons (socle → fichiers/permissions →
> pilotage système → routeur cloud → packaging) sont terminés. **X11 est pleinement
> supporté ; le port Wayland est expérimental** (cf. [§7](#7--sécurité) et
> [docs/INSTALLATION.md](docs/INSTALLATION.md)).

---

## 📸 Aperçu

> _Captures à venir (placeholders) :_
>
> | Popup au raccourci | Confirmation d'action | Mode cloud (opt-in) |
> |--------------------|-----------------------|---------------------|
> | `docs/media/popup.png` | `docs/media/confirm.png` | `docs/media/cloud.png` |
>
> _GIF de démonstration : `docs/media/demo.gif` (« range mon dossier Téléchargements
> par type » → plan → confirmation → exécution)._

---

## 📑 Sommaire

1. [Vision](#1--vision)
2. [Concept en une image](#2--concept-en-une-image)
3. [Fonctionnalités](#3--fonctionnalités)
4. [Installation](#4--installation)
5. [Utilisation](#5--utilisation)
6. [Le moteur de permissions](#6--le-moteur-de-permissions)
7. [Sécurité](#7--sécurité)
8. [Architecture](#8--architecture)
9. [Roadmap](#9--roadmap)
10. [Arborescence du projet](#10--arborescence-du-projet)
11. [Développement](#11--développement)
12. [Contribuer](#12--contribuer)
13. [Licence](#13--licence) · [Crédits](#14--crédits)

---

## 1 · Vision

My_OS répond à une idée simple : et si l'assistant IA n'était pas une application qu'on
ouvre, mais une **capacité du système** lui-même, disponible partout et tout le temps
via un raccourci clavier ?

- **Local par défaut** — le modèle Qwen tourne sur la machine via Ollama. Aucune donnée
  ne sort sans action explicite.
- **Cloud en option** — avec une clé API, on peut router une requête vers un modèle plus
  puissant (Claude). Opt-in, **par requête**, visible et journalisé.
- **Accès réel au système** — fichiers, paquets, paramètres, processus. L'IA agit, elle
  ne fait pas que répondre.
- **Sûr par conception** — chaque action est classée par risque ; les actions sensibles
  demandent confirmation ; tout est journalisé ; rien de destructeur n'est silencieux.

**Objectifs du projet** : portfolio technique, projet d'école (Ynov), et base open
source réutilisable.

---

## 2 · Concept en une image

```
                    ┌─────────────────────────────┐
   Raccourci  ──▶   │   Popup My_OS (Qt/PySide6)  │
   clavier global   │   « Tapez votre message… »  │
                    └──────────────┬──────────────┘
                                   │  IPC (socket Unix)
                    ┌──────────────▼──────────────┐
                    │      Daemon myosd            │
                    │   (orchestrateur résident)   │
                    └──────────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                     ▼
      ┌───────────────┐   ┌────────────────┐   ┌─────────────────┐
      │ Modèle local  │   │ Moteur de      │   │ Routeur cloud   │
      │ Qwen/Ollama   │   │ permissions    │   │ Claude (opt-in) │
      │ (défaut)      │   │ + audit        │   │                 │
      └───────────────┘   └───────┬────────┘   └─────────────────┘
                                  │ valide chaque action
                          ┌───────▼────────┐
                          │  Outils        │
                          │ fichiers,      │
                          │ paquets, D-Bus,│
                          │ processus      │
                          └────────────────┘
```

---

## 3 · Fonctionnalités

Toutes ces capacités sont **opérationnelles** (v1) et passent par le moteur de
permissions.

- 🗂️ **Fichiers** — lire, lister, écrire, déplacer, créer, supprimer. Ex. « range mon
  dossier Téléchargements par type ». L'escalade de risque détecte les chemins sensibles
  (`~/.ssh`, `/etc`…).
- 📦 **Paquets** (`pacman`) — rechercher, installer, supprimer, mettre à jour. Élévation
  ponctuelle via polkit (`pkexec`), jamais root en permanence.
- 🔧 **Réglages système** (D-Bus / `pactl`) — luminosité, volume, sourdine, Wi-Fi.
- 📊 **Processus** (`psutil`) — lister les processus, en tuer un (avec garde-fous :
  PID critiques bloqués).
- 🧠 **Modèle local** — Qwen via Ollama, par défaut, **100 % sur la machine**.
- ☁️ **Cloud opt-in** — Claude (API Anthropic), activé **par requête** via un toggle
  visible ; clé API dans le trousseau du système (`keyring`), jamais en clair.
- 🛡️ **Permissions & audit** — 4 niveaux de risque, confirmations, blocklist
  infranchissable, journal SQLite de chaque action.
- ⌨️ **Raccourci global** — X11 (`pynput`, nominal) et Wayland (portal
  `GlobalShortcuts`, **expérimental**).
- 💬 **Popup instantané** — PySide6 + `QTextBrowser`, rendu markdown, réponses en
  streaming, thème sombre.

---

## 4 · Installation

Deux voies. Détails complets dans **[docs/INSTALLATION.md](docs/INSTALLATION.md)**.

### a) Via l'ISO live (le plus simple)

Une ISO bootable (base Arch + Xfce/X11) embarque My_OS prêt à l'emploi. Le profil
[archiso](https://wiki.archlinux.org/title/Archiso) et le script de build sont dans
[`packaging/`](packaging/) ; la construction se fait sur un hôte Arch
(`sudo ./packaging/build_iso.sh`) puis se teste en VM. Au 1er boot, les dépendances et
le modèle local sont récupérés (réseau requis). Voir [packaging/README.md](packaging/README.md).

### b) Depuis les sources

> Prérequis : Arch Linux (ou dérivé), Python 3.10+, [Ollama](https://ollama.com/download).

```bash
git clone https://github.com/gonicolas12/My_OS && cd My_OS
pip install -r requirements.txt
ollama pull qwen3.5:4b          # modèle local recommandé (≈ 8 Go RAM)
./launch_dev.sh                 # lance daemon + popup (session X11)
```

Pour une installation **résidente** (service `systemd` utilisateur, autostart), suivre
[docs/INSTALLATION.md](docs/INSTALLATION.md). **Notes X11/Wayland** : X11 fonctionne tel
quel ; Wayland nécessite un compositeur avec `layer-shell` + portal `GlobalShortcuts` et
reste expérimental.

---

## 5 · Utilisation

1. Appuyez sur **Ctrl+Alt+Espace** (raccourci par défaut, modifiable dans `config.yaml`).
2. Le popup s'ouvre, centré et au-dessus de tout. Tapez votre demande en langage naturel.
3. Pour une action sensible, une **confirmation** détaillée apparaît (avec, au besoin, une
   élévation polkit). Vous pouvez autoriser *une fois*, *pour ce dossier*, ou *pour la
   session*.
4. `Ctrl+L` démarre une nouvelle conversation ; `Échap` referme le popup.

**Exemples de requêtes :**
- « Range mon dossier Téléchargements par type. »
- « Installe VLC. »
- « Baisse la luminosité à 30 %. »
- « Qu'est-ce qui mange ma RAM en ce moment ? »
- « Résume ce fichier. » *(le contenu lu reste une donnée, jamais une instruction)*

**Mode cloud (optionnel) :** cochez « ☁ Cloud (Claude) », saisissez votre clé API
Anthropic (stockée dans le trousseau) ; un indicateur signale que **cette** requête part
au cloud. Conseil : `Ctrl+L` avant, pour minimiser les données envoyées.

---

## 6 · Le moteur de permissions

C'est le composant le plus important du projet : il garantit que **la sûreté ne dépend
pas de la fiabilité du modèle**.

| Niveau | Nom | Exemples | Comportement |
|--------|-----|----------|--------------|
| **0** | Auto | lire un fichier, lister, monitorer | Exécuté sans confirmation |
| **1** | Confirmation | écrire/déplacer, installer un paquet | Dialogue simple |
| **2** | Renforcée | supprimer, `sudo`, modifier le système, `/etc` `/boot` | Confirmation explicite + détail |
| **3** | Bloqué | `rm -rf /`, formatage, zones critiques | **Jamais exécuté, même confirmé** |

- **Statique d'abord** : le niveau vient du code (`permissions/risk_levels.py`), jamais
  du LLM.
- **Escalade par arguments** : ne peut qu'**augmenter** le risque (ex. écrire dans `~/.ssh`).
- **Blocklist en premier** : le niveau 3 est vérifié avant tout et ne peut être contourné.
- **Mémorisation** : *une fois* / *ce dossier* / *cette session*.
- **Audit** : chaque action et décision est journalisée dans `data/audit.db` (SQLite).

---

## 7 · Sécurité

La sécurité de My_OS repose sur l'**architecture**, pas sur des correctifs ajoutés après
coup. Trois invariants non négociables :

1. **Le LLM ne décide jamais de son niveau de permission** — c'est du code statique. La
   blocklist (niveau 3) est infranchissable.
2. **Tout contenu lu (fichier, web, écran, sortie de commande) est une donnée non
   fiable**, jamais une instruction — défense contre l'injection de prompt indirecte.
3. **Le daemon tourne en utilisateur, jamais root** — élévation ponctuelle via polkit ;
   IPC sur socket Unix locale ; secrets via `keyring`.

Le **cloud reste opt-in par requête** et le **modèle local par défaut** (privé). Sous
Wayland, le **confinement** de la surface (layer-shell) est un *plus* de sécurité.

➡️ Modèle de menaces complet : **[docs/SECURITY.md](docs/SECURITY.md)**.

---

## 8 · Architecture

Cinq couches, construites de bas en haut :

| Couche | Rôle |
|--------|------|
| **0 · Base Arch Linux** | kernel, drivers, paquets |
| **1 · Daemon `myosd`** | service résident, raccourci global, IPC, orchestration |
| **2 · Outils + permissions** | outils système + moteur de permissions + audit (choke point) |
| **3a · Modèle local** | Qwen via Ollama (défaut, privé) |
| **3b · Routeur cloud** | Claude via clé API (opt-in) |
| **4 · Popup Qt** | interface invoquée au raccourci |

Daemon et popup sont **deux processus** communiquant par socket Unix. Toute action passe
par le point de passage unique `permissions/policy_engine.py`.

➡️ Détails : **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** · contrats d'interface :
**[docs/INTERFACES.md](docs/INTERFACES.md)**.

---

## 9 · Roadmap

**v1 — terminée ✅** (détails et vision long terme : [docs/ROADMAP.md](docs/ROADMAP.md)).

- [x] **Jalon 1 — Socle** : daemon systemd utilisateur, raccourci global (X11), IPC socket
  Unix, popup Qt.
- [x] **Jalon 2 — Fichiers + permissions** : outils fichiers, moteur de permissions
  complet, audit SQLite, confirmations, boucle agentique, Ollama.
- [x] **Jalon 3 — Pilotage système** : paquets (`pacman`), réglages (D-Bus), processus
  (`psutil`), élévation polkit ponctuelle.
- [x] **Jalon 4 — Routeur cloud** : clé API via `keyring`, toggle opt-in par requête,
  indicateur visible, journalisation.
- [x] **Jalon 5 — Packaging** : port Wayland (expérimental), profil ISO `archiso`,
  finalisation de la documentation.
- [x] **Sécurité (transverse)** : daemon non-root, séparation données/instructions,
  `SECURITY.md` tenu à jour.

**Au-delà de la v1 (vision long terme, non promise) :** contrôle d'applications via
accessibilité (AT-SPI), contrôle par vision d'écran, défenses anti-injection avancées,
Wayland natif grand public. Voir [docs/ROADMAP.md](docs/ROADMAP.md).

---

## 10 · Arborescence du projet

```
My_OS/
├── daemon/                   # Couche 1 — cœur résident
│   ├── myosd.py              # point d'entrée du service
│   ├── hotkey_listener.py    # raccourci global (X11 pynput / Wayland portal)
│   ├── ipc_server.py         # socket Unix daemon ↔ popup
│   ├── orchestrator.py       # boucle agentique requête → modèle → outils
│   └── confirmation_provider.py
├── permissions/              # Couche 2 — sécurité (cœur)
│   ├── risk_levels.py        # table statique outil → niveau
│   ├── policy_engine.py      # décision + escalade (point de passage unique)
│   ├── blocklist.py          # niveau 3, jamais autorisé
│   ├── confirmation.py
│   ├── session_grants.py     # une fois / dossier / session
│   └── audit_log.py          # journal SQLite
├── tools/                    # outils système
│   ├── base_tool.py          # base + risk_level
│   ├── files.py              # fichiers
│   ├── packages.py           # pacman
│   ├── system_settings.py    # D-Bus / pactl
│   └── processes.py          # psutil
├── models/                   # Couche 3 — IA
│   ├── local_llm.py          # Qwen/Ollama (défaut)
│   ├── cloud_router.py       # routeur + API Anthropic (opt-in)
│   ├── secrets.py            # clé API via keyring
│   └── stub_model.py         # backend de règles (tests / sans Ollama)
├── ui/                       # Couche 4 — popup Qt
│   ├── popup.py              # fenêtre PySide6
│   ├── markdown_render.py    # rendu via QTextBrowser
│   ├── confirm_dialog.py     # dialogue de confirmation
│   ├── styles.py             # thème sombre/orange
│   └── wayland_layer.py      # présentation Wayland (layer-shell), repli X11
├── core/
│   ├── config.py             # config + résolution socket
│   ├── ipc.py                # cadrage des messages
│   ├── elevation.py          # exécution système + polkit (pkexec)
│   ├── session.py            # détection X11/Wayland
│   └── logger.py
├── data/
│   └── audit.db              # journal d'audit (généré)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SECURITY.md           # modèle de menaces
│   ├── INTERFACES.md         # contrats d'interface
│   ├── INSTALLATION.md       # install (sources + ISO)
│   └── ROADMAP.md            # v1 + vision long terme
├── packaging/                # ISO live (archiso)
│   ├── build_iso.sh
│   ├── README.md
│   └── archiso/              # profiledef, packages, airootfs
├── tests/                    # ~390 tests (permissions en priorité)
├── config.yaml
├── myosd.service             # service systemd utilisateur
├── requirements.txt
├── launch_dev.sh
└── pyproject.toml
```

---

## 11 · Développement

```bash
pip install -r requirements.txt
./launch_dev.sh                 # daemon + popup en dev (X11)

pytest                          # tous les tests
pytest tests/test_permissions/  # le cœur de sécurité (prioritaire)
ruff check . && ruff format .   # lint + format
```

Conventions : Python 3.10+, type hints partout, docstrings sur les fonctions publiques.
Tout nouvel outil hérite de `tools/base_tool.py` et **doit** déclarer un `risk_level`.
Aucune action ne contourne `permissions/policy_engine.py`. Aucun secret en clair.

---

## 12 · Contribuer

Les contributions sont bienvenues !

1. Forkez, créez une branche de feature (`git checkout -b ma-feature`).
2. Respectez les conventions ci-dessus et les contrats de [docs/INTERFACES.md](docs/INTERFACES.md).
3. **Couvrez le module `permissions/` en priorité** ; `ruff` propre, tests verts.
4. Pour la sécurité, lisez d'abord [docs/SECURITY.md](docs/SECURITY.md) — aucun changement
   ne doit affaiblir les trois invariants.
5. Ouvrez une Pull Request claire (un sujet = une PR).

Signalements de bugs et idées : via les *issues* GitHub.

---

## 13 · Licence

[MIT](LICENSE). Vous êtes libre d'utiliser, modifier et redistribuer My_OS.

## 14 · Crédits

Conçu dans la lignée de [My_AI](https://github.com/gonicolas12/My_AI). Construit pour
rester **local, privé et sûr**. Merci aux projets qui le rendent possible : Arch Linux,
archiso, Ollama, PySide6/Qt, polkit, et l'écosystème Python.
