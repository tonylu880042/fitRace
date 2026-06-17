# FitRaceStudio Cloud Update Test Plan

This document defines the test plan for validating cloud-delivered software updates before deploying the update system to Raspberry Pi hardware.

The first goal is to prove that Central Hub can reach Amazon-hosted update metadata and artifacts, validate the release, and report the correct update decision. Actual service restart, OS reboot, and production installation can remain disabled during the first cloud test.

For the formal Hub runtime test, the Central Hub service must run from the active release symlink:

```text
/opt/fitracestudio/current -> /opt/fitracestudio/releases/hub-<version>
fitracestudio-hub.service WorkingDirectory=/opt/fitracestudio/current
```

This makes the cloud update test exercise the same path that production updates use: staged release, symlink switch, Hub service restart, health check.

Related documents:

- [AWS_CLOUD_REQUIREMENTS.md](AWS_CLOUD_REQUIREMENTS.md)
- [OTA_UPDATE.md](OTA_UPDATE.md)
- [DEPLOYMENT.md](DEPLOYMENT.md)

## 1. Test Objectives

Validate the following:

```text
AWS/S3 update repository can host release metadata and artifacts
Central Hub can download manifest and artifacts over HTTPS
Manifest signature verification can be tested
Artifact SHA-256 verification can be tested
Hub update check can identify available, current, invalid, and failed updates
Edge update artifact can be downloaded by Hub for later LAN distribution
No live race operation depends on cloud access
No destructive update, reboot, or shutdown action runs during this test phase
```

## 2. Cloud Setup Plan

### 2.1 AWS Environment

Use a staging environment first.

Recommended AWS region:

```text
ap-northeast-1 or the region already used by the company Amazon account
```

Required AWS resources:

```text
S3 staging bucket
CloudFront distribution or temporary S3 HTTPS access
IAM release publisher role
IAM read-only auditor role
CloudTrail logging for bucket writes
CloudWatch metrics or access log review
```

Recommended staging names:

```text
S3 bucket: fitracestudio-updates-staging
CloudFront domain: staging-updates.fitracestudio.com
Channel: staging
```

If a custom domain is not ready, use the CloudFront default domain for the first test:

```text
https://<cloudfront-distribution>.cloudfront.net/channels/staging/manifest.json
```

### 2.2 S3 Bucket Configuration

Configure the staging bucket:

```text
Block all public access: enabled
Bucket versioning: enabled
Server-side encryption: enabled
Object ownership: bucket owner enforced
CloudTrail data events: enabled for write operations
Lifecycle policy: expire staging test releases after 30-90 days
```

Preferred access pattern:

```text
Central Hub -> HTTPS -> CloudFront -> private S3 bucket
```

Fallback for early testing:

```text
Central Hub -> HTTPS -> S3 object URL or presigned URL
```

Production should use stable HTTPS URLs, not presigned URLs.

### 2.3 Object Layout

Create this staging structure:

```text
s3://fitracestudio-updates-staging/
  channels/
    staging/
      manifest.json
      manifest.json.sig
  releases/
    0.1.1-test.1/
      fitrace-hub-0.1.1-test.1.tar.zst
      fitrace-hub-0.1.1-test.1.tar.zst.sha256
      fitrace-edge-0.1.1-test.1.tar.zst
      fitrace-edge-0.1.1-test.1.tar.zst.sha256
      release-notes.md
```

Upload versioned artifacts first. Update `channels/staging/manifest.json` last.

## 3. Test Release Preparation

### 3.1 Artifact Content

For the first cloud test, artifacts can be minimal tar archives and do not need to contain production code.

Example artifact contents:

```text
VERSION
release-notes.md
manifest-test-marker.txt
```

Example `VERSION` file:

```text
component=hub
version=0.1.1-test.1
build=test-cloud-update
```

Create one Hub artifact and one Edge artifact:

```text
fitrace-hub-0.1.1-test.1.tar.zst
fitrace-edge-0.1.1-test.1.tar.zst
```

### 3.2 Checksums

Generate SHA-256 files:

```bash
sha256sum fitrace-hub-0.1.1-test.1.tar.zst > fitrace-hub-0.1.1-test.1.tar.zst.sha256
sha256sum fitrace-edge-0.1.1-test.1.tar.zst > fitrace-edge-0.1.1-test.1.tar.zst.sha256
```

On macOS:

```bash
shasum -a 256 fitrace-hub-0.1.1-test.1.tar.zst > fitrace-hub-0.1.1-test.1.tar.zst.sha256
shasum -a 256 fitrace-edge-0.1.1-test.1.tar.zst > fitrace-edge-0.1.1-test.1.tar.zst.sha256
```

### 3.3 Manifest

Use this staging manifest shape:

```json
{
  "schema_version": 1,
  "product": "fitracestudio",
  "release_version": "0.1.1-test.1",
  "channel": "staging",
  "published_at": "2026-06-16T00:00:00Z",
  "minimum_hub_version": "0.1.0",
  "minimum_edge_version": "0.1.0",
  "components": {
    "hub": {
      "version": "0.1.1-test.1",
      "artifact_url": "https://staging-updates.fitracestudio.com/releases/0.1.1-test.1/fitrace-hub-0.1.1-test.1.tar.zst",
      "sha256": "replace-with-real-hub-sha256",
      "requires_reboot": false,
      "systemd_units": ["fitracestudio-hub.service"]
    },
    "edge": {
      "version": "0.1.1-test.1",
      "artifact_url": "https://staging-updates.fitracestudio.com/releases/0.1.1-test.1/fitrace-edge-0.1.1-test.1.tar.zst",
      "sha256": "replace-with-real-edge-sha256",
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
  "notes": "Staging cloud update test release."
}
```

### 3.4 Signature

For the first integration test, prepare both paths:

```text
Unsigned manifest test: Hub should reject or mark as untrusted once verification is implemented
Signed manifest test: Hub should accept after public key is configured
```

Recommended signing tool:

```text
Minisign
```

The private signing key must not be uploaded to S3.

## 4. Hub Test Environment

Use one local development Hub first, then one lab Central Hub device.

Required Hub configuration values:

```text
FITRACE_UPDATE_CHANNEL=staging
FITRACE_UPDATE_MANIFEST_URL=https://staging-updates.fitracestudio.com/channels/staging/manifest.json
FITRACE_UPDATE_SIGNATURE_URL=https://staging-updates.fitracestudio.com/channels/staging/manifest.json.sig
FITRACE_POWER_COMMANDS_ENABLED unset or 0
```

For this phase, keep installation destructive actions disabled:

```text
No service restart
No symlink switch
No reboot
No shutdown
No Edge Node install command
```

The test should only download, verify, and report status unless explicitly moved to the installation phase.

## 5. Test Phases

### Phase 1: Cloud Object Reachability

Purpose: verify cloud access from a laptop or development Hub.

Steps:

```text
1. Open manifest URL in browser or curl.
2. Download manifest.json.
3. Download manifest.json.sig.
4. Download Hub artifact.
5. Download Edge artifact.
6. Compare downloaded artifact SHA-256 with manifest value.
```

Pass criteria:

```text
All URLs return HTTP 200
No S3 permission error
No CloudFront origin error
SHA-256 matches manifest values
CloudTrail shows expected upload/write events
```

### Phase 2: Manifest Validation

Purpose: verify manifest schema and trust behavior.

Steps:

```text
1. Validate manifest JSON parses.
2. Validate required fields exist.
3. Validate product is fitracestudio.
4. Validate channel is staging.
5. Validate hub and edge components exist.
6. Validate artifact URLs are HTTPS.
7. Validate signed manifest when signature verification is available.
```

Pass criteria:

```text
Valid manifest is accepted
Malformed JSON is rejected
Wrong product is rejected
Missing component is rejected
HTTP artifact URL is rejected
Bad signature is rejected
```

### Phase 3: Hub Update Check Dry Run

Purpose: verify Hub can detect update availability without installing.

Steps:

```text
1. Set current local Hub version lower than staging release version.
2. Trigger update check.
3. Confirm Hub reports update available.
4. Confirm Hub can show Hub target version and Edge target version.
5. Confirm no service restart or filesystem install occurs.
6. Set current version equal to staging release version.
7. Trigger update check again.
8. Confirm Hub reports no update needed.
```

Pass criteria:

```text
Update available state is correct
No update needed state is correct
Artifacts are not installed during dry run
Power actions remain dry-run
Race operation still works locally if cloud is later disconnected
```

### Phase 4: Artifact Download and Checksum

Purpose: verify Hub can download release artifacts to local cache safely.

Steps:

```text
1. Trigger update download in dry-run/staging mode.
2. Download Hub artifact into local update cache.
3. Download Edge artifact into local update cache.
4. Verify each artifact SHA-256.
5. Confirm checksum mismatch blocks the release.
6. Confirm corrupt or partial downloads are not marked ready.
```

Pass criteria:

```text
Correct artifacts are cached
Checksum mismatch fails safely
Partial downloads are cleaned up or marked invalid
Downloaded artifact paths are outside application runtime directories
```

### Phase 5: Race Safety Gate

Purpose: verify updates cannot install during active race states.

Steps:

```text
1. Configure race so Hub state becomes READY.
2. Try install/update action.
3. Start race so Hub state becomes RUNNING.
4. Try install/update action.
5. Stop race so Hub state becomes STOPPED.
6. Try install/update action.
7. Reset race so Hub state becomes IDLE.
8. Try dry-run install/update action.
```

Pass criteria:

```text
READY blocks install
RUNNING blocks install
STOPPED blocks install if policy requires only IDLE
IDLE allows dry-run install preparation
No race telemetry or dashboard flow breaks
```

### Phase 6: Failure Scenario Tests

Run these negative tests:

```text
Manifest URL unreachable
CloudFront returns 403
CloudFront returns 404
Manifest JSON malformed
Manifest product mismatch
Manifest version older than current version
Manifest missing edge component
Artifact checksum mismatch
Artifact URL unreachable
Signature missing
Signature invalid
Clock/time unavailable on Hub
Internet removed after manifest download
Internet removed during artifact download
```

Pass criteria:

```text
Hub reports clear error state
Hub does not install anything
Hub keeps existing version active
Race functions remain local and available
Errors are visible in admin UI or update status API
```

### Phase 7: Lab Central Hub Test

Purpose: run the same tests from a lab Hub on a real local network without RPi deployment.

Setup:

```text
Central Hub connected to internet
Central Hub connected to fitRace26 or test LAN
No Edge install enabled
Power commands dry-run
Staging update URLs configured
```

Steps:

```text
1. Confirm Hub dashboard works locally.
2. Confirm Hub can reach staging manifest URL.
3. Trigger update check.
4. Trigger artifact download.
5. Disconnect internet.
6. Confirm race dashboard and local APIs still work.
7. Reconnect internet.
8. Trigger update check again.
```

Pass criteria:

```text
Cloud update check works when internet exists
Local race operation works without internet
No reboot/shutdown occurs
No Edge Node update command is sent
```

## 6. Test Data and Logs to Capture

Capture:

```text
Manifest URL
Manifest JSON used
Manifest signature file
Artifact URLs
Artifact SHA-256 values
Hub update status response
Hub application logs
CloudFront access logs if enabled
CloudTrail object write events
Screenshots of admin update status
Failure test result table
```

Minimum test result table:

```text
Test case
Environment
Input manifest version
Expected result
Actual result
Pass/fail
Log path or screenshot path
Notes
```

## 7. Rollback and Cleanup

For staging:

```text
Keep the latest successful test release
Delete or expire failed test artifacts through lifecycle policy
Keep CloudTrail logs
Do not delete signed manifest records until test review is complete
```

If any test accidentally enables install behavior:

```text
Stop immediately
Record current Hub version and active path
Confirm no systemd service was restarted
Confirm no current symlink was changed
Restore previous local files if needed
Mark the release channel manifest invalid or remove it from staging
```

For the formal runtime update test, record these values before and after applying a Hub update:

```bash
readlink -f /opt/fitracestudio/current
systemctl status fitracestudio-hub.service --no-pager
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/api/updates/status
```

## 8. Exit Criteria

Cloud update testing is ready to move to RPi/lab hardware only when:

```text
Staging bucket and CloudFront are stable
Manifest and artifacts download over HTTPS
Checksum validation passes
Bad manifest and bad checksum fail safely
Hub can detect update available in dry-run mode
Hub blocks install while race is not IDLE
Hub continues local race operation without internet
Cloud teammate has documented bucket, domain, IAM roles, and upload process
Release signing public key handling is decided
```

## 9. Responsibilities

Cloud/platform teammate:

```text
Create S3 bucket and CloudFront distribution
Create IAM roles
Upload test release artifacts
Enable CloudTrail logging
Return manifest URLs and release upload instructions
```

Application teammate:

```text
Implement update check and dry-run download logic
Verify manifest schema
Verify signature and checksum behavior
Expose update status in Hub admin API/UI
Keep install/reboot/shutdown disabled during cloud test
```

QA/test owner:

```text
Run test phases
Capture logs and screenshots
Record pass/fail table
Confirm local race operation is unaffected by cloud outages
```

## 10. Recommended First Milestone

Milestone 1 should be limited to:

```text
One staging S3 bucket
One CloudFront URL
One fake Hub artifact
One fake Edge artifact
One staging manifest
One update check dry-run from local development Hub
No installation
No reboot
No Edge update command
```

This milestone proves the cloud path without risking device stability.
