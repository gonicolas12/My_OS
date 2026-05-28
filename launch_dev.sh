#!/usr/bin/env bash
# Lance My_OS en mode développement (jalon 1) : daemon + popup, sans installer le
# service systemd. À exécuter dans une session X11 — le raccourci global
# (pynput/X11) et la socket Unix nécessitent Linux.
#
# Raccourci par défaut : Ctrl+Alt+Espace (modifiable dans config.yaml).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="${PYTHON:-python3}"

cleanup() {
    if [[ -n "${DAEMON_PID:-}" ]]; then
        kill "$DAEMON_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

echo "Démarrage du daemon myosd…"
"$PYTHON" -m daemon.myosd &
DAEMON_PID=$!

sleep 1  # laisse le daemon créer la socket avant le popup

echo "Démarrage du popup (résident). Raccourci : Ctrl+Alt+Espace. Ctrl+C pour quitter."
"$PYTHON" -m ui.popup

# À la fermeture du popup, le trap arrête le daemon.
