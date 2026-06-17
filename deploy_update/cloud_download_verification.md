# Cloud Update Download Verification

驗證時間：2026-06-17

## 結論

雲端 artifacts 可以從 CloudFront 下載，且 SHA-256 正確。

但目前 `channels/stable/manifest.json` 內的 `artifact_url` 仍指向 `https://updates.fitracestudio.com/...`。該網域目前無法解析，所以 Hub 若照 manifest 內容下載 artifact，會失敗。

`manifest.json.sig` 目前是測試 placeholder，不是正式簽章。因此目前只能驗證下載與 checksum，不能驗證正式簽章信任流程。

## 已驗證 URL

```text
https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json
https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json.sig
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-hub-0.1.0.tar.zst
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-edge-0.1.0.tar.zst
```

## Checksum 結果

```text
fitrace-hub-0.1.0.tar.zst
04101e833d43e56e65d55478c6d1319023ff42acffb65b52214699a58135e608

fitrace-edge-0.1.0.tar.zst
96c51b875f693ece9398c59560f0413938b34d0ea0585f5e29e12885bb9fb69d
```

以上兩個 hash 與 manifest 內 `components.*.sha256` 相符。

## 目前阻塞項

RD 需要重新上傳 `channels/stable/manifest.json`，把 artifact URLs 改成 CloudFront domain：

```text
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-hub-0.1.0.tar.zst
https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-edge-0.1.0.tar.zst
```

接著重新產生並上傳 `channels/stable/manifest.json.sig`，再建立 CloudFront invalidation：

```text
/channels/stable/manifest.json
/channels/stable/manifest.json.sig
```

## 驗證命令

```bash
mkdir -p /tmp/fitrace_cloud_update_verify
cd /tmp/fitrace_cloud_update_verify

curl -fsSLO https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json
curl -fsSLO https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json.sig
curl -fsSLO https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-hub-0.1.0.tar.zst
curl -fsSLO https://dd9tnec1hh2ts.cloudfront.net/releases/0.1.0/fitrace-edge-0.1.0.tar.zst

shasum -a 256 fitrace-*.tar.zst
```
