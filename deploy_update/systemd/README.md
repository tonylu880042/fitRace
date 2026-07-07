# FitRaceStudio Hub systemd deployment

These units make `/opt/fitracestudio/current` the actual Central Hub runtime path.
Cloud update tests must use this layout so the updater service verifies the same symlink switch used in production.

## Units

```text
fitracestudio-hub.service
  Runs the Hub application from /opt/fitracestudio/current.

fitracestudio-hub-updater.service
  Applies a staged Hub release from /opt/fitracestudio/update-cache and restarts the Hub service.
```

## Initial device setup

Run once on the Hub device:

```bash
sudo useradd --system --home /opt/fitracestudio --shell /usr/sbin/nologin fitrace || true
sudo mkdir -p /opt/fitracestudio/releases /opt/fitracestudio/update-cache /opt/fitracestudio/rollback
sudo chown -R fitrace:fitrace /opt/fitracestudio/update-cache
```

Install the first release. The release directory must contain the Python packages at its top level, including `hub_server`, `edge_node`, and `fitrace_common`.

```bash
sudo mkdir -p /opt/fitracestudio/releases/hub-0.1.0
sudo rsync -a --delete ./ /opt/fitracestudio/releases/hub-0.1.0/
sudo ln -sfn /opt/fitracestudio/releases/hub-0.1.0 /opt/fitracestudio/current
```

Install and start the units:

```bash
sudo cp deploy_update/systemd/fitracestudio-hub.service /etc/systemd/system/
sudo cp deploy_update/systemd/fitracestudio-hub-updater.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fitracestudio-hub.service
```

## Management tokens

Production deployments must configure local management tokens before enabling operator power controls or authenticated admin pages.

| Variable | Required on | Purpose |
| --- | --- | --- |
| `FITRACE_ADMIN_TOKEN` | Hub and Edge services | Protects HTTP management APIs such as race control, station assignment, updates, power actions, Edge Wi-Fi status, and Edge BLE troubleshooting scan. |
| `FITRACE_NODE_COMMAND_TOKEN` | Hub and Edge services | Protects Hub-to-Edge MQTT commands such as Edge shutdown. Hub and every Edge Node in one shipped system must share this value. |
| `FITRACE_POWER_COMMANDS_ENABLED` | Hub and Edge services | Set to `1` only on deployed hardware where real reboot/shutdown commands are allowed. Omit or set anything else for dry-run mode. |
| `FITRACE_EDGE_MONITOR_PATH` | Edge service | Stores recent UART RX/TX and MQTT publish monitor events shown on the Edge setup page. |

Use a systemd drop-in so release updates do not overwrite local secrets:

```bash
sudo install -d -m 0750 /etc/fitracestudio
sudo tee /etc/fitracestudio/hub.env >/dev/null <<'EOF'
FITRACE_ADMIN_TOKEN=replace-with-local-admin-token
FITRACE_NODE_COMMAND_TOKEN=replace-with-hub-edge-command-token
FITRACE_RACE_RESULTS_PATH=/opt/fitracestudio/race_results.jsonl
# FITRACE_POWER_COMMANDS_ENABLED=1
EOF
sudo chown root:fitrace /etc/fitracestudio/hub.env
sudo chmod 0640 /etc/fitracestudio/hub.env

sudo systemctl edit fitracestudio-hub.service
```

Add this drop-in content:

```ini
[Service]
EnvironmentFile=/etc/fitracestudio/hub.env
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart fitracestudio-hub.service
```

If an Edge Node service is installed separately, configure the same variables through that Edge service's own `EnvironmentFile`. The Hub and Edge services must agree on `FITRACE_NODE_COMMAND_TOKEN`; otherwise Hub-sent MQTT shutdown commands will be rejected by Edge Nodes.

Example Edge service environment:

```bash
sudo tee /etc/fitracestudio/edge.env >/dev/null <<'EOF'
FITRACE_ADMIN_TOKEN=replace-with-local-admin-token
FITRACE_NODE_COMMAND_TOKEN=replace-with-hub-edge-command-token
FITRACE_EDGE_MONITOR_PATH=/opt/fitracestudio/edge_monitor.jsonl
# FITRACE_POWER_COMMANDS_ENABLED=1
EOF
sudo chown root:fitrace /etc/fitracestudio/edge.env
sudo chmod 0640 /etc/fitracestudio/edge.env
```

## Runtime verification

```bash
set -a
. /etc/fitracestudio/hub.env
set +a
readlink -f /opt/fitracestudio/current
systemctl status fitracestudio-hub.service --no-pager
curl -fsS http://127.0.0.1:8000/health
curl -fsS -H "X-FitRace-Admin-Token: $FITRACE_ADMIN_TOKEN" http://127.0.0.1:8000/api/updates/status
```

An unauthenticated management request should fail once `FITRACE_ADMIN_TOKEN` is configured:

```bash
curl -i http://127.0.0.1:8000/api/updates/status
```

Expected result: HTTP `401`.

## Cloud update apply test

After `/api/updates/download` and `/api/updates/install/hub` report success, apply the staged release:

```bash
curl -fsS -X POST \
  -H "X-FitRace-Admin-Token: $FITRACE_ADMIN_TOKEN" \
  http://127.0.0.1:8000/api/updates/apply/hub
sleep 3
readlink -f /opt/fitracestudio/current
curl -fsS http://127.0.0.1:8000/health
```

Expected result: `/opt/fitracestudio/current` points at the new `/opt/fitracestudio/releases/hub-<version>` directory and `fitracestudio-hub.service` is running from that release.
