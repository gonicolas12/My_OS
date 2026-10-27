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

### v1.4 — correctif du son (octobre 2026)
- [x] ISO publiée : [release `v1.4`](https://github.com/gonicolas12/My_OS/releases/tag/v1.4)
  (mêmes paquets que la v1.2 et la v1.3 : instantané Arch Linux Archive du 2026-10-20),
  avec sa somme SHA-256 ; validée en VM (modèle local, 42 scénarios de permissions,
  interface, saisie de la clé API, son au premier usage).
- [x] Premier réglage du son de la session : le serveur son démarre à la demande et sa
  sortie n'existait pas encore, si bien que `set_volume` et `set_mute` échouaient
  (« No such entity ») à la première demande après le démarrage. La commande est
  rejouée le temps que la sortie apparaisse (~0,1 s mesuré en VM, 5 s au plus). Trouvé
  pendant la validation de la v1.3.

### v1.5 — contrôle de l'ordinateur (octobre 2026)
- [x] ISO publiée : [release `v1.5`](https://github.com/gonicolas12/My_OS/releases/tag/v1.5)
  (instantané Arch Linux Archive du 2026-10-20, comme la v1.2 à la v1.4, plus les paquets
  du contrôle : `xdotool`, Tesseract et ses données française et anglaise,
  `python-pillow`), avec sa somme SHA-256 ; validée en VM (modèle local, 42 scénarios de
  permissions, interface, son au premier usage, saisie de la clé API, et contrôle de
  l'ordinateur sur processeur seul : liste écrite dans Mousepad, arrêt par Échap,
  terminal escaladé et refusé, y compris par Ctrl+Alt+T et par « Exécuter un
  programme »).
- [x] Le modèle **voit l'écran et agit** à la souris et au clavier (session X11) : sept
  outils (`observe_screen`, `click`, `type_text`, `press_keys`, `scroll`, `open_app`,
  `focus_window`), en **local** par défaut.
- [x] **Perception sans modèle de vision** : arbre d'accessibilité AT-SPI (GTK, Qt,
  Firefox, LibreOffice ; Chromium et Electron avec la variable de session), complété par
  l'OCR Tesseract là où une application n'expose rien ; éléments numérotés, le modèle
  agit par numéro. Capture annotée en plus sur carte graphique, et pour le cloud.
- [x] **Indicateur** : bords de l'écran lumineux en orange clair, qui apparaissent et
  disparaissent en fondu, et barre « Cliquez ici ■ ou appuyez sur Échap pour arrêter »
  avec l'action en cours. Vérifié en VM en échantillonnant l'écran : l'affichage suit
  l'animation à ~50 ms près.
- [x] **Sécurité** : accord par requête (niveau 2), arrêt garanti (Échap, ■), cible
  réelle jugée et revérifiée avant d'agir, blocklist (fenêtres de My_OS, mots de passe,
  écran verrouillé, polkit, combinaisons système), escalade (terminaux, paramètres,
  libellés destructeurs). Cf. [SECURITY.md](SECURITY.md) menace 5.
- [x] Validé en VM sur une ISO de la branche, avec le modèle local réel : Mousepad
  (ouvrir, taper, compte rendu), formulaire web dans Firefox (champs, liste, case à
  cocher, envoi confirmé), Thunar ; arrêt par Échap et par le bouton ■ ; terminal
  escaladé et refusé ; 42 scénarios de permissions de la v1 toujours conformes ;
  754 tests sous Linux.
- [x] Trouvés en tournant la démo du README sur un écran en 1280×720, corrigés et
  re-testés : une confirmation demandée pendant le contrôle passait en partie sous la
  barre d'arrêt (elle s'affiche maintenant au-dessus) ; « Dans Firefox, remplis… »
  rouvrait Firefox en double (`open_app` passe à la fenêtre d'une application déjà
  ouverte).
- [x] Trouvé pendant la validation de l'ISO : l'ouverture d'un terminal refusée, le
  modèle passait par Ctrl+Alt+T ; et le lanceur « Exécuter un programme » (Alt+F2), qui
  exécute une commande sans terminal, n'était pas escaladé. Lanceurs de commandes et
  raccourcis qui ouvrent un terminal ou un lanceur passent au niveau 2.

**Mesuré avant de choisir (labo, octobre 2026).** Sur 28 tâches réparties sur 6 écrans
réels de l'ISO (Thunar, Mousepad, « Enregistrer sous », paramètres, recherche
d'applications, bureau), qwen3.5:4b désigne le bon élément 27 fois sur 28 avec la liste
numérotée seule (2,6 s par tâche sur l'iGPU du labo), 28 sur 28 avec la capture annotée
en plus (10,8 s), mais **3 sur 28** quand il doit donner des coordonnées sur la capture
brute. La note qui suit (« nécessite un modèle cloud ») était donc juste pour le
*grounding* au pixel d'un petit modèle, fausse pour un contrôle par éléments numérotés.

### v1.6 — français et anglais (octobre 2026)
- [x] ISO publiée : [release `v1.6`](https://github.com/gonicolas12/My_OS/releases/tag/v1.6)
  (instantané Arch Linux Archive du 2026-10-20, les mêmes paquets que la v1.5), avec sa
  somme SHA-256 ; validée en VM sur processeur seul, avec le modèle local : My_OS en
  anglais au premier lancement de l'ISO, demandes au modèle dans les deux langues,
  contrôle de l'ordinateur en anglais puis en français (Mousepad, arrêt par Échap,
  terminal et lanceur de commandes refusés), 42 scénarios de permissions dans chaque
  langue, son au premier usage, saisie de la clé API.
- [x] **Toute l'application dans les deux langues** : interface du popup (barre de
  titre, suggestions, statuts, cartes d'outils, confirmations, accord et barre d'arrêt
  du contrôle), réponses du modèle local et du cloud (un prompt système et des
  descriptions d'outils par langue), messages des outils, description de l'écran et
  noms des applications pendant le contrôle de l'ordinateur.
- [x] **Langue de Linux au premier lancement** : français si la session est en
  français, anglais pour toute autre langue (`C` et `POSIX` compris). Bouton 🌐 FR / EN
  dans la barre de titre : changement immédiat, sans redémarrer, choix mémorisé ;
  `language: auto | fr | en` dans `config.yaml`.
- [x] **La langue voyage avec chaque requête** : le daemon traite toute la requête
  (prompt, outils, confirmations, notes au modèle) dans la langue du popup.
- [x] **Sécurité inchangée** : la langue ne décide de rien, mêmes niveaux et mêmes
  décisions ; libellés destructeurs et combinaisons reconnus dans les deux langues,
  quelle que soit celle de My_OS. Cf. [SECURITY.md](SECURITY.md) §4, règle 9.
- [x] **Le français ne change pas** : prompts et textes français repris à l'identique de
  la v1.5 (comparés avant et après) ; seule une consigne de l'écran s'y ajoute
  (formulaire : pas d'Entrée dans un champ).
- [x] Validé en VM (ISO de la v1.5 avec le code de la branche, modèle local sur l'iGPU
  du labo) : 42 scénarios de permissions dans chaque langue, mêmes décisions ;
  13 demandes en langage naturel, bon outil 13 fois sur 13 dans chaque langue ;
  contrôle de l'ordinateur en anglais (Mousepad ; terminal escaladé et refusé par
  toutes les voies) ; ~860 tests sous Linux.
- [x] Trouvés pendant la validation, corrigés et re-testés : les exemples entre
  guillemets droits des descriptions d'outils anglaises étaient recopiés par le modèle
  (« Open a terminal and run ls » ouvrait Mousepad 11 fois sur 20 ; 0 sur 20 sans
  guillemets) ; après un passage du français à l'anglais en cours de conversation, le
  modèle répondait parfois dans l'ancienne langue (environ 1 fois sur 10 ; 25 sur 25
  avec un rappel de la langue à chaque demande quand la conversation en mélange deux).
- [x] Trouvés en tournant les médias du README, corrigés et re-testés : « Submit », le
  libellé anglais le plus courant d'un envoi de formulaire, n'était pas escaladé comme
  « Envoyer » et « Send » ; le modèle tapait un champ avec Entrée, ce qui envoyait le
  formulaire dès le premier champ, sans la confirmation renforcée de son bouton : Entrée
  dans un champ d'une ligne est désormais escaladée quand la fenêtre montre un bouton
  sensible, et le prompt demande de cliquer le bouton (démo anglaise refaite : champs
  remplis sans Entrée, « Submit » confirmé au niveau 2) ; l'écran d'accueil écrasait ses
  suggestions quand la demande en cours de saisie faisait plusieurs lignes (bas du texte
  coupé sur la première image de la démo du contrôle) : le logo s'efface, puis
  l'accueil défile.
- [x] **README en anglais** ([README.md](../README.md)) et en français
  ([README.fr.md](../README.fr.md)), captures et démos refaites dans les deux langues,
  bouton de langue visible.

### Encore à éprouver
- [ ] Un vrai PC (pilotes Wi-Fi, rétroéclairage, cartes graphiques ; son et Wi-Fi ont été
  validés en VM avec du matériel simulé). Le modèle sur une vraie carte graphique reste à
  mesurer.
- [ ] Le mode cloud avec une vraie clé API.

---

## 🔭 Au-delà de la v1 (vision long terme, **non promise**)

Documenté pour montrer la direction. Chaque item est un projet en soi.

- ~~**Contrôle d'applications via accessibilité (AT-SPI)**~~ — fait (cf. « Contrôle de
  l'ordinateur » ci-dessus).
- **Vision pure, sans arbre ni texte** (jeux, canevas, applications sans accessibilité ni
  texte lisible) — fait en partie : capture annotée sur carte graphique. Reste un
  modèle local spécialisé dans la localisation d'éléments (GUI-Owl-1.5, UI-Venus-1.5,
  Holo2 : 57 à 71 sur ScreenSpot-Pro selon leurs auteurs, disponibles en GGUF) en
  complément, sur carte graphique. Sur processeur, une capture coûte ~1 000 tokens par
  étape : trop lent.
- **Sécurité anti-injection avancée** — défenses plus poussées contre l'injection de
  prompt indirecte (problème de recherche ouvert).
- **Wayland natif grand public** — port Wayland sorti du statut expérimental, confinement
  Wayland comme couche de sécurité supplémentaire, ISO installable par tous.
- **D'autres langues** — une langue de plus, c'est un catalogue `core/locales/<code>.py`,
  un prompt système et des descriptions d'outils (cf. [INTERFACES.md](INTERFACES.md)
  §11), puis la même validation avec le modèle local. Les libellés destructeurs lus à
  l'écran ne sont aujourd'hui reconnus qu'en français et en anglais.

> Le routage par mode d'accès (commandes directes → accessibilité → vision) suit toujours
> le principe : **essayer le mode le plus fiable d'abord, la vision en dernier recours.**

### Améliorations identifiées pendant les tests en VM
Plus modestes, et directement issues de l'usage réel :

- **Annuler une action** : le contrat des outils prévoit `reversible`/`undo_data`, pas
  encore exploités.
- **Consulter l'historique** depuis le popup (aujourd'hui : fichier SQLite d'audit).
- **Messages polkit traduits** (« Request dismissed » s'affiche tel quel).
- **Menus standard de Qt** : le clic droit dans le champ de la clé API reste en anglais,
  même en français (traductions de Qt à charger, paquet `qt6-translations`).
- **Confirmations en double** : le modèle demande parfois « Voulez-vous vraiment… ? »
  en texte avant d'appeler l'outil, puis la fenêtre de confirmation redemande ; après
  un refus, il lui arrive aussi de reproposer la même action, et rarement d'affirmer
  l'avoir faite (vu une fois en octobre 2026, la carte de l'outil affichant bien le
  refus). À cadrer dans le prompt (la fenêtre est la seule confirmation nécessaire).
- **Contrôle de l'ordinateur plus rapide** : chaque étape coûte un tour du modèle
  (~5 à 25 s sur l'iGPU du labo selon ce qu'il écrit). Pistes : plusieurs actions par
  tour quand elles ne dépendent pas l'une de l'autre, observation plus courte.
- **Valider un formulaire par Entrée** n'est escaladé (v1.6) que si la fenêtre montre un
  bouton au libellé reconnu ; un formulaire sans bouton nommé, ou une icône sans
  libellé, ne l'est pas.
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
