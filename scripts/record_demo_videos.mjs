import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("/Users/tunghunglu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const ROOT = process.cwd();
// Cut switch: FITRACE_DEMO_CUT=race produces the five-scene "race modes"
// walkthrough (gameAdmin individual setup -> live signup -> individual race
// -> gameAdmin team setup -> live team race) into output/videos/en-race/,
// English UI forced regardless of FITRACE_DEMO_LANG. FITRACE_DEMO_CUT=class
// produces the eleven-scene Class Mode walkthrough (DEMO_SCRIPT.md's "課程
// 模式劇本" section, scenes C1-C11) into output/videos/en-class/, also
// English UI only. Any other value (or unset) is the original 14-scene cut,
// unchanged.
const CUT =
  process.env.FITRACE_DEMO_CUT === "race" ? "race" :
  process.env.FITRACE_DEMO_CUT === "class" ? "class" :
  "default";
// Language switch: FITRACE_DEMO_LANG=en produces the English cut into
// output/videos/en/ (zh's output/videos/ files are left untouched); the
// default/anything else is the original zh-TW cut. One parameterised
// script - scene order, actions, and target durations are identical for
// both, only the UI locale and the overlay/narration copy differ. Ignored
// entirely when CUT === "race" or CUT === "class" (those cuts are always
// English).
const LANG = process.env.FITRACE_DEMO_LANG === "en" ? "en" : "zh";
const LOCALE = (CUT === "race" || CUT === "class") ? "en-US" : (LANG === "en" ? "en-US" : "zh-TW");
// Clean-recording switch: FITRACE_DEMO_NO_OVERLAY=1 records the same scenes,
// actions, UI, resolution and timing as usual, but with addOverlay()/
// hideOverlay() turned into no-ops, so the Key Message captions are never
// burned into the recorded pixels. Output goes to output/videos/en-clean/
// (LANG=en, CUT=default only) so output/videos/en/*.webm is never touched.
// Post-production composites all caption layers back on top of these clean
// clips instead, with independent fades/timing per layer - see
// postproduce_demo.mjs.
const NO_OVERLAY = process.env.FITRACE_DEMO_NO_OVERLAY === "1";
const VIDEOS_ROOT = path.join(ROOT, "output/videos");
// CUT === "class" combined with NO_OVERLAY routes to its own en-class-clean/
// directory (mirroring the default cut's en/ -> en-clean/ split above) so a
// clean re-record for a "class final" post-production pass never clobbers
// the existing, still-used output/videos/en-class/*.webm (captions burned
// in) that the "class" cut (CUT=class, NO_OVERLAY unset) reads/writes.
const OUTPUT_DIR =
  CUT === "race" ? path.join(VIDEOS_ROOT, "en-race") :
  CUT === "class" ? path.join(VIDEOS_ROOT, NO_OVERLAY ? "en-class-clean" : "en-class") :
  LANG === "en" ? path.join(VIDEOS_ROOT, NO_OVERLAY ? "en-clean" : "en") : VIDEOS_ROOT;
const VIDEO_SIZE = { width: 1920, height: 1080 };
const PORTRAIT_SIZE = { width: 390, height: 844 };
// Hub port: configurable via FITRACE_DEMO_HUB_PORT (default 8010, matching
// demo_hub.py's own default) so a second recording run - e.g. this script's
// own "race" cut running alongside someone else's "default" cut - can bind
// its own port instead of silently talking to whichever hub already holds
// 8010 (see the HUB_STATE_DIR comment below for the other half of that
// isolation).
const HUB_PORT = process.env.FITRACE_DEMO_HUB_PORT || "8010";
const BASE_URL = `http://127.0.0.1:${HUB_PORT}`;
// Scratch dir for the hub's persisted race_settings.json/race_results.jsonl
// (see startHub()). Keyed by cut AND port - not shared across concurrent
// runs like the old single "hub-state" directory was - so one run's fresh-
// state rm() at startup can never delete a different, still-running run's
// in-flight state out from under it.
const HUB_STATE_DIR = path.join(VIDEOS_ROOT, ".tmp", `hub-state-${CUT}-${HUB_PORT}`);
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

// Overlay copy for the "race" cut (r01-r05). This cut is English-only
// (LOCALE is forced to "en-US" above regardless of FITRACE_DEMO_LANG), so
// there is no zh column to key off - these are used verbatim.
const RACE_OVERLAY_TEXT = {
  r01: "Set the race format in seconds",
  r02: "Athletes register from their own phone",
  r03: "Live ranking, updated as they ride",
  r04: "Same hardware, switched to a team race",
  r05: "Team scores aggregate in real time",
};

// Overlay copy for the "class" cut (c01-c11). Copied verbatim from the
// 字幕 line of each C1-C11 section in DEMO_SCRIPT.md's "課程模式劇本
// （Class Mode）". English-only, same reasoning as RACE_OVERLAY_TEXT above.
const CLASS_OVERLAY_TEXT = {
  c01: "One venue, two modes",
  c02: "Every block, your call",
  c03: "Build a circuit in one click",
  c04: "Save it once, teach it weekly",
  c05: "Your class library",
  c06: "Press start, the room follows",
  c07: "Intensity changes, the board keeps up",
  c08: "The screen coaches for you",
  c09: "Same room, three intensities",
  c10: "The coach ends it, not the clock",
  c11: "Race days and training days. One system.",
};

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

// Collapses a DOM innerText dump (one item per line, blank lines between
// cards) into a single line for error messages, so a failed assertion logs
// as one greppable line instead of a wall of bare numbers and names.
function singleLine(text, maxLen = 400) {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length > maxLen ? `${collapsed.slice(0, maxLen)}...` : collapsed;
}

function startHub() {
  // RaceManager persists station assignments / race config to disk
  // (data/race_settings.json by default) and reloads them on the next
  // process start. Point that - and the finished-race results log - at
  // HUB_STATE_DIR (scoped per cut+port, see its own comment above) so each
  // recording run starts from genuinely empty state instead of replaying
  // whatever a previous recording (or the developer's real local hub) left
  // behind, and two concurrent runs on different ports never share - or
  // rm() - the same scratch files.
  const child = spawn(
    path.join(ROOT, ".venv/bin/python"),
    ["-m", "scripts.demo_hub"],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        TESTING: "1",
        FITRACE_ENABLE_TEST_TELEMETRY: "1",
        FITRACE_DEMO_HUB_PORT: HUB_PORT,
        FITRACE_RACE_SETTINGS_PATH: path.join(HUB_STATE_DIR, "race_settings.json"),
        FITRACE_RACE_RESULTS_PATH: path.join(HUB_STATE_DIR, "race_results.jsonl"),
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

// Bridges the "race" cut's r03 (individual race, just finished/stopped) into
// r04/r05 (team race). /api/race/reset clears registrations and config but
// deliberately keeps station assignments (see race_manager.reset_race), so
// this only needs to re-register every athlete - now with their team name -
// and refresh telemetry so each station reads "online" again for the team
// readiness check. It does NOT call /api/race/configure: r04 sets and saves
// competition_mode=team live, on camera, via the gameAdmin UI.
async function reseedForTeamRace() {
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

// ---------------------------------------------------------------------------
// "class" cut helpers - the class plan actually started in C6/run through
// C10, and the telemetry driver that keeps On target/Under target alive on
// the dashboard during C7-C9. See mainClassCut()'s own comment for the full
// timing rationale.
// ---------------------------------------------------------------------------

// The plan the class actually RUNS (seeded via /api/class/configure right
// before C6 clicks Start Class - see the note above mainClassCut()). This is
// deliberately a DIFFERENT object from the plan C2/C3/C4/C5 build/save/
// restore on screen: the on-screen plan exists to demonstrate the editor
// UX, this one is DEMO_SCRIPT.md's own "示範課表（環狀 + 強度階梯）" table,
// COPIED VERBATIM (not inflated for recording overhead the way the old
// single-target 170W plan was) - the whole point of this revision is that
// this exact ladder is what the camera has to catch on the projector, so
// the plan the camera watches has to be the plan the spec describes.
//
// Segment boundaries, ms from class start (matches DEMO_SCRIPT.md's own
// "0 18 40 50 72 81 90 112 132" timeline exactly):
//   0 warmup        [     0, 18000)
//   1 work   120W   [ 18000, 40000)  <- C7 opens here, watches it end
//   2 rest          [ 40000, 50000)
//   3 work   175W   [ 50000, 72000)  <- C7 watches the target change to this
//   4 rest          [ 72000, 81000)  <- C8 opens here
//   5 changeover    [ 81000, 90000)  <- C8 must also reach this (required,
//                                        not a bonus - see its own comment)
//   6 work   210W   [ 90000,112000)  <- C9 opens here
//   7 cooldown      [112000,132000)
//
// Because these segments are short (the recording preface's own
// requirement - a multi-minute real segment would leave the camera "stuck"
// on one state, unable to show the plan advancing on its own), C7/C8/C9 do
// NOT simply open cold and poll from whenever the browser happens to be
// ready - mainClassCut() pads the real (off-camera) time between scenes to
// land each scene's own recording start at a chosen point on THIS clock
// first (see padUntilClassElapsedMs() and the C7/C8/C9_START_TARGET_MS
// constants below it), and only then falls back on each scene's own
// adaptive poll loop to absorb whatever timing slop is left.
const CLASS_RUN_PLAN = {
  segments: [
    { kind: "warmup", duration_sec: 18 },
    { kind: "work", duration_sec: 22, target_watts: 120 },
    { kind: "rest", duration_sec: 10 },
    { kind: "work", duration_sec: 22, target_watts: 175 },
    { kind: "rest", duration_sec: 9 },
    { kind: "changeover", duration_sec: 9 },
    { kind: "work", duration_sec: 22, target_watts: 210 },
    { kind: "cooldown", duration_sec: 20 },
  ],
};

// Pure port of hub_server/domain/class_models.py::segment_at (also mirrored
// client-side as classClockAt in hub_server/static/index.html) - which
// segment is active `elapsedMs` into `plan`. Needed here so the recorder
// can independently decide, from the real classT0 it captured off the
// /api/race/start response, when a transition is about to happen or just
// happened - without depending on the DOM to expose that as text.
function classSegmentAt(elapsedMs, plan) {
  const safeElapsedMs = Math.max(0, elapsedMs);
  const segments = plan.segments;
  const totalMs = segments.reduce((sum, s) => sum + s.duration_sec * 1000, 0);
  const lastIndex = segments.length - 1;
  if (safeElapsedMs >= totalMs) {
    const last = segments[lastIndex];
    return { index: lastIndex, kind: last.kind, durationMs: last.duration_sec * 1000, segmentRemainingMs: 0, totalRemainingMs: 0, finished: true, targetWatts: last.target_watts ?? null };
  }
  let cumulativeMs = 0;
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    const segmentDurationMs = segment.duration_sec * 1000;
    const segmentEndMs = cumulativeMs + segmentDurationMs;
    if (safeElapsedMs < segmentEndMs) {
      return {
        index,
        kind: segment.kind,
        durationMs: segmentDurationMs,
        segmentRemainingMs: segmentEndMs - safeElapsedMs,
        totalRemainingMs: totalMs - safeElapsedMs,
        finished: false,
        targetWatts: segment.target_watts ?? null,
      };
    }
    cumulativeMs = segmentEndMs;
  }
  // Unreachable given the totalMs check above.
  const last = segments[lastIndex];
  return { index: lastIndex, kind: last.kind, durationMs: last.duration_sec * 1000, segmentRemainingMs: 0, totalRemainingMs: 0, finished: true, targetWatts: last.target_watts ?? null };
}

// Per-station wattage, held CONSTANT for the entire class (not varied by
// segment or time) - DEMO_SCRIPT.md's own recipe for getting a genuine
// on/under mix at every rung of the ladder for free: hold each athlete
// near a roughly constant output and let the TARGET move instead. Against
// this plan's three work targets (120W/175W/210W):
//   vs 120W -> under: [0]            on: [1,2,3,4,5]   (1 under, 5 on)
//   vs 175W -> under: [0,1,2]        on: [3,4,5]        (3 under, 3 on)
//   vs 210W -> under: [0,1,2,3,4]    on: [5]            (5 under, 1 on)
// station 1 (Ethan Lin, 140W) flips on->under between the 120W and 175W
// blocks; station 4 (Noah Park, 200W) flips on->under between the 175W and
// 210W blocks - so C7 and C9 each show a real reshuffle, not just a
// re-labelled static split. Outside a work segment there is no target on
// the current segment (classTargetStatus in index.html reads
// clock.targetWatts, which is null for warmup/rest/changeover/cooldown),
// so holding the same number there too is harmless - it just keeps the
// board's numbers from looking frozen at zero.
const CLASS_NODE_WATTS = [110, 140, 165, 185, 200, 220];

function classPowerForNode(nodeIndex) {
  return CLASS_NODE_WATTS[nodeIndex] ?? 150;
}

// Background telemetry driver for the class cut. Runs on a plain interval
// independent of whichever scene function happens to be recording at the
// moment - the power level it sends is always computed from nodeIndex
// alone (see CLASS_NODE_WATTS above), never from scene-local timing - so
// it stays correct regardless of Playwright's own per-scene overhead.
// Returns a handle whose stop() must be called before /api/race/stop (C10)
// so it does not keep POSTing telemetry into a class that has already
// ended.
function startClassTelemetry(classT0) {
  let stopped = false;
  const timer = setInterval(() => {
    if (stopped) return;
    const elapsedMs = Date.now() - classT0;
    Promise.all(demoNodes.map((node, i) => {
      const power = classPowerForNode(i);
      const distanceM = Math.round((elapsedMs / 1000) * (2 + i * 0.4));
      return api("/api/test/telemetry", {
        method: "POST",
        body: JSON.stringify({
          node_id: node.nodeId,
          equipment_type: node.equipment,
          distance_m: distanceM,
          elapsed_time_ms: elapsedMs,
          instantaneous_speed_kph: Math.max(4, Math.round(power / 10)),
          power_watts: power,
          calories: Math.round(distanceM / 12),
        }),
      });
    })).catch(() => {
      // A request racing the interval's own teardown (stop() fires between
      // this tick starting and it finishing) is expected, not an error.
    });
  }, 1100);
  return {
    stop() {
      stopped = true;
      clearInterval(timer);
    },
  };
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
  if (NO_OVERLAY) return;
  await page.evaluate(({ text, align }) => {
    let el = document.getElementById("demo-overlay");
    if (!el) {
      el = document.createElement("div");
      el.id = "demo-overlay";
      document.body.appendChild(el);
      const style = document.createElement("style");
      // Landscape scenes record at 1920x1080 now (was 1280x720) - a caption
      // box/font tuned for 720p reads undersized on the bigger canvas, so
      // every dimension here is scaled by the same 1.5x (1920/1280 ==
      // 1080/720) as the recording resolution: font 30px -> 45px, and the
      // box's own padding/max-width/edge offsets/glow scale with it so the
      // bigger text still has proportionally the same room to wrap in and
      // the same relative margin from the frame edge. The narrow-viewport
      // override below stays untouched - it only applies to S4/R2's
      // portrait 390x844 recordings, which are not changing size.
      style.textContent = `
        #demo-overlay {
          position: fixed;
          z-index: 2147483647;
          left: 54px;
          right: auto;
          bottom: 51px;
          max-width: min(780px, calc(100vw - 72px));
          padding: 24px 33px;
          border: 1px solid rgba(226,255,59,.75);
          border-radius: 6px;
          background: rgba(9,9,11,.86);
          color: #f7f7f8;
          font: 800 45px/1.25 "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", Outfit, Inter, system-ui, sans-serif;
          letter-spacing: 0;
          box-shadow: 0 0 42px rgba(226,255,59,.18);
          backdrop-filter: blur(12px);
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
          right: 54px;
        }
        #demo-overlay.top {
          top: 51px;
          bottom: auto;
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
          #demo-overlay.top {
            top: 14px;
            bottom: auto;
          }
        }
      `;
      document.head.appendChild(style);
    }
    el.textContent = text;
    el.className = align === "right" ? "right" : align === "top" ? "top" : "";
    requestAnimationFrame(() => el.classList.add("show"));
  }, { text, align });
}

async function hideOverlay(page) {
  if (NO_OVERLAY) return;
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
  // The signup form fills the whole portrait viewport edge to edge (avatar
  // picker, name/team inputs, submit button all stacked), so a bottom-edge
  // caption - left or right - collides with the submit button regardless of
  // horizontal alignment. Top placement is the only position that clears
  // every input this scene interacts with.
  await addOverlay(page, overlayText("s04"), "top");
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
  // Race Rules fields (incl. Target Distance) sit in the left 8-of-12
  // columns of the panel and run right up against the viewport's bottom
  // edge, so a left-aligned bottom caption lands directly on top of them.
  // Right-aligned clears the form - it only overlaps the readiness sidebar.
  await addOverlay(page, overlayText("s05"), "right");
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
//
// The 3-2-1-GO countdown is broadcast over the race websocket at the
// instant Start Race is clicked. The previous version of this scene
// recorded gameAdmin, clicked Start there, and only THEN navigated the
// same (recorded) page to the dashboard - by the time that navigation
// finished and the dashboard's own websocket connected, the countdown had
// already been broadcast and missed entirely (confirmed from a recorded
// clip: still "READY / Waiting for Start Race" at t=4.2s, already
// "Running 00:00.3" by t=6.5s - the countdown overlay never rendered).
//
// Fixed sequencing: open the DASHBOARD on the recorded page first (so its
// websocket is already live), then click Start Race from a second,
// unrecorded page/context pointed at gameAdmin. The countdown then
// broadcasts straight into an already-connected socket and renders on the
// recorded page.
// ---------------------------------------------------------------------------
async function sceneS06(browser) {
  const rec = await newRecordedPage(browser, "s06_start_race.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await waitForZhText(page, "#race-stage-kicker");
  await addOverlay(page, overlayText("s06"));
  await delay(2400);

  // Second, unrecorded page/context: only used to click Start Race on
  // gameAdmin while the dashboard above keeps recording. It needs its own
  // preparePage() (admin token/password in localStorage) or the click
  // would be rejected as unauthenticated.
  const controlContext = await browser.newContext();
  const controlPage = await controlContext.newPage();
  await preparePage(controlPage);
  await controlPage.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await controlPage.waitForFunction(
    () => document.getElementById("summary-readiness")?.innerText?.trim().length > 0,
    { timeout: 8000 },
  ).catch(() => {});
  await controlPage.click("#btn-start-race");
  await delay(700);
  await controlContext.close();

  // Wait for the countdown or the live-race stage rather than matching the
  // kicker's translated text (which is zh-only in the zh cut and en-only in
  // the en cut). getRaceStageDetails() in index.html sets race-stage-banner's
  // class to "countdown" or "running" for those two stages regardless of
  // locale, so key off that instead.
  await page.waitForFunction(
    () => {
      const banner = document.getElementById("race-stage-banner");
      return !!banner && (banner.classList.contains("countdown") || banner.classList.contains("running"));
    },
    { timeout: 7000 },
  ).catch(() => {});
  // The banner-class wait above now resolves almost immediately instead of
  // burning its old 7s timeout, so the fixed post-wait delay needs padding
  // back out - otherwise the scene lands right at (or just under) the
  // checker's -25% floor for S6's 15s target in both cuts. This also has to
  // cover the full 3-2-1-GO countdown playing out on screen.
  await delay(7000);
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
  // Same language-independence concern as S6: wait for the finished-stage
  // banner class or the podium overlay, not translated body text. The old
  // `a && b || c` here also made the innerText clause dead in practice
  // (`.podium-overlay.show` alone satisfied it) - both branches are now
  // explicit, language-independent OR conditions.
  await page.waitForFunction(
    () => document.querySelector(".race-stage-banner.finished") !== null ||
      document.querySelector(".podium-overlay.show") !== null,
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

// ---------------------------------------------------------------------------
// "race" cut - five-scene focused walkthrough of individual vs. team race
// modes, English UI only. Scenes below are named r01-r05 to keep them
// unambiguous from the S01-S14 cut above; they share every helper (api(),
// addOverlay(), runRaceTelemetry(), raceCurves, demoNodes, etc.) with it.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// R1 - Individual race setup: gameAdmin race rules, competition mode
// "individual", save. Asserts the saved config actually round-trips through
// /api/race/state, and that the panel itself rendered in English.
// ---------------------------------------------------------------------------
async function sceneR01(browser) {
  const rec = await newRecordedPage(browser, "r01_setup_individual.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });

  // English-locale assertion #1: gameAdmin renders entirely from its own
  // embedded en-US/zh-TW dictionary, keyed off localStorage "fitrace.locale"
  // (preparePage() sets that to LOCALE = "en-US" for this cut, forced above
  // regardless of FITRACE_DEMO_LANG). If i18n ever silently fell back to
  // zh-TW, this would read "比賽控制" instead and the assertion below fires
  // before a single frame of a mislabelled "English" cut gets recorded.
  // Compared case-insensitively: #race-title's CSS applies
  // text-transform: uppercase (.panel-title in gameAdmin.html), so
  // innerText renders "RACE CONTROL" even though the dictionary's actual
  // string - and textContent - is "Race Control".
  const raceTitle = (await page.locator("#race-title").innerText()).trim();
  if (raceTitle.toLowerCase() !== "race control") {
    throw new Error(`r01: expected English gameAdmin UI, got race-title="${raceTitle}"`);
  }

  await addOverlay(page, RACE_OVERLAY_TEXT.r01);
  await delay(2600);

  for (const mode of ["calories", "time", "distance"]) {
    await page.selectOption("#race-type", mode);
    await delay(700);
  }
  await delay(700);
  await page.selectOption("#competition-mode", "individual");
  await delay(1400);
  // Individual mode leaves the team fields disabled/greyed - pause on one
  // so that's visible on camera, not just implied by the dropdown value.
  await page.locator("#team-scoring-field").hover({ timeout: 3000 }).catch(() => {});
  await delay(1500);
  await page.fill("#race-target", "400");
  await delay(1400);
  for (const mode of ["classic", "race_track"]) {
    await page.selectOption("#leaderboard-display-mode", mode);
    await delay(700);
  }
  await delay(700);

  await page.click("#btn-save-race");
  await page.waitForFunction(
    () => !document.getElementById("rules-dirty-badge")?.classList.contains("show"),
    { timeout: 8000 },
  ).catch(() => {});
  await delay(3400);

  // The check that matters: the save actually persisted server-side, not
  // just flipped a client-side "saved" badge.
  const state = await api("/api/race/state");
  if (state.config?.competition_mode !== "individual" || Number(state.config?.target_value) !== 400) {
    throw new Error(`r01: /api/race/state did not reflect the saved config: ${JSON.stringify(state.config)}`);
  }

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// R2 - Athlete signup on a portrait "phone" viewport. One athlete (Marcus
// Lee, station 1) registers live on camera; asserts he is really registered
// server-side afterward, not just a client-side success animation.
// ---------------------------------------------------------------------------
async function sceneR02(browser) {
  const rec = await newRecordedPage(browser, "r02_signup.webm", PORTRAIT_SIZE);
  const { page } = rec;
  const node = demoNodes[0]; // Marcus Lee / station 1 / team Velocity
  await page.goto(`${BASE_URL}/static/signup.html?station=${node.station}`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => !(document.getElementById("station-lbl")?.innerText || "").includes("Not selected"),
    { timeout: 8000 },
  ).catch(() => {});
  // Top placement clears the stacked avatar/name/team/submit form on this
  // portrait viewport - see S04's identical note above.
  await addOverlay(page, RACE_OVERLAY_TEXT.r02, "top");
  await delay(2000);
  await page.locator('[data-type="male"]').click();
  await delay(1600);
  await page.fill("#athlete-name", node.athlete);
  await delay(1200);
  await page.fill("#team-name", node.team);
  await delay(1800);
  await page.click("#submit-btn");
  await page.waitForFunction(
    () => document.getElementById("success-msg")?.classList.contains("show") ||
      getComputedStyle(document.getElementById("success-msg")).display !== "none",
    { timeout: 8000 },
  ).catch(() => {});
  await delay(5000);

  // The check that matters: the athlete typed on camera is really
  // registered at this station server-side.
  const stations = await api("/api/stations");
  const registered = stations.stations?.[String(node.station)];
  if (!registered?.registered || registered.athlete_name !== node.athlete) {
    throw new Error(`r02: station ${node.station} was not registered as "${node.athlete}": ${JSON.stringify(registered)}`);
  }

  await delay(5600);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// R3 - The individual race running live: start from gameAdmin, countdown on
// the Dashboard, telemetry-driven ranking movement, stop/finish. Asserts the
// leaderboard actually renders athlete names and that its content changes
// as telemetry comes in (not a blank/stuck board).
// ---------------------------------------------------------------------------
async function sceneR03(browser) {
  const rec = await newRecordedPage(browser, "r03_live_individual.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => document.getElementById("summary-readiness")?.innerText?.trim().length > 0,
    { timeout: 8000 },
  ).catch(() => {});
  await addOverlay(page, RACE_OVERLAY_TEXT.r03);
  await delay(1400);
  await page.click("#btn-start-race");
  await delay(700);

  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  // Locale-independent stage wait (see S06's note): key off race-stage-banner's
  // class, not its translated text.
  await page.waitForFunction(
    () => {
      const banner = document.getElementById("race-stage-banner");
      return !!banner && (banner.classList.contains("countdown") || banner.classList.contains("running"));
    },
    { timeout: 7000 },
  ).catch(() => {});
  await delay(1600);

  const boardBefore = await page.locator("#leaderboard-container").innerText().catch(() => "");
  const raceState = await api("/api/race/state").catch(() => ({}));
  await runRaceTelemetry({ stepDelayMs: 2200, steps: 7, skipStart: raceState.state === "RUNNING" });
  const boardAfter = await page.locator("#leaderboard-container").innerText().catch(() => "");

  // The check that matters: the dashboard genuinely shows the athletes and
  // their values moved, not an empty/stuck board. Compared case-insensitively:
  // .leaderboard-item's athlete name is rendered inside this dashboard's
  // all-caps styling (text-transform: uppercase throughout index.html), so
  // innerText reads "MARCUS LEE" even though the athlete was registered as
  // "Marcus Lee".
  const liveAthlete = demoNodes[0].athlete;
  if (!boardAfter.toLowerCase().includes(liveAthlete.toLowerCase())) {
    throw new Error(`r03: expected "${liveAthlete}" on the dashboard leaderboard, got: ${singleLine(boardAfter)}`);
  }
  if (boardAfter === boardBefore) {
    throw new Error("r03: leaderboard did not change after telemetry - dashboard may be blank/stuck");
  }

  await delay(1200);
  await api("/api/race/stop", { method: "POST" });
  await page.waitForFunction(
    () => document.querySelector(".race-stage-banner.finished") !== null ||
      document.querySelector(".podium-overlay.show") !== null,
    { timeout: 9000 },
  ).catch(() => {});
  await delay(4500);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// R4 - Team race setup: same gameAdmin panel, competition mode -> "team",
// team scoring/completion policy, Team Battle leaderboard, save. Asserts
// the mode switch actually persisted server-side.
// ---------------------------------------------------------------------------
async function sceneR04(browser) {
  const rec = await newRecordedPage(browser, "r04_setup_team.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await addOverlay(page, RACE_OVERLAY_TEXT.r04);
  await delay(2600);

  // team-scoring-policy/team-completion-policy are disabled selects until
  // competition-mode is switched to "team" (see gameAdmin.html's
  // syncCompetitionFields()), so this order matters - selecting them first
  // would fail as "not enabled" in Playwright.
  await page.selectOption("#competition-mode", "team");
  await delay(2200);
  await page.locator("#team-rule-summary").hover({ timeout: 3000 }).catch(() => {});
  await delay(1400);
  await page.selectOption("#team-scoring-policy", "average");
  await delay(1200);
  await page.selectOption("#team-completion-policy", "aggregate");
  await delay(1800);
  await page.fill("#race-target", "500");
  await delay(1200);
  await page.selectOption("#leaderboard-display-mode", "team_battle");
  await delay(2200);

  await page.click("#btn-save-race");
  await page.waitForFunction(
    () => !document.getElementById("rules-dirty-badge")?.classList.contains("show"),
    { timeout: 8000 },
  ).catch(() => {});
  await delay(3500);

  // The check that matters: the mode switch actually persisted server-side.
  const state = await api("/api/race/state");
  if (state.config?.competition_mode !== "team") {
    throw new Error(`r04: /api/race/state did not switch to team mode: ${JSON.stringify(state.config)}`);
  }

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// ---------------------------------------------------------------------------
// R5 - The team race running live: start, Team Battle leaderboard on the
// Dashboard, telemetry-driven team-standings movement, finish. Every
// athlete maps 1:1 to a distinct team (see demoNodes), so raceCurves' late
// Marcus Lee surge (already used by r03/the S07 curve set) drives a real
// team-standings lead change: Redline (Ava Chen) leads for most of the
// race, Velocity (Marcus Lee) wins in the final two frames. Also carries
// this cut's second English-locale assertion, on the Dashboard rather than
// gameAdmin - a locale regression scoped to just index.html would not be
// caught by r01 alone.
// ---------------------------------------------------------------------------
async function sceneR05(browser) {
  const rec = await newRecordedPage(browser, "r05_live_team.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/gameAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => document.getElementById("summary-readiness")?.innerText?.trim().length > 0,
    { timeout: 8000 },
  ).catch(() => {});
  await addOverlay(page, RACE_OVERLAY_TEXT.r05);
  await delay(1400);
  await page.click("#btn-start-race");
  await delay(700);

  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });

  // English-locale assertion #2: the Dashboard is a separate page/surface
  // from gameAdmin with its own translation fetch (loadLocale() hitting
  // /api/locales/{locale}), so r01's gameAdmin check alone would miss an
  // i18n regression scoped to just index.html. Compared case-insensitively
  // for the same reason as r01's check: #leaderboard-panel-title's parent
  // ".card-title" (index.html) applies text-transform: uppercase, so
  // innerText renders "LIVE LEADERBOARD".
  const panelTitle = (await page.locator("#leaderboard-panel-title").innerText()).trim();
  if (panelTitle.toLowerCase() !== "live leaderboard") {
    throw new Error(`r05: expected English dashboard UI, got leaderboard-panel-title="${panelTitle}"`);
  }

  await page.waitForFunction(
    () => {
      const banner = document.getElementById("race-stage-banner");
      return !!banner && (banner.classList.contains("countdown") || banner.classList.contains("running"));
    },
    { timeout: 7000 },
  ).catch(() => {});
  await delay(1600);

  const boardBefore = await page.locator("#leaderboard-container").innerText().catch(() => "");
  const raceState = await api("/api/race/state").catch(() => ({}));
  await runRaceTelemetry({ stepDelayMs: 2200, steps: 7, skipStart: raceState.state === "RUNNING" });
  const boardAfter = await page.locator("#leaderboard-container").innerText().catch(() => "");

  // The check that matters: the Team Battle board genuinely shows team
  // names and the standings moved, not an empty/stuck board. Both the
  // eventual winner (Velocity) and the mid-race leader (Redline) must
  // appear, since raceCurves' surge only overtakes in the final frames.
  // Compared case-insensitively for the same reason as r03's check -
  // .team-battle-name renders inside this dashboard's all-caps styling.
  for (const teamName of ["Velocity", "Redline"]) {
    if (!boardAfter.toLowerCase().includes(teamName.toLowerCase())) {
      throw new Error(`r05: expected team "${teamName}" on the Team Battle board, got: ${singleLine(boardAfter)}`);
    }
  }
  if (boardAfter === boardBefore) {
    throw new Error("r05: team leaderboard did not change after telemetry - dashboard may be blank/stuck");
  }

  await delay(1400);
  await api("/api/race/stop", { method: "POST" });
  await page.waitForFunction(
    () => document.querySelector(".race-stage-banner.finished") !== null ||
      document.querySelector(".podium-overlay.show") !== null,
    { timeout: 9000 },
  ).catch(() => {});
  await delay(4500);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

async function concatVideos(outputs, finalFileName = "demo_full_4min.mp4") {
  const listFile = path.join(OUTPUT_DIR, "concat_list.txt");
  const normalizedDir = path.join(OUTPUT_DIR, ".normalized");
  await rm(normalizedDir, { recursive: true, force: true });
  await mkdir(normalizedDir, { recursive: true });

  // Scenes are a mix of 1920x1080 landscape and S4's 390x844 portrait clip.
  // Normalize *every* clip (not just S4) through the same
  // scale-to-fit-inside-1920x1080-then-letterbox filter, plus a common
  // fps/pix_fmt/codec, so the concat step works from a uniform stream
  // instead of assuming the landscape clips already match exactly.
  const normalized = [];
  for (const file of outputs) {
    const dest = path.join(normalizedDir, `${path.basename(file, path.extname(file))}.mp4`);
    await runFfmpeg([
      "-y",
      "-i", file,
      "-vf",
      "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p",
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

  const finalOutput = path.join(OUTPUT_DIR, finalFileName);
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

async function mainDefaultCut() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  // Guarantee a genuinely fresh hub-state scratch dir every run (see
  // startHub()'s FITRACE_RACE_SETTINGS_PATH/FITRACE_RACE_RESULTS_PATH). Only
  // touches this cut+port's own HUB_STATE_DIR - never a concurrent run's.
  await rm(HUB_STATE_DIR, { recursive: true, force: true });
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

// The "race" cut: five scenes, individual race then team race, English UI
// only. See sceneR01-sceneR05 and reseedForTeamRace() above for the
// per-scene story and the assertions that fail the run loudly rather than
// silently shipping a blank or mistranslated recording.
async function mainRaceCut() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  // Guarantee a genuinely fresh hub-state scratch dir every run (see
  // startHub()'s FITRACE_RACE_SETTINGS_PATH/FITRACE_RACE_RESULTS_PATH). Only
  // touches this cut+port's own HUB_STATE_DIR - never a concurrent run's.
  await rm(HUB_STATE_DIR, { recursive: true, force: true });
  const hub = startHub();
  let browser;
  try {
    await waitForHub();
    browser = await chromium.launch(
      process.env.FITRACE_HEADED
        ? { headless: false, channel: "chrome", slowMo: 120 }
        : { headless: true }
    );

    // Bootstrap: zero-out telemetry for all six demo nodes and assign every
    // station off-camera (this cut never visits System Admin - the "race
    // modes" story starts directly on gameAdmin), but register no one yet
    // so r02 can register the first athlete live.
    await seedConfiguredRace({ registerAthletes: false, assignStations: true });

    const outputs = [];
    outputs.push(await sceneR01(browser));
    outputs.push(await sceneR02(browser));

    // Off-camera: fill in the remaining five athletes r02 didn't register
    // interactively, so r03's readiness checks pass.
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

    outputs.push(await sceneR03(browser));

    // Between the individual and team races: reset (keeps station
    // assignments, clears config/registrations/progress) and re-register
    // every athlete with their team name off-camera, so r04's team-mode
    // readiness checks (>= 2 distinct teams, every athlete on a team, fresh
    // telemetry) pass when r05 starts the team race.
    await reseedForTeamRace();

    outputs.push(await sceneR04(browser));
    outputs.push(await sceneR05(browser));

    for (const file of outputs) {
      const info = await stat(file);
      console.log(`${path.relative(ROOT, file)} ${(info.size / 1024 / 1024).toFixed(2)} MB`);
    }

    const finalVideo = await concatVideos(outputs, "fitrace_race_modes_en.mp4");
    const info = await stat(finalVideo);
    console.log(`${path.relative(ROOT, finalVideo)} ${(info.size / 1024 / 1024).toFixed(2)} MB`);
  } finally {
    if (browser) await browser.close();
    hub.kill("SIGINT");
    await delay(600);
  }
}

// ---------------------------------------------------------------------------
// "class" cut - eleven scenes (C1-C11) covering DEMO_SCRIPT.md's "課程模式
// 劇本（Class Mode）" section, English UI only. Scenes are named c01-c11 to
// keep them unambiguous from the S/R cuts above; they share every generic
// helper (api(), addOverlay(), newRecordedPage(), etc.) with those, plus
// the class-specific helpers declared just above preparePage() (
// CLASS_RUN_PLAN, classSegmentAt(), classPowerForNode(),
// startClassTelemetry()).
// ---------------------------------------------------------------------------

// C1 - Switch Projector to Class Mode, from IDLE, then cut to the dashboard
// on the same recorded page to show it become the Training Class Board.
async function sceneC01(browser) {
  const rec = await newRecordedPage(browser, "c01_switch_mode.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/classAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.getElementById("summary-state")?.textContent || "").trim().length > 0,
    { timeout: 8000 },
  ).catch(() => {});
  await addOverlay(page, CLASS_OVERLAY_TEXT.c01);
  await delay(2200);
  await page.locator("#summary-total-duration").hover({ timeout: 3000 }).catch(() => {});
  await delay(2000);

  await page.click("#btn-switch-to-class-mode");
  await page.waitForFunction(
    () => (document.getElementById("class-message")?.textContent || "").length > 0,
    { timeout: 6000 },
  ).catch(() => {});
  await delay(2200);

  // The check that matters: the switch actually persisted server-side, not
  // just a client-side message string.
  const state = await api("/api/race/state");
  if (state.session_mode !== "class") {
    throw new Error(`c01: /api/race/state did not switch session_mode to "class": ${JSON.stringify(state.session_mode)}`);
  }

  // Same venue, same screen, different mode: cut to the dashboard on the
  // SAME recorded page - the leaderboard panel is now the Training Class
  // Board (still idle; nothing has started running yet, that is C6).
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await delay(3600);

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// C2 - Build the class plan in the editor: Add Segment x5, five segments of
// three different kinds, with TWO different work targets (120W then 175W) -
// DEMO_SCRIPT.md's own requirement that the camera hold on both target
// fields long enough for the viewer to see the numbers actually differ.
async function sceneC02(browser) {
  const rec = await newRecordedPage(browser, "c02_plan_editor.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/classAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => typeof addSegmentRow === "function", { timeout: 8000 }).catch(() => {});
  // Start the on-screen build from a genuinely empty editor so "Add
  // Segment" x5 reads as building the plan from scratch (DEMO_SCRIPT.md's
  // C2 narrative), not as editing the three DEFAULT_PLAN_ROWS a fresh page
  // load starts with.
  await page.evaluate(() => {
    state.rows = [];
    state.rowsDirty = true;
    renderAll();
  });
  await addOverlay(page, CLASS_OVERLAY_TEXT.c02);
  await delay(1800);

  await page.click('button[onclick="addSegmentRow()"]');
  await delay(1200);
  await page.locator("#segment-row-0 select").selectOption("warmup");
  await delay(1000);
  await page.locator("#segment-row-0 input").first().fill("18");
  await delay(1600);

  await page.click('button[onclick="addSegmentRow()"]');
  await delay(1200);
  // addSegmentRow() defaults every new row to kind "work" - already correct
  // for this row, only duration/target need setting.
  await page.locator("#segment-row-1 input").first().fill("22");
  await delay(900);
  await page.locator("#segment-row-1 input").nth(1).fill("120");
  await delay(2400); // hold on this target field - the viewer has to actually read "120"

  await page.click('button[onclick="addSegmentRow()"]');
  await delay(1200);
  await page.locator("#segment-row-2 select").selectOption("rest");
  await delay(1000);
  await page.locator("#segment-row-2 input").first().fill("10");
  await delay(1400);

  await page.click('button[onclick="addSegmentRow()"]');
  await delay(1200);
  // Deliberately a DIFFERENT target from row 1's 120W - the two work rows
  // carrying two different numbers is the entire point of this scene.
  await page.locator("#segment-row-3 input").first().fill("22");
  await delay(900);
  await page.locator("#segment-row-3 input").nth(1).fill("175");
  await delay(2600); // hold here too - the number visibly differs from row 1's

  await page.click('button[onclick="addSegmentRow()"]');
  await delay(1200);
  await page.locator("#segment-row-4 select").selectOption("changeover");
  await delay(1000);
  await page.locator("#segment-row-4 input").first().fill("9");
  await delay(1600);

  // The checks that matter: the Plan Preview really reflects the five rows
  // just built, not a stale render, AND the two work rows' target fields
  // genuinely hold two different numbers (not just two rows that both
  // ended up "180" by copy-paste accident) - the literal values the camera
  // held on, not the tooltip-only Plan Preview timeline blocks.
  const previewText = await page.locator("#plan-preview").innerText().catch(() => "");
  if (!previewText.includes("Segments") || !/\b5\b/.test(previewText)) {
    throw new Error(`c02: plan preview did not reflect the 5 built segments: ${singleLine(previewText)}`);
  }
  const work120Value = await page.locator("#segment-row-1 input").nth(1).inputValue();
  const work175Value = await page.locator("#segment-row-3 input").nth(1).inputValue();
  if (work120Value !== "120" || work175Value !== "175") {
    throw new Error(`c02: expected the two work rows to carry different targets (120 then 175), got "${work120Value}" then "${work175Value}"`);
  }

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// C3 - Repeat Selected x3 turns one work+rest+changeover trio into three
// full rounds of circuit (warmup + 3x[work,rest,changeover] = 10 rows).
async function sceneC03(browser) {
  const rec = await newRecordedPage(browser, "c03_repeat_intervals.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/classAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => typeof addSegmentRow === "function", { timeout: 8000 }).catch(() => {});
  // Off-camera: a fresh 4-row circuit unit - warmup, then exactly one
  // work+rest+changeover trio, matching DEMO_SCRIPT.md's C3 operation
  // ("select work + rest + changeover, Times = 3"). This is a fresh page/
  // JS context (state.rows resets to DEFAULT_PLAN_ROWS on load), built
  // directly rather than literally continuing C2's 5-row on-screen state
  // (which has two work rows at different targets and no single contiguous
  // work-rest-changeover run to select) - C2 and C3 each demonstrate a
  // different editor feature from their own clean starting point, same as
  // every other scene in this cut.
  await page.evaluate(() => {
    state.rows = [
      { kind: "warmup", durationSec: 18 },
      { kind: "work", durationSec: 22, targetWatts: 120 },
      { kind: "rest", durationSec: 10 },
      { kind: "changeover", durationSec: 9 },
    ];
    state.rowsDirty = true;
    renderAll();
  });
  await addOverlay(page, CLASS_OVERLAY_TEXT.c03);
  await delay(2400);

  await page.fill("#repeat-from", "2");
  await delay(1400);
  await page.fill("#repeat-to", "4");
  await delay(1400);
  await page.fill("#repeat-times", "3");
  await delay(1800);
  await page.click("#btn-repeat-group");
  await delay(6500);

  // The check that matters: the repeat really turned one work+rest+
  // changeover trio into three full rounds of circuit (warmup +
  // 3x[work,rest,changeover] = 10 rows), not a no-op.
  const rowCount = await page.locator("#plan-rows > div").count();
  if (rowCount !== 10) {
    throw new Error(`c03: expected 10 rows after Repeat Selected x3 (1 warmup + 3x[work,rest,changeover]), got ${rowCount}`);
  }

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// C4 - Name the plan and Save Plan: persists to the named class-plan
// library (POST /api/class/plans) and configures it as the active plan.
async function sceneC04(browser) {
  const rec = await newRecordedPage(browser, "c04_save_plan.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/classAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => typeof addSegmentRow === "function", { timeout: 8000 }).catch(() => {});
  // Off-camera: recreate C3's finished 10-row circuit (fresh page, see C3's
  // own note above for why this reconstruction is faithful).
  await page.evaluate(() => {
    const work = { kind: "work", durationSec: 22, targetWatts: 120 };
    const rest = { kind: "rest", durationSec: 10 };
    const changeover = { kind: "changeover", durationSec: 9 };
    const rows = [{ kind: "warmup", durationSec: 18 }];
    for (let i = 0; i < 3; i += 1) rows.push({ ...work }, { ...rest }, { ...changeover });
    state.rows = rows;
    state.rowsDirty = true;
    renderAll();
  });
  await addOverlay(page, CLASS_OVERLAY_TEXT.c04);
  await delay(1800);

  await page.fill("#class-name", "Tuesday Circuit 30");
  await delay(2600);
  await page.click("#btn-save-plan");
  await page.waitForFunction(
    () => (document.getElementById("class-message")?.textContent || "").length > 0,
    { timeout: 6000 },
  ).catch(() => {});
  await delay(3000);

  // The check that matters: the named plan really landed in the server-side
  // library with all 10 segments, not just a local "Plan saved." message.
  const plans = await api("/api/class/plans");
  const saved = (plans.plans || []).find((entry) => entry.name === "Tuesday Circuit 30");
  if (!saved || saved.plan?.segments?.length !== 10) {
    throw new Error(`c04: "Tuesday Circuit 30" was not saved with 10 segments: ${JSON.stringify((plans.plans || []).map((p) => p.name))}`);
  }

  await delay(2000);
  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// C5 - Saved Class dropdown: New Class (clears) -> Tuesday Circuit 30
// (restores the editor + Plan Preview). Hovers Delete Saved Class - never
// clicks it, this plan has to survive into C6+.
async function sceneC05(browser) {
  const rec = await newRecordedPage(browser, "c05_saved_class_library.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/classAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.getElementById("saved-class-picker")?.options?.length || 0) > 1,
    { timeout: 8000 },
  ).catch(() => {});
  await addOverlay(page, CLASS_OVERLAY_TEXT.c05);
  await delay(1800);

  await page.selectOption("#saved-class-picker", "");
  await delay(2000);
  await page.selectOption("#saved-class-picker", "Tuesday Circuit 30");
  await delay(2200);

  // The check that matters: picking it back up genuinely restored the
  // 10-row circuit into the editor, not just the dropdown's label.
  const rowCount = await page.locator("#plan-rows > div").count();
  if (rowCount !== 10) {
    throw new Error(`c05: expected the restored "Tuesday Circuit 30" plan to have 10 rows, got ${rowCount}`);
  }

  await page.locator("#btn-delete-saved-class").hover({ timeout: 3000 }).catch(() => {});
  await delay(2200); // hovered only - never clicked, this saved plan must survive

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// C6 - Start Class. Clicks the real button (so the "Class started." message
// is genuine), then reads classT0 straight off the server's own
// /api/race/state rather than a client-side timestamp, and starts the
// background telemetry driver from it. Cuts to the dashboard on the SAME
// recorded page afterward to show the projector already on Live Class.
async function sceneC06(browser) {
  const rec = await newRecordedPage(browser, "c06_start_class.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/classAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.getElementById("station-list")?.children.length || 0) > 0,
    { timeout: 8000 },
  ).catch(() => {});
  await addOverlay(page, CLASS_OVERLAY_TEXT.c06);
  await delay(2000);
  await page.locator("#station-list").hover({ timeout: 3000 }).catch(() => {});
  await delay(2400);

  await page.click("#btn-start-class");
  await page.waitForFunction(
    () => (document.getElementById("class-message")?.textContent || "").length > 0,
    { timeout: 6000 },
  ).catch(() => {});

  const raceState = await api("/api/race/state");
  if (raceState.state !== "RUNNING" || !raceState.start_time_epoch_ms) {
    throw new Error(`c06: class did not start (state=${raceState.state}, start_time_epoch_ms=${raceState.start_time_epoch_ms})`);
  }
  const classT0 = raceState.start_time_epoch_ms;
  console.log(`[class] classT0=${classT0} (${new Date(classT0).toISOString()})`);
  const telemetry = startClassTelemetry(classT0);

  await delay(1400);
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await delay(5000);

  await hideOverlay(page);
  await delay(600);
  const file = await rec.close();
  return { file, classT0, telemetry };
}

// Waits real (off-camera, unrecorded) time until `targetElapsedMs` has
// passed on the class's own wall clock (classT0), rather than a fixed
// real-time delay(). The class clock keeps advancing through every
// browser-context teardown/setup between scenes - real time that varies
// run to run with page-load/network jitter - so a fixed delay() between
// scenes would drift the NEXT scene's recording start by however much that
// inter-scene overhead happened to be. Padding to an absolute class-clock
// TARGET instead makes each scene start at (close to) the same point on
// CLASS_RUN_PLAN's timeline every run, regardless of how long getting
// there actually took. This does NOT replace the adaptive poll loops in
// sceneC07/sceneC08/sceneC09 - it only removes most of the variance those
// loops would otherwise have to absorb; the loops are still the actual
// correctness net (they keep recording until the segment is genuinely
// observed, and throw if it never shows up). A no-op if the target has
// already passed.
async function padUntilClassElapsedMs(classT0, targetElapsedMs) {
  const waitMs = targetElapsedMs - (Date.now() - classT0);
  if (waitMs > 0) await delay(waitMs);
}

// Class-clock targets (ms since classT0) for when C7/C8/C9 should each
// BEGIN their own recording - see padUntilClassElapsedMs() above and
// CLASS_RUN_PLAN's own comment for the segment boundaries these are chosen
// against:
//   C7 must start comfortably inside the 120W work block (18000-40000)
//   with enough of it left to also watch the auto transition through rest
//   (40000-50000) into the 175W work block (50000-72000) - all inside its
//   own ~30s scene budget. 27000 (plus ~1.5-3s of page-nav/addOverlay
//   overhead before the poll loop itself starts) leaves roughly 10-13s of
//   visible 120W before rest begins.
//   C8 must start just before the SECOND rest (72000-81000) so it is not
//   spending its own ~20s budget waiting out the tail of the 175W work
//   block first, with enough runway left afterward to also reach
//   changeover (81000-90000) - both are required (see sceneC08's comment).
//   C9 must start just after work210 (90000-112000) begins, so the
//   "most of the room falls under target" contrast is on screen from near
//   the start of its own ~14s budget, not spent waiting for it to arrive.
const C7_START_TARGET_MS = 27000;
const C8_START_TARGET_MS = 69000;
const C9_START_TARGET_MS = 92000;

// C7 - Live class board, full screen on the dashboard. Polls the real class
// clock (classSegmentAt against the authoritative classT0) instead of
// trusting a fixed delay() to land on the right segment - see
// padUntilClassElapsedMs()'s comment for how mainClassCut() times this
// scene's own start. Keeps recording until it has actually SEEN the 120W
// work block with a genuine on/under mix AND the later 175W work block -
// the target NUMBER changing on camera is the entire point of this scene
// (see the recording brief), so both states have to be independently
// observed, not assumed from the plan's schedule.
async function sceneC07(browser, classT0) {
  const rec = await newRecordedPage(browser, "c07_live_class.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, CLASS_OVERLAY_TEXT.c07);
  await delay(1200);
  // The station grid (and its On target/Under target dots) sits below the
  // fold at 1920x1080 under the hero countdown - scroll it into view up
  // front. Unlike the old cut (a single transition, scrolled once it was
  // first observed), this scene needs BOTH target states on camera from
  // early on, so there is no single "wait for the first transition" moment
  // to hang the scroll off of anymore.
  await page.mouse.wheel(0, 420);

  const MAX_TOTAL_LOOP_MS = 32000;
  const MIN_POST_175_MS = 9000;
  const POLL_MS = 800;

  const loopStart = Date.now();
  let saw120Mixed = false;
  let saw175At = null;
  while (Date.now() - loopStart < MAX_TOTAL_LOOP_MS) {
    const elapsedClassMs = Date.now() - classT0;
    const clock = classSegmentAt(elapsedClassMs, CLASS_RUN_PLAN);
    if (clock.kind === "work" && clock.targetWatts === 120 && !saw120Mixed) {
      const onCount = await page.locator(".class-target-on").count();
      const underCount = await page.locator(".class-target-under").count();
      if (onCount > 0 && underCount > 0) {
        saw120Mixed = true;
        console.log(`[class] c07 saw 120W mix (on=${onCount} under=${underCount}) at class elapsed=${(elapsedClassMs / 1000).toFixed(1)}s`);
      }
    }
    if (clock.kind === "work" && clock.targetWatts === 175 && saw175At === null) {
      saw175At = Date.now();
      console.log(`[class] c07 reached the 175W work block at class elapsed=${(elapsedClassMs / 1000).toFixed(1)}s`);
    }
    if (saw175At !== null && Date.now() - saw175At >= MIN_POST_175_MS) break;
    await delay(POLL_MS);
  }

  // The checks that matter: both target states were genuinely observed
  // live, not just assumed from the plan's schedule - a class-mode board
  // that only ever shows one flat target would make the ladder read as
  // decoration, exactly the problem this revision exists to fix.
  if (!saw120Mixed) {
    throw new Error("c07: never observed the 120W work block with a genuine on/under mix - C7_START_TARGET_MS or CLASS_RUN_PLAN needs recalibrating against actual inter-scene overhead");
  }
  if (saw175At === null) {
    throw new Error("c07: never observed the auto transition into the 175W work block - C7_START_TARGET_MS or CLASS_RUN_PLAN needs recalibrating against actual inter-scene overhead");
  }
  const onCount175 = await page.locator(".class-target-on").count();
  const underCount175 = await page.locator(".class-target-under").count();
  if (onCount175 === 0 || underCount175 === 0) {
    throw new Error(`c07: expected the 175W block to also show a genuine on/under mix, got on=${onCount175} under=${underCount175}`);
  }
  const boardText = await page.locator("#leaderboard-container").innerText().catch(() => "");
  if (!/Segment\s+\d+\s+of\s+\d+/i.test(boardText)) {
    throw new Error(`c07: expected a "Segment N of M" label, got: ${singleLine(boardText)}`);
  }
  if (!/Target\s+175\s*W/i.test(boardText)) {
    throw new Error(`c07: expected the board to end on "Target 175 W", got: ${singleLine(boardText)}`);
  }

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// C8 - Rest/changeover instruction, full screen on the dashboard. Same
// adaptive-poll approach as C7. Unlike the old cut (where changeover was a
// best-effort bonus), DEMO_SCRIPT.md's C8 requires BOTH segments on camera
// - this is the circuit-training beat, the whole reason changeover exists
// in the plan at all - so this keeps recording until it has seen both.
async function sceneC08(browser, classT0) {
  const rec = await newRecordedPage(browser, "c08_rest_changeover.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, CLASS_OVERLAY_TEXT.c08);
  await delay(800);

  const MAX_TOTAL_LOOP_MS = 22000;
  const MIN_POST_CHANGEOVER_MS = 4500;
  const POLL_MS = 800;

  const loopStart = Date.now();
  let sawRest = false;
  let changeoverAt = null;
  while (Date.now() - loopStart < MAX_TOTAL_LOOP_MS) {
    const elapsedClassMs = Date.now() - classT0;
    const clock = classSegmentAt(elapsedClassMs, CLASS_RUN_PLAN);
    if (clock.kind === "rest") sawRest = true;
    if (clock.kind === "changeover" && changeoverAt === null) {
      changeoverAt = Date.now();
      console.log(`[class] c08 reached changeover at class elapsed=${(elapsedClassMs / 1000).toFixed(1)}s`);
    }
    if (changeoverAt !== null && Date.now() - changeoverAt >= MIN_POST_CHANGEOVER_MS) break;
    await delay(POLL_MS);
  }
  console.log(`[class] c08 sawRest=${sawRest} sawChangeover=${changeoverAt !== null}`);

  // The checks that matter: both the rest instruction ("Recover at your
  // station") and the changeover instruction ("Move to the next machine")
  // were genuinely on screen, not assumed from the plan's schedule.
  if (!sawRest) {
    throw new Error("c08: never observed a rest segment - C8_START_TARGET_MS or CLASS_RUN_PLAN needs recalibrating against actual inter-scene overhead");
  }
  if (changeoverAt === null) {
    throw new Error("c08: never observed the changeover segment - C8_START_TARGET_MS or CLASS_RUN_PLAN needs recalibrating against actual inter-scene overhead");
  }

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// C9 - NEW scene: the 210W work block, where most of the room falls to
// Under target - the deliberate contrast against C7's 120W block
// (DEMO_SCRIPT.md: "同一批人，不同強度，狀態完全不同"). Same adaptive-poll
// approach: keeps recording until the segment is genuinely observed with a
// majority-under mix, not assumed from the schedule.
async function sceneC09(browser, classT0) {
  const rec = await newRecordedPage(browser, "c09_high_intensity.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, CLASS_OVERLAY_TEXT.c09);
  await delay(1500);
  await page.mouse.wheel(0, 420);

  const MAX_TOTAL_LOOP_MS = 18000;
  const MIN_DWELL_MS = 9000;
  const POLL_MS = 800;

  const loopStart = Date.now();
  let saw210At = null;
  while (Date.now() - loopStart < MAX_TOTAL_LOOP_MS) {
    const elapsedClassMs = Date.now() - classT0;
    const clock = classSegmentAt(elapsedClassMs, CLASS_RUN_PLAN);
    if (clock.kind === "work" && clock.targetWatts === 210 && saw210At === null) {
      saw210At = Date.now();
      console.log(`[class] c09 reached the 210W work block at class elapsed=${(elapsedClassMs / 1000).toFixed(1)}s`);
    }
    if (saw210At !== null && Date.now() - saw210At >= MIN_DWELL_MS) break;
    await delay(POLL_MS);
  }

  // The checks that matter: the 210W block was genuinely observed live,
  // with a real majority-under mix - not just a couple of stragglers, the
  // contrast DEMO_SCRIPT.md is built around ("most of the room").
  if (saw210At === null) {
    throw new Error("c09: never observed the 210W work block - C9_START_TARGET_MS or CLASS_RUN_PLAN needs recalibrating against actual inter-scene overhead");
  }
  const onCount = await page.locator(".class-target-on").count();
  const underCount = await page.locator(".class-target-under").count();
  if (onCount === 0 || underCount === 0) {
    throw new Error(`c09: expected both On target and Under target on the 210W block, got on=${onCount} under=${underCount}`);
  }
  if (underCount <= onCount) {
    throw new Error(`c09: expected a majority-under mix (the contrast DEMO_SCRIPT.md calls for) on the 210W block, got on=${onCount} under=${underCount}`);
  }
  const boardText = await page.locator("#leaderboard-container").innerText().catch(() => "");
  if (!/Target\s+210\s*W/i.test(boardText)) {
    throw new Error(`c09: expected the board to show "Target 210 W", got: ${singleLine(boardText)}`);
  }

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// C10 - Stop Class (manual - the plan never auto-stops, see
// classAdmin.guidance_manual_stop) and Class History. Stops the background
// telemetry driver so it does not keep POSTing into an already-ended class.
async function sceneC10(browser, telemetry) {
  const rec = await newRecordedPage(browser, "c10_stop_class.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/classAdmin`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => { const btn = document.getElementById("btn-stop-class"); return !!btn && !btn.disabled; },
    { timeout: 8000 },
  ).catch(() => {});
  await addOverlay(page, CLASS_OVERLAY_TEXT.c10);
  await delay(2600);

  await page.click("#btn-stop-class");
  await page.waitForFunction(
    () => (document.getElementById("class-message")?.textContent || "").length > 0,
    { timeout: 6000 },
  ).catch(() => {});
  telemetry.stop();
  await delay(3200);

  // The check that matters: the class really stopped server-side and got
  // logged to history, not just a client-side "Class stopped." string.
  const raceState = await api("/api/race/state");
  if (raceState.state !== "STOPPED") {
    throw new Error(`c10: expected race state STOPPED after Stop Class, got ${raceState.state}`);
  }
  const history = await api("/api/class/history?limit=5");
  if (!(history.classes || []).length) {
    throw new Error("c10: /api/class/history is empty after stopping the class");
  }

  // The page's own class-history poll runs every 4s (scheduleRefresh) -
  // force it now rather than racing that timer, so the scroll-to/render
  // below is deterministic.
  await page.evaluate(() => { if (typeof refreshClassHistory === "function") refreshClassHistory(); });
  await page.waitForFunction(
    () => (document.getElementById("class-history-list")?.innerText || "").trim().length > 0,
    { timeout: 6000 },
  ).catch(() => {});
  await page.locator("#class-history-list").scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => {});
  await delay(8200);

  await hideOverlay(page);
  await delay(600);
  return rec.close();
}

// C11 - Outro card over the dashboard.
async function sceneC11(browser) {
  const rec = await newRecordedPage(browser, "c11_outro.webm");
  const { page } = rec;
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await addOverlay(page, CLASS_OVERLAY_TEXT.c11);
  await delay(2800);
  await hideOverlay(page);
  await delay(800);
  return rec.close();
}

// The "class" cut: eleven scenes covering DEMO_SCRIPT.md's "課程模式劇本
// （Class Mode）" section, English UI only.
//
// Timing: a class's segment clock (segment_at in
// hub_server/domain/class_models.py, mirrored client-side as classClockAt
// in index.html) advances purely off a real wall clock from
// start_time_epoch_ms - it has no idea a browser-automation script is
// opening and closing separate Playwright contexts between scenes, and
// that inter-scene overhead is NOT under this script's control the way an
// in-scene delay() is. CLASS_RUN_PLAN (seeded via /api/class/configure
// right before C6 clicks Start Class - a DIFFERENT object from the plan
// C2-C5 build/save/restore on screen; see CLASS_RUN_PLAN's own comment for
// why) is DEMO_SCRIPT.md's own short intensity-ladder table verbatim, so
// mainClassCut() pads the real (off-camera) time between C6/C7/C8/C9 to an
// absolute point on that clock first (padUntilClassElapsedMs(), see its
// own comment and the C7/C8/C9_START_TARGET_MS constants above sceneC07),
// and sceneC07/sceneC08/sceneC09 each still poll the real class clock and
// adapt how long they keep recording rather than trusting a fixed delay()
// to land on the right segment - see their own comments for the resulting
// guarantee.
async function mainClassCut() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  // Guarantee a genuinely fresh hub-state scratch dir every run (see
  // startHub()'s FITRACE_RACE_SETTINGS_PATH/FITRACE_RACE_RESULTS_PATH) -
  // required so C4's "Saved Class dropdown gains an entry" is actually
  // visible against an empty library, and so C1 starts genuinely IDLE.
  await rm(HUB_STATE_DIR, { recursive: true, force: true });
  const hub = startHub();
  let browser;
  try {
    await waitForHub();
    browser = await chromium.launch(
      process.env.FITRACE_HEADED
        ? { headless: false, channel: "chrome", slowMo: 120 }
        : { headless: true }
    );

    // Reuse the main film's 6 stations/6 athletes (recording preface:
    // "沿用主片的 6 站與 6 位選手，避免重新報名") - class mode has no signup
    // scene of its own. Session mode starts at its default ("race"), which
    // C1 switches to "class" on camera; race state starts IDLE, required
    // since Switch Projector to Class Mode is locked while RUNNING.
    await seedConfiguredRace({ registerAthletes: true, assignStations: true });

    const outputs = [];
    outputs.push(await sceneC01(browser));
    outputs.push(await sceneC02(browser));
    outputs.push(await sceneC03(browser));
    outputs.push(await sceneC04(browser));
    outputs.push(await sceneC05(browser));

    // Off-camera: swap the server's ACTIVE class plan to CLASS_RUN_PLAN
    // right before Start Class is pressed. C5 already froze the classAdmin
    // editor's local state (rowsDirty=true, from picking "Tuesday Circuit
    // 30" back up), so this does not touch what C5 just showed restored on
    // screen - it only changes what actually runs once C6 clicks Start.
    await api("/api/class/configure", {
      method: "POST",
      body: JSON.stringify(CLASS_RUN_PLAN),
    });

    const { file: c06File, classT0, telemetry } = await sceneC06(browser);
    outputs.push(c06File);

    await padUntilClassElapsedMs(classT0, C7_START_TARGET_MS);
    outputs.push(await sceneC07(browser, classT0));

    await padUntilClassElapsedMs(classT0, C8_START_TARGET_MS);
    outputs.push(await sceneC08(browser, classT0));

    await padUntilClassElapsedMs(classT0, C9_START_TARGET_MS);
    outputs.push(await sceneC09(browser, classT0));

    outputs.push(await sceneC10(browser, telemetry));
    outputs.push(await sceneC11(browser));

    for (const file of outputs) {
      const info = await stat(file);
      console.log(`${path.relative(ROOT, file)} ${(info.size / 1024 / 1024).toFixed(2)} MB`);
    }

    const finalVideo = await concatVideos(outputs, "class_demo_raw.mp4");
    const info = await stat(finalVideo);
    console.log(`${path.relative(ROOT, finalVideo)} ${(info.size / 1024 / 1024).toFixed(2)} MB`);
  } finally {
    if (browser) await browser.close();
    hub.kill("SIGINT");
    await delay(600);
  }
}

async function main() {
  if (CUT === "race") {
    await mainRaceCut();
  } else if (CUT === "class") {
    await mainClassCut();
  } else {
    await mainDefaultCut();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
