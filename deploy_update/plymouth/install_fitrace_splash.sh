#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
THEME_SOURCE="$SCRIPT_DIR/fitrace"
THEME_TARGET="/usr/share/plymouth/themes/fitrace"
PLYMOUTH_CONFIG="/etc/plymouth/plymouthd.conf"
BACKUP_DIR="/var/backups/fitrace-plymouth"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

set_theme() {
  local theme="$1"
  if grep -q '^Theme=' "$PLYMOUTH_CONFIG"; then
    sed -i "s/^Theme=.*/Theme=$theme/" "$PLYMOUTH_CONFIG"
  elif grep -q '^\[Daemon\]' "$PLYMOUTH_CONFIG"; then
    sed -i "/^\\[Daemon\\]/a Theme=$theme" "$PLYMOUTH_CONFIG"
  else
    printf '\n[Daemon]\nTheme=%s\n' "$theme" >>"$PLYMOUTH_CONFIG"
  fi
}

rebuild_initramfs() {
  /usr/sbin/update-initramfs -u -k all
}

if [[ "${1:-}" == "--restore-pix" ]]; then
  set_theme "pix"
  rebuild_initramfs
  echo "Restored the Raspberry Pi pix Plymouth theme."
  exit 0
fi

install -d -m 0755 "$THEME_TARGET" "$BACKUP_DIR"
backup_path="$BACKUP_DIR/plymouthd.conf.$(date +%Y%m%d%H%M%S)"
cp -a "$PLYMOUTH_CONFIG" "$backup_path"

install -m 0644 "$THEME_SOURCE/fitrace.plymouth" "$THEME_TARGET/fitrace.plymouth"
install -m 0644 "$THEME_SOURCE/fitrace.script" "$THEME_TARGET/fitrace.script"
install -m 0644 "$THEME_SOURCE/splash.png" "$THEME_TARGET/splash.png"

set_theme "fitrace"
rebuild_initramfs

echo "Installed the FitRace Studio Plymouth theme."
echo "Config backup: $backup_path"
