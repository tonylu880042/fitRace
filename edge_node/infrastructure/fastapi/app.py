import os
from dataclasses import asdict

from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from edge_node.infrastructure.ble.ftms_scanner import BleakFtmsScanner
from edge_node.infrastructure.network.wifi_status import LinuxWifiStatusReader, WifiStatus
from edge_node.usecases.ftms_scanner import scan_ftms_devices
from fitrace_common.power_manager import PowerActionError, PowerManager


app = FastAPI(title="FitRaceStudio Edge Node")
ftms_scanner = BleakFtmsScanner()
wifi_status_reader = LinuxWifiStatusReader()
power_manager = PowerManager(
    target="edge",
    service_name="fitracestudio-edge.service",
)


class PowerActionPayload(BaseModel):
    confirmation: str | None = None


def require_admin(request: Request):
    expected_token = os.getenv("FITRACE_ADMIN_TOKEN")
    if not expected_token:
        return
    provided_token = request.headers.get("X-FitRace-Admin-Token")
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Admin token required")


@app.get("/health")
def health_check():
    return {"status": "ok", "role": "edge"}


@app.get("/api/system/power/status")
def get_power_status(request: Request):
    require_admin(request)
    return power_manager.status()


def run_power_action(action):
    try:
        return asdict(action())
    except PowerActionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/system/power/restart-service")
def restart_edge_service(request: Request):
    require_admin(request)
    return run_power_action(power_manager.restart_service)


@app.post("/api/system/power/reboot")
def reboot_edge(payload: PowerActionPayload, request: Request):
    require_admin(request)
    return run_power_action(lambda: power_manager.reboot(payload.confirmation))


@app.post("/api/system/power/shutdown")
def shutdown_edge(payload: PowerActionPayload, request: Request):
    require_admin(request)
    return run_power_action(lambda: power_manager.shutdown(payload.confirmation))


@app.get("/", response_class=HTMLResponse)
def edge_setup_page():
    return HTMLResponse(EDGE_SETUP_HTML)


@app.get("/api/ble/scan")
async def scan_ble_ftms_devices(
    adapter: str = Query(
        "hci1",
        description="Linux BLE adapter to scan with. Use hci1 for the USB dongle by default.",
    ),
    timeout_sec: float = Query(5.0, gt=0, le=30),
    include_all: bool = Query(
        False,
        description="Return all BLE devices, not only devices advertising the FTMS service UUID.",
    ),
):
    try:
        devices = await scan_ftms_devices(
            ftms_scanner,
            timeout_sec=timeout_sec,
            adapter=adapter,
            include_all=include_all,
        )
        return {
            "adapter": adapter,
            "timeout_sec": timeout_sec,
            "include_all": include_all,
            "devices": [device.model_dump() for device in devices],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/wifi/status")
def get_wifi_status(
    interface: str = Query(
        "wlan0",
        description="Linux Wi-Fi interface to inspect for RSSI.",
    )
):
    return wifi_status_reader.read(interface=interface).model_dump()


EDGE_SETUP_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FitRace Edge Node Setup</title>
  <style>
    :root {
      --bg: #0b0d10;
      --panel: #15191f;
      --panel-2: #101419;
      --border: #2a313b;
      --text: #f4f7fb;
      --muted: #9aa6b2;
      --accent: #d7ff3f;
      --warning: #f6a524;
      --danger: #ef476f;
      --ok: #34d399;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }

    .shell {
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }

    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
    }

    h1 {
      margin: 0;
      font-size: 28px;
      letter-spacing: 0;
    }

    .sub {
      margin-top: 4px;
      color: var(--muted);
      font-size: 14px;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }

    .language-select {
      width: auto;
      min-width: 150px;
      height: 38px;
      border-radius: 6px;
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--ok);
      box-shadow: 0 0 12px rgba(52, 211, 153, 0.45);
    }

    main {
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 18px;
      margin-top: 20px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px;
    }

    .panel h2 {
      margin: 0 0 14px;
      font-size: 16px;
      letter-spacing: 0;
    }

    .stack {
      display: grid;
      gap: 18px;
    }

    label {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }

    input,
    select {
      width: 100%;
      height: 42px;
      padding: 0 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #090b0e;
      color: var(--text);
      font: inherit;
    }

    .field { margin-bottom: 14px; }

    .toggle {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--muted);
      font-size: 14px;
    }

    .toggle input {
      width: 18px;
      height: 18px;
      accent-color: var(--accent);
    }

    button {
      width: 100%;
      min-height: 44px;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #070807;
      cursor: pointer;
      font-weight: 800;
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.45;
    }

    .status {
      display: grid;
      gap: 10px;
      margin-top: 16px;
    }

    .status-line {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 13px;
    }

    .status-line strong {
      color: var(--text);
      font-weight: 700;
    }

    .wifi-meter {
      display: grid;
      gap: 10px;
    }

    .wifi-score {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel-2);
    }

    .wifi-icon-wrap {
      display: flex;
      align-items: center;
      gap: 14px;
      min-width: 0;
    }

    .wifi-icon {
      width: 86px;
      height: 64px;
      flex: 0 0 auto;
    }

    .wifi-arc,
    .wifi-dot-mark {
      stroke: #5a6470;
      transition: stroke 0.25s ease, fill 0.25s ease;
    }

    .wifi-dot-mark {
      fill: #5a6470;
      stroke: none;
    }

    .wifi-icon.excellent .wifi-arc,
    .wifi-icon.excellent .wifi-arc.active,
    .wifi-icon.excellent .wifi-dot-mark { stroke: #22c55e; fill: #22c55e; }

    .wifi-icon.good .wifi-arc.active,
    .wifi-icon.good .wifi-dot-mark { stroke: #f97316; fill: #f97316; }

    .wifi-icon.fair .wifi-arc.active,
    .wifi-icon.fair .wifi-dot-mark { stroke: var(--warning); fill: var(--warning); }

    .wifi-icon.weak .wifi-arc.active,
    .wifi-icon.weak .wifi-dot-mark,
    .wifi-icon.poor .wifi-arc.active,
    .wifi-icon.poor .wifi-dot-mark { stroke: var(--danger); fill: var(--danger); }

    .wifi-status-text {
      min-width: 0;
    }

    .wifi-level {
      color: var(--text);
      font-size: 24px;
      font-weight: 800;
      line-height: 1.05;
      overflow-wrap: anywhere;
    }

    .wifi-sub {
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }

    .progress {
      height: 10px;
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: #090b0e;
    }

    .progress-fill {
      width: 0%;
      height: 100%;
      background: var(--accent);
      transition: width 0.2s ease;
    }

    .message {
      min-height: 22px;
      color: var(--muted);
      font-size: 13px;
    }

    .message.error { color: var(--danger); }
    .message.ok { color: var(--ok); }

    .device-list {
      display: grid;
      gap: 10px;
    }

    .device {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      padding: 14px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel-2);
    }

    .device-name {
      font-size: 15px;
      font-weight: 800;
    }

    .device-meta {
      margin-top: 4px;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .rssi {
      align-self: start;
      min-width: 62px;
      padding: 6px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--accent);
      text-align: center;
      font-weight: 800;
    }

    .empty {
      padding: 42px 16px;
      border: 1px dashed var(--border);
      border-radius: 8px;
      color: var(--muted);
      text-align: center;
    }

    @media (max-width: 820px) {
      main { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>FitRace Edge Node</h1>
        <div class="sub" data-i18n="edge.subtitle">Local Edge Node setup</div>
      </div>
      <div class="header-actions">
        <select id="language-select" class="language-select" aria-label="System language">
          <option value="en-US">English</option>
          <option value="zh-TW">繁體中文</option>
          <option value="it">Italiano</option>
          <option value="fr">Français</option>
          <option value="de-CH">Deutsch (Schweiz)</option>
          <option value="sv">Svenska</option>
        </select>
        <div class="badge"><span class="dot"></span><span>Local web setup :8001</span></div>
      </div>
    </header>

    <main>
      <div class="stack">
        <section class="panel" aria-labelledby="wifi-title">
          <h2 id="wifi-title" data-i18n="wifi.title">Wi-Fi Signal</h2>
          <div class="wifi-meter" aria-live="polite">
            <div class="wifi-score">
              <div class="wifi-icon-wrap">
                <svg id="wifi-icon" class="wifi-icon" viewBox="0 0 96 72" role="img" aria-label="Wi-Fi signal level">
                  <path class="wifi-arc" data-arc="4" d="M10 24 C31 4 65 4 86 24" fill="none" stroke-width="7" stroke-linecap="round"/>
                  <path class="wifi-arc" data-arc="3" d="M23 37 C37 24 59 24 73 37" fill="none" stroke-width="7" stroke-linecap="round"/>
                  <path class="wifi-arc" data-arc="2" d="M35 50 C42 44 54 44 61 50" fill="none" stroke-width="7" stroke-linecap="round"/>
                  <circle class="wifi-dot-mark" cx="48" cy="62" r="5"/>
                </svg>
                <div class="wifi-status-text">
                  <div class="wifi-level" id="wifi-level">Checking</div>
                  <div class="wifi-sub" id="wifi-sub">Reading Wi-Fi status</div>
                </div>
              </div>
              <div class="badge"><span class="dot" id="wifi-dot"></span><span id="wifi-interface">wlan0</span></div>
            </div>
            <div class="status-line"><span>SSID</span><strong id="wifi-ssid">--</strong></div>
            <div class="message" id="wifi-message">Reading current Wi-Fi status...</div>
          </div>
        </section>

        <section class="panel" aria-labelledby="scan-title">
          <h2 id="scan-title" data-i18n="scan.title">BLE Scan</h2>
          <div class="field">
            <label for="adapter" data-i18n="scan.adapter">Adapter</label>
            <select id="adapter">
              <option value="hci1" selected>hci1 USB dongle</option>
              <option value="hci0">hci0 onboard</option>
            </select>
          </div>
          <div class="field">
            <label for="timeout" data-i18n="scan.timeout">Timeout seconds</label>
            <input id="timeout" type="number" min="1" max="30" value="5">
          </div>
          <div class="field">
            <label class="toggle" for="include-all">
            <span data-i18n="scan.include_all">Include non-FTMS devices</span>
            <input id="include-all" type="checkbox">
            </label>
          </div>
          <button id="scan-btn" type="button" data-i18n="scan.button">Scan FTMS Devices</button>

          <div class="status" aria-live="polite">
            <div class="status-line"><span data-i18n="scan.status">Status</span><strong id="scan-state">Idle</strong></div>
            <div class="status-line"><span data-i18n="scan.elapsed">Elapsed</span><strong id="elapsed">0.0s</strong></div>
            <div class="status-line"><span data-i18n="scan.remaining">Remaining</span><strong id="remaining">0.0s</strong></div>
            <div class="progress" aria-hidden="true"><div class="progress-fill" id="progress-fill"></div></div>
            <div class="message" id="message">Ready to scan nearby FTMS equipment.</div>
          </div>
        </section>
      </div>

      <section class="panel" aria-labelledby="results-title">
        <h2 id="results-title" data-i18n="results.title">Discovered Devices</h2>
        <div id="results" class="device-list">
          <div class="empty" data-i18n="results.empty">No scan results yet.</div>
        </div>
      </section>
    </main>
  </div>

  <script>
    const scanBtn = document.getElementById("scan-btn");
    const languageSelect = document.getElementById("language-select");
    const adapterInput = document.getElementById("adapter");
    const timeoutInput = document.getElementById("timeout");
    const includeAllInput = document.getElementById("include-all");
    const scanState = document.getElementById("scan-state");
    const elapsedLabel = document.getElementById("elapsed");
    const remainingLabel = document.getElementById("remaining");
    const progressFill = document.getElementById("progress-fill");
    const message = document.getElementById("message");
    const results = document.getElementById("results");
    const wifiIcon = document.getElementById("wifi-icon");
    const wifiLevel = document.getElementById("wifi-level");
    const wifiSub = document.getElementById("wifi-sub");
    const wifiDot = document.getElementById("wifi-dot");
    const wifiInterface = document.getElementById("wifi-interface");
    const wifiSsid = document.getElementById("wifi-ssid");
    const wifiMessage = document.getElementById("wifi-message");
    let timer = null;
    let currentLocale = localStorage.getItem("fitrace.edge.locale") || "en-US";
    const dictionaries = {
      "en-US": {
        "edge.subtitle": "Local Edge Node setup",
        "wifi.title": "Wi-Fi Signal",
        "wifi.checking": "Checking",
        "wifi.reading": "Reading Wi-Fi status",
        "wifi.excellent": "Excellent Wi-Fi",
        "wifi.good": "Good Wi-Fi",
        "wifi.fair": "Usable Wi-Fi",
        "wifi.weak": "Weak Wi-Fi",
        "wifi.poor": "Poor Wi-Fi",
        "wifi.disconnected": "Disconnected",
        "wifi.position_hint": "Signal state helps adjust on-site placement",
        "wifi.connect_hint": "Confirm the Edge Node is connected to the AP",
        "scan.title": "BLE Scan",
        "scan.adapter": "Adapter",
        "scan.timeout": "Timeout seconds",
        "scan.include_all": "Include non-FTMS devices",
        "scan.button": "Scan FTMS Devices",
        "scan.status": "Status",
        "scan.elapsed": "Elapsed",
        "scan.remaining": "Remaining",
        "scan.idle": "Idle",
        "scan.scanning": "Scanning",
        "scan.complete": "Complete",
        "scan.failed": "Failed",
        "scan.ready": "Ready to scan nearby FTMS equipment.",
        "scan.in_progress": "Scanning {adapter}. Results update when the scan window completes.",
        "scan.done": "Scan complete. Found {count} device(s).",
        "results.title": "Discovered Devices",
        "results.empty": "No scan results yet.",
        "results.none": "No FTMS devices found. Try include-all mode for troubleshooting.",
        "results.scanning": "Scanning BLE advertisements from the selected adapter...",
        "results.no_available": "No results available."
      }
    };
    dictionaries["zh-TW"] = {
      ...dictionaries["en-US"],
      "edge.subtitle": "Edge Node 本機設定",
      "wifi.title": "Wi-Fi 訊號",
      "wifi.checking": "檢查中",
      "wifi.reading": "讀取 Wi-Fi 狀態中",
      "wifi.excellent": "極佳 Wi-Fi",
      "wifi.good": "良好 Wi-Fi",
      "wifi.fair": "可用 Wi-Fi",
      "wifi.weak": "弱 Wi-Fi",
      "wifi.poor": "不良 Wi-Fi",
      "wifi.disconnected": "未連線",
      "wifi.position_hint": "訊號狀態可用於現場位置調整",
      "wifi.connect_hint": "請確認 Edge Node 已連上 AP",
      "scan.title": "BLE 掃描",
      "scan.button": "掃描 FTMS 設備",
      "scan.ready": "準備掃描附近 FTMS 設備。",
      "results.title": "已發現設備",
      "results.empty": "尚無掃描結果。"
    };
    ["it", "fr", "de-CH", "sv"].forEach((locale) => {
      dictionaries[locale] = { ...dictionaries["en-US"] };
    });

    function t(key, params = {}) {
      let value = (dictionaries[currentLocale] || dictionaries["en-US"])[key] || dictionaries["en-US"][key] || key;
      Object.entries(params).forEach(([name, replacement]) => {
        value = value.replaceAll(`{${name}}`, String(replacement));
      });
      return value;
    }

    function applyTranslations() {
      document.documentElement.lang = currentLocale;
      languageSelect.value = currentLocale;
      document.querySelectorAll("[data-i18n]").forEach((element) => {
        element.innerText = t(element.dataset.i18n);
      });
      scanState.textContent = t("scan.idle");
      if (!message.classList.contains("error") && !message.classList.contains("ok")) {
        message.textContent = t("scan.ready");
      }
    }

    languageSelect.addEventListener("change", () => {
      currentLocale = languageSelect.value;
      localStorage.setItem("fitrace.edge.locale", currentLocale);
      applyTranslations();
    });

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function setMessage(text, type = "") {
      message.textContent = text;
      message.className = `message ${type}`.trim();
    }

    function renderDevices(devices) {
      if (!devices.length) {
        results.innerHTML = `<div class="empty">${escapeHtml(t("results.none"))}</div>`;
        return;
      }

      results.innerHTML = devices.map((device) => {
        const services = (device.matched_services && device.matched_services.length)
          ? device.matched_services
          : device.service_uuids || [];
        return `
          <article class="device">
            <div>
              <div class="device-name">${escapeHtml(device.name || "Unnamed BLE Device")}</div>
              <div class="device-meta">${escapeHtml(device.address)}</div>
              <div class="device-meta">${escapeHtml(services.join(", ") || "No service UUID advertised")}</div>
            </div>
            <div class="rssi">${device.rssi === null || device.rssi === undefined ? "RSSI -" : `${escapeHtml(device.rssi)} dBm`}</div>
          </article>
        `;
      }).join("");
    }

    function renderWifiStatus(status) {
      const connected = Boolean(status.connected);
      const level = status.quality_level || "unknown";
      const labels = {
        excellent: t("wifi.excellent"),
        good: t("wifi.good"),
        fair: t("wifi.fair"),
        weak: t("wifi.weak"),
        poor: t("wifi.poor"),
        unknown: t("wifi.disconnected"),
      };
      const arcCounts = {
        excellent: 4,
        good: 3,
        fair: 2,
        weak: 1,
        poor: 1,
        unknown: 0,
      };
      const activeCount = connected ? (arcCounts[level] ?? 0) : 0;

      wifiLevel.textContent = connected ? (labels[level] || t("wifi.checking")) : t("wifi.disconnected");
      wifiSub.textContent = connected ? t("wifi.position_hint") : t("wifi.connect_hint");
      wifiInterface.textContent = status.interface || "wlan0";
      wifiSsid.textContent = status.ssid || (connected ? "Unknown" : "Not connected");
      wifiMessage.textContent = status.recommendation || "No Wi-Fi status available.";
      wifiMessage.className = `message ${connected ? "" : "error"}`.trim();
      wifiDot.style.background = connected ? "var(--ok)" : "var(--danger)";
      wifiDot.style.boxShadow = connected
        ? "0 0 12px rgba(52, 211, 153, 0.45)"
        : "0 0 12px rgba(239, 71, 111, 0.45)";
      wifiIcon.setAttribute("class", `wifi-icon ${connected ? level : "unknown"}`.trim());
      wifiIcon.querySelectorAll(".wifi-arc").forEach((arc) => {
        const arcLevel = Number(arc.dataset.arc);
        arc.classList.toggle("active", arcLevel <= activeCount);
      });
    }

    async function refreshWifiStatus() {
      try {
        const response = await fetch("/api/wifi/status?interface=wlan0");
        const status = await response.json();
        if (!response.ok) {
          throw new Error(status.detail || "Failed to read Wi-Fi status");
        }
        renderWifiStatus(status);
      } catch (error) {
        renderWifiStatus({
          interface: "wlan0",
          connected: false,
          recommendation: error.message,
        });
      }
    }

    function startProgress(timeoutSec) {
      const started = Date.now();
      clearInterval(timer);
      timer = setInterval(() => {
        const elapsed = Math.max(0, (Date.now() - started) / 1000);
        const remaining = Math.max(0, timeoutSec - elapsed);
        const percent = Math.min(100, (elapsed / timeoutSec) * 100);
        elapsedLabel.textContent = `${elapsed.toFixed(1)}s`;
        remainingLabel.textContent = `${remaining.toFixed(1)}s`;
        progressFill.style.width = `${percent}%`;
      }, 100);
    }

    function stopProgress(done = false) {
      clearInterval(timer);
      timer = null;
      if (done) {
        progressFill.style.width = "100%";
        remainingLabel.textContent = "0.0s";
      }
    }

    scanBtn.addEventListener("click", async () => {
      const adapter = adapterInput.value;
      const timeoutSec = Math.max(1, Math.min(30, Number(timeoutInput.value) || 5));
      const includeAll = includeAllInput.checked;
      const params = new URLSearchParams({
        adapter,
        timeout_sec: String(timeoutSec),
        include_all: String(includeAll),
      });

      scanBtn.disabled = true;
      scanState.textContent = t("scan.scanning");
      elapsedLabel.textContent = "0.0s";
      remainingLabel.textContent = `${timeoutSec.toFixed(1)}s`;
      progressFill.style.width = "0%";
      results.innerHTML = `<div class="empty">${escapeHtml(t("results.scanning"))}</div>`;
      setMessage(t("scan.in_progress", { adapter }));
      startProgress(timeoutSec);

      try {
        const response = await fetch(`/api/ble/scan?${params.toString()}`);
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Scan failed");
        }
        stopProgress(true);
        scanState.textContent = t("scan.complete");
        setMessage(t("scan.done", { count: payload.devices.length }), "ok");
        renderDevices(payload.devices);
      } catch (error) {
        stopProgress(false);
        scanState.textContent = t("scan.failed");
        setMessage(error.message, "error");
        results.innerHTML = `<div class="empty">${escapeHtml(t("results.no_available"))}</div>`;
      } finally {
        scanBtn.disabled = false;
      }
    });

    applyTranslations();
    refreshWifiStatus();
    setInterval(refreshWifiStatus, 5000);
  </script>
</body>
</html>
"""
