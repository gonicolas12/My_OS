#!/usr/bin/env bash
# Profil archiso de My_OS — dérivé de `releng`.
# Métadonnées de l'ISO + permissions des fichiers ajoutés par My_OS.
# Les variables sont consommées par mkarchiso (d'où le SC2034 "non utilisées").
# shellcheck disable=SC2034

iso_name="my_os"
iso_label="MYOS_$(date +%Y%m)"
iso_publisher="My_OS <https://github.com/gonicolas12/My_OS>"
iso_application="My_OS Live — assistant IA local et sécurisé"
iso_version="$(date +%Y.%m.%d)"
install_dir="myos"
buildmodes=('iso')
bootmodes=(
  'bios.syslinux.mbr'
  'bios.syslinux.eltorito'
  'uefi-ia32.systemd-boot.esp'
  'uefi-x64.systemd-boot.esp'
  'uefi-ia32.systemd-boot.eltorito'
  'uefi-x64.systemd-boot.eltorito'
)
arch="x86_64"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'xz' '-Xbcj' 'x86' '-b' '1M' '-Xdict-size' '1M')
bootstrap_tarball_compression=('zstd' '-c' '-T0' '--auto-threads=logical' '-19')
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  ["/etc/gshadow"]="0:0:400"
  ["/root"]="0:0:750"
  ["/etc/sudoers.d/10-myos-wheel"]="0:0:440"
  # Lanceurs My_OS (résolvent le venv utilisateur puis exécutent daemon/popup).
  ["/usr/local/bin/myos-daemon"]="0:0:755"
  ["/usr/local/bin/myos-popup"]="0:0:755"
  ["/usr/local/bin/myos-user-firstrun"]="0:0:755"
  ["/usr/local/bin/myos-prepare"]="0:0:755"
  # Code de My_OS : root:root, lecture seule (l'app n'écrit jamais dans /opt).
  ["/opt/my_os"]="0:0:755"
)
# NB : /home/myos n'est pas dans l'image — il est créé au 1er boot par
# `useradd -m` (myos-prepare.service), qui peuple le home depuis /etc/skel avec
# le bon propriétaire. Cela évite toute gymnastique de permissions au build.
