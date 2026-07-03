# 📦 Packaging — ISO live de My_OS (archiso)

Ce dossier produit une **ISO live bootable** de My_OS, dérivée du profil `releng`
d'[archiso](https://wiki.archlinux.org/title/Archiso).

> **Le build ne se fait PAS sous Windows ni en CI.** Il requiert un hôte **Arch
> Linux** avec le paquet `archiso`, et `mkarchiso` doit tourner en **root**. Le
> profil, les scripts et cette doc sont livrés ; **le build et le boot réels sont
> une étape manuelle en VM** (cf. plus bas).

---

## 1 · Décisions de packaging (et pourquoi)

| Sujet | Choix | Raison |
|-------|-------|--------|
| **Base** | dérivée de `releng` (overlay) | kernel, `mkinitcpio`, bootloaders et `pacman.conf` restent ceux, à jour, d'archiso ; aucune config bootloader maintenue à la main. |
| **Compositeur** | **Xfce / X11** | chemin **nominal** pleinement supporté (le port Wayland reste expérimental, cf. §5). Robuste pour une première ISO. |
| **Modèle local** | **téléchargé au 1er boot** (`ollama pull qwen3.5:4b`) | ISO légère ; reste *local et privé* à l'usage. Compromis : **réseau requis au 1er démarrage**. Alternative « modèle embarqué » (ISO lourde, hors-ligne) : cf. §6. |
| **Dépendances Python** | venv `--system-site-packages` au 1er boot | les paquets système (`pyside6`, `python-dbus`, `python-gobject`…) viennent de pacman ; seul le **PyPI-only** (`pynput`, `anthropic`, `ollama`) passe par `pip`. |
| **Utilisateur** | `myos`, **non-root**, créé au 1er boot | le daemon ne tourne **jamais** en root (cf. [SECURITY.md](../docs/SECURITY.md) menace 3). Élévation ponctuelle via polkit/`pkexec`. |
| **Autologin** | LightDM → session Xfce de `myos` | démo immédiate ; le service utilisateur `myosd` et le popup démarrent dans la session. |

Mot de passe par défaut de l'utilisateur live : **`myos`** (défini par
`myos-prepare`). **À changer pour tout usage réel** (`passwd` une fois connecté).

---

## 2 · Structure du profil

```
packaging/
├── build_iso.sh              # dérive releng + overlay + active les services + mkarchiso
├── README.md                 # ce fichier
└── archiso/
    ├── profiledef.sh         # métadonnées ISO + permissions des fichiers My_OS
    ├── packages.x86_64       # AJOUTS de paquets (fusionnés avec releng au build)
    └── airootfs/             # fichiers déposés dans le système live
        ├── etc/motd                                  # message d'accueil
        ├── etc/lightdm/lightdm.conf.d/…              # autologin → Xfce
        ├── etc/sudoers.d/10-myos-wheel               # wheel admin (polkit reste la voie d'élévation)
        ├── etc/systemd/system/myos-prepare.service   # crée l'utilisateur au 1er boot
        ├── etc/skel/.xprofile                        # init session : firstrun + démarre myosd
        ├── etc/skel/.config/autostart/…              # autostart du popup résident
        ├── etc/skel/.config/systemd/user/myosd.service  # service UTILISATEUR (ISO)
        ├── usr/local/bin/myos-daemon|popup           # lanceurs (venv utilisateur)
        ├── usr/local/bin/myos-prepare                # création utilisateur (1er boot)
        ├── usr/local/bin/myos-user-firstrun          # venv + deps + pull modèle (1er run)
        └── opt/my_os/                                # code (copié par build_iso.sh)
```

**Activation des services.** `build_iso.sh` crée les symlinks systemd dans
l'image (`default.target` → `graphical.target`, `display-manager.service` →
LightDM, `NetworkManager`, `ollama`, `myos-prepare`). Ces symlinks ne sont pas
versionnés (ils seraient peu fiables depuis Windows et « pendants » tant que les
paquets ne sont pas installés) : ils sont posés au build, sur l'hôte Arch.

**Chaîne de démarrage (1er boot).**
1. `myos-prepare.service` crée l'utilisateur non-root `myos` **avant** l'autologin.
2. LightDM ouvre automatiquement une session **Xfce** pour `myos`.
3. `~/.xprofile` lance `myos-user-firstrun` (venv + `pip` + `ollama pull` en tâche
   de fond), importe `DISPLAY`/`XAUTHORITY` dans systemd `--user`, puis démarre
   `myosd.service`.
4. L'autostart XDG lance le popup résident. Raccourci : **Ctrl+Alt+Espace**.

> ⏱️ **Le 1er boot prend plusieurs minutes** (installation des dépendances +
> téléchargement du modèle). Les démarrages suivants sont immédiats.

---

## 3 · Construire l'ISO (hôte Arch)

> **Prérequis d'espace disque.** Le répertoire de travail a besoin de **~15 Gio** et
> **ne doit pas être sur un tmpfs** (le `/tmp` d'Arch est souvent en RAM → erreur
> `No space left on device` à l'étape ESP/FAT). `build_iso.sh` place donc son travail
> sous **`/var/tmp`** (sur disque) par défaut ; utilisez `-w REP` pour cibler un autre
> disque. Prévoyez aussi ~2 Gio pour l'ISO de sortie.

```bash
# 1) Système à jour AVANT d'installer des paquets (sinon 404 sur des versions
#    périmées de la base pacman) :
sudo pacman -Syu

# 2) Installer archiso (shellcheck est OPTIONNEL — juste pour linter les scripts,
#    il tire toute la chaîne Haskell ; les scripts passent déjà bash -n / sh -n) :
sudo pacman -S archiso

# 3) Depuis la racine du dépôt My_OS :
sudo ./packaging/build_iso.sh                 # ISO dans packaging/out/
sudo ./packaging/build_iso.sh -o /mnt/iso     # répertoire de sortie explicite
sudo ./packaging/build_iso.sh -w /mnt/build   # répertoire de travail sur un gros disque
```

`build_iso.sh` exporte le code suivi par git (`git archive HEAD`) : **buildez
depuis un commit propre** (les modifications non committées ne sont pas incluses).

### Linter les scripts (recommandé avant build)
```bash
shellcheck packaging/build_iso.sh packaging/archiso/profiledef.sh \
           packaging/archiso/airootfs/usr/local/bin/* \
           packaging/archiso/airootfs/etc/skel/.xprofile
```

---

## 4 · Tester l'ISO en VM

```bash
# UEFI (OVMF requis : pacman -S edk2-ovmf) :
qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd \
  -cdrom packaging/out/my_os-*.iso

# BIOS (legacy) :
qemu-system-x86_64 -enable-kvm -m 4096 -smp 2 -cdrom packaging/out/my_os-*.iso
```

Donnez à la VM **assez de RAM** (≥ 4 Go) et un **accès réseau** (1er boot). À
vérifier au boot :
1. Autologin vers Xfce, utilisateur `myos` (non-root : `whoami` → `myos`).
2. Fin du 1er run (venv + `ollama pull` ; voir `~/.local/share/my_os/model-pull.log`).
3. **Ctrl+Alt+Espace** ouvre le popup, centré et au-dessus de tout.
4. Une requête simple (« liste mon dossier home ») fonctionne en local.
5. Une action sensible déclenche bien une **confirmation** (polkit pour l'élévation).

---

## 5 · Limites & honnêteté

- **Wayland reste expérimental.** L'ISO démarre en **Xfce/X11** (nominal). Les
  paquets Wayland (`qt6-wayland`, `layer-shell-qt`) sont inclus pour
  *expérimenter* une session Wayland, mais le raccourci global (portal
  `GlobalShortcuts`) et le popup overlay (`layer-shell`) dépendent du compositeur
  (cf. [INTERFACES.md §9](../docs/INTERFACES.md)). Aucun support Wayland n'est promis sur l'ISO.
- **Noms de paquets** : valables sur Arch fin 2026 ; à vérifier contre les dépôts
  courants au moment du build (`pacman -Si <pkg>`).
- **Build/boot non testés ici** : produits sous Windows, ils n'ont pas été
  exécutés. Le `bash -n`/`sh -n` valide la syntaxe ; `shellcheck` est recommandé
  sur l'hôte Arch. La validation réelle est l'étape VM ci-dessus.
- **1er boot en ligne** : sans réseau, les dépendances `pip` et le modèle ne sont
  pas récupérés ; l'assistant signalera l'absence de modèle jusqu'au prochain
  `ollama pull qwen3.5:4b`.
- **Mot de passe live connu** (`myos`) : acceptable pour une démo live, à changer
  pour un usage réel.

---

## 6 · Variante « modèle embarqué » (hors-ligne)

Pour une ISO utilisable **sans réseau** au 1er boot, embarquez le modèle dans
l'image (ISO nettement plus lourde, +~3 Go) :

1. Sur l'hôte de build, récupérez le modèle dans un dossier dédié :
   `OLLAMA_MODELS=/tmp/ollama-models ollama pull qwen3.5:4b`.
2. Copiez-le dans l'airootfs avant le build (ex. `airootfs/var/lib/ollama/`) et
   ajustez les permissions/propriétaire du service `ollama`.
3. Retirez le `ollama pull` de `myos-user-firstrun`.

Ce compromis (taille vs réseau) est laissé au choix de l'intégrateur ; le défaut
livré privilégie une **ISO légère**.

---

## 7 · Sécurité (rappel)

L'ISO ne déroge à aucun invariant (cf. [SECURITY.md](../docs/SECURITY.md)) :
daemon **non-root**, élévation ponctuelle via **polkit**, clé API cloud **jamais**
dans l'image (toujours via le trousseau `keyring`, saisie au runtime), IPC sur
**socket Unix locale**. Le confinement Wayland, quand il est disponible, est un
**plus** de sécurité, pas un prérequis.
