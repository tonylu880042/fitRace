# OTA 0.1.1 上傳清單

先上傳 release 檔案：

```text
deploy_update/releases/0.1.1/fitrace-hub-0.1.1.tar.zst
deploy_update/releases/0.1.1/fitrace-hub-0.1.1.tar.zst.sha256
deploy_update/releases/0.1.1/fitrace-edge-0.1.1.tar.zst
deploy_update/releases/0.1.1/fitrace-edge-0.1.1.tar.zst.sha256
deploy_update/releases/0.1.1/release-notes.md
```

上傳到 S3 key：

```text
releases/0.1.1/fitrace-hub-0.1.1.tar.zst
releases/0.1.1/fitrace-hub-0.1.1.tar.zst.sha256
releases/0.1.1/fitrace-edge-0.1.1.tar.zst
releases/0.1.1/fitrace-edge-0.1.1.tar.zst.sha256
releases/0.1.1/release-notes.md
```

最後上傳 stable channel：

```text
deploy_update/upload_config/channels/stable/manifest.json
deploy_update/upload_config/channels/stable/manifest.json.sig
```

上傳到 S3 key：

```text
channels/stable/manifest.json
channels/stable/manifest.json.sig
```

CloudFront invalidation：

```text
/channels/stable/manifest.json
/channels/stable/manifest.json.sig
```

驗證：

```bash
curl -fsS https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json
curl -fsS https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json.sig
curl -fsSI https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.1/fitrace-hub-0.1.1.tar.zst
curl -fsSI https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.1/fitrace-edge-0.1.1.tar.zst
```
