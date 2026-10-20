#!/usr/bin/env bash
# Construit l'ISO live de My_OS à partir du profil overlay packaging/archiso/.
#
# Stratégie (cf. packaging/README.md) : on DÉRIVE du profil `releng` fourni par
# archiso (base Arch à jour : kernel, mkinitcpio, bootloaders, pacman.conf), puis
# on applique l'overlay My_OS (profiledef, paquets, airootfs) et on active les
# services. On ne maintient ainsi aucune configuration de bootloader à la main.
#
# PRÉREQUIS : hôte Arch Linux, paquet `archiso` installé, exécution en root
# (mkarchiso l'exige). NE se construit PAS sous Windows ni en CI.
#
# ESPACE DISQUE : le répertoire de travail a besoin de ~15 Gio et NE doit PAS être
# sur un tmpfs (RAM). Défaut : /var/tmp (sur disque). Adaptez avec -w au besoin.
#
# PAQUETS FIGÉS : l'ISO est construite depuis un instantané daté de l'Arch Linux
# Archive (défaut : la veille, en UTC ; -s pour en choisir un), et le système live
# garde ce même instantané comme miroir. Les bases pacman synchronisées au
# démarrage correspondent donc exactement aux paquets installés : `pacman -S`
# fonctionne en live sans mise à jour partielle, quel que soit l'âge de l'ISO.
#
# Usage :  sudo ./packaging/build_iso.sh [-o REP_SORTIE] [-w REP_TRAVAIL] [-s AAAA/MM/JJ]
set -euo pipefail

RELENG="/usr/share/archiso/configs/releng"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OVERLAY="$SCRIPT_DIR/archiso"
OUTDIR="$SCRIPT_DIR/out"
# Parent du répertoire de travail temporaire. /var/tmp est sur disque (contrairement
# à /tmp, souvent un tmpfs en RAM trop petit pour une ISO desktop → 'No space left').
WORKPARENT="/var/tmp"
ARCHIVE_URL="https://archive.archlinux.org/repos"
SNAPSHOT=""

usage() {
    echo "Usage : sudo $0 [-o REP_SORTIE] [-w REP_TRAVAIL] [-s AAAA/MM/JJ]" >&2
    echo "  -o REP_SORTIE   répertoire de sortie de l'ISO (défaut : $OUTDIR)" >&2
    echo "  -w REP_TRAVAIL  parent du répertoire de travail, ~15 Gio requis," >&2
    echo "                  JAMAIS un tmpfs (défaut : $WORKPARENT)" >&2
    echo "  -s AAAA/MM/JJ   instantané de l'Arch Linux Archive (défaut : la veille, UTC)" >&2
}

while getopts ":o:w:s:h" opt; do
    case "$opt" in
        o) OUTDIR="$OPTARG" ;;
        w) WORKPARENT="$OPTARG" ;;
        s) SNAPSHOT="$OPTARG" ;;
        h) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done

die() {
    echo "Erreur : $*" >&2
    exit 1
}

# --- Vérifications de l'environnement -----------------------------------------
[ "$(id -u)" -eq 0 ] || die "à exécuter en root (mkarchiso l'exige) : sudo $0"
command -v mkarchiso >/dev/null 2>&1 || die "mkarchiso introuvable — installez 'archiso' (pacman -S archiso)"
[ -d "$RELENG" ] || die "profil releng introuvable dans $RELENG — installez 'archiso'"
[ -f "$OVERLAY/profiledef.sh" ] || die "overlay introuvable : $OVERLAY/profiledef.sh"
command -v curl >/dev/null 2>&1 || die "curl introuvable — installez 'curl'"

# Instantané de l'Arch Linux Archive : la veille par défaut (celui du jour peut
# être encore incomplet). Vérifié AVANT le long build.
SNAPSHOT="${SNAPSHOT:-$(date -u -d yesterday +%Y/%m/%d)}"
[[ "$SNAPSHOT" =~ ^[0-9]{4}/[0-9]{2}/[0-9]{2}$ ]] || die "instantané invalide : $SNAPSHOT (attendu AAAA/MM/JJ)"
curl -fsI --max-time 30 "$ARCHIVE_URL/$SNAPSHOT/core/os/x86_64/core.db" >/dev/null \
    || die "instantané $SNAPSHOT introuvable sur $ARCHIVE_URL (réseau ? date ?)"

# Répertoire de travail sur disque (cf. WORKPARENT). Avertit si l'espace semble
# insuffisant (une ISO desktop demande ~15 Gio d'espace de travail).
mkdir -p "$WORKPARENT"
avail_gib="$(df -Pk "$WORKPARENT" | awk 'NR==2 {printf "%d", $4 / 1024 / 1024}')"
if [ "${avail_gib:-0}" -lt 15 ]; then
    echo "Attention : ~${avail_gib:-0} Gio libres sur $WORKPARENT ; une ISO desktop" >&2
    echo "  demande ~15 Gio. En cas d'echec 'No space left on device', pointez -w" >&2
    echo "  vers un disque plus grand (ex. -w /mnt/build)." >&2
fi

WORK="$(mktemp -d -p "$WORKPARENT" myos-iso.XXXXXX)"
PROFILE="$WORK/profile"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

echo ">> Dérivation du profil releng → $PROFILE"
mkdir -p "$PROFILE"
cp -a "$RELENG/." "$PROFILE/"

echo ">> Application de l'overlay My_OS (profiledef + airootfs + fond du menu BIOS)"
cp -a "$OVERLAY/profiledef.sh" "$PROFILE/profiledef.sh"
cp -aT "$OVERLAY/airootfs" "$PROFILE/airootfs"
cp -a "$OVERLAY/syslinux/." "$PROFILE/syslinux/"

echo ">> Menus de démarrage aux couleurs de My_OS"
# releng affiche « Arch Linux install medium » (et le logo Arch en BIOS) dans les
# trois chargeurs : syslinux (BIOS), systemd-boot (UEFI) et GRUB (loopback, Ventoy).
find "$PROFILE/syslinux" "$PROFILE/efiboot" "$PROFILE/grub" -type f \
    \( -name '*.cfg' -o -name '*.conf' \) -exec sed -i \
    -e 's/It allows you to install Arch Linux or perform system maintenance with speech feedback\./It runs My_OS from memory, with speech feedback; nothing is written to the disk./' \
    -e 's/It allows you to install Arch Linux or perform system maintenance\./It runs My_OS from memory; nothing is written to the disk./' \
    -e 's/Boot the Arch Linux \(install\|live\) medium/Boot My_OS live/' \
    -e 's/Arch Linux \(install\|live\) medium/My_OS live/g' \
    -e 's/^MENU TITLE Arch Linux$/MENU TITLE My_OS/' {} +
if grep -rqE "Arch Linux (install|live) medium|install Arch Linux" \
    "$PROFILE/syslinux" "$PROFILE/efiboot" "$PROFILE/grub"; then
    die "menus de démarrage : libellés Arch Linux non remplacés (releng a changé ?)"
fi

echo ">> Fusion de la liste de paquets (releng + ajouts My_OS)"
merged="$WORK/packages.x86_64"
# Union triée, commentaires et lignes vides retirés (mkarchiso les ignore).
grep -vhE '^[[:space:]]*(#|$)' \
    "$PROFILE/packages.x86_64" "$OVERLAY/packages.x86_64" | sort -u > "$merged"
mv "$merged" "$PROFILE/packages.x86_64"

echo ">> Paquets figés sur l'instantané Arch Linux Archive du $SNAPSHOT"
# Build (pacman.conf du profil, lu par mkarchiso) ET système live (miroir installé
# au démarrage par myos-prepare) sur le même instantané : cf. en-tête du script.
snapshot_server="Server = $ARCHIVE_URL/$SNAPSHOT/\$repo/os/\$arch"
sed -i "s|^Include = /etc/pacman.d/mirrorlist\$|$snapshot_server|" "$PROFILE/pacman.conf"
if grep -q '^Include = /etc/pacman.d/mirrorlist' "$PROFILE/pacman.conf" \
    || ! grep -qF "$snapshot_server" "$PROFILE/pacman.conf"; then
    die "pacman.conf du profil : redirection vers l'instantané impossible"
fi
mkdir -p "$PROFILE/airootfs/etc/myos"
printf '# Miroir figé de cette ISO My_OS (instantané %s, build_iso.sh -s).\n%s\n' \
    "$SNAPSHOT" "$snapshot_server" > "$PROFILE/airootfs/etc/myos/mirrorlist"

echo ">> Espace d'écriture live (cowspace) : 50 % de la RAM"
# Défaut archiso : 256 Mio, saturés par le venv puis le `ollama pull` (~3,3 Go) du
# 1er run. tmpfs n'occupe que ce qui est écrit ; 50 % laisse la RAM nécessaire au
# chargement du modèle. Paramètre ajouté à toutes les entrées de boot de releng.
grep -rlZ 'archisobasedir=' "$PROFILE/syslinux" "$PROFILE/efiboot" "$PROFILE/grub" 2>/dev/null \
    | xargs -0 -r sed -i '/archisobasedir=/ s/$/ cow_spacesize=50%/'

echo ">> Copie des sources de My_OS → airootfs/opt/my_os"
dest="$PROFILE/airootfs/opt/my_os"
mkdir -p "$dest"
if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    # Export propre du contenu suivi par git (exclut .git, venv, data/*.db…).
    git -C "$REPO_ROOT" archive --format=tar HEAD | tar -x -C "$dest"
else
    cp -a "$REPO_ROOT/." "$dest/"
    rm -rf "$dest/.git" "$dest/.venv" "$dest/packaging/out"
fi
# Backend local Ollama sur l'ISO (le modèle est tiré au 1er run) ; config.yaml
# versionné garde le stub, pratique en dev. config.local.yaml le surcharge.
printf 'model:\n  backend: ollama\n' > "$dest/config.local.yaml"

echo ">> Activation des services (symlinks systemd dans l'image)"
sysdir="$PROFILE/airootfs/etc/systemd/system"
mkdir -p "$sysdir/multi-user.target.wants"
# Cible graphique par défaut + gestionnaire d'affichage = LightDM.
ln -sf /usr/lib/systemd/system/graphical.target "$sysdir/default.target"
ln -sf /usr/lib/systemd/system/lightdm.service "$sysdir/display-manager.service"
# Services système (liens potentiellement « pendants » au build : leurs cibles
# n'existent qu'une fois les paquets installés par mkarchiso — c'est attendu).
for unit in NetworkManager.service ollama.service; do
    ln -sf "/usr/lib/systemd/system/$unit" "$sysdir/multi-user.target.wants/$unit"
done
# Réseau géré par NetworkManager SEUL : releng active systemd-networkd et iwd, qui
# configureraient les mêmes interfaces en parallèle (double DHCP, conflit Wi-Fi
# avec le wpa_supplicant de NetworkManager). networkd est MASQUÉ (et non juste
# désactivé) : les presets systemd le réactivent à l'installation des paquets.
rm -f "$sysdir/multi-user.target.wants/iwd.service" \
    "$sysdir/multi-user.target.wants/systemd-networkd.service" \
    "$sysdir/sockets.target.wants/systemd-networkd.socket" \
    "$sysdir/network-online.target.wants/systemd-networkd-wait-online.service" \
    "$sysdir/dbus-org.freedesktop.network1.service"
for unit in systemd-networkd.service systemd-networkd.socket \
    systemd-networkd-varlink.socket systemd-networkd-varlink-metrics.socket \
    systemd-networkd-resolve-hook.socket systemd-networkd-wait-online.service; do
    ln -sf /dev/null "$sysdir/$unit"
done
# Services My_OS (fichiers réguliers déjà présents dans l'image).
for unit in myos-prepare.service myos-pacman-sync.service; do
    ln -sf "/etc/systemd/system/$unit" "$sysdir/multi-user.target.wants/$unit"
done

echo ">> Construction de l'ISO (mkarchiso) — cela peut être long"
mkdir -p "$OUTDIR"
mkarchiso -v -w "$WORK/build" -o "$OUTDIR" "$PROFILE"

echo ">> Terminé (paquets : instantané $SNAPSHOT). ISO disponible dans : $OUTDIR"
ls -1sh "$OUTDIR"/*.iso 2>/dev/null || true
