import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("/Users/tunghunglu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const ROOT = process.cwd();
const OUTPUT_DIR = path.join(ROOT, "output/videos");
const VIDEO_SIZE = { width: 1280, height: 720 };
const BASE_URL = "http://127.0.0.1:8010";

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
  const child = spawn(
    path.join(ROOT, ".venv/bin/python"),
    ["-m", "scripts.demo_hub"],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        TESTING: "1",
        FITRACE_ENABLE_TEST_TELEMETRY: "1",
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

async function seedConfiguredRace({ registerAthletes = true } = {}) {
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
    await api("/api/stations/assign", {
      method: "POST",
      body: JSON.stringify({ station_number: node.station, node_id: node.nodeId }),
    });
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

async function sendTelemetryFrame(step, totalSteps) {
  const raceCurves = [
    [0, 34, 72, 126, 198, 285, 390, 500],
    [0, 42, 88, 144, 214, 302, 402, 500],
    [0, 28, 82, 168, 238, 330, 432, 500],
    [0, 36, 70, 118, 190, 275, 372, 492],
    [0, 30, 68, 132, 206, 286, 360, 456],
    [0, 24, 62, 118, 188, 262, 342, 432],
  ];
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

async function runRaceTelemetry({ stepDelayMs = 1900, steps = 7 } = {}) {
  await api("/api/race/start", { method: "POST" });
  await delay(800);
  for (let step = 1; step <= steps; step += 1) {
    await sendTelemetryFrame(step, steps);
    await delay(stepDelayMs);
  }
}

async function waitForLiveLeaderboard(page) {
  await page.evaluate(() => {
    if (typeof fetchState === "function") fetchState();
  });
  await page.waitForFunction(() => {
    const text = document.querySelector("#leaderboard-container")?.innerText || "";
    return (
      /AVA CHEN|ETHAN LIN|MARCUS LEE/.test(text) &&
      !text.includes("NOT CONFIGURED") &&
      /[1-9][0-9]*\s*DISTANCE/.test(text.replace(/\n/g, " "))
    );
  }, { timeout: 8000 });
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
  await page.addInitScript(() => {
    localStorage.setItem("fitrace.adminToken", "demo");
    localStorage.setItem("fitrace.adminPassword", "demo");
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
          max-width: 520px;
          padding: 16px 22px;
          border: 1px solid rgba(226,255,59,.75);
          border-radius: 4px;
          background: rgba(9,9,11,.86);
          color: #f7f7f8;
          font: 800 30px/1.12 Outfit, Inter, system-ui, sans-serif;
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

async function recordOverview(browser) {
  await seedConfiguredRace({ registerAthletes: true });
  const rec = await newRecordedPage(browser, "01_overview.webm");
  const { page } = rec;

  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, "Live cardio racing for studios and events");
  await delay(4600);

  await page.goto(`${BASE_URL}/static/signup.html?station=1`, { waitUntil: "networkidle" });
  await addOverlay(page, "Athletes register from their phones");
  await page.fill("#athlete-name", "Kai Morgan");
  await page.fill("#team-name", "Demo");
  await delay(2200);

  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, "Coaches control the race from Game Admin", "right");
  await page.selectOption("#race-type", "distance");
  await page.fill("#race-target", "500");
  await delay(2800);

  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, "Real-time ranking, progress, and results");
  const race = runRaceTelemetry({ stepDelayMs: 1700, steps: 7 });
  await delay(3600);
  await waitForLiveLeaderboard(page);
  await race;
  await delay(1800);

  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, "Multiple challenge formats", "right");
  for (const mode of ["time", "calories", "max_power", "distance"]) {
    await page.selectOption("#race-type", mode);
    await delay(650);
  }
  await delay(900);

  await page.goto(`${BASE_URL}/systemAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, "Technical setup stays in System Admin");
  await delay(4600);
  await hideOverlay(page);
  await delay(400);
  return rec.close();
}

async function recordSignup(browser) {
  await seedConfiguredRace({ registerAthletes: false });
  const rec = await newRecordedPage(browser, "02_signup.webm", { width: 390, height: 844 });
  const { page } = rec;
  await page.goto(`${BASE_URL}/static/signup.html?station=3`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => !document.querySelector("#station-lbl")?.innerText.includes("NOT SELECTED"));
  await addOverlay(page, "Register by station");
  await delay(1600);
  await page.locator('[data-type="female"]').click();
  await delay(900);
  await page.fill("#athlete-name", "Ava Chen");
  await delay(450);
  await page.fill("#team-name", "Redline");
  await delay(900);
  await addOverlay(page, "Submit from any phone");
  await page.click("#submit-btn");
  await delay(2600);
  await hideOverlay(page);
  await delay(500);
  return rec.close();
}

async function recordGameAdmin(browser) {
  await seedConfiguredRace({ registerAthletes: true });
  const rec = await newRecordedPage(browser, "03_game_admin_race.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, "Coach race control");
  await delay(1600);
  await page.selectOption("#race-type", "distance");
  await page.fill("#race-target", "500");
  await delay(1200);
  await addOverlay(page, "Distance Challenge demo", "right");
  await delay(1200);
  await page.click("#btn-configure");
  await delay(1000);
  await page.click("#btn-start");
  await delay(1200);
  await addOverlay(page, "Race starts from Game Admin");
  await delay(2600);
  await hideOverlay(page);
  await delay(500);
  return rec.close();
}

async function recordSystemAdmin(browser) {
  await seedConfiguredRace({ registerAthletes: true });
  const rec = await newRecordedPage(browser, "04_system_admin_nodes.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/systemAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, "Edge Nodes stay visible");
  await delay(2600);
  await page.locator("#station-number").fill("6");
  await delay(500);
  await page.locator("#node-select").selectOption(demoNodes[5].nodeId);
  await addOverlay(page, "Station setup in System Admin", "right");
  await delay(2200);
  await page.mouse.wheel(0, 600);
  await delay(1500);
  await addOverlay(page, "Updates and power controls");
  await delay(2600);
  await hideOverlay(page);
  await delay(500);
  return rec.close();
}

async function recordLiveRace(browser) {
  await seedConfiguredRace({ registerAthletes: true });
  const rec = await newRecordedPage(browser, "05_live_race.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, "20-second live race");
  await delay(1300);
  const race = runRaceTelemetry({ stepDelayMs: 1650, steps: 7 });
  await delay(3600);
  await waitForLiveLeaderboard(page);
  await race;
  await addOverlay(page, "Final ranking updates instantly", "right");
  await delay(2600);
  await hideOverlay(page);
  await delay(500);
  return rec.close();
}

async function recordFullSystemWorkflow(browser) {
  await seedConfiguredRace({ registerAthletes: false });
  const rec = await newRecordedPage(browser, "13_full_system_workflow.webm");
  const { page } = rec;
  const workflowTeams = ["Velocity", "Velocity", "Apex", "Apex", "Pulse", "Pulse"];

  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, "1. Race setup: safety checks keep Start Race locked", "right");
  await delay(3600);

  await page.goto(`${BASE_URL}/static/signup.html?station=1`, { waitUntil: "networkidle" });
  await addOverlay(page, "2. Athlete registration from a station device");
  await page.locator('[data-type="male"]').click();
  await page.fill("#athlete-name", demoNodes[0].athlete);
  await page.fill("#team-name", workflowTeams[0]);
  await delay(1400);
  await page.click("#submit-btn");
  await delay(2200);

  for (const [index, node] of demoNodes.slice(1).entries()) {
    await api("/api/race/register", {
      method: "POST",
      body: JSON.stringify({
        station_number: node.station,
        athlete_name: node.athlete,
        team_name: workflowTeams[index + 1],
      }),
    });
  }

  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await page.selectOption("#competition-mode", "team");
  await page.selectOption("#team-scoring-policy", "total");
  await page.selectOption("#team-completion-policy", "all_members");
  await page.selectOption("#leaderboard-display-mode", "team_battle");
  await page.fill("#race-target", "500");
  await page.click("#btn-configure");
  await api("/api/race/configure", {
    method: "POST",
    body: JSON.stringify({
      race_type: "distance",
      competition_mode: "team",
      team_scoring_policy: "total",
      team_completion_policy: "all_members",
      target_value: 500,
      duration_sec: 0,
    }),
  });
  await api("/api/leaderboard/display", {
    method: "POST",
    body: JSON.stringify({ mode: "team_battle" }),
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForFunction(() => document.querySelector("#summary-readiness")?.innerText.includes("READY"));
  await addOverlay(page, "3. Team Race: Team Total + Everyone Finishes", "right");
  await delay(4200);

  await page.click("#btn-start");
  await delay(700);
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, "4. Dashboard receives the 3, 2, 1, Go start signal");
  await page.waitForFunction(() => document.body.innerText.includes("LIVE RACE"), { timeout: 9000 });
  await delay(1500);

  await addOverlay(page, "5. Team Battle updates live from every station", "right");
  for (let step = 1; step <= 7; step += 1) {
    await sendTelemetryFrame(step, 7);
    await delay(1150);
  }
  await api("/api/race/stop", { method: "POST" });
  await delay(1000);
  await addOverlay(page, "6. Results lock when the race is finished", "right");
  await delay(3600);

  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, "7. Choose distance, calories, time, or max power", "right");
  for (const mode of ["distance", "calories", "time", "max_power"]) {
    await page.selectOption("#race-type", mode);
    await delay(800);
  }
  await addOverlay(page, "8. Choose Classic, Race Track, Team Battle, or Sprint Board", "right");
  for (const mode of ["classic", "race_track", "team_battle", "sprint_board"]) {
    await page.selectOption("#leaderboard-display-mode", mode);
    await delay(850);
  }
  await hideOverlay(page);
  await delay(500);
  return rec.close();
}

async function main() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  const hub = startHub();
  let browser;
  try {
    await waitForHub();
    browser = await chromium.launch({ headless: true });
    const outputs = [];
    if (!process.env.FITRACE_RECORD_FULL_ONLY) {
      outputs.push(await recordOverview(browser));
      outputs.push(await recordSignup(browser));
      outputs.push(await recordGameAdmin(browser));
      outputs.push(await recordSystemAdmin(browser));
      outputs.push(await recordLiveRace(browser));
    }
    outputs.push(await recordFullSystemWorkflow(browser));

    for (const file of outputs) {
      const info = await stat(file);
      console.log(`${path.relative(ROOT, file)} ${(info.size / 1024 / 1024).toFixed(2)} MB`);
    }
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
