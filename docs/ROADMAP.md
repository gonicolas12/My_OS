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

### v1.1 — interface du popup repensée (octobre 2026)
- [x] ISO publiée : [release `v1.1`](https://github.com/gonicolas12/My_OS/releases/tag/v1.1)
  (paquets figés sur l'instantané Arch Linux Archive du 2026-10-20), avec sa somme
  SHA-256 ; validée en VM (interface sous xfwm4, 42 scénarios de permissions, modèle local).
- [x] Fenêtre libre : redimensionnable par les bords et les coins, déplaçable, agrandie
  (double-clic sur la barre de titre), plein écran (`F11`) ; taille, position et état
  mémorisés. Validé en VM sous xfwm4, confirmation comprise au-dessus du plein écran.
- [x] Rappel « Échap pour fermer » en haut à droite.
- [x] Conversation sur le gabarit de l'interface de My_AI, en noir et orange : bulle
  de l'utilisateur, réponse sous un en-tête (heure, durée), séparation nette entre les
  échanges, blocs de code avec « Copier », écran d'accueil avec suggestions, mise en
  page qui s'adapte à la largeur.
- [x] Sorties d'outils lisibles : message IPC `tool_result`, carte avec le statut
  (terminé, échec, refusé, bloqué), texte brut où les retours à la ligne sont gardés.
- [x] État du service visible (local, cloud, hors ligne) : une demande envoyée sans
  daemon le signale au lieu d'attendre indéfiniment.

### v1.2 — modèle local (octobre 2026)
- [x] ISO publiée : [release `v1.2`](https://github.com/gonicolas12/My_OS/releases/tag/v1.2)
  (paquets figés sur l'instantané Arch Linux Archive du 2026-10-20), avec sa somme
  SHA-256 ; validée en VM (modèle local, 42 scénarios de permissions, interface).
- [x] Ollama joint sur `127.0.0.1` : un « localhost » configuré y est ramené.
- [x] Contexte du modèle (cache K/V) en 8 bits sur l'ISO, par un drop-in systemd du
  service `ollama` : deux fois moins de mémoire, mêmes outils choisis.
- [x] Fenêtre de contexte de 16 384 tokens (`model.num_ctx`), historique envoyé ajusté à
  la fenêtre : le prompt système n'est plus coupé et les réponses ne s'arrêtent plus net
  au fil d'une conversation. Validé en VM : 8 demandes à la suite sans `Ctrl+L`, résumés
  de documents de 12 et 28 Ko, 13 demandes de la sonde du modèle.
- [x] Prompt lu par lots de 256 tokens : une nouvelle conversation ne relit que ~250 tokens
  du prompt système au lieu de ~1 000 (médiane ~10 s au lieu de ~30 s sans GPU, en VM).
- [x] Modèle gardé chargé 1 h après une requête (`model.keep_alive`, 5 min chez Ollama) :
  plus de relecture complète du prompt système après une courte pause.
- [x] Ollama en local seul (`OLLAMA_NO_CLOUD=1` sur l'ISO) : plus d'appels à ollama.com au
  démarrage ni toutes les 4 h, seulement pour télécharger un modèle.
- [x] Carte graphique sur l'ISO : backend Vulkan d'Ollama et pilotes Vulkan libres (AMD,
  Intel, NVIDIA via NVK), +18 Mo d'ISO. Validé en VM sans GPU (rien ne change sur
  processeur) et sur un périphérique Vulkan logiciel (modèle chargé en entier dessus,
  contexte en 8 bits compris, réponse juste).

### v1.3 — correctif du mode cloud (octobre 2026)
- [x] ISO publiée : [release `v1.3`](https://github.com/gonicolas12/My_OS/releases/tag/v1.3)
  (mêmes paquets que la v1.2 : instantané Arch Linux Archive du 2026-10-20), avec sa
  somme SHA-256 ; validée en VM (modèle local, 42 scénarios de permissions, interface,
  saisie de la clé API).
- [x] La saisie de la clé API (bascule « ☁ Cloud » ou bouton de la clé) et l'alerte
  d'échec d'enregistrement s'ouvrent au-dessus du popup. Sous xfwm4, elles s'ouvraient
  dessous : modales, elles bloquaient le popup sans être visibles. Trouvé en VM en
  préparant les captures de présentation.

### Encore à éprouver
- [ ] Un vrai PC (pilotes Wi-Fi, rétroéclairage, cartes graphiques ; son et Wi-Fi ont été
  validés en VM avec du matériel simulé). Le modèle sur une vraie carte graphique reste à
  mesurer.
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
- **Messages polkit traduits** (« Request dismissed » s'affiche tel quel).
- **Confirmations en double** : le modèle demande parfois « Voulez-vous vraiment… ? »
  en texte avant d'appeler l'outil, puis la fenêtre de confirmation redemande ; après
  un refus, il lui arrive aussi de reproposer la même action, et rarement d'affirmer
  l'avoir faite (vu une fois en octobre 2026, la carte de l'outil affichant bien le
  refus). À cadrer dans le prompt (la fenêtre est la seule confirmation nécessaire).
- **Temps de réponse sans GPU** : ~10 s par demande neuve en VM 8 cœurs depuis les lots
  de 256 tokens, mais près d'une minute quand le modèle doit être chargé (première
  requête de la session, ou après 1 h sans requête) : il relit alors tout le prompt
  système et les schémas d'outils (~2 430 tokens). Pistes : prompt et schémas plus
  courts, modèle préchargé à l'ouverture de session, modèle plus petit par défaut sur les
  machines modestes.

---

## Principe directeur

Quelle que soit l'évolution, les **trois invariants de sécurité** (cf.
[SECURITY.md](SECURITY.md)) restent non négociables : le LLM ne décide jamais de son
niveau de permission ; tout contenu lu est une donnée, pas une instruction ; le daemon
ne tourne jamais en root.
