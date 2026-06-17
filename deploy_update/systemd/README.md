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

## Runtime verification

```bash
readlink -f /opt/fitracestudio/current
systemctl status fitracestudio-hub.service --no-pager
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/api/updates/status
```

## Cloud update apply test

After `/api/updates/download` and `/api/updates/install/hub` report success, apply the staged release:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/updates/apply/hub
sleep 3
readlink -f /opt/fitracestudio/current
curl -fsS http://127.0.0.1:8000/health
```

Expected result: `/opt/fitracestudio/current` points at the new `/opt/fitracestudio/releases/hub-<version>` directory and `fitracestudio-hub.service` is running from that release.
