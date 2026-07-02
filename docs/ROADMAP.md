# 🗺️ Roadmap de My_OS

Ce document retrace la **v1 (terminée)** et la **vision long terme (post-v1)**, clairement
séparées. La v1 est le produit livré et fonctionnel ; la vision long terme décrit des
directions possibles, **non promises**.

---

## ✅ v1 — terminée

Chaque jalon a produit quelque chose de démontrable. Ils ont été construits dans l'ordre,
chacun stable et testé avant le suivant.

### Jalon 1 — Socle
- [x] Daemon `myosd` en service `systemd` **utilisateur** (jamais root).
- [x] Capture du raccourci clavier global (X11, `pynput`).
- [x] IPC daemon ↔ popup (socket Unix locale).
- [x] Popup Qt : apparaît centré au raccourci, prend le focus, se referme.

### Jalon 2 — Fichiers + permissions *(1re démo clé)*
- [x] Branchement Qwen/Ollama dans le daemon (+ stub de règles pour les tests).
- [x] Outils fichiers : lire, lister, écrire, déplacer, créer, supprimer.
- [x] **Moteur de permissions complet** : niveaux, escalade par arguments, blocklist,
  grants de session.
- [x] Journal d'audit SQLite ; dialogues de confirmation ; boucle agentique.

### Jalon 3 — Pilotage système *(démo « wow »)*
- [x] Paquets (`pacman`) : rechercher / installer / supprimer / mettre à jour.
- [x] Réglages via D-Bus (`set_brightness`/`set_volume`/`set_mute`/`set_wifi`).
- [x] Processus (`psutil`) : lister / tuer (avec garde-fous).
- [x] Élévation `polkit` ponctuelle par action (`pkexec`), daemon jamais root.

### Jalon 4 — Routeur cloud
- [x] Stockage de la clé API via `keyring` (jamais en clair).
- [x] Routage local/cloud **par requête** (toggle opt-in).
- [x] Indicateur « mode cloud actif » + journalisation des envois (sans secret ni contenu).

### Jalon 5 — Packaging
- [x] **Port Wayland (expérimental)** : raccourci via portal `GlobalShortcuts`, popup via
  `layer-shell` ; sélection de backend selon la session ; **repli X11 nominal intact**.
- [x] **Profil ISO `archiso`** : ISO live Xfce/X11, utilisateur non-root, service `myosd`
  utilisateur, modèle récupéré au 1er boot, script de build + doc.
- [x] **Documentation finalisée** : projet présenté comme open source fini, install réelle
  (sources + ISO), limites Wayland explicites.

### Sécurité — transverse à tous les jalons
- [x] Daemon en utilisateur, élévation `polkit` ponctuelle.
- [x] Séparation données/instructions appliquée dès le jalon 2.
- [x] [docs/SECURITY.md](SECURITY.md) tenu à jour (checklist par jalon).

---

## 🔭 Au-delà de la v1 (vision long terme, **non promise**)

Documenté pour montrer la direction. Chaque item est un projet en soi.

- **Contrôle d'applications via accessibilité (AT-SPI)** — piloter des applications par
  leur arbre d'accessibilité (cliquer un bouton nommé, remplir un champ). Local, mais
  support inégal selon les applications.
- **Contrôle par vision d'écran** — capture + modèle multimodal qui localise où cliquer.
  Universel mais lent, faillible, et **nécessite un modèle cloud** (les modèles locaux ne
  sont pas assez fiables en *grounding* GUI en 2026). Sécurité renforcée requise
  (confirmation avant chaque séquence, stop toujours accessible, périmètre limité à la
  fenêtre active).
- **Sécurité anti-injection avancée** — défenses plus poussées contre l'injection de
  prompt indirecte (problème de recherche ouvert).
- **Wayland natif grand public** — port Wayland sorti du statut expérimental, confinement
  Wayland comme couche de sécurité supplémentaire, ISO installable par tous.

> Le routage par mode d'accès (commandes directes → accessibilité → vision) suit toujours
> le principe : **essayer le mode le plus fiable d'abord, la vision en dernier recours.**

---

## Principe directeur

Quelle que soit l'évolution, les **trois invariants de sécurité** (cf.
[SECURITY.md](SECURITY.md)) restent non négociables : le LLM ne décide jamais de son
niveau de permission ; tout contenu lu est une donnée, pas une instruction ; le daemon
ne tourne jamais en root.
