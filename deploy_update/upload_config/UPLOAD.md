# OTA 0.1.2 上傳清單

注意：`manifest.json.sig` 必須先用正式 release private key 重新產生。
本機目前只有 public key，不能產生可被 Hub 驗證通過的簽章。

先上傳 release 檔案：

```text
deploy_update/releases/0.1.2/fitrace-hub-0.1.2.tar.zst
deploy_update/releases/0.1.2/fitrace-hub-0.1.2.tar.zst.sha256
deploy_update/releases/0.1.2/fitrace-edge-0.1.2.tar.zst
deploy_update/releases/0.1.2/fitrace-edge-0.1.2.tar.zst.sha256
deploy_update/releases/0.1.2/release-notes.md
```

上傳到 S3 key：

```text
releases/0.1.2/fitrace-hub-0.1.2.tar.zst
releases/0.1.2/fitrace-hub-0.1.2.tar.zst.sha256
releases/0.1.2/fitrace-edge-0.1.2.tar.zst
releases/0.1.2/fitrace-edge-0.1.2.tar.zst.sha256
releases/0.1.2/release-notes.md
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
curl -fsSI https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.2/fitrace-hub-0.1.2.tar.zst
curl -fsSI https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.2/fitrace-edge-0.1.2.tar.zst
```
