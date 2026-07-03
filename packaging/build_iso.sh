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
# Usage :  sudo ./packaging/build_iso.sh [-o REP_SORTIE] [-w REP_TRAVAIL]
set -euo pipefail

RELENG="/usr/share/archiso/configs/releng"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OVERLAY="$SCRIPT_DIR/archiso"
OUTDIR="$SCRIPT_DIR/out"
# Parent du répertoire de travail temporaire. /var/tmp est sur disque (contrairement
# à /tmp, souvent un tmpfs en RAM trop petit pour une ISO desktop → 'No space left').
WORKPARENT="/var/tmp"

usage() {
    echo "Usage : sudo $0 [-o REP_SORTIE] [-w REP_TRAVAIL]" >&2
    echo "  -o REP_SORTIE   répertoire de sortie de l'ISO (défaut : $OUTDIR)" >&2
    echo "  -w REP_TRAVAIL  parent du répertoire de travail, ~15 Gio requis," >&2
    echo "                  JAMAIS un tmpfs (défaut : $WORKPARENT)" >&2
}

while getopts ":o:w:h" opt; do
    case "$opt" in
        o) OUTDIR="$OPTARG" ;;
        w) WORKPARENT="$OPTARG" ;;
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

echo ">> Application de l'overlay My_OS (profiledef + airootfs)"
cp -a "$OVERLAY/profiledef.sh" "$PROFILE/profiledef.sh"
cp -aT "$OVERLAY/airootfs" "$PROFILE/airootfs"

echo ">> Fusion de la liste de paquets (releng + ajouts My_OS)"
merged="$WORK/packages.x86_64"
# Union triée, commentaires et lignes vides retirés (mkarchiso les ignore).
grep -vhE '^[[:space:]]*(#|$)' \
    "$PROFILE/packages.x86_64" "$OVERLAY/packages.x86_64" | sort -u > "$merged"
mv "$merged" "$PROFILE/packages.x86_64"

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
# Service My_OS (fichier régulier déjà présent dans l'image).
ln -sf /etc/systemd/system/myos-prepare.service \
    "$sysdir/multi-user.target.wants/myos-prepare.service"

echo ">> Construction de l'ISO (mkarchiso) — cela peut être long"
mkdir -p "$OUTDIR"
mkarchiso -v -w "$WORK/build" -o "$OUTDIR" "$PROFILE"

echo ">> Terminé. ISO disponible dans : $OUTDIR"
ls -1sh "$OUTDIR"/*.iso 2>/dev/null || true
