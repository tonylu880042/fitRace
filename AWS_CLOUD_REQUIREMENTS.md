# FitRaceStudio AWS OTA 建置步驟

請 RD 只做以下項目。這版只用來讓 Central Hub 下載 OTA 更新檔。

## 0. 目前已建好的 CloudFront OTA 位置

Amazon CloudFront distribution 已可讀取 OTA 測試檔。

目前 base URL：

```text
https://dd9tnec1hh2ts.cloudfront.net
```

目前可用檔案：

```text
https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json
https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json.sig
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-hub-0.1.0.tar.zst
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-edge-0.1.0.tar.zst
```

驗證結果：

```text
manifest.json: HTTP 200, content-length 1169
manifest.json.sig: HTTP 200, content-length 143
fitrace-hub-0.1.0.tar.zst: HTTP 200, content-length 1611905
fitrace-edge-0.1.0.tar.zst: HTTP 200, content-length 35074
```

注意：

```text
使用者原本提供的 edge artifact URL 結尾是 .tar.zs，該 URL 回 403。
正確檔名是 fitrace-edge-0.1.0.tar.zst。
```

目前 manifest 內容仍有一個要修正的地方：`artifact_url` 仍指向 `https://updates.fitracestudio.com/...`，但目前可用的是 CloudFront domain `https://dd9tnec1hh2ts.cloudfront.net/...`。

請 RD 重新上傳 `channels/stable/manifest.json`，將 `components.hub.artifact_url` 與 `components.edge.artifact_url` 改成：

```text
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-hub-0.1.0.tar.zst
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-edge-0.1.0.tar.zst
```

更新 manifest 後，請同步更新 `channels/stable/manifest.json.sig`，並建立 CloudFront invalidation：

```text
/channels/stable/manifest.json
/channels/stable/manifest.json.sig
```

## 1. 建立 S3 Bucket

建立 bucket：

```text
fitracestudio-updates-prod
```

設定：

```text
Block all public access: enabled
Bucket versioning: enabled
Server-side encryption: enabled
Object ownership: bucket owner enforced
```

不要開 public read。不要開 public write。

## 2. 建立 S3 目錄

建立以下 key/path：

```text
channels/stable/
channels/beta/
releases/0.1.0/
```

## 3. 上傳第一版測試檔

先上傳假檔案即可，檔名必須照下面：

```text
releases/0.1.0/fitrace-hub-0.1.0.tar.zst
releases/0.1.0/fitrace-hub-0.1.0.tar.zst.sha256
releases/0.1.0/fitrace-edge-0.1.0.tar.zst
releases/0.1.0/fitrace-edge-0.1.0.tar.zst.sha256
releases/0.1.0/release-notes.md
channels/stable/manifest.json
channels/stable/manifest.json.sig
```

`manifest.json` 內容先用：

```json
{
  "schema_version": 1,
  "product": "fitracestudio",
  "release_version": "0.1.0",
  "channel": "stable",
  "published_at": "2026-06-16T00:00:00Z",
  "minimum_hub_version": "0.1.0",
  "minimum_edge_version": "0.1.0",
  "components": {
    "hub": {
      "version": "0.1.0",
      "artifact_url": "https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-hub-0.1.0.tar.zst",
      "sha256": "replace-with-real-sha256",
      "requires_reboot": false,
      "systemd_units": ["fitracestudio-hub.service"]
    },
    "edge": {
      "version": "0.1.0",
      "artifact_url": "https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-edge-0.1.0.tar.zst",
      "sha256": "replace-with-real-sha256",
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
  "notes": "Initial cloud distribution test release."
}
```

`sha256` 請換成實際檔案 SHA-256。

`manifest.json.sig` 先放測試簽章檔。正式簽章 key 不要放在 S3。

## 4. 建立 CloudFront

建立 CloudFront distribution：

```text
Origin: fitracestudio-updates-prod S3 bucket
Origin access: Origin Access Control
Viewer protocol policy: Redirect HTTP to HTTPS
Allowed methods: GET, HEAD
```

S3 bucket policy 只允許這個 CloudFront distribution 讀取。

## 5. 目前網域

目前先使用 CloudFront 預設網域：

```text
dd9tnec1hh2ts.cloudfront.net
```

暫時不要設定自訂網域。日後若要改成 `updates.fitracestudio.com`，再建立 ACM 憑證並指到 CloudFront。

## 6. 建立 IAM 權限

建立一個 release publisher role/user。

需要權限：

```text
s3:PutObject
s3:GetObject
s3:ListBucket
cloudfront:CreateInvalidation
```

範圍限制在：

```text
arn:aws:s3:::fitracestudio-updates-prod
arn:aws:s3:::fitracestudio-updates-prod/*
dd9tnec1hh2ts.cloudfront.net 的 CloudFront distribution
```

不要給 production `s3:DeleteObject`。

## 7. 發布更新時照這個順序

```text
1. 上傳 releases/{version}/fitrace-hub-{version}.tar.zst
2. 上傳 releases/{version}/fitrace-hub-{version}.tar.zst.sha256
3. 上傳 releases/{version}/fitrace-edge-{version}.tar.zst
4. 上傳 releases/{version}/fitrace-edge-{version}.tar.zst.sha256
5. 上傳 releases/{version}/release-notes.md
6. 上傳 channels/stable/manifest.json
7. 上傳 channels/stable/manifest.json.sig
8. 建立 CloudFront invalidation
```

Invalidation path：

```text
/channels/stable/manifest.json
/channels/stable/manifest.json.sig
```

## 8. RD 完成後請回傳

請回傳以下資訊：

```text
Update base URL: https://dd9tnec1hh2ts.cloudfront.net
Stable manifest URL: https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json
S3 bucket name
CloudFront distribution ID
ACM certificate ARN
IAM publisher role/user ARN
Release upload command or steps
CloudFront invalidation command or steps
```

## 9. 驗收方式

以下 URL 必須可從外部下載：

```text
https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json
https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json.sig
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-hub-0.1.0.tar.zst
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-edge-0.1.0.tar.zst
```

Central Hub 只需要能連：

```text
dd9tnec1hh2ts.cloudfront.net:443
```
