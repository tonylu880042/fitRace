#!/bin/bash
set -euo pipefail

ACTION="${1:-install}"
EDGE_OPERATOR_USER="${2:-${SUDO_USER:-tony}}"
DROPIN_DIR="/etc/systemd/system/fitracestudio-edge-web-config.service.d"
DROPIN_PATH="$DROPIN_DIR/edge-service-restart.conf"
SUDOERS_PATH="/etc/sudoers.d/fitrace-edge-service-restart"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

if ! id "$EDGE_OPERATOR_USER" >/dev/null 2>&1; then
  echo "Unknown Edge service user: $EDGE_OPERATOR_USER" >&2
  exit 1
fi

case "$ACTION" in
  install)
    install -d -m 0755 "$DROPIN_DIR"

    dropin_tmp="$(mktemp)"
    sudoers_tmp="$(mktemp)"
    trap 'rm -f "$dropin_tmp" "$sudoers_tmp"' EXIT

    printf '%s\n' \
      '[Service]' \
      'Environment=FITRACE_EDGE_SERVICE_RESTART_ENABLED=1' \
      >"$dropin_tmp"

    printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl restart fitracestudio-edge.service\n' \
      "$EDGE_OPERATOR_USER" >"$sudoers_tmp"
    chmod 0440 "$sudoers_tmp"
    visudo -cf "$sudoers_tmp"

    install -m 0644 "$dropin_tmp" "$DROPIN_PATH"
    install -m 0440 "$sudoers_tmp" "$SUDOERS_PATH"
    ;;
  uninstall)
    rm -f "$DROPIN_PATH" "$SUDOERS_PATH"
    ;;
  *)
    echo "Usage: $0 [install|uninstall] [edge-service-user]" >&2
    exit 2
    ;;
esac

systemctl daemon-reload
systemctl restart fitracestudio-edge-web-config.service
systemctl is-active --quiet fitracestudio-edge-web-config.service

echo "Edge service restart capability: $ACTION complete"
