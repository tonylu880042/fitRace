# FitRaceStudio 0.1.2

## 更新內容

- 新增團隊競賽模式與 Team Battle、Race Track、Sprint Board leaderboard 視覺。
- Game Admin 可切換 leaderboard 呈現、團隊完成規則與開賽音效。
- Dashboard 主頁只負責顯示，不提供比賽設定或操作。
- 新增 3, 2, 1, Go 開賽倒數畫面與人聲提示音。
- 改善 Game Admin/System Admin 操作權限與事件流程提示。
- 新增 Dashboard UX 自動化瀏覽器驗證腳本。
- 新增 RPi4 Dashboard 長時間實機測試計畫。
- 報名頭像改為手機端壓縮 WebP 後上傳，降低 20 人同時上傳大圖時的 Hub 負載。

## 注意

- `manifest.json.sig` 必須使用正式 release private key 重新簽章後才能上雲。
- 已安裝 0.1.1 的 Hub 會以 manifest 的 Hub version `0.1.2` 判定有新版本。
- Edge 套用流程仍保留為後續 LAN 分發規劃，這次 Edge artifact 主要提供版本一致性與後續測試使用。
