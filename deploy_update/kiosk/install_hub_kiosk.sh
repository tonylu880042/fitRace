#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root, for example: sudo $0" >&2
  exit 1
fi

KIOSK_USER="${FITRACE_KIOSK_USER:-tony}"
KIOSK_URL="${FITRACE_KIOSK_URL:-http://127.0.0.1:8000/static/index.html}"
KIOSK_HEALTH_URL="${FITRACE_KIOSK_HEALTH_URL:-http://127.0.0.1:8000/health}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
USER_HOME="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"

if [ -z "$USER_HOME" ] || [ ! -d "$USER_HOME" ]; then
  echo "Cannot find home directory for user: $KIOSK_USER" >&2
  exit 1
fi

install -m 0755 "$SCRIPT_DIR/fitracestudio-kiosk" /usr/local/bin/fitracestudio-kiosk

install -d -m 0755 "$USER_HOME/.config/labwc"
install -d -m 0755 "$USER_HOME/.config/fitracestudio"

STAMP="$(date +%Y%m%d%H%M%S)"
for file in autostart rc.xml; do
  if [ -f "$USER_HOME/.config/labwc/$file" ]; then
    cp "$USER_HOME/.config/labwc/$file" "$USER_HOME/.config/labwc/$file.fitrace-backup-$STAMP"
  fi
done

if [ -f /etc/xdg/labwc/autostart ]; then
  cp /etc/xdg/labwc/autostart "/etc/xdg/labwc/autostart.fitrace-backup-$STAMP"
fi

install -m 0644 "$SCRIPT_DIR/labwc-autostart" "$USER_HOME/.config/labwc/autostart"
install -m 0644 "$SCRIPT_DIR/labwc-rc.xml" "$USER_HOME/.config/labwc/rc.xml"
install -m 0644 "$SCRIPT_DIR/labwc-system-autostart" /etc/xdg/labwc/autostart

cat > "$USER_HOME/.config/fitracestudio/kiosk.env" <<EOF
FITRACE_KIOSK_URL=$KIOSK_URL
FITRACE_KIOSK_HEALTH_URL=$KIOSK_HEALTH_URL
EOF

chown -R "$KIOSK_USER:$KIOSK_USER" \
  "$USER_HOME/.config/labwc" \
  "$USER_HOME/.config/fitracestudio"

if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_boot_behaviour B4 >/dev/null 2>&1 || true
fi

if systemctl list-unit-files lightdm.service >/dev/null 2>&1; then
  systemctl enable lightdm.service >/dev/null 2>&1 || true
fi

echo "FitRaceStudio kiosk installed for user $KIOSK_USER"
echo "Dashboard URL: $KIOSK_URL"
echo "Reboot to enter kiosk mode."
