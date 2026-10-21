# 🧩 Installation de My_OS

Deux façons d'installer My_OS : **depuis les sources** (pour développer ou installer sur
une Arch existante) et **via l'ISO live** (pour essayer rapidement). My_OS cible **Arch
Linux** (ou dérivés : EndeavourOS, etc.).

> **X11 est pleinement supporté.** Le port **Wayland est expérimental** : il requiert un
> compositeur exposant le portal `GlobalShortcuts` (raccourci) et le protocole
> `layer-shell` (popup overlay). Voir [§4](#4--notes-x11--wayland).

---

## 1 · Prérequis

- Arch Linux (ou dérivé), à jour.
- Python **3.10+**.
- [Ollama](https://ollama.com/download) pour le modèle local.
- Une session graphique (X11 nominal ; Wayland expérimental).

---

## 2 · Installation depuis les sources

### 2.1 · Dépendances système (pacman)

```bash
sudo pacman -S --needed \
    python python-pip \
    pyside6 \
    python-psutil python-dbus python-gobject python-yaml python-keyring python-evdev \
    polkit gnome-keyring libsecret \
    ollama \
    pipewire pipewire-pulse wireplumber libpulse \
    networkmanager \
    noto-fonts noto-fonts-emoji
```

> `pactl` (réglage du volume) vient de `libpulse`. `polkit` fournit `pkexec` (élévation
> ponctuelle). `gnome-keyring`/`libsecret` fournissent le trousseau (Secret Service) pour
> la clé API cloud. **`python-evdev`** est la dépendance native de `pynput` : sans lui,
> `pip install pynput` doit la compiler (gcc requis) et échoue souvent. Les polices
> `noto-fonts*` affichent les symboles du popup (☁, ⚠). Les noms de paquets peuvent
> évoluer : vérifiez avec `pacman -Si <pkg>`.

### 2.2 · Récupérer le projet et les dépendances Python

```bash
git clone https://github.com/gonicolas12/My_OS && cd My_OS
pip install -r requirements.txt        # ou un venv : python -m venv .venv && . .venv/bin/activate
```

> Astuce : avec un venv créé en `--system-site-packages`, vous réutilisez `pyside6`,
> `python-dbus` et `python-gobject` installés par pacman, et `pip` ne complète que le
> reste (`pynput`, `anthropic`, `ollama`…).

### 2.3 · Modèle local

```bash
# Ollama est un service SYSTÈME sous Arch (paquet `ollama`) :
sudo systemctl enable --now ollama  # ou `ollama serve` dans un terminal
ollama pull qwen3.5:4b              # ~3,3 Go ; recommandé ≥ 8 Go de RAM
```

Le paquet `ollama` seul ne sait utiliser que le processeur. Avec une **carte graphique**,
installez aussi le backend d'Ollama qui lui correspond, puis `sudo systemctl restart ollama` :

| Carte graphique | Paquets |
|---|---|
| NVIDIA, pilote propriétaire | `ollama-cuda` (avec `nvidia-utils`) |
| AMD | `ollama-rocm`, ou plus léger : `ollama-vulkan` + `vulkan-radeon` |
| Intel Arc | `ollama-vulkan` + `vulkan-intel` |
| NVIDIA Turing et suivantes, pilote libre | `ollama-vulkan` + `vulkan-nouveau` |

Après une requête, `ollama ps` affiche `100% GPU` dans sa colonne PROCESSOR. L'ISO
embarque la voie Vulkan.

Comme sur l'ISO, passez le **contexte du modèle en 8 bits** (cache K/V) : deux fois moins
de mémoire pour les mêmes réponses. Le même réglage met Ollama **en local seul**
(`OLLAMA_NO_CLOUD=1`) : il ne contacte plus ollama.com qu'au téléchargement d'un modèle,
alors qu'il le fait sinon au démarrage puis toutes les 4 h. C'est un drop-in systemd du
service `ollama`, fourni dans le dépôt :

```bash
sudo install -Dm644 packaging/archiso/airootfs/etc/systemd/system/ollama.service.d/10-myos.conf \
    /etc/systemd/system/ollama.service.d/10-myos.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

> Mesuré en VM (Ollama 0.40.6, sans GPU) : avec la fenêtre de 16 384 tokens de My_OS,
> cache de 512 à 272 Mio ; mêmes outils choisis sur 12 demandes et même vitesse (comparés
> avec une fenêtre de 4 096). Le cache en 8 bits exige la flash attention, qu'Ollama active
> seul : ne posez pas `OLLAMA_FLASH_ATTENTION=0` à côté, le modèle ne se chargerait plus.
> Ces réglages valent pour tous les modèles de cet Ollama ; le local seul coupe les modèles
> cloud et la recherche web d'Ollama, que My_OS n'utilise pas. Supprimez le fichier pour
> les annuler. Avec `ollama serve` lancé à la main :
> `OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_NO_CLOUD=1 ollama serve`.

> Sans GPU, une réponse du modèle 4B prend de quelques secondes à plus d'une minute
> selon la machine (mesuré en VM, 8 cœurs, sans GPU : 20 s à 1 min 50 ; la première
> requête charge le modèle en mémoire).

Activez le backend Ollama dans `config.local.yaml` (non versionné) :

```yaml
model:
  backend: ollama
  name: qwen3.5:4b
  num_ctx: 16384   # fenêtre de contexte (défaut)
```

La **fenêtre de contexte** (`num_ctx`, en tokens) est envoyée à chaque requête. Le prompt
système et les outils en prennent ~2 400 ; My_OS n'envoie au modèle que la part de
l'historique qui tient dans le reste : les plus vieux échanges partent d'abord, et chaque
résultat d'outil est plafonné au quart de la fenêtre (un long fichier n'est lu qu'en
partie, et le modèle le sait). Le prompt est lu par lots de 256 tokens : une nouvelle
conversation ne relit que ~250 tokens du prompt système, quelle que soit la fenêtre, et
une conversation qui se poursuit ne lit que ses nouveaux messages (sans GPU, ~10 s par
demande neuve en VM 8 cœurs). `num_ctx: 4096` économise 200 Mio, au prix d'une mémoire
de conversation presque nulle.

Sans cette config, My_OS utilise un **stub** à base de règles (pratique pour tester
l'UI et les permissions sans Ollama).

### 2.4 · Lancement en développement

```bash
./launch_dev.sh        # démarre le daemon puis le popup (session X11)
```

Raccourci par défaut : **Ctrl+Alt+Espace** (modifiable dans `config.yaml`).

### 2.5 · Installation résidente (service systemd utilisateur)

Le daemon est prévu comme **service `systemd` utilisateur** (jamais root, cf.
[SECURITY.md](SECURITY.md) menace 3) :

```bash
mkdir -p ~/.config/systemd/user
cp myosd.service ~/.config/systemd/user/
# Adaptez WorkingDirectory/PYTHONPATH au chemin du dépôt (défaut : %h/my_os).
systemctl --user daemon-reload
systemctl --user enable --now myosd.service

# En session graphique, importez l'environnement d'affichage une fois :
systemctl --user import-environment DISPLAY XAUTHORITY
```

Le **popup** est un processus résident séparé : lancez-le au démarrage de session via un
autostart XDG (`~/.config/autostart/`) pointant sur `python -m ui.popup`, ou démarrez-le
manuellement. Il reste caché jusqu'au raccourci.

Le journal d'audit est écrit dans `~/.local/share/my_os/audit.db` (le dossier du code
peut donc rester en lecture seule).

---

## 3 · Mode cloud (optionnel)

Le cloud (Claude) est **opt-in, par requête** et **désactivé par défaut**.

1. Dans le popup, activez **« ☁ Cloud »** (sous la zone de saisie).
2. À la première activation, saisissez votre **clé API Anthropic** : elle est stockée
   dans le **trousseau du système** (`keyring`), **jamais** en clair (ni `config.yaml`,
   ni logs, ni audit).
3. Un indicateur signale quand une requête part au cloud. Conseil : `Ctrl+L` avant, pour
   minimiser les données envoyées.

---

## 4 · Notes X11 / Wayland

| | X11 (nominal) | Wayland (expérimental) |
|---|---|---|
| **Raccourci global** | `pynput` (fonctionne tel quel) | portal `org.freedesktop.portal.GlobalShortcuts` |
| **Popup centré/au-dessus** | `move()` + always-on-top | surface overlay `layer-shell` |
| **Détection** | automatique (`XDG_SESSION_TYPE`) | automatique |

Le backend est choisi **au démarrage** selon la session (cf.
[INTERFACES.md §9](INTERFACES.md)). Pour Wayland, installez aussi :

```bash
sudo pacman -S --needed qt6-wayland layer-shell-qt python-gobject
```

**Limites honnêtes** : le portal `GlobalShortcuts` est bien supporté sous GNOME/KDE,
**inégal sous wlroots** (sway, labwc…) ; certains compositeurs demandent à l'utilisateur
de **lier lui-même** la combinaison. Si le compositeur n'expose pas `layer-shell`, le
popup retombe sur une fenêtre normale (centrage approximatif). En cas de souci Wayland,
utilisez une session **X11** (pleinement supportée).

---

## 5 · Installation via l'ISO live

Une ISO bootable (base Arch + Xfce/X11) embarque My_OS prêt à l'emploi, avec le modèle
local activé par défaut. Elle a été validée en VM (QEMU/KVM, BIOS et UEFI) en
octobre 2026.

- **Construire l'ISO** (sur un hôte Arch avec `archiso`) :
  ```bash
  sudo pacman -S archiso
  sudo ./packaging/build_iso.sh                # ISO dans packaging/out/
  sudo ./packaging/build_iso.sh -s 2026/10/19  # instantané de paquets explicite
  ```
  Les paquets viennent d'un **instantané daté de l'Arch Linux Archive** (la veille par
  défaut) ; le système live garde ce même instantané comme miroir, ce qui permet
  d'installer des paquets en live sans mise à jour partielle.
- **RAM** : ≥ 4 Go pour démarrer ; **≥ 12 Go pour le modèle local en live** (tout ce qui
  est écrit vit en RAM, modèle compris). En dessous, le modèle n'est pas téléchargé et
  le popup signale son absence.
- **Carte graphique** : le modèle tourne sur une carte dédiée AMD, Intel ou NVIDIA
  récente (Vulkan) quand il y en a une, sinon sur le processeur. Pas encore éprouvé sur
  du matériel réel : cf. [packaging/README.md](../packaging/README.md) §5.
- **1er boot** (réseau requis) : autologin vers Xfce (utilisateur non-root `myos`, mot
  de passe `myos`), synchronisation des bases pacman en arrière-plan, installation des
  dépendances Python puis téléchargement du modèle (~3,3 Go, quelques minutes).

### Démarrer sur une clé USB

Copier le fichier `.iso` sur une clé ne suffit pas : la clé doit être préparée. Les deux
méthodes ci-dessous **effacent la clé** — sauvegardez son contenu avant.

- **[Ventoy](https://www.ventoy.net)** (recommandé si la clé sert aussi à autre chose) :
  installez Ventoy sur la clé une fois, puis copiez simplement l'ISO dessus. Au démarrage,
  choisissez l'ISO puis « Boot in normal mode ». En UEFI, Ventoy affiche ensuite son
  propre menu, **sans compte à rebours** : validez « My_OS live » avec Entrée.
- **[Rufus](https://rufus.ie)** sous Windows, en **mode DD** (ou `dd` sous Linux) : l'ISO
  est écrite telle quelle, la clé ne sert plus qu'à My_OS jusqu'à son reformatage.

Ensuite : **désactivez le Secure Boot** (l'ISO n'est pas signée) et démarrez sur la clé
via le menu de démarrage du PC (F12, F11 ou Échap selon les marques). Rien n'est écrit sur
le disque du PC. Sur un PC professionnel, vérifiez d'abord votre politique informatique
(et la clé de récupération BitLocker : changer le Secure Boot peut la faire demander).

Validé en VM pour les quatre cas : Ventoy et clé brute, en BIOS et en UEFI.

Procédure détaillée, décisions et limites : **[packaging/README.md](../packaging/README.md)**.

---

## 6 · Dépannage

- **Le popup ne s'ouvre pas au raccourci (Wayland)** : votre compositeur ne supporte
  peut-être pas le portal `GlobalShortcuts`, ou la combinaison doit être liée dans ses
  réglages. Repli : session X11.
- **« aucune clé » en mode cloud** : la clé n'est pas (encore) dans le trousseau, ou le
  Secret Service n'est pas démarré (`gnome-keyring`). My_OS replie alors en **local**.
- **Le modèle ne répond pas** : vérifiez `ollama serve` et `ollama list` (le modèle
  `qwen3.5:4b` doit être présent).
- **Le modèle reste sur le processeur malgré une carte graphique** :
  `journalctl -u ollama | grep "inference compute"` liste ce qu'Ollama a trouvé au
  démarrage. Vérifiez qu'un backend GPU est installé (§2.3) et que `vulkaninfo --summary`
  voit la carte. Ollama écarte par défaut les GPU intégrés : pour les essayer, ajoutez
  `Environment="OLLAMA_IGPU_ENABLE=1"` dans un drop-in du service `ollama`.
- **Le modèle ne se charge plus** (« quantized V cache requires flash_attn to be
  enabled » dans `journalctl -u ollama`) : `OLLAMA_FLASH_ATTENTION=0` est posé alors que
  le contexte est en 8 bits (`OLLAMA_KV_CACHE_TYPE=q8_0`). Retirez l'un des deux ;
  `systemctl cat ollama` montre où chacun est posé.
- **Élévation refusée** : un agent polkit doit tourner dans la session (Xfce lance
  `polkit-gnome`). Sinon, installez/lancez un agent d'authentification polkit.
  Un « Request dismissed » signifie simplement que la fenêtre polkit a été annulée.
- **Rechercher/installer un paquet échoue en live** (`database file for 'core' does not
  exist`) : les bases n'ont pas encore été synchronisées (réseau absent au démarrage).
  Vérifiez `journalctl -u myos-pacman-sync`, ou lancez `sudo pacman -Sy` (sans risque :
  le miroir est l'instantané de l'ISO).
- **Le modèle n'est pas là en live** : lisez `~/.local/share/my_os/model-pull.log`
  (RAM insuffisante ou pas de réseau), puis `ollama pull qwen3.5:4b` une fois la cause
  réglée.
- **Dépendances Python manquantes en live** : `~/.local/share/my_os/firstrun.log` ;
  l'installation est retentée à la session suivante.
- **Session verrouillée après inactivité (live)** : mot de passe `myos`.
