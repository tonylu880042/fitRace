#!/usr/bin/env bash
set -euo pipefail

BUCKET="fitracestudio-updates-prod"
DISTRIBUTION_ID="${DISTRIBUTION_ID:?set CloudFront distribution id}"

aws s3 cp deploy_update/releases/0.1.1/fitrace-hub-0.1.1.tar.zst "s3://${BUCKET}/releases/0.1.1/fitrace-hub-0.1.1.tar.zst"
aws s3 cp deploy_update/releases/0.1.1/fitrace-hub-0.1.1.tar.zst.sha256 "s3://${BUCKET}/releases/0.1.1/fitrace-hub-0.1.1.tar.zst.sha256"
aws s3 cp deploy_update/releases/0.1.1/fitrace-edge-0.1.1.tar.zst "s3://${BUCKET}/releases/0.1.1/fitrace-edge-0.1.1.tar.zst"
aws s3 cp deploy_update/releases/0.1.1/fitrace-edge-0.1.1.tar.zst.sha256 "s3://${BUCKET}/releases/0.1.1/fitrace-edge-0.1.1.tar.zst.sha256"
aws s3 cp deploy_update/releases/0.1.1/release-notes.md "s3://${BUCKET}/releases/0.1.1/release-notes.md"
aws s3 cp deploy_update/upload_config/channels/stable/manifest.json "s3://${BUCKET}/channels/stable/manifest.json"
aws s3 cp deploy_update/upload_config/channels/stable/manifest.json.sig "s3://${BUCKET}/channels/stable/manifest.json.sig"

aws cloudfront create-invalidation \
  --distribution-id "${DISTRIBUTION_ID}" \
  --paths "/channels/stable/manifest.json" "/channels/stable/manifest.json.sig"
