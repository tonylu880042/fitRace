import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, rm } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const playwrightModulePath = process.env.FITRACE_PLAYWRIGHT_MODULE_PATH ||
  "/Users/tunghunglu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright";
const { chromium } = require(playwrightModulePath);

const ROOT = process.cwd();
const BASE_URL = "http://127.0.0.1:8011";
const SCREENSHOT_DIR = path.join(ROOT, "output/screenshots/dashboard-ux");

const demoNodes = [
  { station: 1, nodeId: "verify-bike-01", equipment: "fan_bike", athlete: "Mika Chen", team: "Volt" },
  { station: 2, nodeId: "verify-bike-02", equipment: "fan_bike", athlete: "Tony Lin", team: "Volt" },
  { station: 3, nodeId: "verify-row-01", equipment: "rower", athlete: "Ava Wang", team: "Apex" },
  { station: 4, nodeId: "verify-ski-01", equipment: "skierg", athlete: "Leo Huang", team: "Apex" },
];

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function startHub() {
  return spawn(
    path.join(ROOT, ".venv/bin/uvicorn"),
    ["hub_server.infrastructure.fastapi.app:app", "--host", "127.0.0.1", "--port", "8011"],
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
}

async function waitForHub() {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE_URL}/health`);
      if (res.ok) return;
    } catch (_) {
      await delay(250);
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

async function seedTeamRace() {
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
    await api("/api/race/register", {
      method: "POST",
      body: JSON.stringify({
        station_number: node.station,
        athlete_name: node.athlete,
        team_name: node.team,
      }),
    });
  }
  await api("/api/race/configure", {
    method: "POST",
    body: JSON.stringify({
      race_type: "distance",
      target_value: 1000,
      duration_sec: 0,
      competition_mode: "team",
      team_scoring_policy: "total",
      team_completion_policy: "all_members",
    }),
  });
  await api("/api/leaderboard/display", {
    method: "POST",
    body: JSON.stringify({ mode: "team_battle" }),
  });
  await api("/api/race/start-sound", {
    method: "POST",
    body: JSON.stringify({ enabled: true }),
  });
}

async function sendTelemetryFrame(distances, elapsedTimeMs) {
  await Promise.all(demoNodes.map((node, index) => api("/api/test/telemetry", {
    method: "POST",
    body: JSON.stringify({
      node_id: node.nodeId,
      equipment_type: node.equipment,
      distance_m: distances[index],
      elapsed_time_ms: elapsedTimeMs,
      instantaneous_speed_kph: 24 + index,
      power_watts: 210 + index * 12,
      calories: Math.round(distances[index] / 10),
    }),
  })));
}

async function expectText(page, selector, pattern, label) {
  const locator = page.locator(selector);
  const texts = await locator.allInnerTexts();
  const text = texts.join("\n");
  if (!pattern.test(text)) {
    throw new Error(`${label} mismatch. Expected ${pattern}, got: ${text}`);
  }
  return text;
}

async function main() {
  await rm(SCREENSHOT_DIR, { recursive: true, force: true });
  await mkdir(SCREENSHOT_DIR, { recursive: true });

  const hub = startHub();
  let browser;
  try {
    hub.stdout.on("data", (data) => process.stdout.write(`[hub] ${data}`));
    hub.stderr.on("data", (data) => process.stderr.write(`[hub] ${data}`));
    await waitForHub();
    await seedTeamRace();

    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.goto(`${BASE_URL}/static/index.html`, { waitUntil: "networkidle" });

    await expectText(page, "#race-stage-banner", /READY[\s\S]*TEAM RACE READY/i, "READY stage");
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "01-ready.png"), fullPage: true });

    const countdown = api("/api/race/countdown-start", { method: "POST" });
    await page.waitForTimeout(500);
    await expectText(page, "#race-stage-banner", /COUNTDOWN[\s\S]*3, 2, 1, GO/i, "COUNTDOWN stage");
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "02-countdown.png"), fullPage: true });
    await countdown;

    await sendTelemetryFrame([620, 710, 540, 430], 9200);
    await page.waitForTimeout(900);
    await expectText(page, "#race-stage-banner", /LIVE RACE[\s\S]*TEAM DISTANCE RACE/i, "RUNNING stage");
    await expectText(page, ".team-battle-status", /MEMBERS? TO FINISH/i, "Team Battle incomplete status");
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "03-running.png"), fullPage: true });

    await sendTelemetryFrame([1000, 1000, 1000, 1000], 15500);
    await page.waitForTimeout(900);
    await expectText(page, ".team-battle-status", /FINISHED|ALL IN/i, "Team Battle finished status");
    await api("/api/race/stop", { method: "POST" });
    await page.waitForTimeout(500);
    await expectText(page, "#race-stage-banner", /RESULT[\s\S]*(TEAM RESULT LOCKED|RACE RESULT LOCKED)/i, "RESULT stage");
    await page.screenshot({ path: path.join(SCREENSHOT_DIR, "04-result.png"), fullPage: true });

    await api("/api/race/reset", { method: "POST" });
    await api("/api/leaderboard/display", {
      method: "POST",
      body: JSON.stringify({ mode: "classic" }),
    });
    console.log(`Dashboard UX verification passed. Screenshots: ${path.relative(ROOT, SCREENSHOT_DIR)}`);
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
