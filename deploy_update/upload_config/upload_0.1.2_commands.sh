#!/usr/bin/env bash
set -euo pipefail

BUCKET="fitracestudio-updates-prod"
DISTRIBUTION_ID="${DISTRIBUTION_ID:?set CloudFront distribution id}"

if [[ ! -s deploy_update/upload_config/channels/stable/manifest.json.sig ]]; then
  echo "Missing deploy_update/upload_config/channels/stable/manifest.json.sig"
  echo "Sign manifest.json with the release private key before uploading."
  exit 1
fi

aws s3 cp deploy_update/releases/0.1.2/fitrace-hub-0.1.2.tar.zst "s3://${BUCKET}/releases/0.1.2/fitrace-hub-0.1.2.tar.zst"
aws s3 cp deploy_update/releases/0.1.2/fitrace-hub-0.1.2.tar.zst.sha256 "s3://${BUCKET}/releases/0.1.2/fitrace-hub-0.1.2.tar.zst.sha256"
aws s3 cp deploy_update/releases/0.1.2/fitrace-edge-0.1.2.tar.zst "s3://${BUCKET}/releases/0.1.2/fitrace-edge-0.1.2.tar.zst"
aws s3 cp deploy_update/releases/0.1.2/fitrace-edge-0.1.2.tar.zst.sha256 "s3://${BUCKET}/releases/0.1.2/fitrace-edge-0.1.2.tar.zst.sha256"
aws s3 cp deploy_update/releases/0.1.2/release-notes.md "s3://${BUCKET}/releases/0.1.2/release-notes.md"
aws s3 cp deploy_update/upload_config/channels/stable/manifest.json "s3://${BUCKET}/channels/stable/manifest.json"
aws s3 cp deploy_update/upload_config/channels/stable/manifest.json.sig "s3://${BUCKET}/channels/stable/manifest.json.sig"

aws cloudfront create-invalidation \
  --distribution-id "${DISTRIBUTION_ID}" \
  --paths "/channels/stable/manifest.json" "/channels/stable/manifest.json.sig"
