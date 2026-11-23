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

### v1.7 — mise à jour de sécurité de l'ISO (novembre 2026)
- [x] ISO publiée : [release `v1.7`](https://github.com/gonicolas12/My_OS/releases/tag/v1.7),
  avec sa somme SHA-256 ; elle remplace celle de la v1.6, pour une raison de sécurité.
  Paquets figés sur l'instantané Arch Linux Archive du **2026-11-09** (le 2026-10-20 pour
  la v1.2 à la v1.6) : les 758 paquets de la v1.6, dont 111 mis à jour, plus le SDK du
  mode cloud et ses 9 dépendances (768 en tout).
- [x] **Certificats** : ca-certificates-mozilla 3.133 (avis Arch du 21 octobre 2026,
  certificats compromis de l'autorité intermédiaire eMudhra) ; le magasin de l'ISO passe
  de quatre racines emSign à une. TLS sert au `pip` du premier démarrage, au
  téléchargement du modèle et au mode cloud.
- [x] **Lecture de l'écran** : Tesseract 5.5.4 corrige une utilisation après libération
  (GHSA-jrf4-mx9r-v94h) qu'une image choisie par un attaquant déclenche quand plusieurs
  langues sont lues en mode LSTM, le cas de My_OS (`fra+eng`).
- [x] **Python 3.14.9** (pip 26.2.2, certifi 2026.10.23) et **Ollama 0.40.15** (appels
  d'outils de qwen3.5 dont un argument contient `</function>`) ; noyau 7.2.14, Mesa
  26.2.6.
- [x] **SDK du mode cloud depuis le dépôt Arch** (`python-anthropic` 2.6.0, paquet signé et
  figé avec l'instantané) au lieu de `pip` au premier démarrage, qui prenait la dernière
  version publiée (2.7.0 au 10 novembre) ; `pip` n'installe plus que `pynput` et `ollama`.
- [x] **ISO constructible sans root** avec archiso 91 et plus (`build_iso.sh` vérifie
  archiso, les plages de `/etc/subuid` et `/etc/subgid` et l'espace de noms avant le
  build ; en root, rien ne change). Validé en root dans le labo ; le build sans root y
  est impossible (`newuidmap` sans ses capacités sous WSL) et reste à éprouver ailleurs.
- [x] **Mode cloud avec Claude Sonnet 5.5** : sa réflexion revient telle quelle dans la
  boucle d'outils d'une demande, celle des demandes précédentes est retirée ; une
  réponse coupée (16 000 tokens par réponse, ou fenêtre de contexte pleine) ou refusée
  est signalée dans le popup, sans lancer d'outil. Éprouvé avec une vraie clé (cf.
  « Encore à éprouver » et [INTERFACES.md](INTERFACES.md) §5.2).
- [x] **Menus standard de Qt dans la langue de My_OS** : le clic droit dans un champ de
  texte (clé API, saisie, conversation) donne Annuler, Couper, Copier, Coller… en
  français quand My_OS est en français, en anglais sinon ; vérifié en VM sur le champ
  de la clé API.
- [x] Validé en VM sur processeur seul (novembre 2026) : sonde du modèle 5 sur 5 en
  français et en anglais, injection cachée dans un fichier ignorée ; contrôle de
  l'ordinateur en anglais (Mousepad, arrêt par Échap, terminal refusé par tous les
  chemins, commande tapée dans « Run Program » escaladée et refusée) et en français ;
  42 scénarios de permissions dans chaque langue, mêmes décisions ; OCR de l'écran en
  `fra+eng` ; son au premier usage ; saisie de la clé API ; Ollama en local seul et cache
  K/V en 8 bits toujours chargé. **Mêmes temps que la v1.6** sur les mêmes demandes :
  première demande à froid 80 et 102 s (v1.6 : 85 et 113 s), puis 11 à 12 s par demande
  (v1.6 : 10 à 12 s), ~10 tokens/s, bon outil 12 fois sur 12 à chaque passe.

### v1.8 — mise à jour de sécurité de l'ISO (novembre 2026)
- [x] ISO publiée : [release `v1.8`](https://github.com/gonicolas12/My_OS/releases/tag/v1.8),
  avec sa somme SHA-256 ; elle remplace celle de la v1.7, pour une raison de sécurité.
  Paquets figés sur l'instantané Arch Linux Archive du **2026-11-16** : les 768 paquets
  de la v1.7, dont 113 mis à jour, plus `openssl-3` pour Python (769 en tout).
- [x] **Serveur X** : xorg-server 21.1.26 (avis de X.Org du 10 novembre 2026) corrige
  huit failles, dont une écriture hors limites dans la pile de XTEST (CVE-2026-109093),
  l'extension par laquelle le contrôle de l'ordinateur clique et tape, et une écriture
  hors limites par l'EDID d'un écran malveillant (CVE-2026-109091) ; les autres touchent
  Xv, glamor et XRes.
- [x] **Python 3.14.10** : `ssl` refuse les clés de l'autorité intermédiaire eMudhra
  compromise, et le filtre d'extraction de `tarfile` est corrigé (CVE-2026-115557,
  CVE-2026-114583). Python passe sur `openssl-3` 3.5.9, la branche maintenue à long
  terme.
- [x] **OpenSSL 4.1** pour le reste du système (curl, pacman, systemd, Qt…) : la 3.6 de
  la v1.7 n'est plus maintenue. Aucune bibliothèque introuvable de plus qu'en v1.7 (les
  mêmes 55 fichiers, tous des modules facultatifs). TLS vérifié par curl, pacman
  (synchronisation, téléchargement et signature d'un paquet), `openssl s_client`,
  Python (`pip` du premier démarrage, `urllib`), Ollama (téléchargement du modèle) et le
  mode cloud. qt6-base 6.12.0-5 corrige les blocages de Qt avec OpenSSL 4.1 : popup
  sans blocage, `QSslSocket` sur OpenSSL 4.1.
- [x] **Noyau 7.3.4** (la branche 7.2 est en fin de vie), **glibc 2.44+r78**
  (CVE-2026-84243, traversée de répertoire par la variable `LANGUAGE`), **Ollama
  0.40.16** (llama.cpp mis à jour), Mesa 26.2.7, PipeWire 1.6.11, systemd 262.2 et
  `python-anthropic` 2.8.0.
- [x] **Son au premier usage** : avec PipeWire 1.6.11 et WirePlumber 0.5.19, la sortie
  son apparaît un peu plus tard au démarrage du serveur, et WirePlumber y restaure
  l'état mémorisé (volume, son coupé) juste après la commande : un réglage posé à ce
  moment-là était perdu sans erreur (l'outil répondait « Son coupé » ou « Son
  rétabli »). Quand le serveur démarrait, la commande réussie est désormais reposée 0,5
  puis 1,5 s après. Mesuré en VM, PipeWire arrêté avant chaque essai, en alternance sur
  la même ISO : 18 réglages sur 18 tenus avec le correctif, 13 sur 18 avec le code de
  la v1.7. Trouvé pendant la validation.
- [x] **Mode cloud moins cher** : cache de prompt (outils, prompt système et
  conversation relus au vingtième du prix d'entrée) : 0,16 centime par demande en
  rafale au lieu de 1,04, 0,82 au lieu de 2,87 dans une conversation avec outils, 9,9
  au lieu de 20,3 sur un scénario de bout en bout en VM (cf. « Cache de prompt en mode
  cloud » plus bas et [INTERFACES.md](INTERFACES.md) §5.2).
- [x] **Premier démarrage plus robuste** : sans réseau à l'ouverture de la session, les
  dépendances Python s'installent en tâche de fond dès son retour, un avis du bureau
  dit si My_OS est prêt, et le téléchargement du modèle est retenté (5 essais, relancé
  à la session suivante s'il n'a pas abouti) ; tant que le modèle manque, le popup dit
  pourquoi et donne la commande (cf. « Téléchargement du modèle au premier démarrage »
  et « Premier démarrage hors ligne » plus bas).
- [x] **Contrôle de l'ordinateur en mode cloud** : les captures restent jusqu'à la fin
  de la demande, si bien que la réflexion de Claude n'est plus écartée et que le cache
  relit la conversation (11 à 31 % moins cher par tâche ; cf. « Captures gardées en
  mode cloud » plus bas).
- [x] **Raccourci après une reconnexion rapide** : myosd redémarre à chaque ouverture
  de session, si bien que Ctrl+Alt+Espace répond même après une reconnexion en moins de
  ~10 s, et son arrêt ne finit plus en erreur (cf. « Reconnexion rapide » plus bas).
- [x] Validé en VM sur processeur seul (novembre 2026) : sonde du modèle 5 sur 5 en
  français et en anglais, injection cachée dans un fichier ignorée ; contrôle de
  l'ordinateur en anglais (Mousepad, arrêt par Échap, terminal refusé par tous les
  chemins, commande tapée dans « Run Program » escaladée et refusée) et en français ;
  42 scénarios de permissions dans chaque langue, mêmes décisions ; OCR de l'écran en
  `fra+eng` ; saisie de la clé API ; menus de Qt dans les deux langues ; tests du mode
  cloud, des menus de Qt et des permissions lancés dans la VM avec le SDK de l'ISO ;
  mode cloud de bout en bout avec une vraie clé (Claude Sonnet 5.5). **Mêmes temps que la v1.7**, mesurés coup sur coup sur le même
  hôte : première demande à froid 72 et 98 s (v1.7 : 93 et 104 s), puis 11 à 12,5 s par
  demande (v1.7 : 10 à 14 s), 9 à 10 tokens/s, bon outil 12 fois sur 12 à chaque passe ;
  cache K/V toujours chargé en 8 bits, Ollama en local seul.

### v1.9 — mise à jour de sécurité de l'ISO (novembre 2026)
- [ ] ISO à publier : elle remplacera celle de la v1.8, pour une raison de sécurité.
  Paquets figés sur l'instantané Arch Linux Archive du **2026-11-23** : les 769 paquets
  de la v1.8, dont 55 mis à jour, plus `simdutf` (770 en tout).
- [x] **GPGME de pacman** : gpgme 2.3.1 corrige une double libération qu'un identifiant
  d'utilisateur très long, dans une clé OpenPGP piégée, déclenche dans le code qui lit
  les sorties de gpg (CVE-2026-129975, exécution de code à distance non exclue).
  pacman s'en sert pour vérifier la signature des paquets, en root : synchronisation au
  démarrage (`myos-pacman-sync`), `install_package` et `update_system` (via `pkexec`).
- [x] **Polices du serveur X** : libXfont2 2.0.10 et libfontenc 1.1.10 (avis de X.Org du
  18 novembre 2026). Un client local pouvait faire planter le serveur X par de simples
  requêtes, sans police piégée (CVE-2026-109920) : tout programme de la session, y compris
  un programme lancé par l'assistant, pouvait donc fermer la session. Une police PCF
  piégée faisait lire hors limites (CVE-2026-109909), un fichier d'encodage écrire hors
  limites (CVE-2026-109908, élévation de privilèges si le serveur X tourne en root).
- [x] **fontconfig 2.18.4** (accès hors limites dans `FcCharSetMerge`), qui sert à Qt
  (popup), GTK et Pango.
- [x] **Magasin de certificats** : NSS et ca-certificates-mozilla 3.134. Racines pour TLS :
  115 → 110 (Buypass Class 2 et 3, Certigna, QuoVadis Root CA 1 G3 et 3 G3 retirées) ;
  pour le courriel : 86 → 81, dont emSign Root CA - G1 (suite de l'affaire eMudhra de la
  v1.7), qui ne vaut plus que pour TLS, avec une date de défiance posée par NSS.
- [x] **Noyau 7.3.5**, **Ollama 0.40.17** (chaque rechargement du modèle est désormais
  journalisé avec sa raison), **Mesa 26.3.1** (nouvelle série : corrige notamment des
  lenteurs de llama.cpp en Vulkan sur Intel et AMD), **at-spi2-core 2.62.2**
  (l'accessibilité que lit le contrôle de l'ordinateur ne plante plus quand une connexion
  directe échoue tôt), gnutls 3.8.14, libseccomp 2.6.2, `python-anthropic` 2.12.0,
  `python-yaml` 6.0.4. glibc reste en 2.44+r78.
- [x] Validé en VM (novembre 2026, labo du PC portable : VM de 4 Go, sans modèle local
  dans la VM ; modèle sur la carte graphique de l'hôte pour les demandes au modèle) :
  pacman (synchronisation, téléchargement, signature vérifiée par GPGME 2.3.1, paquet
  altéré refusé) et `install_package` par le popup avec la fenêtre de polkit ; TLS du
  magasin 3.134 (pacman, `pip` du premier démarrage, curl, Python, `ollama pull`, SDK
  du mode cloud jusqu'à api.anthropic.com) ; 42 scénarios de permissions dans chaque
  langue, mêmes décisions ; sonde du modèle 5 sur 5 en français et en anglais,
  injection cachée dans un fichier ignorée ; contrôle de l'ordinateur en anglais
  (Mousepad, arrêt par Échap, terminal refusé, commande tapée dans « Run Program »
  escaladée et refusée) et en français ; OCR de l'écran en `fra+eng` ; son au vrai
  premier usage (12 réglages sur 12 tenus) ; saisie de la clé API ; menus de Qt dans les
  deux langues ; 488 tests lancés dans la VM avec le Python et le SDK 2.12 de l'ISO ;
  premier démarrage hors ligne puis en ligne ; deux reconnexions rapides et une lente.
  Ollama de l'ISO sur processeur, hors VM (8 cœurs de l'hôte, chiffres d'un autre
  banc que ceux de la v1.8), contre celui de la v1.8 en alternance : **mêmes temps** —
  première demande à froid 77 à 80 s (v1.8 : 79 à 86 s), puis 7,5 à 7,7 s par demande,
  11,8 tokens/s, bon outil 12 fois sur 12 à chaque passe, mêmes outils et mêmes
  réponses ; cache K/V toujours chargé en 8 bits, Ollama en local seul, aucun
  rechargement du modèle entre deux demandes. Restent à éprouver : le mode cloud avec
  une vraie clé (seule la connexion TLS l'a été, avec une fausse clé) et Mesa 26.3 sur
  une vraie carte graphique.

### Encore à éprouver
- [ ] Un vrai PC (pilotes Wi-Fi, rétroéclairage, cartes graphiques ; son et Wi-Fi ont été
  validés en VM avec du matériel simulé). Le modèle sur une vraie carte graphique reste à
  mesurer.
- [x] Le mode cloud avec une vraie clé (novembre 2026, Claude Sonnet 5.5). De bout en
  bout dans la VM de l'ISO v1.7, par le popup et le daemon : processus listés, écriture
  d'un fichier confirmée au niveau 1, lecture en anglais après un changement de langue
  dans la même conversation, contrôle de l'ordinateur accordé au niveau 2 (Mousepad
  ouvert, texte tapé) ; 9 appels, aucune erreur ni bloc de réflexion écarté, 1 à 2 s par
  appel. Au banc (SDK 2.6, 13 demandes en français et 13 en anglais, deux passes) : bon
  outil 48 fois sur 52 avec le réglage par défaut (les 4 autres : « Désinstalle glibc »,
  refusé par écrit avec une explication), ~1 centime par requête. Réglage par défaut
  gardé (cf. [INTERFACES.md](INTERFACES.md) §5.2).

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
- ~~**Cache de prompt en mode cloud**~~ — fait : les outils et le prompt système (~5 200
  tokens en français, ~4 300 en anglais), relus jusque-là à plein tarif à chaque
  requête, sont lus depuis le cache au vingtième du prix d'entrée (et non au dixième :
  0,10 $ contre 2 $ par million de tokens sur Claude Sonnet 5.5) par toute requête de
  la même langue à moins de 5 minutes de la précédente, et la conversation aussi dans la
  boucle d'outils. Mesuré avec une vraie clé en novembre 2026 : 0,16 centime par demande
  en rafale au lieu de 1,04 (0,25 en moyenne sur 52 demandes, premières écritures
  comprises), 0,82 au lieu de 2,87 dans une conversation avec outils ; mêmes outils
  choisis, même délai de réponse. De bout en bout dans la VM de l'ISO v1.8, par le
  popup : 9,9 centimes au lieu de 20,3 pour un même scénario de 15 appels (processus,
  écriture confirmée, changement de langue, contrôle de l'écran). `drop_block` gardé
  (cf. [INTERFACES.md](INTERFACES.md) §5.2).
- ~~**Captures gardées en mode cloud**~~ — fait : pendant le contrôle de l'écran, la
  capture de chaque étape reste jusqu'à la fin de la requête au lieu d'être retirée à
  la suivante. L'historique envoyé ne change plus : la réflexion de Claude n'est plus
  écartée (aucun bloc, au lieu de 2 à 10 par tâche) et le cache relit la conversation
  au lieu de la réécrire. Mesuré de bout en bout dans la VM avec une vraie clé
  (novembre 2026), les deux codes en alternance : 3,1 centimes au lieu de 3,5 et 4,0 au
  lieu de 5,8 sur deux tâches dans Mousepad, toutes réussies, mêmes durées. Aucune
  capture de plus ne part : ce sont les mêmes, renvoyées (cf.
  [INTERFACES.md](INTERFACES.md) §5.2 et [SECURITY.md](SECURITY.md) menace 2).
- **Consulter l'historique** depuis le popup (aujourd'hui : fichier SQLite d'audit).
- **Messages polkit traduits** (« Request dismissed » s'affiche tel quel).
- ~~**Menus standard de Qt**~~ — fait : le clic droit dans un champ (clé API, saisie,
  conversation) suit la langue de My_OS (catalogue `qtbase` de Qt, paquet
  `qt6-translations`, déjà sur l'ISO).
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
- **Alt+F4 sur le bureau pendant le contrôle de l'ordinateur** : sans fenêtre
  d'application au premier plan, Alt+F4 (niveau 1) ouvre la fenêtre « Log out » de Xfce
  (Log Out, Restart, Shut Down…). Elle passe au-dessus de la confirmation de niveau 2 et
  capte le clavier : un Entrée pour refuser y choisit « Log Out ». Vu en VM en novembre
  2026 (ISO v1.9, code inchangé depuis la v1.5) : le modèle, privé de terminal, a fermé
  « la fenêtre » puis voulu cliquer sur « Log Out », clic bien escaladé au niveau 2,
  mais la touche Entrée du refus a fermé la session. Piste : Alt+F4 au niveau 2 quand
  aucune fenêtre d'application n'a le focus, comme les lanceurs de commandes.
- **Frappe en cours après l'arrêt du contrôle** : Échap ou ■ coupent la session, mais un
  `xdotool type` déjà lancé finit son texte (jusqu'à 10 s) dans la fenêtre qui a le
  focus, retours à la ligne compris ; vu en VM en novembre 2026, la fin d'une ligne en
  cours a été tapée dans le popup. Piste : arrêter la frappe en cours à l'arrêt, ou taper
  par morceaux en vérifiant le garde entre deux.
- **Temps de réponse sans GPU** : ~10 s par demande neuve en VM 8 cœurs depuis les lots
  de 256 tokens, mais 1 min 20 à près de 2 min quand le modèle doit être chargé
  (première requête de la session, ou après 1 h sans requête) : il relit alors tout le
  prompt système et les schémas d'outils (~3 500 tokens depuis le contrôle de
  l'ordinateur, ~2 430 en v1.2 ; mesuré en novembre 2026). Pistes : prompt et schémas plus
  courts, modèle préchargé à l'ouverture de session, modèle plus petit par défaut sur les
  machines modestes.
- ~~**Téléchargement du modèle au premier démarrage**~~ — fait : il n'avait droit qu'à
  un essai, et un réseau pas encore prêt (vu une fois en VM en novembre 2026 : délai
  dépassé pendant la connexion TLS au registre d'Ollama) laissait le modèle local
  absent jusqu'à un `ollama pull qwen3.5:4b` à la main. Il fait maintenant jusqu'à 5
  essais en tâche de fond, espacés de 30 s, 1, 2 puis 4 min, chacun après au plus 1 min
  d'attente du réseau, sans retarder la session ; l'espace libre est vérifié avant le
  premier, comme avant. Tant que le modèle manque, le popup dit pourquoi (il se
  télécharge encore, les essais ont échoué, la place manque) et donne la commande, en
  français ou en anglais ; une demande que le modèle n'a pas pu voir n'est plus gardée
  dans la conversation (cf. [INTERFACES.md](INTERFACES.md) §6.5). Validé en VM
  (novembre 2026), registre d'Ollama coupé depuis l'hôte dès le démarrage : l'ISO v1.8
  reste sans modèle après son unique essai ; avec le nouveau script, le premier essai
  échoue (délai dépassé), le deuxième, 30 s plus tard, télécharge le modèle une fois le
  registre rétabli, et le popup répond. Registre refusé jusqu'au bout : 5 essais en
  7 min 30, puis l'état d'échec. Messages du popup vérifiés dans les deux langues
  (téléchargement en cours, échec, place insuffisante) ; un `ollama pull` lancé à la
  main pendant le téléchargement le suit sans le refaire, et un téléchargement
  interrompu reprend là où il s'était arrêté.
- ~~**Premier démarrage hors ligne**~~ — fait : sans réseau à l'ouverture de la session
  (Wi-Fi pas encore choisi), `pip` échouait, le script s'arrêtait avant le modèle, et rien
  n'était retenté avant la session suivante, soit jamais en live. Vu en VM sur l'ISO v1.8,
  lien réseau coupé dès le démarrage : 19 s d'écran noir pendant les essais de `pip`, puis
  un bureau sans aucun message ; sans le module `ollama`, myosd plantait en boucle (71
  redémarrages en 4 min) et le raccourci n'ouvrait rien, même 2 min après le retour du
  réseau. Désormais le script crée le venv et rend la main (bureau en 2 s) ; en tâche de
  fond, il installe les dépendances et réessaie tant que la session dure, dès que
  NetworkManager annonce une connexion ; un avis du bureau dans la langue de My_OS dit,
  après 30 s sans réponse d'Internet, que My_OS n'est pas encore prêt, puis, une fois
  myosd démarré, qu'il l'est. myosd ne démarre qu'avec ses dépendances (condition de son
  unité, plus de plantage en boucle) : c'est le script qui le lance. À la session
  suivante, un téléchargement du modèle qui n'a pas abouti est relancé, y compris s'il
  n'a jamais commencé (état `pending`, noté avant le marqueur des dépendances) ; un
  modèle déjà là, tiré à la main, est noté téléchargé sans contrôle d'espace. Le popup ne
  suppose plus de cause : le modèle « se télécharge, ou attend un nouvel essai », ou « n'a
  pas pu être téléchargé (essais en échec ou interrompus) ». Validé en VM (novembre
  2026), puis revérifié après une relecture finale : lien rétabli au bout de 3 min,
  dépendances installées en 2 s, myosd démarré sans un redémarrage, l'avis « prêt »
  remplace l'autre, le popup répond (modèle en cours de téléchargement) ; modèle en échec
  après 5 essais (registre refusé), puis nouvelle session : téléchargement relancé, modèle
  là 3 min plus tard, réponse du popup. En ligne : bureau en 2 à 6 s (création du venv),
  raccourci prêt 4 s après l'ouverture de session, aucun avis. Une session fermée
  pendant l'attente arrête sa tâche (LightDM arrête le groupe de processus de la
  session) : la suivante relance la sienne. Avis vérifiés en français et en anglais.
- ~~**Reconnexion rapide**~~ — fait : après une fin de session suivie d'une reconnexion
  en moins de ~10 s, le gestionnaire systemd utilisateur survit, et le myosd de la
  session précédente avec lui, son écoute du raccourci restée sur l'ancien serveur X :
  `~/.xprofile` le « démarrait » sans effet, et Ctrl+Alt+Espace n'ouvrait plus rien
  (défaut de la v1.8, vu en VM en novembre 2026 en validant le premier démarrage hors
  ligne). `~/.xprofile` le redémarre désormais à chaque ouverture de session : il écoute
  le serveur X de la session courante et repart à neuf, grants de session compris ;
  tant que les dépendances du premier démarrage manquent, le démarrage reste sauté et
  c'est le script qui le lance. Son arrêt, une fois le serveur X disparu, ne finit plus
  en erreur (trace et état d'échec au journal en v1.8), et ne peut plus retarder la
  session de plus de 10 s. Validé en VM sur l'ISO v1.8 avec le code de la branche :
  deux reconnexions rapides (l'ancien daemon s'arrête proprement, le nouveau démarre
  50 ms plus tard), une lente (gestionnaire relancé), un premier démarrage hors ligne
  puis en ligne, chacun en reconnexion rapide (démarrage sauté, puis lancé par le
  script) : le raccourci ouvre le popup à chaque fois.

---

## Principe directeur

Quelle que soit l'évolution, les **trois invariants de sécurité** (cf.
[SECURITY.md](SECURITY.md)) restent non négociables : le LLM ne décide jamais de son
niveau de permission ; tout contenu lu est une donnée, pas une instruction ; le daemon
ne tourne jamais en root.
