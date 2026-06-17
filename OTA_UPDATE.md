# FitRaceStudio OTA Update Strategy

This document defines how Central Hub and Edge Nodes should check, download, install, and roll back software updates when the Central Hub has internet access.

The system remains local-first during races. OTA is an idle maintenance workflow and must not be required for live race operation.

AWS/S3 cloud distribution requirements are documented in [AWS_CLOUD_REQUIREMENTS.md](AWS_CLOUD_REQUIREMENTS.md).
Cloud update validation steps are documented in [CLOUD_UPDATE_TEST_PLAN.md](CLOUD_UPDATE_TEST_PLAN.md).

## 1. Goals

The update system must support:

- Central Hub checking for updates through the public internet.
- Central Hub updating its own application bundle.
- Central Hub coordinating Edge Node updates over the local `fitRace26` LAN.
- Edge Nodes reporting their current software version and update state.
- Safe installation with signature verification and rollback.
- Deferred installation when a race is active.

The update system must not:

- Depend on internet access during a race.
- Allow unsigned or untrusted packages to install.
- Update all Edge Nodes blindly without compatibility checks.
- Modify AP credentials or customer network settings as part of ordinary app updates.

## 2. Recommended Update Architecture

Use Central Hub as the only device that talks to the public update service.

```text
Public update server
    |
    | HTTPS manifest + signed artifacts
    v
Central Hub update manager
    |
    | HTTP/MQTT commands over fitRace26 LAN
    v
Edge Node update agents
```

This keeps Edge Nodes simple and avoids requiring every Edge Node to have direct internet access.

## 3. Release Artifact Model

Each released version should publish:

```text
manifest.json
hub application artifact
edge application artifact
signature files
checksum files
release notes
```

Recommended artifact formats:

```text
fitrace-hub-1.2.3.tar.zst
fitrace-edge-1.2.3.tar.zst
fitrace-hub-1.2.3.tar.zst.sha256
fitrace-edge-1.2.3.tar.zst.sha256
manifest.json
manifest.json.sig
```

The manifest is the source of truth. Artifacts are downloaded only after the manifest signature is trusted.

## 4. Manifest Schema

Recommended `manifest.json`:

```json
{
  "schema_version": 1,
  "product": "fitracestudio",
  "release_version": "1.2.3",
  "channel": "stable",
  "published_at": "2026-06-16T00:00:00Z",
  "minimum_hub_version": "1.0.0",
  "minimum_edge_version": "1.0.0",
  "components": {
    "hub": {
      "version": "1.2.3",
      "artifact_url": "https://updates.example.com/fitrace-hub-1.2.3.tar.zst",
      "sha256": "hex-encoded-sha256",
      "requires_reboot": false,
      "systemd_units": ["fitracestudio-hub.service"]
    },
    "edge": {
      "version": "1.2.3",
      "artifact_url": "https://updates.example.com/fitrace-edge-1.2.3.tar.zst",
      "sha256": "hex-encoded-sha256",
      "requires_reboot": false,
      "systemd_units": ["fitracestudio-edge.service", "fitracestudio-edge-web.service"]
    }
  },
  "compatibility": {
    "mqtt_api_version": 1,
    "telemetry_schema_version": 1,
    "edge_config_schema_version": 1
  },
  "rollout": {
    "install_mode": "manual",
    "allow_during_race": false
  },
  "notes": "Short release summary"
}
```

## 5. Version Reporting

The existing Edge Node heartbeat already includes `software_version`. Extend it with update fields:

```json
{
  "edge_node_id": "fitrace-edge-01",
  "status": "online",
  "software_version": "1.2.2",
  "update": {
    "state": "idle",
    "target_version": null,
    "last_error": null,
    "last_checked_epoch_ms": 1780000000000
  }
}
```

Recommended update states:

```text
idle
checking
available
downloading
downloaded
installing
restarting
updated
failed
rollback
```

Central Hub should expose these states through `GET /api/nodes` and through the dashboard/control UI.

## 6. Hub Update Workflow

Python application updates should be treated as service restarts, not true hot patches. Do not rely on `importlib.reload()` or in-process module replacement for production updates because existing objects, async tasks, open sockets, and dependency state can remain from the old code.

Recommended Central Hub flow:

```text
1. Confirm internet is reachable.
2. Download manifest over HTTPS.
3. Verify manifest signature with a pinned public key.
4. Compare current hub version with manifest hub version.
5. Confirm the system is idle and no race is active.
6. Download hub artifact into a staging directory.
7. Verify artifact SHA-256.
8. Unpack into a versioned release directory.
9. Run preflight checks.
10. Switch the active symlink to the new release.
11. Restart the Hub systemd service.
12. Run health check.
13. Keep previous release for rollback.
```

Current implementation:

```text
Check cycle: no periodic timer yet.
Startup: Central Hub checks once after FastAPI starts.
Manual check: POST /api/updates/check
Download artifacts: POST /api/updates/download
Install Hub artifact: POST /api/updates/install/hub
Apply Hub update: POST /api/updates/apply/hub
Status: GET /api/updates/status
Signature: manifest.json.sig is verified with fitrace_common/release-ed25519-public.pem
Formal Hub runtime: fitracestudio-hub.service runs from /opt/fitracestudio/current
Formal update cache: /opt/fitracestudio/update-cache/{release_version}/
Hub install path: /opt/fitracestudio/update-cache/installed/hub-{release_version}/
Updater service: fitracestudio-hub-updater.service
Disable startup check: FITRACE_UPDATE_AUTO_CHECK=0
```

Recommended filesystem layout:

```text
/opt/fitracestudio/
  releases/
    hub-1.2.2/
    hub-1.2.3/
    edge-1.2.3/
  current -> /opt/fitracestudio/releases/hub-1.2.3
  update-cache/
  rollback/
```

Formal Hub systemd deployment uses the active symlink as the actual runtime path:

```text
fitracestudio-hub.service
  WorkingDirectory=/opt/fitracestudio/current
  ExecStart=/usr/bin/python3 -m hub_server.main

fitracestudio-hub-updater.service
  WorkingDirectory=/opt/fitracestudio/current
  FITRACE_UPDATE_CACHE_DIR=/opt/fitracestudio/update-cache
  FITRACE_RELEASE_ROOT=/opt/fitracestudio/releases
  FITRACE_CURRENT_LINK=/opt/fitracestudio/current
```

This is required for cloud update testing: after `POST /api/updates/apply/hub`, the updater switches `/opt/fitracestudio/current` to the staged release and restarts `fitracestudio-hub.service`, so the next Hub process runs the downloaded release.

The updater should be a separate privileged service, not the FastAPI web process itself. The Hub web app requests an update; the updater performs filesystem writes and service restarts.

Recommended systemd units:

```text
fitracestudio-hub.service
fitracestudio-hub-updater.service
fitracestudio-hub-updater.timer
```

Most Hub application updates should restart only `fitracestudio-hub.service`. Full OS reboot should be required only for kernel, OS package, driver, firmware, or low-level dependency changes.

## 7. Edge Node Update Workflow

Recommended Edge Node flow:

```text
1. Hub downloads and verifies the Edge artifact.
2. Hub checks each Edge Node heartbeat and compatibility.
3. Hub sends update prepare command to one Edge Node at a time or in small batches.
4. Edge downloads the artifact from Hub over the LAN.
5. Edge verifies SHA-256 and optional artifact signature.
6. Edge stages the release in a versioned directory.
7. Edge stops telemetry capture only when the system is idle.
8. Edge switches active release.
9. Edge restarts its services.
10. Edge reports updated heartbeat.
11. Hub continues to the next Edge Node.
```

Recommended Hub-hosted LAN endpoint:

```text
GET /api/updates/artifacts/edge/{version}
```

Recommended Edge endpoints:

```text
GET  /api/update/status
POST /api/update/prepare
POST /api/update/install
POST /api/update/rollback
```

Recommended MQTT command topics if using MQTT instead of HTTP:

```text
fitrace/nodes/{edge_node_id}/commands/update
fitrace/nodes/{edge_node_id}/events/update
```

HTTP is simpler for downloading artifacts. MQTT is useful for commands and progress events.

Most Edge application updates should restart only Edge services. Full Edge Node reboot should be required only for kernel, OS package, BLE/Wi-Fi driver, firmware, antenna board firmware, or low-level dependency changes.

## 8. Update Safety Rules

Updates should be blocked when:

- A race is running, paused, or waiting to start.
- Any Edge Node is actively streaming equipment data for a race.
- The Hub cannot verify the release manifest signature.
- The artifact checksum does not match.
- The target release requires a newer config schema than the device supports.
- The device has insufficient disk space for both current and staged releases.
- The device battery or power state is unsafe, if battery-backed hardware is used.

Updates may be allowed when:

- The system is idle.
- All required artifacts are downloaded and verified.
- The previous release is still available for rollback.
- At least one health check endpoint passes after install.

## 9. Rollback Requirements

Each device should keep at least one previous working release.

Rollback should happen automatically if:

- The service does not start within a timeout.
- `/health` or `/api/status` fails after restart.
- The Edge Node fails to reconnect to MQTT after restart.
- The Hub detects a version mismatch after an Edge update.

Rollback should also be available manually from the local admin UI.

## 10. Security Requirements

Minimum production security:

- Use HTTPS for public update checks.
- Pin or bundle the update signing public key on shipped devices.
- Sign the manifest, not just the transport.
- Verify SHA-256 for every artifact.
- Require local admin authentication before manual installation.
- Store update logs locally.
- Never execute arbitrary shell commands from the manifest.
- Limit updater service permissions to known directories and known systemd units.

Recommended signing choices:

```text
Minisign
Sigstore
GPG with offline release key
```

For an appliance-style product, Minisign is the simplest operational model.

## 11. Admin UI Requirements

Central Hub admin UI should show:

```text
Current Hub version
Latest available Hub version
Current Edge versions
Which Edge Nodes are behind
Release notes
Download progress
Install progress
Last update error
Rollback button
```

Recommended actions:

```text
Check for updates
Download update
Install Hub update
Install Edge updates
Retry failed Edge update
Rollback device
```

Installation should require explicit confirmation unless the product later adopts scheduled maintenance windows.

## 12. Power Management Requirements

The Hub management system should include controlled power actions for field operation:

```text
Restart Hub service
Reboot Central Hub
Shutdown Central Hub
Restart selected Edge Node services
Reboot selected Edge Node
Shutdown selected Edge Node
```

Power actions must be blocked when:

- A race is running, paused, or waiting to start.
- Update installation is in progress.
- The requesting user is not authenticated as a local admin.
- The command targets an unknown Edge Node.

Power actions should use a separate privileged helper or systemd-controlled command service. The FastAPI web process should request the action, record an audit log, and return status; it should not run arbitrary shell commands directly.

Development builds should keep power actions in dry-run mode. Deployed hardware must opt in explicitly:

```text
FITRACE_POWER_COMMANDS_ENABLED=1
FITRACE_ADMIN_TOKEN=<local-admin-token>
```

Recommended API shape:

```text
GET  /api/system/power/status
POST /api/system/power/restart-service
POST /api/system/power/reboot
POST /api/system/power/shutdown
POST /api/nodes/{edge_node_id}/power/restart-service
POST /api/nodes/{edge_node_id}/power/reboot
POST /api/nodes/{edge_node_id}/power/shutdown
```

Recommended safety behavior:

```text
Require confirmation text for reboot/shutdown
Reject during active race states
Show countdown before poweroff
Send final WebSocket notice to admin UI
Keep audit log with user, action, target, timestamp, and result
```

## 13. Implementation Phases

### Phase A: Version and status foundation

- Add a single source of truth for app version. Done: `fitrace_common.version.APP_VERSION`.
- Include Hub version in `GET /api/health`. Done.
- Include Edge version and update state in Edge heartbeat.
- Show versions in the Hub dashboard/admin UI.

### Phase B: Update check

- Add Hub update manager that downloads `manifest.json` and verifies `manifest.json.sig`. Done.
- Add `GET /api/updates/status`. Done.
- Add `POST /api/updates/check`. Done.
- Add `POST /api/updates/download` for Hub and Edge artifacts with SHA-256 verification. Done.
- Store last check result locally. Done, in process memory.

### Phase C: Hub self-update

- Add `POST /api/updates/install/hub` to unpack the Hub artifact into the local update cache. Done.
- Add a separate updater service. Done: `python -m hub_server.usecases.hub_update_applier`.
- Add `POST /api/updates/apply/hub` to start `fitracestudio-hub-updater.service`. Done.
- Download and stage Hub artifacts.
- Switch versioned release symlink.
- Restart Hub service.
- Verify health and rollback on failure.

### Phase D: Edge coordinated update

- Add Edge update agent endpoints.
- Hub serves verified Edge artifacts over LAN.
- Hub updates Edge Nodes sequentially.
- Edge reports progress and rollback state.

### Phase E: Production hardening

- Add signed releases in CI.
- Add update logs and audit records.
- Add admin authentication.
- Add controlled reboot/shutdown UI through a privileged helper.
- Add scheduled maintenance windows.
- Add staged rollout support by channel.

## 14. Recommended First Code Interfaces

Central Hub:

```text
GET  /api/updates/status
POST /api/updates/check
POST /api/updates/download
POST /api/updates/install/hub
POST /api/updates/install/edges
POST /api/updates/rollback/hub
GET  /api/updates/artifacts/edge/{version}
```

Edge Node:

```text
GET  /api/update/status
POST /api/update/prepare
POST /api/update/install
POST /api/update/rollback
```

Data models:

```text
UpdateManifest
ComponentRelease
UpdateStatus
UpdateCommand
UpdateResult
```

## 15. Operational Recommendation

For FitRaceStudio, the best product behavior is:

```text
Hub checks public update server when internet exists.
Hub downloads both Hub and Edge packages.
Hub installs itself first.
After Hub is healthy, Hub updates Edge Nodes one by one over fitRace26.
No update installs while a race is active.
Every device keeps one previous release for rollback.
```
