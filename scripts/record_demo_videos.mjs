import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("/Users/tunghunglu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const ROOT = process.cwd();
// Language switch: FITRACE_DEMO_LANG=en produces the English cut into
// output/videos/en/ (zh's output/videos/ files are left untouched); the
// default/anything else is the original zh-TW cut. One parameterised
// script - scene order, actions, and target durations are identical for
// both, only the UI locale and the overlay/narration copy differ.
const LANG = process.env.FITRACE_DEMO_LANG === "en" ? "en" : "zh";
const LOCALE = LANG === "en" ? "en-US" : "zh-TW";
const VIDEOS_ROOT = path.join(ROOT, "output/videos");
const OUTPUT_DIR = LANG === "en" ? path.join(VIDEOS_ROOT, "en") : VIDEOS_ROOT;
const VIDEO_SIZE = { width: 1280, height: 720 };
const PORTRAIT_SIZE = { width: 390, height: 844 };
const BASE_URL = "http://127.0.0.1:8010";
const FFMPEG = "/opt/homebrew/bin/ffmpeg";

// Overlay/narration copy per scene and language. zh-TW strings are the
// 字幕 column from DEMO_SCRIPT.md's 自動化錄製分鏡表; en strings are the
// "On-screen overlay" column from its "## English cut (EN)" section, used
// verbatim (S14 has three sequential lines in both languages).
const OVERLAY_TEXT = {
  s01: { zh: "FitRaceStudio — 場館級即時競賽系統", en: "Live racing for studios and events" },
  s02: { zh: "設備自動被發現", en: "Equipment discovered automatically" },
  s03: { zh: "一鍵把設備對應到站位", en: "Map equipment to stations in one click" },
  s04: { zh: "選手用手機自己報名", en: "Athletes register from their phones" },
  s05: { zh: "教練只管比賽，不碰技術設定", en: "Coaches control the race, not the wiring" },
  s06: { zh: "按下開始，大螢幕倒數", en: "Press start, the big screen counts down" },
  s07: { zh: "即時排名 · 即時進度", en: "Real-time ranking and progress" },
  s08: { zh: "成績即時鎖定", en: "Results locked instantly" },
  s09: { zh: "技術維運集中在 System Admin", en: "All maintenance lives in System Admin" },
  s10: { zh: "到場即可換網路", en: "Switch networks on arrival" },
  s11: { zh: "更新只在賽事閒置時允許", en: "Updates only while the race is idle" },
  s12: { zh: "危險操作需要解鎖", en: "Critical actions require unlock" },
  s13: { zh: "一鍵匯出系統報告", en: "One-click system report" },
  s14: {
    zh: ["選手手機報名", "教練一鍵開賽", "技術維運全在瀏覽器"],
    en: ["Athletes register by phone", "Coaches start with one press", "Maintenance runs in the browser"],
  },
};

function overlayText(sceneId) {
  return OVERLAY_TEXT[sceneId][LANG];
}

const demoNodes = [
  { station: 1, nodeId: "fitrace-edge-01-bike-01", equipment: "fan_bike", athlete: "Marcus Lee", team: "Velocity" },
  { station: 2, nodeId: "fitrace-edge-01-bike-02", equipment: "fan_bike", athlete: "Ethan Lin", team: "Apex" },
  { station: 3, nodeId: "fitrace-edge-02-row-01", equipment: "rower", athlete: "Ava Chen", team: "Redline" },
  { station: 4, nodeId: "fitrace-edge-02-ski-01", equipment: "skierg", athlete: "Sofia Wang", team: "NorthFit" },
  { station: 5, nodeId: "fitrace-edge-03-tread-01", equipment: "treadmill", athlete: "Noah Park", team: "Pulse" },
  { station: 6, nodeId: "fitrace-edge-03-bike-01", equipment: "fan_bike", athlete: "Mia Huang", team: "Ignite" },
];

const nodeApiPayload = {
  nodes: [
    {
      edge_node_id: "fitrace-edge-01",
      hostname: "fitrace-edge-01",
      ip: "192.168.0.141",
      status: "online",
      software_version: "0.1.1",
      last_seen_epoch_ms: Date.now(),
      equipment_streams: demoNodes.slice(0, 2).map((node) => ({
        node_id: node.nodeId,
        equipment_id: node.nodeId.split("-").slice(-2).join("-").toUpperCase(),
        equipment_type: node.equipment,
        status: "configured",
        antenna_channel: "BLE-A",
      })),
    },
    {
      edge_node_id: "fitrace-edge-02",
      hostname: "fitrace-edge-02",
      ip: "192.168.0.142",
      status: "online",
      software_version: "0.1.1",
      last_seen_epoch_ms: Date.now() - 1800,
      equipment_streams: demoNodes.slice(2, 4).map((node) => ({
        node_id: node.nodeId,
        equipment_id: node.nodeId.split("-").slice(-2).join("-").toUpperCase(),
        equipment_type: node.equipment,
        status: "configured",
        antenna_channel: "BLE-B",
      })),
    },
    {
      edge_node_id: "fitrace-edge-03",
      hostname: "fitrace-edge-03",
      ip: "192.168.0.143",
      status: "online",
      software_version: "0.1.1",
      last_seen_epoch_ms: Date.now() - 2900,
      equipment_streams: demoNodes.slice(4).map((node) => ({
        node_id: node.nodeId,
        equipment_id: node.nodeId.split("-").slice(-2).join("-").toUpperCase(),
        equipment_type: node.equipment,
        status: "configured",
        antenna_channel: "BLE-C",
      })),
    },
  ],
};

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function startHub() {
  // RaceManager persists station assignments / race config to disk
  // (data/race_settings.json by default) and reloads them on the next
  // process start. Point that - and the finished-race results log - at a
  // scratch path under output/videos/.tmp so each recording run starts
  // from genuinely empty state instead of replaying whatever a previous
  // recording (or the developer's real local hub) left behind. Anchored to
  // VIDEOS_ROOT (not the language-specific OUTPUT_DIR) so the zh and en
  // cuts share one scratch location instead of each growing their own.
  const scratchDir = path.join(VIDEOS_ROOT, ".tmp", "hub-state");
  const child = spawn(
    path.join(ROOT, ".venv/bin/python"),
    ["-m", "scripts.demo_hub"],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        TESTING: "1",
        FITRACE_ENABLE_TEST_TELEMETRY: "1",
        FITRACE_RACE_SETTINGS_PATH: path.join(scratchDir, "race_settings.json"),
        FITRACE_RACE_RESULTS_PATH: path.join(scratchDir, "race_results.jsonl"),
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );

  child.stdout.on("data", (data) => process.stdout.write(`[hub] ${data}`));
  child.stderr.on("data", (data) => process.stderr.write(`[hub] ${data}`));
  return child;
}

async function waitForHub() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE_URL}/health`);
      if (res.ok) return;
    } catch (_) {
      await delay(300);
    }
  }
  throw new Error("Hub did not become ready");
}

async function api(pathname, options = {}) {
  const res = await fetch(`${BASE_URL}${pathname}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  const payload = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new Error(`${pathname} failed: ${res.status} ${text}`);
  }
  return payload;
}

// Seeds baseline race data. `assignStations`/`registerAthletes` default to true
// (matching the original helper's contract); the S1-S6 flow below calls this
// once with both flags off, so the S3/S4 scenes can demonstrate the real
// station-assignment and signup UI flows from a genuinely empty state.
async function seedConfiguredRace({ registerAthletes = true, assignStations = true } = {}) {
  await api("/api/race/reset", { method: "POST" });
  for (const node of demoNodes) {
    await api("/api/test/telemetry", {
      method: "POST",
      body: JSON.stringify({
        node_id: node.nodeId,
        equipment_type: node.equipment,
        distance_m: 0,
        elapsed_time_ms: 0,
        instantaneous_speed_kph: 0,
        power_watts: 0,
        calories: 0,
      }),
    });
    if (assignStations) {
      await api("/api/stations/assign", {
        method: "POST",
        body: JSON.stringify({ station_number: node.station, node_id: node.nodeId }),
      });
    }
  }
  if (registerAthletes) {
    for (const node of demoNodes) {
      await api("/api/race/register", {
        method: "POST",
        body: JSON.stringify({
          station_number: node.station,
          athlete_name: node.athlete,
          team_name: node.team,
        }),
      });
    }
  }
  await api("/api/race/configure", {
    method: "POST",
    body: JSON.stringify({ race_type: "distance", target_value: 500, duration_sec: 0 }),
  });
}

// Distance curves (meters) at telemetry steps 0..7 for a 500m distance race.
// Ava Chen (index 2) leads for most of the race; Marcus Lee (index 0) only
// surges into the lead in the final two frames, so the leaderboard both
// swaps ranks mid-race and produces a late, visible breakaway finish.
const raceCurves = [
  [0, 40, 78, 120, 175, 240, 340, 500], // Marcus Lee - late surge, wins
  [0, 50, 95, 150, 205, 260, 330, 460], // Ethan Lin
  [0, 55, 110, 175, 245, 310, 370, 470], // Ava Chen - early/mid leader
  [0, 45, 85, 135, 190, 250, 320, 440], // Sofia Wang
  [0, 35, 70, 115, 165, 220, 290, 410], // Noah Park
  [0, 30, 60, 100, 145, 195, 260, 380], // Mia Huang
];

async function sendTelemetryFrame(step, totalSteps) {
  const idx = Math.min(step, raceCurves[0].length - 1);
  const elapsed = Math.round((step / Math.max(totalSteps, 1)) * 18500);
  await Promise.all(demoNodes.map((node, i) => {
    const distance = raceCurves[i][idx];
    const prev = raceCurves[i][Math.max(0, idx - 1)];
    const speed = Math.max(8, Math.round(((distance - prev) / 500) * 180));
    return api("/api/test/telemetry", {
      method: "POST",
      body: JSON.stringify({
        node_id: node.nodeId,
        equipment_type: node.equipment,
        distance_m: distance,
        elapsed_time_ms: elapsed,
        instantaneous_speed_kph: speed,
        power_watts: 180 + i * 18 + step * 9,
        calories: Math.round(distance / 12),
      }),
    });
  }));
}

async function runRaceTelemetry({ stepDelayMs = 1900, steps = 7, skipStart = false } = {}) {
  if (!skipStart) {
    await api("/api/race/start", { method: "POST" });
    await delay(800);
  }
  for (let step = 1; step <= steps; step += 1) {
    await sendTelemetryFrame(step, steps);
    await delay(stepDelayMs);
  }
}

async function preparePage(page) {
  await page.route("**/api/nodes**", async (route) => {
    const payload = {
      nodes: nodeApiPayload.nodes.map((node) => ({
        ...node,
        last_seen_epoch_ms: Date.now() - (node.edge_node_id.endsWith("01") ? 900 : 2100),
      })),
    };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
  // addInitScript functions are serialized and run in the browser context,
  // so LOCALE must be passed as an argument rather than captured by
  // closure (a bare closure reference would be a ReferenceError there).
  await page.addInitScript((locale) => {
    localStorage.setItem("fitrace.adminToken", "demo");
    localStorage.setItem("fitrace.adminPassword", "demo");
    localStorage.setItem("fitrace.locale", locale);
  }, LOCALE);
  // The recording hub has no wlan0 and no real update server. Stub both on
  // *every* recorded page (not just S10/S11) so the systemAdmin header
  // strip ("Wi-Fi: fitRace26 -46 dBm" / "Updates: ...") shows the same
  // state consistently across every scene that visits systemAdmin, instead
  // of contradicting itself scene-to-scene (mocked here, disconnected
  // there). Paths/fields come directly from hub_server/static/
  // systemAdmin.html and fitrace_common/wifi_status.py (WifiStatus model).
  await mockWifiEndpoints(page);
  await mockUpdateEndpoints(page);
}

async function mockWifiEndpoints(page) {
  await page.route("**/api/wifi/status**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        interface: "wlan0",
        connected: true,
        ssid: "fitRace26",
        rssi_dbm: -46,
        quality_percent: 82,
        quality_level: "excellent",
        recommendation: "Signal is strong.",
        ip: "192.168.50.176",
      }),
    });
  });
  await page.route("**/api/wifi/networks**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        interface: "wlan0",
        networks: [
          { ssid: "fitRace26", signal: 88, secured: true, saved: true, active: true },
          { ssid: "Studio-Guest", signal: 64, secured: true, saved: false, active: false },
          { ssid: "Downstairs-5G", signal: 41, secured: true, saved: false, active: false },
        ],
      }),
    });
  });
}

async function mockUpdateEndpoints(page) {
  const payload = {
    state: "available",
    current_version: "0.2.0",
    latest_hub_version: "0.3.0",
    latest_edge_version: "0.3.0",
    checked_at_epoch_ms: Date.now(),
    signature_verified: true,
  };
  await page.route("**/api/updates/status**", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });
  await page.route("**/api/updates/check", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
  });
}

async function addOverlay(page, text, align = "left") {
  await page.evaluate(({ text, align }) => {
    let el = document.getElementById("demo-overlay");
    if (!el) {
      el = document.createElement("div");
      el.id = "demo-overlay";
      document.body.appendChild(el);
      const style = document.createElement("style");
      style.textContent = `
        #demo-overlay {
          position: fixed;
          z-index: 2147483647;
          left: 36px;
          right: auto;
          bottom: 34px;
          max-width: min(520px, calc(100vw - 48px));
          padding: 16px 22px;
          border: 1px solid rgba(226,255,59,.75);
          border-radius: 4px;
          background: rgba(9,9,11,.86);
          color: #f7f7f8;
          font: 800 30px/1.25 "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", Outfit, Inter, system-ui, sans-serif;
          letter-spacing: 0;
          box-shadow: 0 0 28px rgba(226,255,59,.18);
          backdrop-filter: blur(8px);
          opacity: 0;
          transform: translateY(10px);
          transition: opacity .22s ease, transform .22s ease;
          pointer-events: none;
        }
        #demo-overlay.show {
          opacity: 1;
          transform: translateY(0);
        }
        #demo-overlay.right {
          left: auto;
          right: 36px;
        }
        @media (max-width: 500px) {
          #demo-overlay {
            left: 16px;
            bottom: 20px;
            padding: 12px 16px;
            font-size: 20px;
          }
          #demo-overlay.right {
            right: 16px;
          }
        }
      `;
      document.head.appendChild(style);
    }
    el.textContent = text;
    el.className = align === "right" ? "right" : "";
    requestAnimationFrame(() => el.classList.add("show"));
  }, { text, align });
}

async function hideOverlay(page) {
  await page.evaluate(() => {
    document.getElementById("demo-overlay")?.classList.remove("show");
  });
}

async function newRecordedPage(browser, fileName, viewport = VIDEO_SIZE) {
  const tempDir = path.join(OUTPUT_DIR, ".tmp", fileName);
  await rm(tempDir, { recursive: true, force: true });
  await mkdir(tempDir, { recursive: true });
  const context = await browser.newContext({
    viewport,
    recordVideo: {
      dir: tempDir,
      size: viewport,
    },
  });
  const page = await context.newPage();
  await preparePage(page);
  return {
    page,
    context,
    async close() {
      await context.close();
      const files = await import("node:fs/promises").then((fs) => fs.readdir(tempDir));
      const webm = files.find((file) => file.endsWith(".webm"));
      if (!webm) throw new Error(`No video file recorded for ${fileName}`);
      const dest = path.join(OUTPUT_DIR, fileName);
      await rm(dest, { force: true });
      await rename(path.join(tempDir, webm), dest);
      await rm(path.dirname(tempDir), { recursive: true, force: true });
      return dest;
    },
  };
}

async function waitForZhText(page, selector, timeoutMs = 8000) {
  await page.waitForFunction(
    (sel) => (document.querySelector(sel)?.textContent || "").trim().length > 0,
    selector,
    { timeout: timeoutMs },
  ).catch(() => {});
}

// ---------------------------------------------------------------------------
// S1 - Intro: Dashboard in its empty "waiting for setup" state, slow zoom.
// ---------------------------------------------------------------------------
async function sceneS01(browser) {
  const rec = await newRecordedPage(browser, "s01_intro.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await waitForZhText(page, "#race-stage-kicker");
  await page.evaluate(() => {
    document.body.style.transition = "transform 11s ease-out";
    document.body.style.transformOrigin = "center center";
    requestAnimationFrame(() => {
      document.body.style.transform = "scale(1.06)";
    });
  });
  await addOverlay(page, overlayText("s01"));
  await delay(10600);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S2 - Edge Nodes online: systemAdmin#edge, scroll through node cards.
// ---------------------------------------------------------------------------
async function sceneS02(browser) {
  const rec = await newRecordedPage(browser, "s02_edge_nodes.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/systemAdmin#edge`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.getElementById("edge-list")?.children.length || 0) > 0,
    { timeout: 8000 },
  ).catch(() => {});
  // The unassigned-streams dialog auto-opens on first load (nothing is
  // assigned yet - that happens in S3). Not part of this scene, close it.
  await page.evaluate(() => {
    if (typeof closeUnassignedDialog === "function") closeUnassignedDialog();
  });
  await addOverlay(page, overlayText("s02"), "right");
  await delay(2600);
  await page.mouse.wheel(0, 320);
  await delay(2600);
  await page.mouse.wheel(0, 320);
  await delay(2600);
  const badge = page.locator("#edge-list .dot.online").first();
  if (await badge.count()) await badge.hover();
  await delay(3800);
  await page.mouse.wheel(0, -640);
  await delay(2600);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S3 - Station assignment: assign one manually, then "assign all" the rest.
// ---------------------------------------------------------------------------
async function sceneS03(browser) {
  const rec = await newRecordedPage(browser, "s03_station_assignment.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/systemAdmin#stations`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.getElementById("node-select")?.options.length || 0) > 0,
    { timeout: 8000 },
  ).catch(() => {});
  // The unassigned-streams dialog auto-opens on first load (growth is
  // measured from an empty starting set). Close it so we can demonstrate
  // the single-assign flow first, then reopen it further below for the
  // "assign all" step.
  await page.evaluate(() => {
    if (typeof closeUnassignedDialog === "function") closeUnassignedDialog();
  });
  await addOverlay(page, overlayText("s03"), "right");
  await delay(2200);

  await page.locator("#station-number").fill("1");
  await page.locator("#node-select").selectOption(demoNodes[0].nodeId);
  await delay(1300);
  await page.locator('.assign-bar button.primary').click();
  await page.waitForFunction(
    () => (document.getElementById("station-list")?.innerText || "").includes("1"),
    { timeout: 6000 },
  ).catch(() => {});
  await delay(2200);

  await page.evaluate(() => {
    if (typeof openUnassignedDialog === "function") openUnassignedDialog();
  });
  await delay(2000);
  const assignAllBtn = page.locator("#unassigned-dialog-assign-btn");
  await assignAllBtn.hover();
  await delay(1400);
  await assignAllBtn.click();
  await page.waitForFunction(
    () => !document.getElementById("unassigned-dialog")?.classList.contains("show"),
    { timeout: 8000 },
  ).catch(() => {});
  await delay(2200);

  const copyBtn = page.locator('button[onclick="copySignupLink()"]');
  await copyBtn.hover();
  await delay(2400);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S4 - Athlete signup on a portrait "phone" viewport.
// ---------------------------------------------------------------------------
async function sceneS04(browser) {
  const rec = await newRecordedPage(browser, "s04_signup.webm", PORTRAIT_SIZE);
  const { page } = rec;
  await page.goto(`${BASE_URL}/static/signup.html?station=3`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => !(document.getElementById("station-lbl")?.innerText || "").includes("Not selected"),
    { timeout: 8000 },
  ).catch(() => {});
  await addOverlay(page, overlayText("s04"));
  await delay(2200);
  await page.locator('[data-type="female"]').click();
  await delay(1800);
  await page.fill("#athlete-name", "Ava Chen");
  await delay(1200);
  await page.fill("#team-name", "Redline");
  await delay(2000);
  await page.click("#submit-btn");
  await page.waitForFunction(
    () => document.getElementById("success-msg")?.classList.contains("show") ||
      getComputedStyle(document.getElementById("success-msg")).display !== "none",
    { timeout: 8000 },
  ).catch(() => {});
  await delay(11500);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S5 - Coach sets race rules on gameAdmin, then saves.
// ---------------------------------------------------------------------------
async function sceneS05(browser) {
  const rec = await newRecordedPage(browser, "s05_race_rules.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, overlayText("s05"));
  await delay(1600);

  for (const mode of ["calories", "time", "max_power", "distance"]) {
    await page.selectOption("#race-type", mode);
    await delay(750);
  }
  await delay(700);
  await page.fill("#race-target", "500");
  await delay(1500);
  await page.selectOption("#competition-mode", "individual");
  await delay(1500);

  for (const mode of ["race_track", "team_battle", "sprint_board", "classic"]) {
    await page.selectOption("#leaderboard-display-mode", mode);
    await delay(750);
  }
  await delay(700);
  await page.selectOption("#start-sound-enabled", "true");
  await delay(1500);

  await page.click("#btn-save-race");
  await page.waitForFunction(
    () => !document.getElementById("rules-dirty-badge")?.classList.contains("show"),
    { timeout: 8000 },
  ).catch(() => {});
  await delay(2600);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S6 - Start: coach presses Start Race on gameAdmin, cut to Dashboard.
// ---------------------------------------------------------------------------
async function sceneS06(browser) {
  const rec = await newRecordedPage(browser, "s06_start_race.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => document.getElementById("summary-readiness")?.innerText?.trim().length > 0,
    { timeout: 8000 },
  ).catch(() => {});
  await addOverlay(page, overlayText("s06"));
  await delay(2400);

  await page.click("#btn-start-race");
  await delay(700);

  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.getElementById("race-stage-kicker")?.innerText || "").includes("賽事直播") ||
      (document.getElementById("race-stage-kicker")?.innerText || "").includes("倒數"),
    { timeout: 7000 },
  ).catch(() => {});
  await delay(3200);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S7 - Live race: telemetry-driven progress bars and a rank swap.
// ---------------------------------------------------------------------------
async function sceneS07(browser) {
  const rec = await newRecordedPage(browser, "s07_live_race.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, overlayText("s07"));
  await delay(1400);
  // S6's countdown-start already transitions the race to RUNNING once the
  // 3,2,1,Go countdown finishes server-side. Wait for that (it should
  // already be done by now); fall back to a direct start if it somehow
  // isn't, so this scene is resilient on its own.
  const raceState = await api("/api/race/state").catch(() => ({}));
  await runRaceTelemetry({ stepDelayMs: 3600, steps: 7, skipStart: raceState.state === "RUNNING" });
  await delay(2600);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S8 - Finish: podium reveal live on the dashboard, then the results page.
// ---------------------------------------------------------------------------
async function sceneS08(browser) {
  const rec = await newRecordedPage(browser, "s08_finish.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, overlayText("s08"));
  await delay(1800);
  await api("/api/race/stop", { method: "POST" });
  await page.waitForFunction(
    () => (document.getElementById("race-stage-kicker")?.innerText || "").length > 0 &&
      document.body.innerText.includes("結果") || document.querySelector(".podium-overlay.show"),
    { timeout: 9000 },
  ).catch(() => {});
  await delay(7500);
  await page.goto(`${BASE_URL}/static/results.html`, { waitUntil: "networkidle" });
  await delay(7500);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S9 - System Admin overview: edge / wifi / updates summary strip.
// ---------------------------------------------------------------------------
async function sceneS09(browser) {
  const rec = await newRecordedPage(browser, "s09_admin_overview.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/systemAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, overlayText("s09"), "right");
  const strip = page.locator(".strip-item").first();
  if (await strip.count()) await strip.hover({ timeout: 4000 }).catch(() => {});
  await delay(4200);
  const strips = page.locator(".strip-item");
  const stripCount = await strips.count();
  if (stripCount > 1) await strips.nth(1).hover({ timeout: 4000 }).catch(() => {});
  await delay(3800);
  if (stripCount > 2) await strips.nth(2).hover({ timeout: 4000 }).catch(() => {});
  await delay(2200);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S10 - Network: mocked Wi-Fi status, expand the network picker (no switch).
// ---------------------------------------------------------------------------
async function sceneS10(browser) {
  const rec = await newRecordedPage(browser, "s10_network.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/systemAdmin#network`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.getElementById("wifi-ssid")?.innerText || "--") !== "--",
    { timeout: 8000 },
  ).catch(() => {});
  await addOverlay(page, overlayText("s10"), "right");
  await delay(3000);
  await page.click("#btn-wifi-choose");
  await page.waitForFunction(
    () => (document.getElementById("wifi-picker-body")?.innerText || "").length > 0,
    { timeout: 6000 },
  ).catch(() => {});
  await delay(4600);
  await page.click(".modal-close");
  await delay(2600);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S11 - Software update: mocked Check Now, hover the gated install actions.
// ---------------------------------------------------------------------------
async function sceneS11(browser) {
  const rec = await newRecordedPage(browser, "s11_software_update.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/systemAdmin#software`, { waitUntil: "networkidle" });
  await addOverlay(page, overlayText("s11"), "right");
  await delay(2200);
  await page.click('button[onclick="checkUpdates()"]');
  await page.waitForFunction(
    () => !document.getElementById("update-actions")?.hidden,
    { timeout: 6000 },
  ).catch(() => {});
  await delay(3400);
  await page.locator('button[onclick="downloadUpdates()"]').hover({ timeout: 4000 }).catch(() => {});
  await delay(2200);
  await page.locator('button[onclick="installHubUpdate()"]').hover({ timeout: 4000 }).catch(() => {});
  await delay(2200);
  await page.locator('button[onclick="applyHubUpdate()"]').hover({ timeout: 4000 }).catch(() => {});
  await delay(2400);
  // The app's own success message ("Update check complete.") is hardcoded
  // English in systemAdmin.html, which we cannot edit. Clear it before the
  // shot ends rather than leave English text on screen in a zh-TW demo.
  await page.evaluate(() => {
    const el = document.getElementById("update-message");
    if (el) el.textContent = "";
  });
  await delay(1200);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S12 - Power controls: Reboot -> confirm dialog -> CANCEL, never confirm.
// ---------------------------------------------------------------------------
// Note: Reboot Hub's confirmation is a native window.confirm() (see
// hub_server/static/systemAdmin.html:2310) - a browser-level dialog outside
// the page surface, which Playwright's recordVideo cannot capture (it
// records the page only). Clicking that button would produce a dead static
// frame on camera and is also the one action in this whole pipeline that
// could ever reach a real power API. So this scene never clicks any power
// button at all - it only pans/hovers them, then demonstrates the
// Maintenance Unlock access-code flow, which does render on screen.
async function sceneS12(browser) {
  const rec = await newRecordedPage(browser, "s12_power_controls.webm");
  const { page } = rec;

  await page.goto(`${BASE_URL}/systemAdmin#power`, { waitUntil: "networkidle" });
  await addOverlay(page, overlayText("s12"), "right");
  await delay(1800);
  await page.locator("#power-lock").hover({ timeout: 4000 }).catch(() => {});
  await delay(1400);
  for (const id of ["#btn-power-restart", "#btn-power-reboot", "#btn-power-shutdown", "#btn-power-shutdown-system"]) {
    await page.locator(id).hover({ timeout: 4000 }).catch(() => {});
    await delay(1200);
  }
  await page.locator(".unlock").click();
  await page.waitForFunction(
    () => document.getElementById("login-modal")?.classList.contains("show") ||
      getComputedStyle(document.getElementById("login-modal")).display !== "none",
    { timeout: 5000 },
  ).catch(() => {});
  await delay(1600);
  await page.locator("#admin-token").click();
  await delay(1400);
  await page.click('button[onclick="closeLogin()"]');
  await delay(2200);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S13 - Support: copy the system report.
// ---------------------------------------------------------------------------
async function sceneS13(browser) {
  const rec = await newRecordedPage(browser, "s13_support.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/systemAdmin#support`, { waitUntil: "networkidle" });
  await addOverlay(page, overlayText("s13"), "right");
  await delay(1600);
  await page.click('button[onclick="copySupportReport()"]');
  await delay(2600);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// S14 - Outro: dashboard record wall / podium with the closing captions.
// ---------------------------------------------------------------------------
async function sceneS14(browser) {
  const rec = await newRecordedPage(browser, "s14_outro.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  const [line1, line2, line3] = overlayText("s14");
  await addOverlay(page, line1);
  await delay(3600);
  await addOverlay(page, line2);
  await delay(3600);
  await addOverlay(page, line3);
  await delay(3600);
  await hideOverlay(page);
  await delay(1200);
  return rec.close();
}

async function concatVideos(outputs) {
  const listFile = path.join(OUTPUT_DIR, "concat_list.txt");
  const normalizedDir = path.join(OUTPUT_DIR, ".normalized");
  await rm(normalizedDir, { recursive: true, force: true });
  await mkdir(normalizedDir, { recursive: true });

  // Scenes are a mix of 1280x720 landscape and S4's 390x844 portrait clip.
  // Normalize *every* clip (not just S4) through the same
  // scale-to-fit-inside-1280x720-then-letterbox filter, plus a common
  // fps/pix_fmt/codec, so the concat step works from a uniform stream
  // instead of assuming the landscape clips already match exactly.
  const normalized = [];
  for (const file of outputs) {
    const dest = path.join(normalizedDir, `${path.basename(file, path.extname(file))}.mp4`);
    await runFfmpeg([
      "-y",
      "-i", file,
      "-vf",
      "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p",
      "-c:v", "libx264",
      "-preset", "veryfast",
      "-crf", "20",
      "-an",
      dest,
    ]);
    normalized.push(dest);
  }

  const { writeFile } = await import("node:fs/promises");
  const listContent = normalized.map((file) => `file '${file.replace(/'/g, "'\\''")}'`).join("\n");
  await writeFile(listFile, listContent, "utf8");

  const finalOutput = path.join(OUTPUT_DIR, "demo_full_4min.mp4");
  await rm(finalOutput, { force: true });
  // All inputs now share codec/fps/pix_fmt after the normalization pass
  // above, so a plain stream copy is enough to concatenate them.
  await runFfmpeg([
    "-y",
    "-f", "concat",
    "-safe", "0",
    "-i", listFile,
    "-c", "copy",
    finalOutput,
  ]);

  const finalStat = await stat(finalOutput).catch(() => null);
  if (!finalStat || finalStat.size === 0) {
    throw new Error(`ffmpeg concat did not produce ${finalOutput}`);
  }

  await rm(normalizedDir, { recursive: true, force: true });
  await rm(listFile, { force: true });
  return finalOutput;
}

function runFfmpeg(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(FFMPEG, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`ffmpeg ${args.join(" ")} exited ${code}\n${stderr.slice(-2000)}`));
    });
  });
}

async function main() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  // Guarantee a genuinely fresh hub-state scratch dir every run (see
  // startHub()'s FITRACE_RACE_SETTINGS_PATH/FITRACE_RACE_RESULTS_PATH).
  await rm(path.join(VIDEOS_ROOT, ".tmp", "hub-state"), { recursive: true, force: true });
  const hub = startHub();
  let browser;
  try {
    await waitForHub();
    // ponytail: FITRACE_HEADED=1 開真 Chrome 視窗在螢幕上跑,方便肉眼確認分鏡;
    // 影片一律由 Playwright 內部錄製,和有沒有顯示視窗無關。
    browser = await chromium.launch(
      process.env.FITRACE_HEADED
        ? { headless: false, channel: "chrome", slowMo: 120 }
        : { headless: true }
    );

    // Bootstrap: zero-out telemetry for all six demo nodes (so S2's Edge
    // Nodes panel shows fresh "Connected" streams) without assigning
    // stations or registering athletes yet - S3/S4 perform those live.
    await seedConfiguredRace({ registerAthletes: false, assignStations: false });

    const outputs = [];
    outputs.push(await sceneS01(browser));
    outputs.push(await sceneS02(browser));
    outputs.push(await sceneS03(browser));
    outputs.push(await sceneS04(browser));

    // Off-camera: fill in the remaining five athletes that S4 didn't
    // register interactively, so S5's readiness checks pass.
    for (const node of demoNodes.slice(1)) {
      await api("/api/race/register", {
        method: "POST",
        body: JSON.stringify({
          station_number: node.station,
          athlete_name: node.athlete,
          team_name: node.team,
        }),
      });
    }

    outputs.push(await sceneS05(browser));
    outputs.push(await sceneS06(browser));
    outputs.push(await sceneS07(browser));
    outputs.push(await sceneS08(browser));
    outputs.push(await sceneS09(browser));
    outputs.push(await sceneS10(browser));
    outputs.push(await sceneS11(browser));
    outputs.push(await sceneS12(browser));
    outputs.push(await sceneS13(browser));
    outputs.push(await sceneS14(browser));

    for (const file of outputs) {
      const info = await stat(file);
      console.log(`${path.relative(ROOT, file)} ${(info.size / 1024 / 1024).toFixed(2)} MB`);
    }

    const finalVideo = await concatVideos(outputs);
    const info = await stat(finalVideo);
    console.log(`${path.relative(ROOT, finalVideo)} ${(info.size / 1024 / 1024).toFixed(2)} MB`);
  } finally {
    if (browser) await browser.close();
    hub.kill("SIGINT");
    await delay(600);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
