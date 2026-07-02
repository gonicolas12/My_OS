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
    python-psutil python-dbus python-yaml python-keyring \
    polkit gnome-keyring libsecret \
    ollama \
    pipewire pipewire-pulse wireplumber libpulse \
    networkmanager
```

> `pactl` (réglage du volume) vient de `libpulse`. `polkit` fournit `pkexec` (élévation
> ponctuelle). `gnome-keyring`/`libsecret` fournissent le trousseau (Secret Service) pour
> la clé API cloud. Les noms de paquets peuvent évoluer : vérifiez avec `pacman -Si <pkg>`.

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
# Installez Ollama (cf. lien ci-dessus), démarrez le service puis tirez le modèle :
systemctl --user start ollama       # ou `ollama serve` dans un terminal
ollama pull qwen3.5:4b              # recommandé (≈ 8 Go RAM)
```

Activez le backend Ollama dans `config.local.yaml` (non versionné) :

```yaml
model:
  backend: ollama
  name: qwen3.5:4b
```

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

---

## 3 · Mode cloud (optionnel)

Le cloud (Claude) est **opt-in, par requête** et **désactivé par défaut**.

1. Dans le popup, cochez **« ☁ Cloud (Claude) »**.
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

Une ISO bootable (base Arch + Xfce/X11) embarque My_OS prêt à l'emploi.

- **Construire l'ISO** (sur un hôte Arch avec `archiso`) :
  ```bash
  sudo pacman -S archiso
  sudo ./packaging/build_iso.sh        # ISO dans packaging/out/
  ```
- **Tester en VM** (QEMU/VirtualBox). Donnez ≥ 4 Go de RAM et un accès réseau.
- **1er boot** : autologin vers Xfce (utilisateur non-root `myos`), puis récupération des
  dépendances et du modèle Ollama (réseau requis, quelques minutes).

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
- **Élévation refusée** : un agent polkit doit tourner dans la session (Xfce lance
  `polkit-gnome`). Sinon, installez/lancez un agent d'authentification polkit.
