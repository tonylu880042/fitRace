#!/bin/bash
# Let a Central Hub shutdown actually power off this machine's Edge Node.
#
# Three gates block it, all configuration, and all of them fail SILENTLY --
# the Hub returns 200 and shuts itself down while the Edge Node ignores the
# command. This installer closes all three:
#
#   1. edge_node/main.py's _is_authorized_node_command() is fail-closed: with
#      no FITRACE_NODE_COMMAND_TOKEN it rejects EVERY command, and the Hub
#      only attaches a token when it has one itself. Hub and Edge must carry
#      the SAME value -- "neither side set" does not work.
#   2. execute_node_shutdown() is dry-run unless FITRACE_POWER_COMMANDS_ENABLED=1.
#   3. the Edge runtime's service user needs passwordless `systemctl poweroff`.
#
# Sibling of install-edge-service-restart.sh, which covers the narrower
# "restart the Edge runtime" capability. This one grants machine power-off,
# so it is deliberately separate.
#
# Run on the Hub box and on every Edge Node with the SAME token:
#   sudo ./install-node-shutdown.sh install <TOKEN> [edge-service-user]
set -euo pipefail

ACTION="${1:-install}"
TOKEN="${2:-}"
EDGE_OPERATOR_USER="${3:-${SUDO_USER:-tony}}"
EDGE_DROPIN_DIR="/etc/systemd/system/fitracestudio-edge.service.d"
HUB_DROPIN_DIR="/etc/systemd/system/fitracestudio-hub.service.d"
DROPIN_NAME="node-shutdown.conf"
SUDOERS_PATH="/etc/sudoers.d/fitrace-edge-poweroff"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

unit_present() { systemctl cat "$1" >/dev/null 2>&1; }

case "$ACTION" in
  install)
    [[ -n "$TOKEN" ]] || {
      echo "A shared token is required: $0 install <TOKEN> [edge-service-user]" >&2
      echo "Use the SAME value on the Hub and on every Edge Node." >&2
      exit 2
    }
    id "$EDGE_OPERATOR_USER" >/dev/null 2>&1 || {
      echo "Unknown Edge service user: $EDGE_OPERATOR_USER" >&2
      exit 1
    }

    restart_units=()

    if unit_present fitracestudio-edge.service; then
      install -d -m 0755 "$EDGE_DROPIN_DIR"
      dropin_tmp="$(mktemp)"
      printf '%s\n' \
        '[Service]' \
        "Environment=FITRACE_NODE_COMMAND_TOKEN=$TOKEN" \
        'Environment=FITRACE_POWER_COMMANDS_ENABLED=1' \
        >"$dropin_tmp"
      install -m 0600 "$dropin_tmp" "$EDGE_DROPIN_DIR/$DROPIN_NAME"
      rm -f "$dropin_tmp"
      restart_units+=(fitracestudio-edge.service)

      # Gate 3 only matters where the runtime is not already root.
      if [[ "$EDGE_OPERATOR_USER" != "root" ]]; then
        sudoers_tmp="$(mktemp)"
        printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff\n' \
          "$EDGE_OPERATOR_USER" >"$sudoers_tmp"
        chmod 0440 "$sudoers_tmp"
        visudo -cf "$sudoers_tmp"
        install -m 0440 "$sudoers_tmp" "$SUDOERS_PATH"
        rm -f "$sudoers_tmp"
      fi
    fi

    # The Hub attaches the token to the command it publishes; without this
    # half, a correctly configured Edge Node still rejects every shutdown.
    if unit_present fitracestudio-hub.service; then
      install -d -m 0755 "$HUB_DROPIN_DIR"
      hub_tmp="$(mktemp)"
      printf '%s\n' \
        '[Service]' \
        "Environment=FITRACE_NODE_COMMAND_TOKEN=$TOKEN" \
        >"$hub_tmp"
      install -m 0600 "$hub_tmp" "$HUB_DROPIN_DIR/$DROPIN_NAME"
      rm -f "$hub_tmp"
      restart_units+=(fitracestudio-hub.service)
    fi

    [[ ${#restart_units[@]} -gt 0 ]] || {
      echo "No fitracestudio hub or edge unit on this machine -- nothing to do." >&2
      exit 1
    }
    ;;
  uninstall)
    rm -f "$EDGE_DROPIN_DIR/$DROPIN_NAME" "$HUB_DROPIN_DIR/$DROPIN_NAME" "$SUDOERS_PATH"
    restart_units=()
    unit_present fitracestudio-edge.service && restart_units+=(fitracestudio-edge.service)
    unit_present fitracestudio-hub.service && restart_units+=(fitracestudio-hub.service)
    ;;
  *)
    echo "Usage: $0 [install|uninstall] <TOKEN> [edge-service-user]" >&2
    exit 2
    ;;
esac

systemctl daemon-reload
for unit in ${restart_units[@]+"${restart_units[@]}"}; do
  systemctl restart "$unit"
  systemctl is-active --quiet "$unit"
  echo "restarted $unit"
done

echo "Hub-led node shutdown: $ACTION complete"
