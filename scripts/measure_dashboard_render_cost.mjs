#!/usr/bin/env node
// Measures the dashboard render-cost fix in hub_server/static/index.html
// by actually EXECUTING the real extracted functions -- from a "before"
// snapshot of the file and the current ("after") file -- against a
// simulated telemetry stream, and counting what they really do:
//
//   - full container rebuilds per second (innerHTML writes)
//   - per-card DOM writes per second (whole-card rewrites for a full
//     rebuild; individual text/style patches for the incremental path)
//   - getBoundingClientRect calls per second
//   - wall-clock time actually spent inside the render path, per second
//     of simulated telemetry
//
// at both 6 stations (the venues size when this was reported) and 20
// stations (the amended target). Nothing here is estimated by hand --
// every number is produced by running the real code under a simulated
// clock with a fake DOM instrumented to count these operations.
//
// Usage:
//   node scripts/measure_dashboard_render_cost.mjs <before.html> <after.html>
//
// The caller is responsible for producing <before.html> (e.g.
// `git show <sha>:hub_server/static/index.html`) -- this script does not
// invoke git itself, so it has no branch/checkout side effects.

import { readFileSync } from "node:fs";
import vm from "node:vm";

const SIMULATED_SECONDS = 3;
const FRAME_MS = 1000 / 60;
const TELEMETRY_HZ = 4;
const STATION_COUNTS = [6, 20];

// A longer window with several short segments, for the dedicated
// segment-boundary-crossing measurement further below. Total plan
// duration (58s) is deliberately shorter than the window (60s) so the
// window also covers the plan finishing -- proving that tail, too, settles
// onto the incremental path instead of rebuilding every tick.
const SEGMENT_BOUNDARY_SIMULATED_SECONDS = 60;
const SEGMENT_BOUNDARY_PLAN = {
  segments: [
    { kind: "warmup", duration_sec: 10 },
    { kind: "work", duration_sec: 12, target_watts: 220 },
    { kind: "rest", duration_sec: 12 },
    { kind: "work", duration_sec: 12, target_watts: 260 },
    { kind: "cooldown", duration_sec: 12 },
  ],
};

// --- source extraction (same brace-depth technique the Python test
// suite uses throughout tests/unit/hub/, ported to JS for this script) --

function stripJsComments(code) {
  const withoutBlocks = code.replace(/\/\*[\s\S]*?\*\//g, "");
  return withoutBlocks.replace(/^[ \t]*\/\/.*$/gm, "");
}

function matchingBraceEnd(source, openIdx) {
  let depth = 0;
  let i = openIdx;
  let inStr = null;
  while (i < source.length) {
    const ch = source[i];
    if (inStr) {
      if (ch === "\\") {
        i += 2;
        continue;
      }
      if (ch === inStr) inStr = null;
    } else if (ch === '"' || ch === "'" || ch === "`") {
      inStr = ch;
    } else if (ch === "{") {
      depth += 1;
    } else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return i;
    }
    i += 1;
  }
  throw new Error("no matching closing brace found");
}

function extractFunction(source, name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  if (start === -1) return null;
  const braceOpen = source.indexOf("{", start);
  const braceEnd = matchingBraceEnd(source, braceOpen);
  return stripJsComments(source.slice(start, braceEnd + 1));
}

function extractBraced(source, anchor) {
  const start = source.indexOf(anchor) + anchor.length;
  const braceStart = source.indexOf("{", start);
  const depthEnd = matchingBraceEnd(source, braceStart);
  return stripJsComments(source.slice(braceStart, depthEnd + 1));
}

// --- shared stubs -----------------------------------------------------

const SHARED_STUBS = `
const t = (key, params = {}) => { let value = "T[" + key + "]"; Object.entries(params).forEach(([n, r]) => { value = value.split("{" + n + "}").join(String(r)); }); return value; };
const metricNumber = (value, fallback = 0) => { const n = Number(value); return Number.isFinite(n) ? n : fallback; };
const escapeHtml = (value) => String(value == null ? "" : value);
const nodeDisplayName = (node) => (node && (node.node_display_name || node.display_name || node.node_id)) || "--";
function renderPodium() { return ""; }
function renderTeamPodium() { return ""; }
function triggerFinishCelebration() {}
function renderRegistrationEmptyState() { return ""; }
function formatTeamScore() { return { value: "", label: "" }; }
function formatResultScore() { return { value: "", label: "" }; }
function stationLabel() { return ""; }
const window = { setTimeout: () => {} };
`;

// --- fake DOM for the classic leaderboard (shared by before/after -- the
// classic-mode markup shape did not change) ----------------------------

function makeLeaderboardFakeDom(counters) {
  return `
function parseContainerRows(html) {
  const rowOpenRe = /<div class="leaderboard-item[^"]*" id="[^"]*" data-node-id="([^"]+)">/g;
  const opens = [];
  let m;
  while ((m = rowOpenRe.exec(html))) opens.push({ nodeId: m[1], start: m.index + m[0].length });
  return opens.map((o, i) => {
    const end = i + 1 < opens.length ? opens[i + 1].start : html.length;
    const segment = html.slice(o.start, end);
    const metricVals = [...segment.matchAll(/<div class="metric-val[^"]*">([^<]*)<\\/div>/g)].map((mm) => mm[1]);
    const fillMatch = segment.match(/<div class="progress-fill" style="width: ([^%]+)%">/);
    return { nodeId: o.nodeId, metricVals, fillWidth: fillMatch ? fillMatch[1] : null };
  });
}
function makeFakeRow(parsed) {
  const metricEls = parsed.metricVals.map((v) => ({ _text: v }));
  metricEls.forEach((el) => {
    Object.defineProperty(el, "textContent", {
      get() { return el._text; },
      set(v) { el._text = v; __counters.cardWrites += 1; },
    });
  });
  let fillEl = null;
  if (parsed.fillWidth !== null) {
    fillEl = { style: { _width: parsed.fillWidth + "%" } };
    Object.defineProperty(fillEl.style, "width", {
      get() { return fillEl.style._width; },
      set(v) { fillEl.style._width = v; __counters.cardWrites += 1; },
    });
  }
  return {
    dataset: { nodeId: parsed.nodeId },
    style: {},
    classList: { add() {}, remove() {} },
    getBoundingClientRect() { __counters.boundingRectCalls += 1; return { top: 0, left: 0 }; },
    querySelectorAll(sel) { return sel === ".metric-val" ? metricEls : []; },
    querySelector(sel) { return sel === ".progress-fill" ? fillEl : null; },
  };
}
function makeContainer() {
  let html = "";
  let rows = [];
  const container = {
    querySelectorAll(sel) { return sel === ".leaderboard-item" ? rows : []; },
    querySelector() { return null; },
  };
  Object.defineProperty(container, "innerHTML", {
    get() { return html; },
    set(value) {
      html = value;
      __counters.fullRebuilds += 1;
      const parsed = parseContainerRows(value);
      __counters.cardWrites += parsed.length;
      rows = parsed.map(makeFakeRow);
    },
  });
  return container;
}
const leaderboardContainer = makeContainer();
const document = {
  getElementById(id) { return id === "leaderboard-container" ? leaderboardContainer : null; },
  querySelectorAll(sel) { return sel.indexOf(".leaderboard-item") !== -1 ? leaderboardContainer.querySelectorAll(".leaderboard-item") : []; },
};
`;
}

// --- fake DOM for the class board (after-only card hooks; before never
// reads any of these back out, it only ever writes innerHTML) ----------

function makeClassBoardFakeDom() {
  return `
function parseClassCards(html) {
  const openRe = /<div data-node-id="([^"]*)" style="/g;
  const opens = [];
  let m;
  while ((m = openRe.exec(html))) opens.push({ nodeId: m[1], start: m.index });
  return opens.map((o, i) => {
    const end = i + 1 < opens.length ? opens[i + 1].start : html.length;
    const segment = html.slice(o.start, end);
    const power = segment.match(/class="class-metric-power">([^<]*)</);
    const speed = segment.match(/class="class-metric-speed" style="[^"]*">([^<]*)</);
    const distance = segment.match(/class="class-metric-distance" style="[^"]*">([^<]*)</);
    const effort = segment.match(/class="class-effort-fill" style="height:100%;width:([^%]+)%/);
    return { nodeId: o.nodeId, power: power ? power[1] : null, speed: speed ? speed[1] : null, distance: distance ? distance[1] : null, effortWidth: effort ? effort[1] : null };
  });
}
function makeMutableText(initial) {
  const box = { _text: initial };
  Object.defineProperty(box, "textContent", { get() { return box._text; }, set(v) { box._text = v; __counters.cardWrites += 1; } });
  return box;
}
function makeMutableWidth(initialPercent) {
  const box = { style: { _width: initialPercent === null ? null : initialPercent + "%" } };
  Object.defineProperty(box.style, "width", { get() { return box.style._width; }, set(v) { box.style._width = v; __counters.cardWrites += 1; } });
  return box;
}
function makeClassContainer() {
  let html = "";
  let cardEls = [];
  let countdownEl = null;
  let totalRemainingEl = null;
  let progressFillEl = null;
  const container = {
    querySelectorAll(sel) { return sel === "[data-node-id]" ? cardEls : []; },
    querySelector(sel) {
      if (sel === ".class-hero-countdown") return countdownEl;
      if (sel === ".class-hero-total-remaining") return totalRemainingEl;
      if (sel === ".class-progress-fill") return progressFillEl;
      return null;
    },
  };
  Object.defineProperty(container, "innerHTML", {
    get() { return html; },
    set(value) {
      html = value;
      __counters.fullRebuilds += 1;
      const cards = parseClassCards(value);
      __counters.cardWrites += cards.length;
      cardEls = cards.map((c) => {
        const powerEl = c.power === null ? null : makeMutableText(c.power);
        const speedEl = c.speed === null ? null : makeMutableText(c.speed);
        const distanceEl = c.distance === null ? null : makeMutableText(c.distance);
        const effortEl = c.effortWidth === null ? null : makeMutableWidth(c.effortWidth);
        return {
          dataset: { nodeId: c.nodeId },
          querySelector(sel) {
            if (sel === ".class-metric-power") return powerEl;
            if (sel === ".class-metric-speed") return speedEl;
            if (sel === ".class-metric-distance") return distanceEl;
            if (sel === ".class-effort-fill") return effortEl;
            return null;
          },
        };
      });
      const cd = value.match(/class="class-hero-countdown" style="[^"]*">([^<]*)</);
      countdownEl = cd ? makeMutableText(cd[1]) : null;
      const tr = value.match(/class="class-hero-total-remaining" style="[^"]*">([^<]*)</);
      totalRemainingEl = tr ? makeMutableText(tr[1]) : null;
      const pf = value.match(/class="class-progress-fill" style="height:100%;width:([^%]+)%/);
      progressFillEl = pf ? makeMutableWidth(pf[1]) : null;
    },
  });
  return container;
}
const leaderboardContainer = makeClassContainer();
const document = { getElementById(id) { return id === "leaderboard-container" ? leaderboardContainer : null; } };
`;
}

// A trivial fake DOM for the BEFORE class board: it only ever writes
// innerHTML wholesale and never reads any element back out, so no card
// parsing is needed -- just count the writes.
function makeClassBoardTrivialFakeDom() {
  return `
const leaderboardContainer = { _html: "" };
Object.defineProperty(leaderboardContainer, "innerHTML", {
  get() { return leaderboardContainer._html; },
  set(value) {
    leaderboardContainer._html = value;
    __counters.fullRebuilds += 1;
    // The before snapshot has no data-node-id hook (that markup was added
    // in this change) -- count station cards by the one styling rule
    // every card wrapper carries in both versions instead.
    __counters.cardWrites += (value.match(/border-radius:4px;padding:1rem;background:var\\(--card-bg\\);/g) || []).length;
  },
});
const document = { getElementById(id) { return id === "leaderboard-container" ? leaderboardContainer : null; } };
`;
}

// --- station data -------------------------------------------------------

function makeStations(count) {
  const stations = [];
  for (let i = 0; i < count; i += 1) {
    stations.push({
      node_id: `n${i}`,
      station_number: i + 1,
      athlete_name: `Athlete ${i}`,
      power_watts: 100,
      instantaneous_speed_kph: 20,
      distance_m: 500,
      progress_percent: 10,
    });
  }
  return stations;
}

function tickStation(station, tickIndex) {
  return {
    ...station,
    power_watts: 100 + (tickIndex % 20),
    instantaneous_speed_kph: 20 + (tickIndex % 5),
    distance_m: 500 + tickIndex * 3,
    progress_percent: Math.min(95, 10 + tickIndex * 0.2),
  };
}

// Builds the merged, time-ordered event schedule for one simulated run:
// one "telemetry" event per station per 250ms tick (staggered across the
// window so stations do not all land on the same millisecond -- exactly
// what six-to-twenty independently-clocked edge nodes would do), plus one
// "classClock" event every 250ms (mirrors the real setInterval(tickClassClock,
// 250) in index.html), plus one "frame" event every 1000/60 ms (the
// browsers paint cadence -- used to drive the OLD unconditional rAF loop
// and, for AFTER, to flush whatever the coalescing scheduler queued).
function buildSchedule(stationCount, totalSeconds) {
  const windowSeconds = totalSeconds || SIMULATED_SECONDS;
  const totalMs = windowSeconds * 1000;
  const events = [];
  const telemetryIntervalMs = 1000 / TELEMETRY_HZ;
  for (let s = 0; s < stationCount; s += 1) {
    const offset = (s * telemetryIntervalMs) / stationCount;
    let t = offset;
    let tick = 0;
    while (t < totalMs) {
      events.push({ t, type: "telemetry", station: s, tick });
      t += telemetryIntervalMs;
      tick += 1;
    }
  }
  for (let t = 0; t < totalMs; t += 250) {
    events.push({ t, type: "classClock" });
  }
  for (let t = 0; t < totalMs; t += FRAME_MS) {
    events.push({ t, type: "frame" });
  }
  events.sort((a, b) => a.t - b.t);
  return events;
}

// --- BEFORE: race mode (unconditional 60fps re-render + uncoalesced
// telemetry) -------------------------------------------------------------

function runBeforeRace(beforeSource, stationCount) {
  const counters = { fullRebuilds: 0, cardWrites: 0, boundingRectCalls: 0 };
  const fns = [
    "sortLeaderboardNodes",
    "isLeaderboardFinal",
    "animateLeaderboardReorder",
    "renderLeaderboard",
    "renderSmoothLeaderboardFrame",
  ]
    .map((name) => extractFunction(beforeSource, name))
    .join("\n");

  const script = `
${SHARED_STUBS}
${makeLeaderboardFakeDom(counters)}
function detectAthleteFinishes() {}
function smoothMetricNumber(key, target) { return Number(target) || 0; }
let leaderboardSmoothValues = new Map();
let leaderboardNodes = [];
let teamLeaderboardRows = [];
let leaderboardRankByNode = new Map();
let leaderboardDisplayMode = "classic";
let currentSessionMode = "race";
let currentState = "RUNNING";
let currentConfig = { race_type: "distance" };
let lastLeaderboardData = null;
let lastTeamLeaderboardData = null;
let isSmoothLeaderboardFrame = false;
let __wallNs = 0n;
const __hrtime = process.hrtime;
function __timed(fn) {
  const start = __hrtime.bigint();
  fn();
  __wallNs += __hrtime.bigint() - start;
}
let __rafCallbacks = [];
function requestAnimationFrame(cb) { __rafCallbacks.push(cb); }
${fns}
// Mirrors initializeDashboard()'s single startup call in the real page --
// renderSmoothLeaderboardFrame then keeps rescheduling itself every frame
// via its own internal requestAnimationFrame call.
requestAnimationFrame(renderSmoothLeaderboardFrame);
globalThis.__run = function (events, stations) {
  const byStation = stations.map((s) => s);
  for (const ev of events) {
    if (ev.type === "telemetry") {
      byStation[ev.station] = __tickStation(byStation[ev.station], ev.tick);
      const payload = {};
      byStation.forEach((s) => { payload[s.node_id] = s; });
      lastLeaderboardData = payload;
      __timed(() => renderLeaderboard(payload));
    } else if (ev.type === "frame") {
      const due = __rafCallbacks;
      __rafCallbacks = [];
      due.forEach((cb) => __timed(() => cb()));
    }
  }
  return { fullRebuilds: __counters.fullRebuilds, cardWrites: __counters.cardWrites, boundingRectCalls: __counters.boundingRectCalls, wallNs: __wallNs.toString() };
};
`;
  return runInSandbox(script, counters, stationCount, { skipClassClock: true });
}

// --- AFTER: race mode (coalesced telemetry via the real ws.onmessage
// untyped-telemetry branch + the real renderLeaderboard fast path) ------

function runAfterRace(afterSource, stationCount) {
  const counters = { fullRebuilds: 0, cardWrites: 0, boundingRectCalls: 0 };
  // The whole ws.onmessage handler is long and covers many unrelated
  // message types this simulation does not need (registration, node
  // status, and so on); extracting just the untyped-telemetry branch --
  // the actual coalescing logic under test -- is both sufficient and a
  // much smaller, less failure-prone span to brace-match.
  const coalesceBranch = extractBraced(
    afterSource,
    "if (data && Object.keys(data).length > 0)"
  );
  const fns = [
    "resetLeaderboardCardCache",
    "resetClassBoardCardCache",
    "buildLeaderboardCardSignature",
    "captureLeaderboardCardRefs",
    "setSmoothedCardText",
    "updateLeaderboardCardValues",
    "sortLeaderboardNodes",
    "isLeaderboardFinal",
    "animateLeaderboardReorder",
    "renderLeaderboard",
  ]
    .map((name) => extractFunction(afterSource, name))
    .join("\n");

  const script = `
${SHARED_STUBS}
${makeLeaderboardFakeDom(counters)}
function detectAthleteFinishes() {}
function smoothMetricNumber(key, target) { return Number(target) || 0; }
let leaderboardSmoothValues = new Map();
let leaderboardNodes = [];
let teamLeaderboardRows = [];
let leaderboardRankByNode = new Map();
let leaderboardCardRefs = new Map();
let leaderboardCardSignature = null;
let classBoardCardRefs = null;
let leaderboardDisplayMode = "classic";
let currentSessionMode = "race";
let currentState = "RUNNING";
let currentConfig = { race_type: "distance" };
let leaderboardRenderScheduled = false;
let pendingLeaderboardData = null;
let __wallNs = 0n;
const __hrtime = process.hrtime;
function __timed(fn) {
  const start = __hrtime.bigint();
  fn();
  __wallNs += __hrtime.bigint() - start;
}
let __rafCallbacks = [];
function requestAnimationFrame(cb) { __rafCallbacks.push(cb); }
${fns}
function handleTelemetryPayload(data) ${coalesceBranch}
globalThis.__run = function (events, stations) {
  const byStation = stations.map((s) => s);
  for (const ev of events) {
    if (ev.type === "telemetry") {
      byStation[ev.station] = __tickStation(byStation[ev.station], ev.tick);
      const payload = {};
      byStation.forEach((s) => { payload[s.node_id] = s; });
      __timed(() => handleTelemetryPayload(payload));
    } else if (ev.type === "frame") {
      const due = __rafCallbacks;
      __rafCallbacks = [];
      due.forEach((cb) => __timed(() => cb()));
    }
  }
  return { fullRebuilds: __counters.fullRebuilds, cardWrites: __counters.cardWrites, boundingRectCalls: __counters.boundingRectCalls, wallNs: __wallNs.toString() };
};
`;
  return runInSandbox(script, counters, stationCount, { skipClassClock: true });
}

// --- BEFORE: class mode (full rebuild on every telemetry tick AND every
// 250ms clock tick) -------------------------------------------------------

function runBeforeClass(beforeSource, stationCount) {
  const counters = { fullRebuilds: 0, cardWrites: 0, boundingRectCalls: 0 };
  const fns = ["classClockAt", "toClockShape", "buildClassBoardHtml", "renderClassBoardFromState", "renderLeaderboard"]
    .map((name) => extractFunction(beforeSource, name))
    .join("\n");
  const script = `
${SHARED_STUBS}
const Intl = { NumberFormat: function () { return { format: (n) => String(n) }; } };
function formatClock(ms) { const s = Math.max(0, Math.floor(metricNumber(ms) / 1000)); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }
${makeClassBoardTrivialFakeDom()}
function detectAthleteFinishes() {}
function smoothMetricNumber(key, target) { return Number(target) || 0; }
function updateClassSegmentCue() {}
let leaderboardNodes = [];
let teamLeaderboardRows = [];
let leaderboardRankByNode = new Map();
let leaderboardDisplayMode = "classic";
let currentSessionMode = "class";
let currentState = "RUNNING";
let currentConfig = null;
let currentLocale = "en-US";
let currentClassPlan = { segments: [{ kind: "work", duration_sec: 3600 }] };
let currentClassLeaderboard = {};
let raceStartTime = Date.now();
let __wallNs = 0n;
const __hrtime = process.hrtime;
function __timed(fn) {
  const start = __hrtime.bigint();
  fn();
  __wallNs += __hrtime.bigint() - start;
}
${fns}
globalThis.__run = function (events, stations) {
  const byStation = stations.map((s) => s);
  for (const ev of events) {
    if (ev.type === "telemetry") {
      byStation[ev.station] = __tickStation(byStation[ev.station], ev.tick);
      const payload = {};
      byStation.forEach((s) => { payload[s.node_id] = s; });
      currentClassLeaderboard = payload;
      __timed(() => renderLeaderboard(payload));
    } else if (ev.type === "classClock") {
      __timed(() => renderClassBoardFromState());
    }
  }
  return { fullRebuilds: __counters.fullRebuilds, cardWrites: __counters.cardWrites, boundingRectCalls: __counters.boundingRectCalls, wallNs: __wallNs.toString() };
};
`;
  return runInSandbox(script, counters, stationCount, { skipFrames: true });
}

// --- AFTER: class mode (real applyClassBoardIncrementalUpdate fast
// path) -------------------------------------------------------------------

function runAfterClass(afterSource, stationCount) {
  const counters = { fullRebuilds: 0, cardWrites: 0, boundingRectCalls: 0 };
  // Class-mode telemetry reaches renderLeaderboard through the exact same
  // untyped-telemetry ws.onmessage branch race-mode telemetry does (see
  // runAfterRace) -- renderLeaderboard only decides class vs race
  // internally, after the coalescing has already happened. Reusing the
  // same branch here is what makes this an honest measurement of what
  // production actually does, not an easier best case.
  const coalesceBranch = extractBraced(
    afterSource,
    "if (data && Object.keys(data).length > 0)"
  );
  const fns = [
    "resetLeaderboardCardCache",
    "resetClassBoardCardCache",
    "classTargetStatusForPatch",
    "computeClassProgressPercentForPatch",
    "buildClassStationSignature",
    "captureClassBoardRefs",
    "applyClassBoardIncrementalUpdate",
    "classClockAt",
    "toClockShape",
    "buildClassBoardHtml",
    "renderClassBoardFromState",
    "renderLeaderboard",
  ]
    .map((name) => extractFunction(afterSource, name))
    .join("\n");
  const script = `
${SHARED_STUBS}
const Intl = { NumberFormat: function () { return { format: (n) => String(n) }; } };
function formatClock(ms) { const s = Math.max(0, Math.floor(metricNumber(ms) / 1000)); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }
${makeClassBoardFakeDom()}
function detectAthleteFinishes() {}
function smoothMetricNumber(key, target) { return Number(target) || 0; }
function updateClassSegmentCue() {}
let leaderboardNodes = [];
let teamLeaderboardRows = [];
let leaderboardRankByNode = new Map();
let leaderboardCardRefs = new Map();
let leaderboardCardSignature = null;
let classBoardCardRefs = null;
let leaderboardDisplayMode = "classic";
let currentSessionMode = "class";
let currentState = "RUNNING";
let currentConfig = null;
let currentLocale = "en-US";
let currentClassPlan = { segments: [{ kind: "work", duration_sec: 3600 }] };
let currentClassLeaderboard = {};
let raceStartTime = Date.now();
let leaderboardRenderScheduled = false;
let pendingLeaderboardData = null;
let __wallNs = 0n;
const __hrtime = process.hrtime;
function __timed(fn) {
  const start = __hrtime.bigint();
  fn();
  __wallNs += __hrtime.bigint() - start;
}
let __rafCallbacks = [];
function requestAnimationFrame(cb) { __rafCallbacks.push(cb); }
${fns}
function handleTelemetryPayload(data) ${coalesceBranch}
globalThis.__run = function (events, stations) {
  const byStation = stations.map((s) => s);
  for (const ev of events) {
    if (ev.type === "telemetry") {
      byStation[ev.station] = __tickStation(byStation[ev.station], ev.tick);
      const payload = {};
      byStation.forEach((s) => { payload[s.node_id] = s; });
      __timed(() => handleTelemetryPayload(payload));
    } else if (ev.type === "classClock") {
      __timed(() => renderClassBoardFromState());
    } else if (ev.type === "frame") {
      const due = __rafCallbacks;
      __rafCallbacks = [];
      due.forEach((cb) => __timed(() => cb()));
    }
  }
  return { fullRebuilds: __counters.fullRebuilds, cardWrites: __counters.cardWrites, boundingRectCalls: __counters.boundingRectCalls, wallNs: __wallNs.toString() };
};
`;
  return runInSandbox(script, counters, stationCount, {});
}

// --- AFTER: class mode, crossing several segment boundaries -- proves the
// last-ten-seconds NEXT-segment announcement (computeUpcomingSegmentAnnouncementForPatch,
// wired through applyClassBoardIncrementalUpdate) does not reintroduce
// per-second full rebuilds. Segment changes themselves are expected to
// rebuild (new markup: hero band, timeline, station target indicators)
// -- what must NOT happen is the announcement crossing its own threshold,
// mid-segment, triggering an extra rebuild on top of that. -------------

function runAfterClassSegmentBoundaries(afterSource, stationCount) {
  const counters = { fullRebuilds: 0, cardWrites: 0, boundingRectCalls: 0 };
  const coalesceBranch = extractBraced(
    afterSource,
    "if (data && Object.keys(data).length > 0)"
  );
  const fns = [
    "resetLeaderboardCardCache",
    "resetClassBoardCardCache",
    "classTargetStatusForPatch",
    "computeClassProgressPercentForPatch",
    "computeUpcomingSegmentAnnouncementForPatch",
    "classSegmentKindLabelForPatch",
    "buildClassStationSignature",
    "captureClassBoardRefs",
    "applyClassBoardIncrementalUpdate",
    "classClockAt",
    "toClockShape",
    "buildClassBoardHtml",
    "renderClassBoardFromState",
    "renderLeaderboard",
  ]
    .map((name) => extractFunction(afterSource, name))
    .join("\n");
  const script = `
${SHARED_STUBS}
const Intl = { NumberFormat: function () { return { format: (n) => String(n) }; } };
function formatClock(ms) { const s = Math.max(0, Math.floor(metricNumber(ms) / 1000)); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }
${makeClassBoardFakeDom()}
function detectAthleteFinishes() {}
function smoothMetricNumber(key, target) { return Number(target) || 0; }
function updateClassSegmentCue() {}
let leaderboardNodes = [];
let teamLeaderboardRows = [];
let leaderboardRankByNode = new Map();
let leaderboardCardRefs = new Map();
let leaderboardCardSignature = null;
let classBoardCardRefs = null;
let leaderboardDisplayMode = "classic";
let currentSessionMode = "class";
let currentState = "RUNNING";
let currentConfig = null;
let currentLocale = "en-US";
let currentClassPlan = ${JSON.stringify(SEGMENT_BOUNDARY_PLAN)};
let currentClassLeaderboard = {};
// Controllable simulated clock, overriding Date.now() -- the only
// wall-clock read among the functions extracted above is inside
// renderClassBoardFromState. Driving it from the event schedule (below)
// instead of real elapsed wall-clock time keeps the several
// segment-boundary crossings in this window deterministic.
let raceStartTime = 1000000;
let __simulatedNowMs = raceStartTime;
const Date = { now: () => __simulatedNowMs };
let leaderboardRenderScheduled = false;
let pendingLeaderboardData = null;
let __wallNs = 0n;
const __hrtime = process.hrtime;
function __timed(fn) {
  const start = __hrtime.bigint();
  fn();
  __wallNs += __hrtime.bigint() - start;
}
let __rafCallbacks = [];
function requestAnimationFrame(cb) { __rafCallbacks.push(cb); }
${fns}
function handleTelemetryPayload(data) ${coalesceBranch}
globalThis.__run = function (events, stations) {
  const byStation = stations.map((s) => s);
  for (const ev of events) {
    __simulatedNowMs = raceStartTime + ev.t;
    if (ev.type === "telemetry") {
      byStation[ev.station] = __tickStation(byStation[ev.station], ev.tick);
      const payload = {};
      byStation.forEach((s) => { payload[s.node_id] = s; });
      __timed(() => handleTelemetryPayload(payload));
    } else if (ev.type === "classClock") {
      __timed(() => renderClassBoardFromState());
    } else if (ev.type === "frame") {
      const due = __rafCallbacks;
      __rafCallbacks = [];
      due.forEach((cb) => __timed(() => cb()));
    }
  }
  return { fullRebuilds: __counters.fullRebuilds, cardWrites: __counters.cardWrites, boundingRectCalls: __counters.boundingRectCalls, wallNs: __wallNs.toString() };
};
`;
  return runInSandbox(script, counters, stationCount, {
    totalSeconds: SEGMENT_BOUNDARY_SIMULATED_SECONDS,
  });
}

// --- sandbox execution --------------------------------------------------

function runInSandbox(script, counters, stationCount, opts) {
  const events = buildSchedule(stationCount, opts.totalSeconds).filter((ev) => {
    if (opts.skipClassClock && ev.type === "classClock") return false;
    if (opts.skipFrames && ev.type === "frame") return false;
    return true;
  });
  const stations = makeStations(stationCount);

  const sandbox = {
    __counters: counters,
    __tickStation: tickStation,
    console,
    process,
    Map,
    Set,
    JSON,
    Math,
    Object,
    Array,
    Number,
    String,
    Boolean,
    Date,
    RegExp,
    Infinity,
  };
  vm.createContext(sandbox);
  vm.runInContext(script, sandbox, { timeout: 20000 });
  return sandbox.__run(events, stations);
}

// --- reporting ------------------------------------------------------------

function perSecond(raw, windowSeconds) {
  return raw / (windowSeconds || SIMULATED_SECONDS);
}

function formatRow(label, result, windowSeconds) {
  const rebuildsPerSec = perSecond(result.fullRebuilds, windowSeconds).toFixed(2);
  const writesPerSec = perSecond(result.cardWrites, windowSeconds).toFixed(1);
  const rectsPerSec = perSecond(result.boundingRectCalls, windowSeconds).toFixed(2);
  const msPerSec = (
    Number(result.wallNs) / 1e6 / (windowSeconds || SIMULATED_SECONDS)
  ).toFixed(3);
  return `${label.padEnd(28)} rebuilds/s=${rebuildsPerSec.padStart(8)}  cardWrites/s=${writesPerSec.padStart(9)}  getBoundingClientRect/s=${rectsPerSec.padStart(8)}  renderMs/s=${msPerSec.padStart(9)}`;
}

function main() {
  const [beforePath, afterPath] = process.argv.slice(2);
  if (!beforePath || !afterPath) {
    console.error(
      "usage: node scripts/measure_dashboard_render_cost.mjs <before.html> <after.html>"
    );
    process.exit(1);
  }
  const beforeSource = readFileSync(beforePath, "utf8");
  const afterSource = readFileSync(afterPath, "utf8");

  console.log(`Simulated window: ${SIMULATED_SECONDS}s, telemetry ${TELEMETRY_HZ}Hz/station, 60fps paint cadence.\n`);

  for (const stationCount of STATION_COUNTS) {
    console.log(`=== ${stationCount} stations, RACE mode ===`);
    console.log(formatRow("before", runBeforeRace(beforeSource, stationCount)));
    console.log(formatRow("after", runAfterRace(afterSource, stationCount)));
    console.log("");

    console.log(`=== ${stationCount} stations, CLASS mode ===`);
    console.log(formatRow("before", runBeforeClass(beforeSource, stationCount)));
    console.log(formatRow("after", runAfterClass(afterSource, stationCount)));
    console.log("");
  }

  console.log(
    `Segment-boundary window: ${SEGMENT_BOUNDARY_SIMULATED_SECONDS}s, ` +
      `plan segments: ${SEGMENT_BOUNDARY_PLAN.segments.map((s) => s.duration_sec).join("+")}s ` +
      `(total ${SEGMENT_BOUNDARY_PLAN.segments.reduce((sum, s) => sum + s.duration_sec, 0)}s, plan finishes inside the window).\n`
  );
  for (const stationCount of STATION_COUNTS) {
    console.log(
      `=== ${stationCount} stations, CLASS mode, crossing segment boundaries ===`
    );
    console.log(
      formatRow(
        "after",
        runAfterClassSegmentBoundaries(afterSource, stationCount),
        SEGMENT_BOUNDARY_SIMULATED_SECONDS
      )
    );
    console.log("");
  }
}

main();
