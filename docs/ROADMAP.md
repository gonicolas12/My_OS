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

### Validation en VM (octobre 2026)
- [x] ISO démarrée en VM QEMU/KVM, **BIOS et UEFI** : autologin, 1er run, raccourci → popup.
- [x] **42 scénarios de permissions** avec effet réel vérifié ; 6 contournements trouvés
  (grants couvrant le niveau 2, chemins relatifs, liens symboliques, `//etc`), corrigés
  et re-testés (cf. [SECURITY.md §8](SECURITY.md)).
- [x] Confirmation **visible** (au-dessus du popup) et **« Refuser » par défaut**.
- [x] Élévation polkit (`pacman`) depuis le service utilisateur ; paquets de l'ISO figés
  sur un instantané de l'Arch Linux Archive (recherche et installation en live).
- [x] Modèle local : bon outil choisi en langage naturel, recherche de paquets par
  mots-clés anglais, injection de prompt dans un fichier résistée.

### Publication de la v1.0 (octobre 2026)
- [x] ISO publiée : [release `v1.0`](https://github.com/gonicolas12/My_OS/releases/tag/v1.0)
  (paquets figés sur l'instantané Arch Linux Archive du 2026-10-19), avec sa somme SHA-256.
- [x] Menus de démarrage aux couleurs de My_OS ; démarrage validé en VM sur CD et sur clé
  USB (Ventoy et image brute), en BIOS et en UEFI.

### Encore à éprouver
- [ ] Un vrai PC (pilotes Wi-Fi, rétroéclairage, cartes graphiques ; son et Wi-Fi ont été
  validés en VM avec du matériel simulé).
- [ ] Le mode cloud avec une vraie clé API.

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

### Améliorations identifiées pendant les tests en VM
Plus modestes, et directement issues de l'usage réel :

- **Annuler une action** : le contrat des outils prévoit `reversible`/`undo_data`, pas
  encore exploités.
- **Consulter l'historique** depuis le popup (aujourd'hui : fichier SQLite d'audit).
- **Sorties d'outils plus lisibles** : les retours à la ligne (liste de fichiers, sortie
  de pacman) sont perdus dans le rendu du popup.
- **Messages polkit traduits** (« Request dismissed » s'affiche tel quel).
- **Confirmations en double** : le modèle demande parfois « Voulez-vous vraiment… ? »
  en texte avant d'appeler l'outil, puis la fenêtre de confirmation redemande ; après
  un refus, il lui arrive aussi de reproposer la même action. À cadrer dans le prompt
  (la fenêtre est la seule confirmation nécessaire).
- **Temps de réponse sans GPU** : de 20 s à près de 2 min par requête en VM 8 cœurs ;
  piste : modèle plus petit par défaut sur les machines modestes.

---

## Principe directeur

Quelle que soit l'évolution, les **trois invariants de sécurité** (cf.
[SECURITY.md](SECURITY.md)) restent non négociables : le LLM ne décide jamais de son
niveau de permission ; tout contenu lu est une donnée, pas une instruction ; le daemon
ne tourne jamais en root.
