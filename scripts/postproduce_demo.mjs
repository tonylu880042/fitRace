// Post-production pass over already-recorded, already-verified English demo
// clips. This script does NOT drive the product, does NOT start the hub,
// and does NOT touch record_demo_videos.mjs / check_demo_videos.mjs or
// their raw output. It only assembles a new chaptered cut with a full
// audio mix, for one of two cuts selected by FITRACE_DEMO_CUT:
//
//   default (unset or anything other than "class"): the 14-scene cut in
//     output/videos/en/s01_intro.webm .. s14_outro.webm, unchanged from
//     before class-cut support existed. Reads six chapter cards, SFX
//     (countdown beeps in S06, a champion cue in S08), and the
//     SPEC_SCENE_STARTS_SEC drift sanity net exactly as they always did.
//   class: the 11-scene Class Mode cut in
//     output/videos/en-class/c01_switch_mode.webm .. c11_outro.webm (see
//     record_demo_videos.mjs's mainClassCut()). Reads two chapter cards, no
//     SFX (the class cut has no countdown/champion cues), and narration
//     parsed straight from the C1-C10 prose "旁白" lines in DEMO_SCRIPT.md's
//     "課程模式劇本（Class Mode）" section (C11 has no 旁白, only 字幕 - see
//     loadClassCutNarration()). No SPEC_SCENE_STARTS_SEC-style drift net
//     exists for this cut yet (no prior verified recording to measure a
//     baseline from).
//
// Shared machinery either way:
//   1. Chapter cards, rendered as inline HTML in headless Chromium (same
//      Playwright runtime record_demo_videos.mjs uses) and screenshot to
//      PNG, then turned into short h264 clips.
//   2. A narration track (say -v "Evan (Enhanced)"), one line per scene,
//      placed at that scene's start time in the NEW (card-inserted)
//      timeline.
//   3. A looped, crossfaded, sidechain-ducked music bed under all of it.
//
// Output: output/videos/en/demo_full_chaptered.mp4 (default cut) or
// output/videos/en-class/class_full.mp4 (class cut) - the existing
// demo_full_4min.mp4 / class_demo_raw.mp4 raw concats are never touched.
//
// Usage:
//   node scripts/postproduce_demo.mjs                              # default cut
//   FITRACE_DEMO_CUT=class node scripts/postproduce_demo.mjs       # class cut
//   node scripts/postproduce_demo.mjs --skip-build # re-run checks only,
//     reusing the previous run's output + manifest (for fast iteration).
//     Honors FITRACE_DEMO_CUT the same way.

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdir, rm, readFile, writeFile, stat, open } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium } = require("/Users/tunghunglu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");

const ROOT = process.cwd();
// "final" is a third cut: the English re-edit built from the CLEAN
// (no-burned-caption) recordings in output/videos/en-clean/ (see
// record_demo_videos.mjs's FITRACE_DEMO_NO_OVERLAY switch), with captions
// composited back on as independently-timed layers instead of being part of
// the recorded pixels. It writes output/videos/en/demo_full_final.mp4 -
// alongside, not over, the existing demo_full_chaptered.mp4 - so EN_DIR
// (the OUTPUT-side directory) is the same output/videos/en/ used by the
// default cut; FINAL_SRC_DIR below is the separate SOURCE-side directory
// this cut reads its clips from. See the "FINAL CUT" section near the
// bottom of this file for everything specific to it.
// "class_final" is a fourth cut, the class-mode analogue of "final": the
// re-edited English Class Mode demo, built from the CLEAN (no-burned-
// caption) recordings in output/videos/en-class-clean/
// (record_demo_videos.mjs's FITRACE_DEMO_CUT=class + FITRACE_DEMO_NO_OVERLAY
// switch), with three independently-timed caption layers composited back on
// in post exactly like "final" does. Writes
// output/videos/en-class/class_full_final.mp4 - alongside, not over, the
// existing class_full.mp4 - so EN_DIR (the OUTPUT-side directory) is the
// same output/videos/en-class/ the "class" cut already writes to;
// CLASS_FINAL_SRC_DIR below is the separate SOURCE-side directory this cut
// reads its clips from. See the "CLASS FINAL CUT" section near the bottom
// of this file for everything specific to it.
const CUT = process.env.FITRACE_DEMO_CUT === "class" ? "class"
  : process.env.FITRACE_DEMO_CUT === "final" ? "final"
  : process.env.FITRACE_DEMO_CUT === "class_final" ? "class_final"
  : "default";
const EN_DIR = (CUT === "class" || CUT === "class_final") ? path.join(ROOT, "output/videos/en-class") : path.join(ROOT, "output/videos/en");
const TMP_DIR = path.join(EN_DIR, CUT === "final" ? ".postproduce_final_tmp" : CUT === "class_final" ? ".postproduce_class_final_tmp" : ".postproduce_tmp");
const MANIFEST_PATH = path.join(TMP_DIR, "manifest.json");
const DEMO_SCRIPT_PATH = path.join(ROOT, "DEMO_SCRIPT.md");
const AUDIO_DIR = path.join(ROOT, "hub_server/static/audio");
const BED_PATH = path.join(ROOT, "output/videos/.assets/bed.mp3");
const OUTPUT_PATH = CUT === "class" ? path.join(EN_DIR, "class_full.mp4")
  : CUT === "final" ? path.join(EN_DIR, "demo_full_final.mp4")
  : CUT === "class_final" ? path.join(EN_DIR, "class_full_final.mp4")
  : path.join(EN_DIR, "demo_full_chaptered.mp4");
const FINAL_SRC_DIR = path.join(ROOT, "output/videos/en-clean");
const CLASS_FINAL_SRC_DIR = path.join(ROOT, "output/videos/en-class-clean");
const FFMPEG = "/opt/homebrew/bin/ffmpeg";
const FFPROBE = "/opt/homebrew/bin/ffprobe";
const TTS_VOICE = "Evan (Enhanced)";

const SKIP_BUILD = process.argv.includes("--skip-build");

// ---------------------------------------------------------------------------
// Fixed inputs
// ---------------------------------------------------------------------------

// S1..S14 -> recorded clip filenames, in scene order. Matches
// check_demo_videos.mjs's SCENE_FILENAMES (duplicated here rather than
// imported - that script is a plain executable, not a module, and is not to
// be modified for this job).
const SCENE_FILES = [
  "s01_intro.webm",
  "s02_edge_nodes.webm",
  "s03_station_assignment.webm",
  "s04_signup.webm",
  "s05_race_rules.webm",
  "s06_start_race.webm",
  "s07_live_race.webm",
  "s08_finish.webm",
  "s09_admin_overview.webm",
  "s10_network.webm",
  "s11_software_update.webm",
  "s12_power_controls.webm",
  "s13_support.webm",
  "s14_outro.webm",
].map((file, i) => ({ id: `s${String(i + 1).padStart(2, "0")}`, file }));

// C1..C11 -> recorded clip filenames, in scene order. Matches
// check_demo_videos.mjs's CLASS_SCENE_FILENAMES and
// record_demo_videos.mjs's mainClassCut() (sceneC01..sceneC11) exactly.
const CLASS_SCENE_FILES = [
  "c01_switch_mode.webm",
  "c02_plan_editor.webm",
  "c03_repeat_intervals.webm",
  "c04_save_plan.webm",
  "c05_saved_class_library.webm",
  "c06_start_class.webm",
  "c07_live_class.webm",
  "c08_rest_changeover.webm",
  "c09_high_intensity.webm",
  "c10_stop_class.webm",
  "c11_outro.webm",
].map((file, i) => ({ id: `c${String(i + 1).padStart(2, "0")}`, file }));

// Chapter cards: rendered before the given scene id. Order matches the spec
// table exactly (Card 1..6).
const CARDS = [
  { before: "s01", kicker: "01 / GET READY", headline: "Fast Setup. Ready to Race.", sub: "Discover equipment and assign stations in minutes." },
  { before: "s04", kicker: "02 / ATHLETE SIGNUP", headline: "Join the Race from Your Phone.", sub: "" },
  { before: "s05", kicker: "03 / RACE CONTROL", headline: "Configure. Start. Compete.", sub: "" },
  { before: "s07", kicker: "04 / LIVE RACE", headline: "Real-Time Performance & Ranking", sub: "" },
  { before: "s09", kicker: "05 / SYSTEM ADMIN", headline: "Everything Managed in One Console", sub: "" },
  { before: "s12", kicker: "06 / OPERATIONS & SUPPORT", headline: "Safe Control. Fast Maintenance.", sub: "" },
];

// Two chapter cards for the class cut, per the job spec: before C1 and
// before C6. Kicker/headline split at the em dash of the spec's own literal
// card text ("07 / CLASS MODE — Coach-Led Training, Same Screen." and
// "08 / LIVE CLASS — The Plan Runs Itself.").
const CLASS_CARDS = [
  { before: "c01", kicker: "07 / CLASS MODE", headline: "Coach-Led Training, Same Screen.", sub: "" },
  { before: "c06", kicker: "08 / LIVE CLASS", headline: "The Plan Runs Itself.", sub: "" },
];

const CARD_DURATION_SEC = 1.2;

// Sanity net: the spec's own measured (pre-card) scene start times, so a
// silent drift in the source clips (re-recorded, trimmed, whatever) gets
// caught loudly instead of quietly shipping a mistimed cut. Tolerance is
// generous (0.2s) because the spec's numbers are rounded to one decimal.
// Default cut only - see buildTimeline()'s `cut` parameter. There is no
// equivalent baseline for the class cut yet (no prior verified recording to
// measure one from); its own tolerance net is check_demo_videos.mjs's
// +/-25% per-scene duration check instead.
//
// Remeasured from output/videos/en/s01..s14 after re-recording the English
// cut at 1920x1080 (was 1280x720) with S6's countdown-sequencing fix (the
// dashboard now opens first and Start Race is clicked from a second,
// unrecorded page, which shifts S6's own duration and everything after it
// by roughly a second - see record_demo_videos.mjs's sceneS06). The old
// numbers here were measured from the pre-fix, 720p recording and are
// stale; DEMO_SCRIPT.md's own per-scene target seconds (checked by
// check_demo_videos.mjs, +/-25%) are unaffected and were not touched.
const SPEC_SCENE_STARTS_SEC = {
  s01: 0.0, s02: 14.0, s03: 30.3, s04: 46.4, s05: 67.4, s06: 85.6, s07: 99.0,
  s08: 130.8, s09: 151.6, s10: 164.0, s11: 176.2, s12: 191.8, s13: 207.2, s14: 213.4,
};
const SPEC_TOTAL_SEC = 227.2;
const SPEC_TOLERANCE_SEC = 0.2;

// Narration is placed at each scene's own start time (no lead-in), per spec.
const NARRATION_LEAD_IN_SEC = 0;

// S06 countdown SFX offsets (seconds from the START of s06_start_race.webm
// itself, not the new timeline).
//
// record_demo_videos.mjs's sceneS06 used to click Start Race on gameAdmin
// and only THEN navigate to the dashboard - by the time that page loaded
// and its websocket reconnected, the countdown-start broadcast (sent at
// the instant of the click) had already been missed, so the on-screen
// "3/2/1/GO" card never rendered in the recording at all. That has been
// fixed: the dashboard now opens first (recorded page, websocket already
// live) and Start Race is clicked from a second, unrecorded page, so the
// countdown card genuinely renders on screen now.
//
// Anchor is therefore direct, not backward-derived: frame-by-frame
// inspection of the CURRENT s06_start_race.webm (native 25fps, one frame =
// 0.04s) shows the "3" card is absent at clip t=4.20s and present at
// t=4.24s - so local t=0 (countdown start) is clip t=4.24s, accurate to
// one frame. This was cross-checked three independent ways against the
// SAME clip and all agree to within one frame:
//   - "1" card: absent at t=5.76s, present at t=5.80s - predicted
//     4.24+1.56=5.80s. Exact match.
//   - "GO" card: absent at t=6.52s, present at t=6.56s - predicted
//     4.24+2.34=6.58s. Within one frame.
//   - STATUS flips "Ready" -> "Running": absent at t=7.32s, present at
//     t=7.36s - predicted via RACE_START_COUNTDOWN_DURATION_MS=3120ms
//     (hub_server/infrastructure/fastapi/app.py's countdown-start endpoint,
//     matching the dashboard's own showRaceCountdownOverlay() step
//     schedule in hub_server/static/index.html) as 4.24+3.12=7.36s. Exact
//     match.
// Re-measure this anchor (and the cross-check) again if S06 is
// re-recorded - see checkS06SfxTimingConsistency() below, which fails the
// build automatically if a future recording drifts far enough that this
// constant and the measured Ready->Running frame disagree.
const S06_LOCAL_T0_SEC = 4.24;
const S06_SFX_OFFSETS_SEC = {
  countdown_3: S06_LOCAL_T0_SEC + 0.0,
  countdown_2: S06_LOCAL_T0_SEC + 0.78,
  countdown_1: S06_LOCAL_T0_SEC + 1.56,
  countdown_go: S06_LOCAL_T0_SEC + 2.34,
};
// Race-start countdown duration used by both the hub and the dashboard
// (see the comment above) - shared with checkS06SfxTimingConsistency().
const RACE_START_COUNTDOWN_DURATION_MS = 3120;

// S08 champion SFX offset (seconds from the start of s08_finish.webm).
// s08_finish.webm was re-recorded (1920x1080) along with every other
// scene, so its old anchor could not simply be assumed to still hold -
// re-measured the same way: frame-by-frame inspection (native 25fps) of
// the CURRENT clip around the podium reveal. Silver/bronze cards and the
// "FINAL RESULTS" title are present by t=3.8s; the gold "champion"
// (place-1) card is absent at t=4.92s and first visible (mid fade-in) at
// t=4.96s - matching the place-2/place-3 CSS animation delays in
// index.html's .podium-overlay-card rules, which give place-1 a longer
// delay than place-2/place-3.
const S08_CHAMPION_OFFSET_SEC = 4.96;

const BED_GAIN = 0.26; // linear gain applied to the music bed before ducking
const VOICE_MIX_GAIN = 1.0; // linear gain applied to voice+SFX before the final mix
// macOS `say` output is naturally quiet and inconsistent line-to-line
// (measured mean ~-18dB on raw aiff). Single-pass loudnorm brings every
// narration line up to a consistent, clearly-dialog-forward level before
// it's delayed and mixed; SFX cues are left at their native level (short
// transient hits, not dialog).
const NARRATION_LOUDNORM = "loudnorm=I=-10:TP=-1:LRA=6:print_format=none";

// ---------------------------------------------------------------------------
// Small process/ffmpeg helpers
// ---------------------------------------------------------------------------

function run(cmd, args, { label } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString()));
    child.stderr.on("data", (d) => (stderr += d.toString()));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve({ stdout, stderr });
      else reject(new Error(`${label || cmd} exited ${code}\n${cmd} ${args.join(" ")}\n${stderr.slice(-4000)}`));
    });
  });
}

function ffmpeg(args, label) {
  return run(FFMPEG, ["-y", ...args], { label: label || "ffmpeg" });
}

async function ffprobeDurationSec(file) {
  const { stdout } = await run(FFPROBE, [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "csv=p=0",
    file,
  ], { label: "ffprobe duration" });
  const value = Number(stdout.trim());
  if (!Number.isFinite(value)) throw new Error(`ffprobe returned no duration for ${file}: "${stdout.trim()}"`);
  return value;
}

async function ffprobeJSON(file, extraArgs = []) {
  const { stdout } = await run(FFPROBE, [
    "-v", "error",
    "-print_format", "json",
    ...extraArgs,
    file,
  ], { label: "ffprobe json" });
  return JSON.parse(stdout);
}

// ---------------------------------------------------------------------------
// DEMO_SCRIPT.md parsing - read the narration text, never retype it.
// ---------------------------------------------------------------------------

async function loadEnglishCutNarration() {
  const text = await readFile(DEMO_SCRIPT_PATH, "utf8");
  const lines = text.split("\n");
  const headingIdx = lines.findIndex((l) => l.trim() === "## English cut (EN)");
  if (headingIdx === -1) {
    throw new Error(`Could not find "## English cut (EN)" heading in ${DEMO_SCRIPT_PATH}`);
  }
  const rows = [];
  for (const line of lines.slice(headingIdx)) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("|")) {
      if (rows.length > 0) break; // left the table
      continue;
    }
    const cells = trimmed
      .split("|")
      .map((c) => c.trim())
      .filter((_, i, arr) => i > 0 && i < arr.length - 1);
    if (cells.length < 3) continue;
    const m = cells[0].match(/^S(\d+)$/);
    if (!m) continue; // header row ("# | On-screen overlay | Narration") and the --- separator both fail this
    rows.push({
      id: `s${m[1].padStart(2, "0")}`,
      index: Number(m[1]),
      overlay: cells[1],
      narration: cells[2],
    });
  }
  if (rows.length !== 14) {
    throw new Error(`Expected 14 narration rows (S1-S14) in the English cut table, found ${rows.length}`);
  }
  rows.sort((a, b) => a.index - b.index);
  return rows;
}

// Class cut's narration source: DEMO_SCRIPT.md's "課程模式劇本（Class
// Mode）" section has no English-cut TABLE like the default cut's - the
// narration is already English, embedded prose under each "## C<N> — ..."
// heading as a "- **旁白**：..." bullet (with "- **字幕**：`...`" alongside
// it for the on-screen overlay, kept here only as a cross-check, not used
// downstream). The section's own outro ("## 收尾 · ...") has a 字幕 but no
// 旁白 at all - the closing card is silent over the music bed - so this
// legitimately returns 10 rows (C1-C10), not 11; synthesizeNarration()
// below only requires a matching scene to exist for scenes IN the list, so
// C11 simply gets no narration line placed for it.
async function loadClassCutNarration() {
  const text = await readFile(DEMO_SCRIPT_PATH, "utf8");
  const lines = text.split("\n");
  const sectionIdx = lines.findIndex((l) => l.trim() === "# 課程模式劇本（Class Mode）");
  if (sectionIdx === -1) {
    throw new Error(`Could not find "# 課程模式劇本（Class Mode）" heading in ${DEMO_SCRIPT_PATH}`);
  }
  const rows = [];
  let currentId = null;
  for (const line of lines.slice(sectionIdx + 1)) {
    const trimmed = line.trim();
    if (trimmed.startsWith("## 課程模式自動化錄製分鏡表")) break; // left the prose section into the shot table
    const headingMatch = trimmed.match(/^## C(\d+)\s+—/);
    if (headingMatch) {
      // Zero-padded to match CLASS_SCENE_FILES's ids ("c01".."c11", not
      // "c1".."c11") - synthesizeNarration() below looks scenes up by
      // exact id, so this must agree with buildTimeline()'s scene ids.
      currentId = `c${headingMatch[1].padStart(2, "0")}`;
      continue;
    }
    if (!currentId) continue;
    const narrationMatch = trimmed.match(/^-\s*\*\*旁白\*\*[:：]\s*(.+)$/);
    if (narrationMatch) {
      rows.push({ id: currentId, index: Number(currentId.slice(1)), narration: narrationMatch[1].trim() });
      currentId = null; // one 旁白 line per section - stop matching further bullets in it
    }
  }
  if (rows.length !== 10) {
    throw new Error(`Expected 10 narration rows (C1-C10; C11 has no 旁白) in the class-mode section, found ${rows.length}`);
  }
  rows.sort((a, b) => a.index - b.index);
  return rows;
}

// ---------------------------------------------------------------------------
// Timeline: interleave the chapter cards with the scenes and compute every
// segment's start offset in the NEW (card-inserted) timeline. Shared by
// both cuts - sceneFiles/cards/checkDrift select which.
// ---------------------------------------------------------------------------

async function buildTimeline(sceneFiles, cards, { checkDrift } = { checkDrift: true }) {
  const cardsByBefore = new Map(cards.map((c) => [c.before, c]));
  const segments = [];
  for (const scene of sceneFiles) {
    const card = cardsByBefore.get(scene.id);
    if (card) {
      segments.push({ type: "card", id: `card_${scene.id}`, card, durationSec: CARD_DURATION_SEC });
    }
    const filePath = path.join(EN_DIR, scene.file);
    const durationSec = await ffprobeDurationSec(filePath);
    segments.push({ type: "scene", id: scene.id, file: filePath, durationSec });
  }

  let cursor = 0;
  for (const seg of segments) {
    seg.startSec = cursor;
    cursor += seg.durationSec;
  }
  const totalSec = cursor;

  if (!checkDrift) {
    return { segments, totalSec, driftReport: [] };
  }

  // Sanity check against the spec's own (pre-card) measured scene starts:
  // recompute offsets with the cards removed and compare.
  let rawCursor = 0;
  const rawStarts = {};
  for (const seg of segments) {
    if (seg.type !== "scene") continue;
    rawStarts[seg.id] = rawCursor;
    rawCursor += seg.durationSec;
  }
  const driftReport = [];
  for (const [id, specStart] of Object.entries(SPEC_SCENE_STARTS_SEC)) {
    const actual = rawStarts[id];
    const drift = Math.abs(actual - specStart);
    driftReport.push({ id, specStart, actual, drift, ok: drift <= SPEC_TOLERANCE_SEC });
  }
  const rawTotalDrift = Math.abs(rawCursor - SPEC_TOTAL_SEC);
  driftReport.push({ id: "TOTAL", specStart: SPEC_TOTAL_SEC, actual: rawCursor, drift: rawTotalDrift, ok: rawTotalDrift <= SPEC_TOLERANCE_SEC });
  const failedDrift = driftReport.filter((r) => !r.ok);
  if (failedDrift.length > 0) {
    const detail = failedDrift.map((r) => `${r.id}: spec=${r.specStart.toFixed(1)}s actual=${r.actual.toFixed(2)}s drift=${r.drift.toFixed(2)}s`).join("; ");
    throw new Error(`Scene timing drifted from DEMO_SCRIPT.md's spec beyond ${SPEC_TOLERANCE_SEC}s tolerance: ${detail}`);
  }

  return { segments, totalSec, driftReport };
}

// ---------------------------------------------------------------------------
// Chapter cards: render to PNG via headless Chromium, then to a short h264
// clip via ffmpeg.
// ---------------------------------------------------------------------------

function cardHtml({ kicker, headline, sub }) {
  const subHtml = sub ? `<div class="sub">${escapeHtml(sub)}</div>` : "";
  // Canvas is 1920x1080 (was 1280x720) - every px dimension below is scaled
  // by the same 1.5x (1920/1280 == 1080/720) as the recording resolution so
  // the card keeps its original proportions instead of the same absolute
  // type/margins reading small on the bigger frame.
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1920px; height: 1080px; background: #09090b; overflow: hidden; }
  body {
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 0 165px;
    font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
    background:
      radial-gradient(1650px 1050px at 18% 34%, rgba(226,255,59,0.10), transparent 60%),
      #09090b;
  }
  .rule { width: 96px; height: 6px; background: #e2ff3b; margin-bottom: 42px; }
  .kicker {
    color: #e2ff3b;
    font-size: 32px;
    font-weight: 800;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    margin-bottom: 33px;
  }
  .headline {
    color: #f7f7f8;
    font-size: 96px;
    font-weight: 800;
    line-height: 1.12;
    letter-spacing: -0.01em;
    max-width: 1470px;
  }
  .sub {
    margin-top: 36px;
    color: rgba(247,247,248,0.72);
    font-size: 39px;
    font-weight: 500;
    max-width: 1140px;
    line-height: 1.4;
  }
  .frame {
    position: absolute;
    inset: 0;
    border: 1px solid rgba(226,255,59,0.22);
    pointer-events: none;
  }
</style></head>
<body>
  <div class="frame"></div>
  <div class="rule"></div>
  <div class="kicker">${escapeHtml(kicker)}</div>
  <div class="headline">${escapeHtml(headline)}</div>
  ${subHtml}
</body></html>`;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

async function renderCards(segments) {
  const cardSegments = segments.filter((s) => s.type === "card");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    for (const seg of cardSegments) {
      await page.setContent(cardHtml(seg.card));
      const pngPath = path.join(TMP_DIR, `${seg.id}.png`);
      await page.screenshot({ path: pngPath });
      seg.pngPath = pngPath;
    }
  } finally {
    await browser.close();
  }
  for (const seg of cardSegments) {
    const clipPath = path.join(TMP_DIR, `${seg.id}_raw.mp4`);
    await ffmpeg([
      "-loop", "1",
      "-i", seg.pngPath,
      "-t", String(CARD_DURATION_SEC),
      "-r", "30",
      "-vf", `fade=t=in:st=0:d=0.18,fade=t=out:st=${(CARD_DURATION_SEC - 0.22).toFixed(3)}:d=0.22,format=yuv420p`,
      "-c:v", "libx264",
      "-preset", "veryfast",
      "-crf", "18",
      "-an",
      clipPath,
    ], `card render ${seg.id}`);
    seg.file = clipPath;
  }
}

// ---------------------------------------------------------------------------
// Video: normalize every segment (scene webm or card mp4) to a common
// 1920x1080/30fps/yuv420p/h264 stream, then concat with -c:v copy so the
// picture is never re-encoded a second time.
// ---------------------------------------------------------------------------

async function normalizeSegment(seg, index) {
  const dest = path.join(TMP_DIR, `norm_${String(index).padStart(2, "0")}_${seg.id}.mp4`);
  await ffmpeg([
    "-i", seg.file,
    "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p",
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "20",
    "-an",
    dest,
  ], `normalize ${seg.id}`);
  return dest;
}

async function buildVideoOnly(segments) {
  const normFiles = [];
  let i = 0;
  for (const seg of segments) {
    normFiles.push(await normalizeSegment(seg, i));
    i += 1;
  }
  const listFile = path.join(TMP_DIR, "concat_list.txt");
  const listContent = normFiles.map((f) => `file '${f.replace(/'/g, "'\\''")}'`).join("\n");
  await writeFile(listFile, listContent, "utf8");

  const videoOnlyPath = path.join(TMP_DIR, "video_only.mp4");
  await ffmpeg([
    "-f", "concat",
    "-safe", "0",
    "-i", listFile,
    "-c", "copy",
    videoOnlyPath,
  ], "concat video");
  return videoOnlyPath;
}

// ---------------------------------------------------------------------------
// Audio: narration (TTS), product SFX, and a ducked/crossfaded music bed.
// ---------------------------------------------------------------------------

async function synthesizeNarration(narrationRows, segments) {
  const sceneStart = new Map(segments.filter((s) => s.type === "scene").map((s) => [s.id, s]));
  const lines = [];
  for (const row of narrationRows) {
    const scene = sceneStart.get(row.id);
    if (!scene) throw new Error(`No scene segment found for narration row ${row.id}`);
    const aiffPath = path.join(TMP_DIR, `narration_${row.id}.aiff`);
    await run("/usr/bin/say", ["-v", TTS_VOICE, "-o", aiffPath, row.narration], { label: `say ${row.id}` });
    const lineDurationSec = await ffprobeDurationSec(aiffPath);
    const budgetSec = scene.durationSec - NARRATION_LEAD_IN_SEC;
    const fits = lineDurationSec <= budgetSec;
    lines.push({
      id: row.id,
      narration: row.narration,
      aiffPath,
      lineDurationSec,
      sceneDurationSec: scene.durationSec,
      leadInSec: NARRATION_LEAD_IN_SEC,
      marginSec: budgetSec - lineDurationSec,
      absoluteOffsetSec: scene.startSec + NARRATION_LEAD_IN_SEC,
      fits,
    });
  }
  const overflow = lines.filter((l) => !l.fits);
  if (overflow.length > 0) {
    const detail = overflow
      .map((l) => `${l.id}: line=${l.lineDurationSec.toFixed(2)}s > budget=${(l.sceneDurationSec - l.leadInSec).toFixed(2)}s (scene=${l.sceneDurationSec.toFixed(2)}s, lead-in=${l.leadInSec}s)`)
      .join("; ");
    throw new Error(`Narration overflow - refusing to silently truncate: ${detail}`);
  }
  return lines;
}

function sfxCues(segments) {
  const sceneStart = new Map(segments.filter((s) => s.type === "scene").map((s) => [s.id, s.startSec]));
  const cues = [];
  for (const [name, localOffset] of Object.entries(S06_SFX_OFFSETS_SEC)) {
    cues.push({
      name,
      file: path.join(AUDIO_DIR, `${name}.wav`),
      absoluteOffsetSec: sceneStart.get("s06") + localOffset,
    });
  }
  cues.push({
    name: "champion",
    file: path.join(AUDIO_DIR, "champion.wav"),
    absoluteOffsetSec: sceneStart.get("s08") + S08_CHAMPION_OFFSET_SEC,
  });
  return cues;
}

// Builds the combined narration+SFX track, padded/trimmed to totalSec.
async function buildVoiceSfxTrack(lines, cues, totalSec) {
  const inputs = [];
  const delayLabels = [];
  for (const line of lines) {
    inputs.push(line.aiffPath);
    delayLabels.push(Math.round(line.absoluteOffsetSec * 1000));
  }
  for (const cue of cues) {
    inputs.push(cue.file);
    delayLabels.push(Math.round(cue.absoluteOffsetSec * 1000));
  }

  const filterParts = [];
  const mixLabels = [];
  inputs.forEach((_, i) => {
    const label = `v${i}`;
    const isNarration = i < lines.length;
    const preFilter = isNarration ? `${NARRATION_LOUDNORM},` : "";
    filterParts.push(
      `[${i}:a]${preFilter}aformat=sample_rates=44100:channel_layouts=stereo,adelay=delays=${delayLabels[i]}:all=1[${label}]`,
    );
    mixLabels.push(`[${label}]`);
  });
  filterParts.push(`${mixLabels.join("")}amix=inputs=${inputs.length}:normalize=0:duration=longest[mixed]`);
  filterParts.push(`[mixed]atrim=0:${totalSec},apad=whole_dur=${totalSec}[voice_sfx]`);

  const outPath = path.join(TMP_DIR, "voice_sfx.wav");
  const args = [];
  for (const input of inputs) args.push("-i", input);
  args.push(
    "-filter_complex", filterParts.join(";"),
    "-map", "[voice_sfx]",
    "-ar", "44100",
    "-ac", "2",
    outPath,
  );
  await ffmpeg(args, "build voice+sfx track");
  return outPath;
}

// Loops the music bed to totalSec with a crossfade at the loop seam, fades
// in/out, and applies BED_GAIN.
async function buildBedTrack(totalSec) {
  const bedDurationSec = await ffprobeDurationSec(BED_PATH);
  // Choose the shortest crossfade (floor 0.5s) that still guarantees the
  // two-copy loop covers totalSec with >= 0.5s of margin, so a short bed and
  // a long video never leave the loop short.
  const rawCrossfade = 2 * bedDurationSec - (totalSec + 0.5);
  const crossfadeSec = Math.min(3, Math.max(0.5, rawCrossfade));
  const pairLenSec = 2 * bedDurationSec - crossfadeSec;
  if (pairLenSec < totalSec) {
    throw new Error(
      `Music bed (${bedDurationSec.toFixed(1)}s) looped twice with a ${crossfadeSec.toFixed(2)}s crossfade ` +
      `only covers ${pairLenSec.toFixed(1)}s, less than the video's ${totalSec.toFixed(1)}s. Need a third loop.`,
    );
  }

  const fadeOutStart = Math.max(0, totalSec - 3);
  const outPath = path.join(TMP_DIR, "bed_full.wav");
  await ffmpeg([
    "-i", BED_PATH,
    "-i", BED_PATH,
    "-filter_complex",
    `[0:a]aformat=sample_rates=44100:channel_layouts=stereo[b0];` +
    `[1:a]aformat=sample_rates=44100:channel_layouts=stereo[b1];` +
    `[b0][b1]acrossfade=d=${crossfadeSec.toFixed(3)}:c1=tri:c2=tri[looped];` +
    `[looped]atrim=0:${totalSec},apad=whole_dur=${totalSec},` +
    `afade=t=in:st=0:d=2,afade=t=out:st=${fadeOutStart.toFixed(3)}:d=3,` +
    `volume=${BED_GAIN}[bed]`,
    "-map", "[bed]",
    "-ar", "44100",
    "-ac", "2",
    outPath,
  ], "build bed track");
  return { outPath, crossfadeSec, bedDurationSec };
}

async function duckBedUnderVoice(bedPath, voiceSfxPath) {
  const outPath = path.join(TMP_DIR, "bed_ducked.wav");
  await ffmpeg([
    "-i", bedPath,
    "-i", voiceSfxPath,
    "-filter_complex",
    "[0:a][1:a]sidechaincompress=threshold=0.008:ratio=20:attack=5:release=300:makeup=1[ducked]",
    "-map", "[ducked]",
    "-ar", "44100",
    "-ac", "2",
    outPath,
  ], "sidechain duck");
  return outPath;
}

async function mixFinalAudio(bedDuckedPath, voiceSfxPath) {
  const outPath = path.join(TMP_DIR, "final_audio.wav");
  await ffmpeg([
    "-i", bedDuckedPath,
    "-i", voiceSfxPath,
    "-filter_complex",
    `[1:a]volume=${VOICE_MIX_GAIN}[voice];` +
    "[0:a][voice]amix=inputs=2:normalize=0:duration=longest,alimiter=limit=0.95[final]",
    "-map", "[final]",
    "-ar", "44100",
    "-ac", "2",
    outPath,
  ], "final audio mix");
  return outPath;
}

async function mux(videoOnlyPath, finalAudioPath) {
  await mkdir(path.dirname(OUTPUT_PATH), { recursive: true });
  await rm(OUTPUT_PATH, { force: true });
  await ffmpeg([
    "-i", videoOnlyPath,
    "-i", finalAudioPath,
    "-map", "0:v:0",
    "-map", "1:a:0",
    "-c:v", "copy",
    "-c:a", "aac",
    "-b:a", "192k",
    "-movflags", "+faststart",
    OUTPUT_PATH,
  ], "final mux");
}

// ---------------------------------------------------------------------------
// Acceptance checks
// ---------------------------------------------------------------------------

async function checkStreamsPresent() {
  const data = await ffprobeJSON(OUTPUT_PATH, ["-show_streams"]);
  const hasVideo = data.streams.some((s) => s.codec_type === "video");
  const hasAudio = data.streams.some((s) => s.codec_type === "audio");
  const ok = hasVideo && hasAudio;
  return { name: "1. streams present (video+audio)", ok, detail: `video=${hasVideo} audio=${hasAudio}`, raw: data.streams.map((s) => `${s.codec_type}:${s.codec_name}`).join(", ") };
}

async function checkAvSync() {
  const data = await ffprobeJSON(OUTPUT_PATH, ["-show_entries", "stream=codec_type,duration:format=duration"]);
  const videoStream = data.streams.find((s) => s.codec_type === "video");
  const audioStream = data.streams.find((s) => s.codec_type === "audio");
  const videoDur = Number(videoStream?.duration ?? data.format.duration);
  const audioDur = Number(audioStream?.duration ?? data.format.duration);
  const delta = Math.abs(videoDur - audioDur);
  const ok = delta <= 0.5;
  return { name: "2. audio/video duration match (<=0.5s)", ok, detail: `video=${videoDur.toFixed(3)}s audio=${audioDur.toFixed(3)}s delta=${delta.toFixed(3)}s` };
}

async function checkVideoFormat() {
  const data = await ffprobeJSON(OUTPUT_PATH, ["-show_streams", "-select_streams", "v:0"]);
  const v = data.streams[0];
  const fps = v.r_frame_rate; // e.g. "30/1"
  const fpsValue = fps.includes("/") ? Number(fps.split("/")[0]) / Number(fps.split("/")[1]) : Number(fps);
  const ok = v.codec_name === "h264" && v.width === 1920 && v.height === 1080 && Math.abs(fpsValue - 30) < 0.01;
  return { name: "3. video is h264 1920x1080 30fps", ok, detail: `codec=${v.codec_name} ${v.width}x${v.height} fps=${fps} (${fpsValue.toFixed(3)})` };
}

async function checkTotalDuration(expectedSec) {
  const durationSec = await ffprobeDurationSec(OUTPUT_PATH);
  const delta = Math.abs(durationSec - expectedSec);
  const ok = delta <= 2;
  // Label text is descriptive only (~234s = the remeasured raw total of
  // 227.2s plus six 1.2s chapter cards) - the actual assertion below always
  // compares against the dynamically computed expectedSec, not this string.
  return { name: `4. total duration ~${expectedSec.toFixed(1)}s (+/-2s)`, ok, detail: `actual=${durationSec.toFixed(2)}s expected=${expectedSec.toFixed(2)}s delta=${delta.toFixed(2)}s` };
}

function checkNarrationFits(manifest) {
  const rows = manifest.narration.map((l) => {
    const status = l.fits ? "PASS" : "FAIL";
    return `${status}  ${l.id}  line=${l.lineDurationSec.toFixed(2)}s  scene=${l.sceneDurationSec.toFixed(2)}s  margin=${l.marginSec.toFixed(2)}s`;
  });
  const ok = manifest.narration.every((l) => l.fits);
  return { name: "5. every narration line fits inside its scene", ok, detail: rows.join("\n") };
}

async function parseEbur128Integrated(file, startSec, durationSec) {
  const { stderr } = await run(FFMPEG, [
    "-y",
    "-ss", String(startSec),
    "-t", String(durationSec),
    "-i", file,
    "-filter_complex", "ebur128=peak=true",
    "-f", "null",
    "-",
  ], { label: "ebur128" });
  const match = stderr.match(/Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+)\s*LUFS/);
  if (!match) throw new Error(`Could not parse ebur128 integrated loudness from ffmpeg output:\n${stderr.slice(-2000)}`);
  return Number(match[1]);
}

async function checkLoudnessDucking(manifest) {
  const { narrationWindow, musicOnlyWindow } = manifest.loudnessWindows;
  const narrationLufs = await parseEbur128Integrated(OUTPUT_PATH, narrationWindow.startSec, narrationWindow.durationSec);
  const musicLufs = await parseEbur128Integrated(OUTPUT_PATH, musicOnlyWindow.startSec, musicOnlyWindow.durationSec);
  const ok = narrationLufs > musicLufs;
  return {
    name: "6. ebur128 loudness: narration segment louder than music-only segment (proves ducking)",
    ok,
    detail:
      `narration window [${narrationWindow.startSec.toFixed(2)}s, +${narrationWindow.durationSec.toFixed(2)}s] (${narrationWindow.label}) = ${narrationLufs.toFixed(2)} LUFS\n` +
      `music-only window [${musicOnlyWindow.startSec.toFixed(2)}s, +${musicOnlyWindow.durationSec.toFixed(2)}s] (${musicOnlyWindow.label}) = ${musicLufs.toFixed(2)} LUFS`,
  };
}

// ---------------------------------------------------------------------------
// SFX-timing regression guards. The countdown-capture fix (see
// S06_LOCAL_T0_SEC's comment) made S06's SFX drift a real, visible bug the
// first time this pipeline ran after S06 was re-recorded: the beeps were
// placed against an anchor measured from the OLD (broken, differently-timed)
// recording, so every beep fired about a second ahead of the number it
// belonged to once the overlay actually started rendering. Nothing in the
// original 6 checks would have caught that - none of them look at where an
// SFX cue lands relative to the visual event it's supposed to sync with.
// These two checks close that gap for S06 specifically (the scene with a
// hard product constant - RACE_START_COUNTDOWN_DURATION_MS - to check
// against) so a future re-recording that shifts S06's timing again fails
// the build instead of shipping a silently desynced cut.
// ---------------------------------------------------------------------------

const S06_FILE = path.join(EN_DIR, "s06_start_race.webm");
const RUNNING_FLIP_SEARCH_WINDOW_SEC = 1.5; // either side of the predicted flip
const RUNNING_FLIP_TOLERANCE_SEC = 0.3; // frame quantization + fade-transition slack

// Finds the Ready->Running flip directly in the CURRENT s06_start_race.webm,
// with no dependency on S06_LOCAL_T0_SEC being correct: ffmpeg's
// tblend=difference+signalstats gives a per-frame "how much did this frame
// change from the previous one" score (YAVG). The flip is a full-banner
// re-render (status text, colour, confetti-free reflow) so it dwarfs the
// small single-card deltas of the "3"/"2"/"1"/"GO" swaps nearby (measured:
// peak YAVG ~6.9 at the flip vs ~1.2-2.9 for a numeral swap) - the max
// within the search window reliably lands on the flip, not a numeral swap.
async function measureS06RunningFlipSec(predictedSec, clipDurationSec) {
  const windowStart = Math.max(0, predictedSec - RUNNING_FLIP_SEARCH_WINDOW_SEC);
  const windowEnd = Math.min(clipDurationSec, predictedSec + RUNNING_FLIP_SEARCH_WINDOW_SEC);
  const { stderr } = await run(FFMPEG, [
    "-y",
    "-i", S06_FILE,
    "-vf", `select='between(t,${windowStart},${windowEnd})',tblend=all_mode=difference,signalstats,metadata=print`,
    "-an",
    "-f", "null",
    "-",
  ], { label: "s06 running-flip frame diff" });
  const ptsMatches = [...stderr.matchAll(/pts_time:([\d.]+)/g)].map((m) => Number(m[1]));
  const yavgMatches = [...stderr.matchAll(/lavfi\.signalstats\.YAVG=([\d.]+)/g)].map((m) => Number(m[1]));
  if (ptsMatches.length === 0 || ptsMatches.length !== yavgMatches.length) {
    throw new Error(
      `Could not parse ffmpeg frame-diff signal for S06 running-flip detection ` +
      `(parsed ${ptsMatches.length} pts_time, ${yavgMatches.length} YAVG values - expected a matching, non-empty pair of each)`,
    );
  }
  let bestIdx = 0;
  for (let i = 1; i < yavgMatches.length; i += 1) {
    if (yavgMatches[i] > yavgMatches[bestIdx]) bestIdx = i;
  }
  return { measuredSec: ptsMatches[bestIdx], peakYavg: yavgMatches[bestIdx], windowStart, windowEnd };
}

async function checkS06SfxAnchorMatchesMeasuredFlip() {
  const clipDurationSec = await ffprobeDurationSec(S06_FILE);
  const predictedSec = S06_LOCAL_T0_SEC + RACE_START_COUNTDOWN_DURATION_MS / 1000;
  const { measuredSec, peakYavg, windowStart, windowEnd } = await measureS06RunningFlipSec(predictedSec, clipDurationSec);
  const delta = Math.abs(measuredSec - predictedSec);
  const ok = delta <= RUNNING_FLIP_TOLERANCE_SEC;
  return {
    name: "7. S06_LOCAL_T0_SEC matches the measured Ready->Running frame in the current clip",
    ok,
    detail:
      `predicted flip = S06_LOCAL_T0_SEC(${S06_LOCAL_T0_SEC.toFixed(2)}s) + countdown(${(RACE_START_COUNTDOWN_DURATION_MS / 1000).toFixed(2)}s) = ${predictedSec.toFixed(2)}s\n` +
      `measured flip = ${measuredSec.toFixed(2)}s (largest frame-to-frame change in [${windowStart.toFixed(2)}s, ${windowEnd.toFixed(2)}s], peak YAVG=${peakYavg.toFixed(2)})\n` +
      `delta=${delta.toFixed(2)}s (tolerance ${RUNNING_FLIP_TOLERANCE_SEC}s) - ` +
      (ok ? "S06_LOCAL_T0_SEC still matches this recording." : "S06 was re-recorded and S06_LOCAL_T0_SEC is now stale - re-measure it (see its comment) before shipping."),
  };
}

async function checkS06SfxFitsInsideClip() {
  const clipDurationSec = await ffprobeDurationSec(S06_FILE);
  const goDurationSec = await ffprobeDurationSec(path.join(AUDIO_DIR, "countdown_go.wav"));
  const goEndSec = S06_SFX_OFFSETS_SEC.countdown_go + goDurationSec;
  const ok = goEndSec <= clipDurationSec;
  return {
    name: "8. S06 countdown_go SFX plays entirely within s06_start_race.webm",
    ok,
    detail: `countdown_go starts at ${S06_SFX_OFFSETS_SEC.countdown_go.toFixed(2)}s, plays ${goDurationSec.toFixed(2)}s, ends at ${goEndSec.toFixed(2)}s; clip duration=${clipDurationSec.toFixed(2)}s`,
  };
}

// Picks: (a) the longest narration line as the "narration window" (start
// slightly after line start so the excerpt is solidly inside the line, cap
// 3.5s so ebur128 has plenty of gated blocks), and (b) the longest
// music-only tail (after the line ends, before the next segment starts,
// skipping s06/s08 which carry SFX) as the "music-only window". Both are
// chosen programmatically from the actual manifest, not hardcoded scene ids.
function chooseLoudnessWindows(narrationLines, segments, totalSec) {
  const sceneById = new Map(segments.filter((s) => s.type === "scene").map((s) => [s.id, s]));
  const longestLine = narrationLines.slice().sort((a, b) => b.lineDurationSec - a.lineDurationSec)[0];
  const narrationWindow = {
    label: `${longestLine.id} narration`,
    startSec: longestLine.absoluteOffsetSec + 0.15,
    durationSec: Math.min(3.5, longestLine.lineDurationSec - 0.3),
  };

  let best = null;
  for (const line of narrationLines) {
    if (line.id === "s06" || line.id === "s08") continue; // SFX scenes
    const scene = sceneById.get(line.id);
    const tailStart = line.absoluteOffsetSec + line.lineDurationSec + 0.3;
    const tailEnd = scene.startSec + scene.durationSec - 0.2;
    const tailLen = tailEnd - tailStart;
    if (tailLen > 3 && (!best || tailLen > best.durationSec)) {
      best = { label: `${line.id} music-only tail`, startSec: tailStart, durationSec: Math.min(4, tailLen) };
    }
  }
  if (!best) {
    // Fall back to a chapter-card window if no scene has enough slack.
    const card = segments.find((s) => s.type === "card");
    best = { label: `${card.id} chapter card`, startSec: card.startSec + 0.1, durationSec: Math.max(0.5, card.durationSec - 0.2) };
  }
  return { narrationWindow, musicOnlyWindow: best };
}

async function runAllChecks(manifest) {
  const results = [];
  results.push(await checkStreamsPresent());
  results.push(await checkAvSync());
  results.push(await checkVideoFormat());
  results.push(await checkTotalDuration(manifest.totalSec));
  results.push(checkNarrationFits(manifest));
  results.push(await checkLoudnessDucking(manifest));
  // S06/S08 SFX-timing regression guards only apply to the default cut -
  // the class cut has no S06/S08 scenes and carries no SFX cues at all
  // (see sfxCues()'s call site in build()).
  if (CUT === "default") {
    results.push(await checkS06SfxAnchorMatchesMeasuredFlip());
    results.push(await checkS06SfxFitsInsideClip());
  }

  console.log("\n================ ACCEPTANCE CHECKS ================\n");
  for (const r of results) {
    console.log(`[${r.ok ? "PASS" : "FAIL"}] ${r.name}`);
    console.log(`  ${r.detail.split("\n").join("\n  ")}`);
    console.log("");
  }
  const failed = results.filter((r) => !r.ok);
  if (failed.length > 0) {
    console.log(`FAIL: ${failed.length}/${results.length} checks failed.`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: all ${results.length} checks passed.`);
  }
  return results;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function build() {
  await rm(TMP_DIR, { recursive: true, force: true });
  await mkdir(TMP_DIR, { recursive: true });

  console.log(`Loading ${CUT} cut narration from DEMO_SCRIPT.md ...`);
  const narrationRows = CUT === "class" ? await loadClassCutNarration() : await loadEnglishCutNarration();

  console.log("Building timeline (scenes + chapter cards) ...");
  const sceneFiles = CUT === "class" ? CLASS_SCENE_FILES : SCENE_FILES;
  const cards = CUT === "class" ? CLASS_CARDS : CARDS;
  const { segments, totalSec, driftReport } = await buildTimeline(sceneFiles, cards, { checkDrift: CUT === "default" });
  if (driftReport.length > 0) {
    console.log("Spec-drift sanity check:");
    for (const r of driftReport) {
      console.log(`  ${r.id}: spec=${r.specStart.toFixed(1)}s actual=${r.actual.toFixed(2)}s drift=${r.drift.toFixed(2)}s [${r.ok ? "ok" : "FAIL"}]`);
    }
  } else {
    console.log("Spec-drift sanity check: skipped (no baseline exists for this cut yet).");
  }

  console.log("Rendering chapter cards via headless Chromium ...");
  await renderCards(segments);

  console.log("Synthesizing narration (say -v \"Evan (Enhanced)\") ...");
  const narrationLines = await synthesizeNarration(narrationRows, segments);
  for (const l of narrationLines) {
    console.log(`  ${l.id}: "${l.narration.slice(0, 60)}${l.narration.length > 60 ? "..." : ""}" -> ${l.lineDurationSec.toFixed(2)}s (scene ${l.sceneDurationSec.toFixed(2)}s, margin ${l.marginSec.toFixed(2)}s)`);
  }

  // The class cut has no countdown/champion SFX cues at all (no S06/S08
  // scenes) - only the default cut calls sfxCues(), which is hardcoded to
  // S06_SFX_OFFSETS_SEC/S08_CHAMPION_OFFSET_SEC.
  const cues = CUT === "class" ? [] : sfxCues(segments);
  console.log("SFX cues:");
  for (const c of cues) console.log(`  ${c.name} @ ${c.absoluteOffsetSec.toFixed(2)}s`);
  if (cues.length === 0) console.log("  (none)");

  console.log("Normalizing + concatenating video (h264 1920x1080 30fps, -c:v copy at concat) ...");
  const videoOnlyPath = await buildVideoOnly(segments);
  const videoOnlyDurationSec = await ffprobeDurationSec(videoOnlyPath);
  console.log(`  video_only.mp4 duration = ${videoOnlyDurationSec.toFixed(3)}s (theoretical total was ${totalSec.toFixed(3)}s)`);

  console.log("Building narration+SFX track ...");
  const voiceSfxPath = await buildVoiceSfxTrack(narrationLines, cues, videoOnlyDurationSec);

  console.log("Building looped/crossfaded music bed ...");
  const { outPath: bedPath, crossfadeSec, bedDurationSec } = await buildBedTrack(videoOnlyDurationSec);
  console.log(`  bed=${bedDurationSec.toFixed(2)}s crossfade=${crossfadeSec.toFixed(2)}s`);

  console.log("Sidechain-ducking the bed under narration+SFX ...");
  const bedDuckedPath = await duckBedUnderVoice(bedPath, voiceSfxPath);

  console.log("Final audio mix ...");
  const finalAudioPath = await mixFinalAudio(bedDuckedPath, voiceSfxPath);

  console.log("Muxing final video (-c:v copy) ...");
  await mux(videoOnlyPath, finalAudioPath);

  const loudnessWindows = chooseLoudnessWindows(narrationLines, segments, videoOnlyDurationSec);
  const manifest = {
    totalSec: videoOnlyDurationSec,
    narration: narrationLines.map(({ aiffPath, ...rest }) => rest),
    sfx: cues,
    loudnessWindows,
  };
  await writeFile(MANIFEST_PATH, JSON.stringify(manifest, null, 2), "utf8");

  const finalStat = await stat(OUTPUT_PATH);
  console.log(`\nWrote ${path.relative(ROOT, OUTPUT_PATH)} (${(finalStat.size / 1024 / 1024).toFixed(2)} MB)`);
  return manifest;
}

// =============================================================================
// FINAL CUT (CUT === "final") - the re-edited English demo.
//
// Reads the CLEAN (no-burned-caption) recordings from
// output/videos/en-clean/ (record_demo_videos.mjs's FITRACE_DEMO_NO_OVERLAY
// switch), trims dead time out of each clip, and composites THREE
// independently-timed caption layers back on top in post instead of relying
// on captions baked into the recorded pixels:
//   1. full-screen chapter cards (5, three-tier: category/headline/
//      description) - reuses cardHtml() above verbatim.
//   2. small top-left "feature label" tags on the System Admin scenes (plus
//      the two Chapter 01 scenes) - category + feature name.
//   3. "key message" value-proposition captions, reusing DEMO_SCRIPT.md's
//      own English-cut overlay column text scene-for-scene (one rewording:
//      S08), fading in/holding/fading out independently of the clip.
// A new 11s outro (no s14_outro.webm - dropped) closes it out. Never
// touches output/videos/en/*.webm, demo_full_chaptered.mp4, the class cut,
// or the race cut - writes only output/videos/en/demo_full_final.mp4.
// =============================================================================

// Source clip filenames in output/videos/en-clean/ (mirrors
// record_demo_videos.mjs's own s01..s13 filenames - s14_outro.webm exists
// there too but is deliberately unused; the new outro replaces it).
const FINAL_SOURCE_FILES = {
  s01: "s01_intro.webm",
  s02: "s02_edge_nodes.webm",
  s03: "s03_station_assignment.webm",
  s04: "s04_signup.webm",
  s05: "s05_race_rules.webm",
  s06: "s06_start_race.webm",
  s07: "s07_live_race.webm",
  s08: "s08_finish.webm",
  s09: "s09_admin_overview.webm",
  s10: "s10_network.webm",
  s11: "s11_software_update.webm",
  s12: "s12_power_controls.webm",
  s13: "s13_support.webm",
};

// Trim plan: which ranges (seconds, in the SOURCE clip's own timeline) to
// keep, in order. Multi-entry scenes (s07, s08) are reassembled from two
// non-contiguous ranges via a concat filter into ONE scene clip - a clean
// cut removing a stretch of "repeated leaderboard updates that say nothing
// new" (s07) or the loading gap of a page navigation (s08), never slicing
// across the two hard-requirement anchors: s06's full 3-2-1-GO-> Running
// countdown stays in one contiguous range, and s08's part 1 range keeps the
// champion-card reveal AND several seconds of hold, with part 2 picking up
// the results page after its navigation settles.
//
// Every range end was measured against output/videos/en-clean/*.webm (see
// the recording-QA pass this job did before writing this plan) and shaved
// a few hundredths of a second short of the clip's real ffprobe duration as
// a safety margin against ffmpeg trimming past EOF. Endpoints otherwise
// remove only measured dead time (page-load waits before the first
// scripted action, and the tail hold after the last one) - never a portion
// of an active UI operation.
const FINAL_TRIM_PLAN = {
  s01: [{ start: 1.0, end: 8.0 }],
  s02: [{ start: 1.5, end: 16.0 }],
  s03: [{ start: 1.2, end: 16.7 }],
  s04: [{ start: 0, end: 11.0 }],
  s05: [{ start: 1.2, end: 17.68 }],
  s06: [{ start: 1.5, end: 13.06 }],
  s07: [{ start: 1.4, end: 7.0 }, { start: 24.42, end: 31.68 }],
  s08: [{ start: 0, end: 8.0 }, { start: 10.3, end: 17.4 }],
  s09: [{ start: 1.0, end: 10.5 }],
  s10: [{ start: 0.8, end: 10.8 }],
  s11: [{ start: 1.5, end: 13.0 }],
  s12: [{ start: 1.0, end: 12.0 }],
  s13: [{ start: 0, end: 6.9 }],
};

// Five chapter cards - three-tier layout (category/headline/description) on
// every one, per the spec's updated table. Reuses cardHtml() verbatim.
const FINAL_CARDS = [
  { kicker: "01 / GET READY", headline: "Fast Setup. Ready to Race.", sub: "Discover equipment and assign stations in minutes." },
  { kicker: "02 / ATHLETE SIGNUP", headline: "Join the Race from Your Phone.", sub: "Scan, register, and get ready in seconds." },
  { kicker: "03 / RACE CONTROL", headline: "Configure. Start. Compete.", sub: "Set race rules and launch the event from one console." },
  { kicker: "04 / LIVE RACE", headline: "Real-Time Performance & Ranking", sub: "Track every athlete's progress live on the big screen." },
  { kicker: "05 / SYSTEM ADMIN", headline: "Manage Everything from One Console", sub: "Network, updates, power controls, and support in one place." },
];
const FINAL_CARD_DURATION_SEC = 1.5;
const FINAL_OUTRO_DURATION_SEC = 11;

// Segment order: scenes, chapter cards (before each chapter's first scene -
// note this cut's own INTRO is a true cold open, no card before s01), and
// the new outro last. s14_outro.webm is not referenced anywhere here.
const FINAL_TIMELINE = [
  { kind: "scene", id: "s01" },
  { kind: "card", index: 0 },
  { kind: "scene", id: "s02" },
  { kind: "scene", id: "s03" },
  { kind: "card", index: 1 },
  { kind: "scene", id: "s04" },
  { kind: "card", index: 2 },
  { kind: "scene", id: "s05" },
  { kind: "scene", id: "s06" },
  { kind: "card", index: 3 },
  { kind: "scene", id: "s07" },
  { kind: "scene", id: "s08" },
  { kind: "card", index: 4 },
  { kind: "scene", id: "s09" },
  { kind: "scene", id: "s10" },
  { kind: "scene", id: "s11" },
  { kind: "scene", id: "s12" },
  { kind: "scene", id: "s13" },
  { kind: "outro" },
];

// Layer timing per scene (seconds, scene-local - i.e. relative to that
// scene's OWN trimmed clip, not the master timeline). featureLabel plays
// first where present, key message always after it with a gap, so the two
// never compete for attention on screen at once (the spec's own
// requirement for the System Admin scenes, applied uniformly to s02/s03
// too since they carry both layers as well). keyMessage.text is filled in
// by buildFinal() from DEMO_SCRIPT.md's own English-cut overlay column
// (loadEnglishCutNarration()) rather than retyped here - textOverride is
// the one approved rewording (S08).
const FINAL_LAYER_TIMING = {
  s01: { keyMessage: { start: 1.0, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3 } },
  s02: {
    featureLabel: { category: "GET READY", feature: "Edge Node Discovery", start: 0.4, fadeIn: 0.3, hold: 2.4, fadeOut: 0.3 },
    keyMessage: { start: 3.7, fadeIn: 0.3, hold: 3.2, fadeOut: 0.3 },
  },
  s03: {
    featureLabel: { category: "GET READY", feature: "Station Assignment", start: 0.4, fadeIn: 0.3, hold: 2.4, fadeOut: 0.3 },
    keyMessage: { start: 3.7, fadeIn: 0.3, hold: 3.2, fadeOut: 0.3 },
  },
  // "left-center": s04 is the portrait/letterboxed scene - see
  // finalKeyMessageHtml()'s comment for why the default bottom-left dock
  // covers the Register button here specifically.
  s04: { keyMessage: { start: 1.0, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3, align: "left-center" } },
  // "bottom-right": see finalKeyMessageHtml()'s comment - Race Rules form
  // fields sit right at the bottom-left of this scene.
  s05: { keyMessage: { start: 1.0, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3, align: "bottom-right" } },
  // Scheduled AFTER the Ready->Running flip (~6.02s local, see
  // FINAL_S06_T0_SEC below) rather than during the countdown itself - the
  // countdown is the hard-requirement anchor for this scene, so the key
  // message stays clear of it in time as well as in screen position.
  s06: { keyMessage: { start: 6.3, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3 } },
  s07: { keyMessage: { start: 0.5, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3 } },
  // Starts just after the podium/champion reveal is fully on screen
  // (~5.5s local, see FINAL_S08_CHAMPION_SEC below) and is allowed to
  // bleed a little past the part-1/part-2 join at 8.0s local into the
  // results page - editorially fine, and still leaves ~5.8s of clean
  // results-only screen time after it fades out.
  s08: { keyMessage: { start: 5.3, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3, textOverride: "Results lock instantly at race finish." } },
  s09: {
    featureLabel: { category: "SYSTEM ADMIN", feature: "Edge Node Management", start: 0.4, fadeIn: 0.3, hold: 2.4, fadeOut: 0.3 },
    keyMessage: { start: 3.7, fadeIn: 0.3, hold: 3.2, fadeOut: 0.3 },
  },
  s10: {
    featureLabel: { category: "SYSTEM ADMIN", feature: "Network Management", start: 0.4, fadeIn: 0.3, hold: 2.4, fadeOut: 0.3 },
    keyMessage: { start: 3.7, fadeIn: 0.3, hold: 3.2, fadeOut: 0.3 },
  },
  s11: {
    featureLabel: { category: "SYSTEM ADMIN", feature: "Software Update", start: 0.4, fadeIn: 0.3, hold: 2.4, fadeOut: 0.3 },
    keyMessage: { start: 3.7, fadeIn: 0.3, hold: 3.2, fadeOut: 0.3 },
  },
  s12: {
    featureLabel: { category: "SYSTEM ADMIN", feature: "Power Control", start: 0.4, fadeIn: 0.3, hold: 2.4, fadeOut: 0.3 },
    keyMessage: { start: 3.7, fadeIn: 0.3, hold: 3.2, fadeOut: 0.3 },
  },
  // s13 is the shortest trimmed scene (6.9s) - both holds are compressed to
  // the low end of their spec ranges (label 2.0s, message 3.0s) so the
  // whole sequence still finishes with margin to spare.
  s13: {
    featureLabel: { category: "SYSTEM ADMIN", feature: "Support & Diagnostics", start: 0.2, fadeIn: 0.3, hold: 2.0, fadeOut: 0.3 },
    keyMessage: { start: 3.0, fadeIn: 0.3, hold: 3.0, fadeOut: 0.3 },
  },
};

// Positioned to fit entirely inside the one genuinely empty rectangle on
// every systemAdmin-family page this cut uses it on: x < ~220px, y < ~170px
// (verified against output/videos/en-clean frames - the page's own
// "FITRACESTUDIO / SYSTEM ADMIN" header text starts at x~=244, and the left
// sidebar nav links, which ARE active controls, start at y~=195). A wider
// box at this same top-left corner (the first version of this layer) read
// fine in isolation but visibly overlapped that header text once composited
// onto the real page - narrow/compact + wrap-to-two-lines keeps it inside
// that margin instead of reading over the product's own header.
function finalFeatureLabelHtml({ category, feature }) {
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1920px; height: 1080px; background: transparent; overflow: hidden; }
  body { position: relative; font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif; }
  .box {
    position: absolute;
    top: 22px;
    left: 16px;
    width: 194px;
    padding: 10px 12px;
    background: rgba(9,9,11,.82);
    border-left: 3px solid #e2ff3b;
    border-radius: 4px;
  }
  .cat {
    color: #e2ff3b;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    margin-bottom: 5px;
  }
  .feat {
    color: #f7f7f8;
    font-size: 20px;
    font-weight: 800;
    line-height: 1.18;
    letter-spacing: -0.01em;
  }
</style></head>
<body><div class="box"><div class="cat">${escapeHtml(category)}</div><div class="feat">${escapeHtml(feature)}</div></div></body></html>`;
}

// align "bottom-left" (default) is correct for most full-bleed 1920x1080
// landscape scenes (verified against every scene's actual bottom-left
// region in the composited output). Two exceptions, both confirmed by
// checking real frames:
//   - s04 is portrait (390x844), letterboxed into the 1920x1080 canvas with
//     black bars either side of a centered ~440px-wide phone frame (x
//     roughly 740-1180) - a bottom-left box sized for landscape scenes
//     reaches past x=740 and covers the bottom of the phone content,
//     including the Register button (an active control). "left-center"
//     docks the box inside the empty LEFT black bar instead, vertically
//     centered - mirroring record_demo_videos.mjs's own sceneS04 comment
//     about why its burned-in overlay used "top" alignment here for the
//     same reason.
//   - s05 (gameAdmin Race Rules) runs its form fields right up against the
//     viewport's bottom edge (see record_demo_videos.mjs's sceneS05
//     comment - the same reason its OWN burned-in overlay used "right"
//     alignment on this scene). "bottom-right" clears the form; it only
//     sits over the read-only Race Readiness checklist, not a control.
function finalKeyMessageHtml(text, align = "bottom-left") {
  const posCss = align === "left-center"
    ? "left: 30px; top: 50%; transform: translateY(-50%); max-width: 660px;"
    : align === "bottom-right"
    ? "right: 54px; bottom: 51px; max-width: 820px;"
    : "left: 54px; bottom: 51px; max-width: 820px;";
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1920px; height: 1080px; background: transparent; overflow: hidden; }
  body { position: relative; font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif; }
  .box {
    position: absolute;
    ${posCss}
    padding: 24px 32px;
    border: 1px solid rgba(226,255,59,.75);
    border-radius: 6px;
    background: rgba(9,9,11,.87);
    box-shadow: 0 0 42px rgba(226,255,59,.16);
  }
  .txt {
    color: #f7f7f8;
    font-size: 40px;
    font-weight: 800;
    line-height: 1.24;
    letter-spacing: 0;
  }
</style></head>
<body><div class="box"><div class="txt">${escapeHtml(text)}</div></div></body></html>`;
}

function finalOutroHtml() {
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1920px; height: 1080px; background: #09090b; overflow: hidden; }
  body {
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 0 165px;
    font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
    background:
      radial-gradient(1650px 1050px at 82% 66%, rgba(226,255,59,0.10), transparent 60%),
      #09090b;
  }
  .frame { position: absolute; inset: 0; border: 1px solid rgba(226,255,59,0.22); pointer-events: none; }
  .kicker {
    color: #e2ff3b;
    font-size: 28px;
    font-weight: 800;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    margin-bottom: 20px;
  }
  .headline {
    color: #f7f7f8;
    font-size: 72px;
    font-weight: 800;
    line-height: 1.15;
    letter-spacing: -0.01em;
    max-width: 1500px;
    margin-bottom: 56px;
  }
  .rows { display: flex; flex-direction: column; gap: 22px; margin-bottom: 64px; }
  .row { display: flex; align-items: baseline; gap: 36px; }
  .row .label {
    width: 230px;
    color: #e2ff3b;
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    flex-shrink: 0;
  }
  .row .value { color: #f7f7f8; font-size: 38px; font-weight: 600; }
  .rule { width: 96px; height: 6px; background: #e2ff3b; margin-bottom: 28px; }
  .brand { color: #f7f7f8; font-size: 34px; font-weight: 800; letter-spacing: 0.04em; margin-bottom: 12px; }
  .tagline { color: rgba(247,247,248,0.72); font-size: 32px; font-weight: 600; }
</style></head>
<body>
  <div class="frame"></div>
  <div class="kicker">FITRACE STUDIO</div>
  <div class="headline">Everything You Need to Run the Race.</div>
  <div class="rows">
    <div class="row"><div class="label">Athletes</div><div class="value">Mobile Registration</div></div>
    <div class="row"><div class="label">Coaches</div><div class="value">One-Press Race Control</div></div>
    <div class="row"><div class="label">Operations</div><div class="value">Browser-Based Management</div></div>
  </div>
  <div class="rule"></div>
  <div class="brand">FITRACE STUDIO</div>
  <div class="tagline">Connect. Compete. Manage.</div>
</body></html>`;
}

async function renderFinalFeatureLabelPng(page, spec, destPath) {
  await page.setContent(finalFeatureLabelHtml(spec));
  await page.screenshot({ path: destPath, omitBackground: true });
}

async function renderFinalKeyMessagePng(page, text, destPath, align = "bottom-left") {
  await page.setContent(finalKeyMessageHtml(text, align));
  await page.screenshot({ path: destPath, omitBackground: true });
}

// Trims (and, for s07/s08, reassembles two non-contiguous ranges of) one
// scene's clean source clip, scales/pads/formats it to the shared
// 1920x1080/30fps/yuv420p target, and composites its feature-label/
// key-message layers on top - all in ONE ffmpeg encode pass (no separate
// trim-then-overlay-then-encode passes).
async function buildFinalSceneClip(page, sceneId, layerSpec, destPath) {
  const parts = FINAL_TRIM_PLAN[sceneId];
  if (!parts) throw new Error(`No FINAL_TRIM_PLAN entry for ${sceneId}`);
  const srcFile = path.join(FINAL_SRC_DIR, FINAL_SOURCE_FILES[sceneId]);
  const sceneDurationSec = parts.reduce((sum, p) => sum + (p.end - p.start), 0);
  const layers = layerSpec || {};

  const inputArgs = ["-i", srcFile];
  const filterParts = [];
  parts.forEach((p, i) => {
    filterParts.push(`[0:v]trim=start=${p.start}:end=${p.end},setpts=PTS-STARTPTS[p${i}]`);
  });
  let rawLabel;
  if (parts.length > 1) {
    filterParts.push(`${parts.map((_, i) => `[p${i}]`).join("")}concat=n=${parts.length}:v=1:a=0[raw]`);
    rawLabel = "raw";
  } else {
    rawLabel = "p0";
  }
  filterParts.push(`[${rawLabel}]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p[base0]`);

  let current = "base0";
  let inputIndex = 1;

  if (layers.featureLabel) {
    const L = layers.featureLabel;
    const fadeOutStart = L.start + L.fadeIn + L.hold;
    const end = fadeOutStart + L.fadeOut;
    if (end > sceneDurationSec + 0.01) {
      throw new Error(`${sceneId}: feature label window ends at ${end.toFixed(2)}s, exceeds scene duration ${sceneDurationSec.toFixed(2)}s`);
    }
    const png = path.join(TMP_DIR, `${sceneId}_label.png`);
    await renderFinalFeatureLabelPng(page, L, png);
    inputArgs.push("-loop", "1", "-r", "30", "-t", sceneDurationSec.toFixed(3), "-i", png);
    const idx = inputIndex++;
    filterParts.push(`[${idx}:v]format=rgba,fade=t=in:st=${L.start}:d=${L.fadeIn}:alpha=1,fade=t=out:st=${fadeOutStart}:d=${L.fadeOut}:alpha=1[lbl]`);
    filterParts.push(`[${current}][lbl]overlay=0:0:enable='between(t,${L.start},${end})'[base1]`);
    current = "base1";
  }

  if (layers.keyMessage) {
    const M = layers.keyMessage;
    const fadeOutStart = M.start + M.fadeIn + M.hold;
    const end = fadeOutStart + M.fadeOut;
    if (end > sceneDurationSec + 0.01) {
      throw new Error(`${sceneId}: key message window ends at ${end.toFixed(2)}s, exceeds scene duration ${sceneDurationSec.toFixed(2)}s`);
    }
    const png = path.join(TMP_DIR, `${sceneId}_msg.png`);
    await renderFinalKeyMessagePng(page, M.text, png, M.align || "bottom-left");
    inputArgs.push("-loop", "1", "-r", "30", "-t", sceneDurationSec.toFixed(3), "-i", png);
    const idx = inputIndex++;
    filterParts.push(`[${idx}:v]format=rgba,fade=t=in:st=${M.start}:d=${M.fadeIn}:alpha=1,fade=t=out:st=${fadeOutStart}:d=${M.fadeOut}:alpha=1[msg]`);
    filterParts.push(`[${current}][msg]overlay=0:0:enable='between(t,${M.start},${end})'[base2]`);
    current = "base2";
  }

  filterParts.push(`[${current}]format=yuv420p[vout]`);

  await ffmpeg([
    ...inputArgs,
    "-filter_complex", filterParts.join(";"),
    "-map", "[vout]",
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "19",
    "-an",
    destPath,
  ], `final scene ${sceneId}`);

  return { destPath, sceneDurationSec };
}

async function buildFinalCardClip(page, card, destPath) {
  await page.setContent(cardHtml(card));
  const pngPath = destPath.replace(/\.mp4$/, ".png");
  await page.screenshot({ path: pngPath });
  await ffmpeg([
    "-loop", "1",
    "-i", pngPath,
    "-t", String(FINAL_CARD_DURATION_SEC),
    "-r", "30",
    "-vf", `fade=t=in:st=0:d=0.18,fade=t=out:st=${(FINAL_CARD_DURATION_SEC - 0.22).toFixed(3)}:d=0.22,format=yuv420p`,
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "18",
    "-an",
    destPath,
  ], "final card render");
  return { destPath, sceneDurationSec: FINAL_CARD_DURATION_SEC };
}

async function buildFinalOutroClip(page, destPath) {
  await page.setContent(finalOutroHtml());
  const pngPath = destPath.replace(/\.mp4$/, ".png");
  await page.screenshot({ path: pngPath });
  await ffmpeg([
    "-loop", "1",
    "-i", pngPath,
    "-t", String(FINAL_OUTRO_DURATION_SEC),
    "-r", "30",
    "-vf", `fade=t=in:st=0:d=0.3,fade=t=out:st=${(FINAL_OUTRO_DURATION_SEC - 0.4).toFixed(3)}:d=0.4,format=yuv420p`,
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "18",
    "-an",
    destPath,
  ], "final outro render");
  return { destPath, sceneDurationSec: FINAL_OUTRO_DURATION_SEC };
}

async function buildFinalVideoOnly(segments) {
  const listFile = path.join(TMP_DIR, "concat_list.txt");
  const listContent = segments.map((s) => `file '${s.file.replace(/'/g, "'\\''")}'`).join("\n");
  await writeFile(listFile, listContent, "utf8");
  const videoOnlyPath = path.join(TMP_DIR, "video_only.mp4");
  await ffmpeg(["-f", "concat", "-safe", "0", "-i", listFile, "-c", "copy", videoOnlyPath], "final concat video");
  return videoOnlyPath;
}

async function synthesizeFinalNarration(narrationRows, segments) {
  const sceneById = new Map(segments.filter((s) => s.kind === "scene").map((s) => [s.id, s]));
  const lines = [];
  for (const row of narrationRows) {
    const scene = sceneById.get(row.id);
    if (!scene) throw new Error(`No scene segment found for narration row ${row.id}`);
    const aiffPath = path.join(TMP_DIR, `narration_${row.id}.aiff`);
    await run("/usr/bin/say", ["-v", TTS_VOICE, "-o", aiffPath, row.narration], { label: `say ${row.id}` });
    const lineDurationSec = await ffprobeDurationSec(aiffPath);
    const budgetSec = scene.durationSec;
    const fits = lineDurationSec <= budgetSec;
    lines.push({
      id: row.id,
      narration: row.narration,
      aiffPath,
      lineDurationSec,
      sceneDurationSec: scene.durationSec,
      marginSec: budgetSec - lineDurationSec,
      absoluteOffsetSec: scene.startSec,
      fits,
    });
  }
  const overflow = lines.filter((l) => !l.fits);
  if (overflow.length > 0) {
    const detail = overflow
      .map((l) => `${l.id}: line=${l.lineDurationSec.toFixed(2)}s > scene=${l.sceneDurationSec.toFixed(2)}s`)
      .join("; ");
    throw new Error(`Narration overflow - refusing to silently truncate: ${detail}`);
  }
  return lines;
}

// SFX offsets re-derived directly from output/videos/en-clean/s06_start_race.webm
// and s08_finish.webm - the actual clean-recording source this cut reads -
// via frame-by-frame difference analysis (ffmpeg tblend=difference+
// signalstats), NOT carried over from the old en/ cut's
// S06_LOCAL_T0_SEC/S08_CHAMPION_OFFSET_SEC constants above (those measure a
// differently-timed recording of the *old*, overlay-burned en/ clips).
//
// s06: consecutive-frame YAVG delta is flat noise (~0.01-0.03) through
// t=4.32s, then jumps to 17.8 at t=4.36s - the "3" card's fade-in. Cross-
// checked against the fixed RACE_START_COUNTDOWN_DURATION_MS=3120ms
// protocol: predicted Ready->Running flip = 4.36+3.12 = 7.48s; measured
// flip (largest frame-to-frame delta in a search window) = 7.52s, delta
// 0.04s. checkFinalS06SfxAnchorMatchesMeasuredFlip() below re-verifies this
// against the live clip on every build rather than trusting the constant
// forever.
const FINAL_S06_T0_SEC = 4.36;
const FINAL_S06_SFX_OFFSETS_SEC = {
  countdown_3: FINAL_S06_T0_SEC + 0.0,
  countdown_2: FINAL_S06_T0_SEC + 0.78,
  countdown_1: FINAL_S06_T0_SEC + 1.56,
  countdown_go: FINAL_S06_T0_SEC + 2.34,
};
// s08: gold ("place-1") podium card's fade-in - same technique, delta 0.34
// (t=4.92s) -> 0.94 (t=4.96s). Matches the OLD cut's anchor numerically
// (the reveal is driven by fixed CSS animation-delay values once the
// "finished" banner renders, not by anything that varies recording to
// recording) but was independently re-measured against THIS clip, not
// assumed from that coincidence.
const FINAL_S08_CHAMPION_SEC = 4.96;

function finalSfxCues(segments) {
  const sceneStart = new Map(segments.filter((s) => s.kind === "scene").map((s) => [s.id, s.startSec]));
  const s06TrimStart = FINAL_TRIM_PLAN.s06[0].start;
  const s08TrimStart = FINAL_TRIM_PLAN.s08[0].start; // champion falls inside part 1
  const cues = [];
  for (const [name, absInSource] of Object.entries(FINAL_S06_SFX_OFFSETS_SEC)) {
    cues.push({
      name,
      file: path.join(AUDIO_DIR, `${name}.wav`),
      absoluteOffsetSec: sceneStart.get("s06") + (absInSource - s06TrimStart),
    });
  }
  cues.push({
    name: "champion",
    file: path.join(AUDIO_DIR, "champion.wav"),
    absoluteOffsetSec: sceneStart.get("s08") + (FINAL_S08_CHAMPION_SEC - s08TrimStart),
  });
  return cues;
}

function chooseLoudnessWindowsFinal(narrationLines, segments, totalSec) {
  const sceneById = new Map(segments.filter((s) => s.kind === "scene").map((s) => [s.id, s]));
  const longestLine = narrationLines.slice().sort((a, b) => b.lineDurationSec - a.lineDurationSec)[0];
  const narrationWindow = {
    label: `${longestLine.id} narration`,
    startSec: longestLine.absoluteOffsetSec + 0.15,
    durationSec: Math.min(3.5, longestLine.lineDurationSec - 0.3),
  };
  let best = null;
  for (const line of narrationLines) {
    if (line.id === "s06" || line.id === "s08") continue; // SFX scenes
    const scene = sceneById.get(line.id);
    const tailStart = line.absoluteOffsetSec + line.lineDurationSec + 0.3;
    const tailEnd = scene.startSec + scene.durationSec - 0.2;
    const tailLen = tailEnd - tailStart;
    if (tailLen > 3 && (!best || tailLen > best.durationSec)) {
      best = { label: `${line.id} music-only tail`, startSec: tailStart, durationSec: Math.min(4, tailLen) };
    }
  }
  if (!best) {
    const outro = segments.find((s) => s.kind === "outro");
    best = { label: "outro", startSec: outro.startSec + 0.2, durationSec: Math.max(0.5, outro.durationSec - 0.4) };
  }
  return { narrationWindow, musicOnlyWindow: best };
}

async function buildFinal() {
  await rm(TMP_DIR, { recursive: true, force: true });
  await mkdir(TMP_DIR, { recursive: true });

  console.log("Loading English cut narration + overlay text from DEMO_SCRIPT.md ...");
  const allRows = await loadEnglishCutNarration(); // 14 rows, s01..s14
  const rowsById = new Map(allRows.map((r) => [r.id, r]));
  const narrationRows = allRows.filter((r) => r.id !== "s14"); // s14 dropped; new outro replaces it, silent

  // Attach key-message text (DEMO_SCRIPT's own overlay column) to each
  // layer spec, applying S08's one approved rewording.
  const layers = {};
  for (const [id, spec] of Object.entries(FINAL_LAYER_TIMING)) {
    const clone = JSON.parse(JSON.stringify(spec));
    if (clone.keyMessage) {
      clone.keyMessage.text = clone.keyMessage.textOverride || rowsById.get(id).overlay;
    }
    layers[id] = clone;
  }

  console.log("Launching headless Chromium for card/label/message rendering ...");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

  const segments = [];
  try {
    for (const entry of FINAL_TIMELINE) {
      if (entry.kind === "scene") {
        const destPath = path.join(TMP_DIR, `${entry.id}.mp4`);
        console.log(`  building scene clip ${entry.id} ...`);
        const { sceneDurationSec } = await buildFinalSceneClip(page, entry.id, layers[entry.id], destPath);
        segments.push({ kind: "scene", id: entry.id, file: destPath, durationSec: sceneDurationSec });
      } else if (entry.kind === "card") {
        const card = FINAL_CARDS[entry.index];
        const destPath = path.join(TMP_DIR, `card_${entry.index}.mp4`);
        console.log(`  building card clip ${entry.index} (${card.kicker}) ...`);
        const { sceneDurationSec } = await buildFinalCardClip(page, card, destPath);
        segments.push({ kind: "card", id: `card_${entry.index}`, file: destPath, durationSec: sceneDurationSec });
      } else if (entry.kind === "outro") {
        const destPath = path.join(TMP_DIR, "outro.mp4");
        console.log("  building outro clip ...");
        const { sceneDurationSec } = await buildFinalOutroClip(page, destPath);
        segments.push({ kind: "outro", id: "outro", file: destPath, durationSec: sceneDurationSec });
      }
    }
  } finally {
    await browser.close();
  }

  let cursor = 0;
  for (const seg of segments) {
    seg.startSec = cursor;
    cursor += seg.durationSec;
  }
  const totalSec = cursor;
  console.log(`Timeline built: ${segments.length} segments, total ${totalSec.toFixed(2)}s`);
  for (const seg of segments) {
    console.log(`  ${seg.id.padEnd(10)} start=${seg.startSec.toFixed(2)}s dur=${seg.durationSec.toFixed(2)}s`);
  }

  // Structural regression guard: Chapter 1 (everything before the second
  // chapter card) must never include the athlete-signup scene (s04). True
  // by construction from FINAL_TIMELINE above, but checked against the
  // BUILT segment list (not just the static timeline) so a future edit
  // that reorders segments fails loudly - see
  // checkNoRegistrationInChapter1() below.
  const secondCardIdx = segments.findIndex((s) => s.id === "card_1");
  const chapter1Ids = segments.slice(0, secondCardIdx === -1 ? segments.length : secondCardIdx).map((s) => s.id);

  console.log("Concatenating normalized segments (-c:v copy) ...");
  const videoOnlyPath = await buildFinalVideoOnly(segments);
  const videoOnlyDurationSec = await ffprobeDurationSec(videoOnlyPath);
  console.log(`  video_only.mp4 duration = ${videoOnlyDurationSec.toFixed(3)}s`);

  console.log("Synthesizing narration (say -v \"Evan (Enhanced)\") for s01..s13 ...");
  const narrationLines = await synthesizeFinalNarration(narrationRows, segments);
  for (const l of narrationLines) {
    console.log(`  ${l.id}: "${l.narration.slice(0, 60)}${l.narration.length > 60 ? "..." : ""}" -> ${l.lineDurationSec.toFixed(2)}s (scene ${l.sceneDurationSec.toFixed(2)}s, margin ${l.marginSec.toFixed(2)}s)`);
  }

  console.log("SFX cues (re-derived against the en-clean trimmed clips) ...");
  const cues = finalSfxCues(segments);
  for (const c of cues) console.log(`  ${c.name} @ ${c.absoluteOffsetSec.toFixed(2)}s`);

  console.log("Building narration+SFX track ...");
  const voiceSfxPath = await buildVoiceSfxTrack(narrationLines, cues, videoOnlyDurationSec);

  console.log("Building looped/crossfaded music bed ...");
  const { outPath: bedPath, crossfadeSec, bedDurationSec } = await buildBedTrack(videoOnlyDurationSec);
  console.log(`  bed=${bedDurationSec.toFixed(2)}s crossfade=${crossfadeSec.toFixed(2)}s`);

  console.log("Sidechain-ducking the bed under narration+SFX ...");
  const bedDuckedPath = await duckBedUnderVoice(bedPath, voiceSfxPath);

  console.log("Final audio mix ...");
  const finalAudioPath = await mixFinalAudio(bedDuckedPath, voiceSfxPath);

  console.log("Muxing final video (-c:v copy) ...");
  await mux(videoOnlyPath, finalAudioPath);

  const loudnessWindows = chooseLoudnessWindowsFinal(narrationLines, segments, videoOnlyDurationSec);
  const manifest = {
    totalSec: videoOnlyDurationSec,
    segments: segments.map(({ id, kind, startSec, durationSec }) => ({ id, kind, startSec, durationSec })),
    chapter1Ids,
    narration: narrationLines.map(({ aiffPath, ...rest }) => rest),
    sfx: cues,
    loudnessWindows,
  };
  await writeFile(MANIFEST_PATH, JSON.stringify(manifest, null, 2), "utf8");

  const finalStat = await stat(OUTPUT_PATH);
  console.log(`\nWrote ${path.relative(ROOT, OUTPUT_PATH)} (${(finalStat.size / 1024 / 1024).toFixed(2)} MB)`);
  return manifest;
}

// ---------------------------------------------------------------------------
// Final-cut-specific acceptance checks (reuses checkStreamsPresent/
// checkAvSync/checkVideoFormat/checkTotalDuration/checkNarrationFits/
// checkLoudnessDucking as-is above - they're generic over OUTPUT_PATH/
// manifest already).
// ---------------------------------------------------------------------------

function checkTotalDurationInRange(totalSec) {
  const ok = totalSec >= 170 && totalSec <= 195;
  const mm = Math.floor(totalSec / 60);
  const ss = (totalSec % 60).toFixed(1).padStart(4, "0");
  return {
    name: "duration budget: total within 2:50-3:15 (170-195s)",
    ok,
    detail: `actual=${totalSec.toFixed(2)}s (${mm}:${ss})`,
  };
}

function checkNoRegistrationInChapter1(manifest) {
  const bad = manifest.chapter1Ids.filter((id) => id === "s04");
  const ok = bad.length === 0;
  return {
    name: "no athlete-registration scene (s04) appears in Chapter 1",
    ok,
    detail: `chapter 1 segments = [${manifest.chapter1Ids.join(", ")}]` + (ok ? "" : " - FOUND s04 in chapter 1!"),
  };
}

// +faststart proof: walk the top-level MP4 box structure from the start of
// the file and confirm 'moov' appears before 'mdat' (the point of
// -movflags +faststart - moov relocated ahead of the (large) mdat payload
// so a player/CDN can start playback after downloading only a small prefix
// of the file), plus that ffprobe reports a major_brand tag at all (proof
// the file is a well-formed MP4/ISO-BMFF container, not just an assertion
// about byte order).
async function checkFaststart() {
  const fh = await open(OUTPUT_PATH, "r");
  let moovPos = -1;
  let mdatPos = -1;
  let ftypPos = -1;
  try {
    const chunkSize = 8 * 1024 * 1024;
    const buf = Buffer.alloc(chunkSize);
    const { bytesRead } = await fh.read(buf, 0, chunkSize, 0);
    let pos = 0;
    while (pos + 8 <= bytesRead) {
      const size = buf.readUInt32BE(pos);
      const type = buf.toString("ascii", pos + 4, pos + 8);
      if (type === "ftyp" && ftypPos === -1) ftypPos = pos;
      if (type === "moov" && moovPos === -1) moovPos = pos;
      if (type === "mdat" && mdatPos === -1) mdatPos = pos;
      if (size < 8) break;
      if (moovPos !== -1 && mdatPos !== -1) break;
      pos += size;
    }
  } finally {
    await fh.close();
  }
  const data = await ffprobeJSON(OUTPUT_PATH, ["-show_format"]);
  const majorBrand = data.format?.tags?.major_brand || data.format?.tags?.MAJOR_BRAND || null;
  const moovBeforeMdat = moovPos !== -1 && (mdatPos === -1 || moovPos < mdatPos);
  const ok = moovBeforeMdat && !!majorBrand;
  return {
    name: "+faststart: moov atom precedes mdat, major_brand tag present",
    ok,
    detail: `ftyp@${ftypPos} moov@${moovPos} mdat@${mdatPos} major_brand=${majorBrand}`,
  };
}

const FINAL_S06_FILE = path.join(FINAL_SRC_DIR, "s06_start_race.webm");

// Same technique as measureS06RunningFlipSec() above, generalized over
// `file` so it can point at output/videos/en-clean/s06_start_race.webm
// instead of the default cut's S06_FILE, without touching that function.
async function measureRunningFlipSecFinal(file, predictedSec, clipDurationSec) {
  const windowStart = Math.max(0, predictedSec - RUNNING_FLIP_SEARCH_WINDOW_SEC);
  const windowEnd = Math.min(clipDurationSec, predictedSec + RUNNING_FLIP_SEARCH_WINDOW_SEC);
  const { stderr } = await run(FFMPEG, [
    "-y",
    "-i", file,
    "-vf", `select='between(t,${windowStart},${windowEnd})',tblend=all_mode=difference,signalstats,metadata=print`,
    "-an",
    "-f", "null",
    "-",
  ], { label: "final s06 running-flip frame diff" });
  const ptsMatches = [...stderr.matchAll(/pts_time:([\d.]+)/g)].map((m) => Number(m[1]));
  const yavgMatches = [...stderr.matchAll(/lavfi\.signalstats\.YAVG=([\d.]+)/g)].map((m) => Number(m[1]));
  if (ptsMatches.length === 0 || ptsMatches.length !== yavgMatches.length) {
    throw new Error(
      `Could not parse ffmpeg frame-diff signal for final-cut S06 running-flip detection ` +
      `(parsed ${ptsMatches.length} pts_time, ${yavgMatches.length} YAVG values)`,
    );
  }
  let bestIdx = 0;
  for (let i = 1; i < yavgMatches.length; i += 1) {
    if (yavgMatches[i] > yavgMatches[bestIdx]) bestIdx = i;
  }
  return { measuredSec: ptsMatches[bestIdx], peakYavg: yavgMatches[bestIdx], windowStart, windowEnd };
}

async function checkFinalS06SfxAnchorMatchesMeasuredFlip() {
  const clipDurationSec = await ffprobeDurationSec(FINAL_S06_FILE);
  const predictedSec = FINAL_S06_T0_SEC + RACE_START_COUNTDOWN_DURATION_MS / 1000;
  const { measuredSec, peakYavg, windowStart, windowEnd } = await measureRunningFlipSecFinal(FINAL_S06_FILE, predictedSec, clipDurationSec);
  const delta = Math.abs(measuredSec - predictedSec);
  const ok = delta <= RUNNING_FLIP_TOLERANCE_SEC;
  return {
    name: "FINAL_S06_T0_SEC matches the measured Ready->Running frame in en-clean/s06_start_race.webm",
    ok,
    detail:
      `predicted flip = FINAL_S06_T0_SEC(${FINAL_S06_T0_SEC.toFixed(2)}s) + countdown(${(RACE_START_COUNTDOWN_DURATION_MS / 1000).toFixed(2)}s) = ${predictedSec.toFixed(2)}s\n` +
      `measured flip = ${measuredSec.toFixed(2)}s (largest frame-to-frame change in [${windowStart.toFixed(2)}s, ${windowEnd.toFixed(2)}s], peak YAVG=${peakYavg.toFixed(2)})\n` +
      `delta=${delta.toFixed(2)}s (tolerance ${RUNNING_FLIP_TOLERANCE_SEC}s) - ` +
      (ok ? "anchor still matches the en-clean recording." : "en-clean/s06_start_race.webm changed and FINAL_S06_T0_SEC is now stale - re-measure it."),
  };
}

async function runFinalChecks(manifest) {
  const results = [];
  results.push(await checkStreamsPresent());
  results.push(await checkAvSync());
  results.push(await checkVideoFormat());
  results.push(await checkFaststart());
  results.push(await checkTotalDuration(manifest.totalSec));
  results.push(checkTotalDurationInRange(manifest.totalSec));
  results.push(checkNarrationFits(manifest));
  results.push(await checkLoudnessDucking(manifest));
  results.push(checkNoRegistrationInChapter1(manifest));
  results.push(await checkFinalS06SfxAnchorMatchesMeasuredFlip());

  console.log("\n================ FINAL CUT ACCEPTANCE CHECKS ================\n");
  for (const r of results) {
    console.log(`[${r.ok ? "PASS" : "FAIL"}] ${r.name}`);
    console.log(`  ${r.detail.split("\n").join("\n  ")}`);
    console.log("");
  }
  const failed = results.filter((r) => !r.ok);
  if (failed.length > 0) {
    console.log(`FAIL: ${failed.length}/${results.length} checks failed.`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: all ${results.length} checks passed.`);
  }
  return results;
}

// =============================================================================
// CLASS FINAL CUT (CUT === "class_final") - the re-edited English Class Mode
// demo.
//
// Reads the CLEAN (no-burned-caption) recordings from
// output/videos/en-class-clean/ (record_demo_videos.mjs's
// FITRACE_DEMO_CUT=class + FITRACE_DEMO_NO_OVERLAY=1 combination), trims
// dead time out of each clip, and composites the same three independently-
// timed caption layers the race "final" cut uses (chapter cards, small
// role-label tags, and fading key-message captions) back on top - plus a
// class-specific fourth layer, the Chapter 04 intensity-emphasis montage.
// c01 (mode switch) is used only for a few seconds of B-roll under the new
// opening; c11 (old outro) is dropped entirely, replaced by a new outro.
// Never touches output/videos/en-class/*.webm, class_full.mp4, the race
// cut, or the default cut - writes only
// output/videos/en-class/class_full_final.mp4.
// =============================================================================

// Source clip filenames in output/videos/en-class-clean/ (mirrors
// record_demo_videos.mjs's own c01..c11 filenames - c01 is used only for a
// few seconds of B-roll under the new opening's title card; c11 is dropped
// entirely, replaced by the new outro).
const CLASS_FINAL_SOURCE_FILES = {
  c01: "c01_switch_mode.webm",
  c02: "c02_plan_editor.webm",
  c03: "c03_repeat_intervals.webm",
  c04: "c04_save_plan.webm",
  c05: "c05_saved_class_library.webm",
  c06: "c06_start_class.webm",
  c07: "c07_live_class.webm",
  c08: "c08_rest_changeover.webm",
  c09: "c09_high_intensity.webm",
  c10: "c10_stop_class.webm",
};

// Trim plan: seconds in each SOURCE clip's own timeline, kept in order.
// Derived from a 1fps frame-by-frame review (contact sheets) of every
// output/videos/en-class-clean/c*.webm clip actually recorded for this job
// - not guessed. Multi-range entries are clean jump cuts removing genuinely
// dead/repetitive time (a long static hold, or the unneeded middle of a
// segment whose state the camera has already established), never a slice
// out of an active UI operation or a countdown mid-tick - every kept range
// plays back at native speed. Endpoints are shaved a little short of each
// clip's real ffprobe duration as a safety margin against ffmpeg trimming
// past EOF.
const CLASS_FINAL_TRIM_PLAN = {
  // Only used for the OPENING's B-roll - classAdmin idle ("Class State:
  // IDLE"), establishing the coach console before the title text fades in
  // over it.
  c01: [{ start: 0.3, end: 3.3 }],
  // Continuous - Add Segment x5 is already paced by the recording script
  // itself (see record_demo_videos.mjs's sceneC02: the two work-target
  // holds are deliberately 2.4s/2.6s so the viewer can read "120" then
  // "175"). Only the pre-action load flash and the post-action tail are
  // true dead time.
  c02: [{ start: 0.6, end: 24.1 }],
  // Keeps the Repeat Selected x3 build-up (typing From/To/Times, clicking)
  // and enough of the settled 10-row result to read as "the whole circuit
  // built itself" - drops most of sceneC03's own 6.5s static hold after
  // the click (already fully conveyed within ~2s of it).
  c03: [{ start: 0.8, end: 14.8 }],
  // Continuous - name typed, Save Plan clicked, confirmation shown.
  c04: [{ start: 0.6, end: 9.5 }],
  // Two ranges: "New Class" cleared (brief), then the restored 10-row
  // "Tuesday Circuit 30" plan with a hover over Delete Saved Class (never
  // clicked) - drops the static hold in between.
  c05: [{ start: 0.3, end: 2.0 }, { start: 5.0, end: 9.5 }],
  // Two ranges: classAdmin Start Class click, then the cut to the
  // dashboard already on WARMUP (the "WARMUP" beat of RUN THE CLASS's one
  // complete cycle) - drops most of the extra WARMUP hold past what's
  // needed to establish the state.
  c06: [{ start: 1.0, end: 4.0 }, { start: 4.3, end: 10.5 }],
  // Three ranges: WORK (120W, target shown) -> REST (countdown + Coming
  // Up) -> WORK (175W, new target shown) - drops the dead middle of the
  // 120W dwell and most of the REST countdown's static middle, keeping
  // every actual transition intact and at real speed.
  c07: [{ start: 0.0, end: 4.0 }, { start: 11.8, end: 16.6 }, { start: 21.8, end: 30.3 }],
  // Three ranges: REST established, the REST -> CHANGEOVER transition,
  // then CHANGEOVER continuing with "Coming Up: Work Target 210 W"
  // clearly visible - drops the static middle of the REST hold.
  c08: [{ start: 0.0, end: 3.5 }, { start: 9.3, end: 14.0 }, { start: 14.5, end: 17.9 }],
  // Single range, minor lead/tail trim only - the 210W block is already a
  // single continuous state (no transition to cut around), and the scene's
  // own recording already stops recording ~9s after reaching it.
  c09: [{ start: 0.3, end: 13.0 }],
  // Two ranges: Stop Class clicked through the "Class stopped."
  // confirmation, then the scrolled Class History entry (the record the
  // narration refers to) - drops the static hold in between.
  c10: [{ start: 1.2, end: 8.3 }, { start: 13.8, end: 18.3 }],
};

// Extra short B-roll range for the Chapter 04 intensity-emphasis montage -
// a few seconds of c07's own board from a stretch NOT used by the Chapter
// 03 trim above (that trim covers [0,4]+[11.8,16.6]+[21.8,30.3]; this uses
// the untouched [4.0,7.0] gap) with the "120 W -> 175 W -> 210 W" callout
// laid over it, per the job spec's "over the actual footage" requirement.
const CLASS_FINAL_EMPHASIS_FOOTAGE_TRIM = [{ start: 4.0, end: 7.0 }];

// Four chapter cards - three-tier layout (category/headline/description),
// same style as the race "final" cut's FINAL_CARDS/cardHtml(). Copied
// verbatim from the job spec's table.
const CLASS_FINAL_CARDS = [
  { kicker: "01 / BUILD THE CLASS", headline: "Design Every Interval Your Way.", sub: "Build warm-up, work, rest and changeover blocks in minutes." },
  { kicker: "02 / SAVE & REUSE", headline: "Build Once. Run It Again.", sub: "Save class plans and bring them back whenever you need them." },
  { kicker: "03 / RUN THE CLASS", headline: "Press Start. The Room Follows.", sub: "Timing, targets and transitions update automatically on the class display." },
  { kicker: "04 / COACH IN CONTROL", headline: "One Class. Three Intensity Blocks.", sub: "Keep one class structure while the target changes block by block." },
];
const CLASS_FINAL_CARD_DURATION_SEC = FINAL_CARD_DURATION_SEC; // 1.5s - within spec's 1.2-1.8s range
const CLASS_FINAL_OPENING_PART_SEC = 3.0; // x2 parts = 6s opening (spec: 5-7s)
const CLASS_FINAL_EMPHASIS_CARD_SEC = 2.5;
const CLASS_FINAL_OUTRO_PART_SEC = [3.2, 3.2, 3.1]; // 9.5s total (spec: 8-10s)

// Every caption string this cut ever puts on screen, listed once here
// (rather than scraped back out of rendered HTML) so
// checkForbiddenCaptionText() below can grep a plain, complete list - not
// a heuristic guess at what the rendered pages say.
const CLASS_FINAL_CAPTION_TEXTS = [
  "FITRACE STUDIO", "CLASS MODE", "Build the class. Guide the room.",
  "One platform for race days and training days.",
  ...CLASS_FINAL_CARDS.flatMap((c) => [c.kicker, c.headline, c.sub]),
  "COACH CONSOLE", "Build & control the class",
  "CLASS DISPLAY", "Guide the room in real time",
  "Build every interval your way.",
  "Turn your plan into a complete circuit.",
  "Build once. Run it again.",
  "Press start. The room follows.",
  "Targets update automatically with every block.",
  "The screen guides the timing. You coach the people.",
  "Every block sets its own target. The board re-scores the room instantly.",
  "The coach ends it, not the clock.", "Stay in control from start to finish.",
  "SAME CLASS", "3 Intensity Blocks", "120 W → 175 W → 210 W",
  "Race days.", "Training days.", "One platform.",
  "One venue. One system.", "More ways to train.",
];

// Big centered title box, translucent over real footage - the OPENING's
// first ~3s (c01 B-roll: classAdmin idle, per CLASS_FINAL_TRIM_PLAN.c01).
function classFinalOpeningTitleHtml() {
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1920px; height: 1080px; background: transparent; overflow: hidden; }
  body { position: relative; font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif; display: flex; align-items: center; justify-content: center; }
  .box {
    padding: 44px 64px;
    border: 1px solid rgba(226,255,59,.75);
    border-radius: 8px;
    background: rgba(9,9,11,.84);
    box-shadow: 0 0 60px rgba(226,255,59,.18);
    text-align: center;
    max-width: 1400px;
  }
  .brand { color: #e2ff3b; font-size: 30px; font-weight: 800; letter-spacing: 0.22em; margin-bottom: 14px; }
  .mode { color: #f7f7f8; font-size: 76px; font-weight: 800; letter-spacing: -0.01em; margin-bottom: 22px; }
  .tag { color: rgba(247,247,248,0.85); font-size: 34px; font-weight: 600; }
</style></head>
<body><div class="box">
  <div class="brand">FITRACE STUDIO</div>
  <div class="mode">CLASS MODE</div>
  <div class="tag">Build the class. Guide the room.</div>
</div></body></html>`;
}

// Generic full-bleed static title card - same background treatment as
// cardHtml()/finalOutroHtml() (radial glow + hairline frame) but with a
// plain centered multi-line message instead of a kicker/headline/sub
// layout. Used for the OPENING's second part and the first two OUTRO
// parts.
function classFinalTitleCardHtml(lines, { kicker = "", size = 64 } = {}) {
  const kickerHtml = kicker ? `<div class="kicker">${escapeHtml(kicker)}</div>` : "";
  const linesHtml = lines.map((l) => `<div class="line">${escapeHtml(l)}</div>`).join("");
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1920px; height: 1080px; background: #09090b; overflow: hidden; }
  body {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
    background:
      radial-gradient(1650px 1050px at 50% 50%, rgba(226,255,59,0.10), transparent 60%),
      #09090b;
    text-align: center;
  }
  .frame { position: absolute; inset: 0; border: 1px solid rgba(226,255,59,0.22); pointer-events: none; }
  .kicker {
    color: #e2ff3b;
    font-size: 30px;
    font-weight: 800;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    margin-bottom: 28px;
  }
  .line {
    color: #f7f7f8;
    font-size: ${size}px;
    font-weight: 800;
    line-height: 1.28;
    letter-spacing: -0.01em;
    max-width: 1500px;
  }
</style></head>
<body>
  <div class="frame"></div>
  ${kickerHtml}
  ${linesHtml}
</body></html>`;
}

// Standalone wordmark card - the outro's closing frame. No dedicated logo
// asset exists in this repo (checked hub_server/static/ - signup.html and
// simulator.html both render "FitRaceStudio" as plain styled text, not an
// image), so this is plain text too, matching finalOutroHtml()'s own
// brand treatment.
function classFinalWordmarkHtml() {
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1920px; height: 1080px; background: #09090b; overflow: hidden; }
  body {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
    background:
      radial-gradient(1650px 1050px at 50% 50%, rgba(226,255,59,0.10), transparent 60%),
      #09090b;
  }
  .frame { position: absolute; inset: 0; border: 1px solid rgba(226,255,59,0.22); pointer-events: none; }
  .brand { color: #f7f7f8; font-size: 88px; font-weight: 800; letter-spacing: 0.06em; }
</style></head>
<body><div class="frame"></div><div class="brand">FITRACE STUDIO</div></body></html>`;
}

// Two-line variant of finalKeyMessageHtml() - a main line plus a smaller
// second line underneath, same box treatment. Used once, on C10's closing
// message ("The coach ends it, not the clock." / "Stay in control from
// start to finish.").
function classFinalTwoLineMessageHtml(main, sub, align = "bottom-left") {
  const posCss = align === "bottom-right"
    ? "right: 54px; bottom: 51px; max-width: 900px;"
    : "left: 54px; bottom: 51px; max-width: 900px;";
  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1920px; height: 1080px; background: transparent; overflow: hidden; }
  body { position: relative; font-family: -apple-system, "SF Pro Display", "Helvetica Neue", Arial, sans-serif; }
  .box {
    position: absolute;
    ${posCss}
    padding: 24px 32px;
    border: 1px solid rgba(226,255,59,.75);
    border-radius: 6px;
    background: rgba(9,9,11,.87);
    box-shadow: 0 0 42px rgba(226,255,59,.16);
  }
  .main { color: #f7f7f8; font-size: 40px; font-weight: 800; line-height: 1.24; margin-bottom: 8px; }
  .sub { color: rgba(247,247,248,0.72); font-size: 26px; font-weight: 600; }
</style></head>
<body><div class="box"><div class="main">${escapeHtml(main)}</div><div class="sub">${escapeHtml(sub)}</div></div></body></html>`;
}

// Renders any of the CLASS FINAL CUT's static (no-video) cards to a short
// h264 clip - generic version of buildFinalOutroClip(), parameterised over
// html/duration so it covers the opening's second part, the intensity-
// emphasis card, and all three outro parts without near-duplicate
// functions.
async function buildClassFinalStaticClip(page, html, durationSec, destPath) {
  await page.setContent(html);
  const pngPath = destPath.replace(/\.mp4$/, ".png");
  await page.screenshot({ path: pngPath });
  await ffmpeg([
    "-loop", "1",
    "-i", pngPath,
    "-t", String(durationSec),
    "-r", "30",
    "-vf", `fade=t=in:st=0:d=0.25,fade=t=out:st=${(durationSec - 0.35).toFixed(3)}:d=0.35,format=yuv420p`,
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "18",
    "-an",
    destPath,
  ], "class final static card render");
  return { destPath, sceneDurationSec: durationSec };
}

// Trims (and, for multi-range scenes, reassembles) one clean clip from
// output/videos/en-class-clean/, scales/pads/formats it to the shared
// 1920x1080/30fps/yuv420p target, and composites 0-2 independently-timed
// overlay layers on top - all in one ffmpeg encode pass. Generalizes
// buildFinalSceneClip() above: `overlays` is a plain array of
// { html, start, fadeIn, hold, fadeOut } rendered in the order given,
// rather than a fixed featureLabel/keyMessage shape, so the same function
// covers the OPENING's title-over-footage layer, role labels, key
// messages, and the Chapter 04 watts callout. `srcFileId` and `trimParts`
// are passed explicitly (rather than looked up from `tmpId` alone) so the
// Chapter 04 emphasis footage can reuse c07's own source file with a
// second, different trim range under a different tmp-file id.
async function buildClassFinalSceneClip(page, tmpId, srcFileId, trimParts, overlays, destPath) {
  const srcFile = path.join(CLASS_FINAL_SRC_DIR, CLASS_FINAL_SOURCE_FILES[srcFileId]);
  const sceneDurationSec = trimParts.reduce((sum, p) => sum + (p.end - p.start), 0);

  // Validate + pre-render every overlay's PNG first (all async work done up
  // front, so the ffmpeg arg-building below is a single synchronous pass).
  const rendered = [];
  for (let i = 0; i < (overlays || []).length; i += 1) {
    const L = overlays[i];
    const fadeOutStart = L.start + L.fadeIn + L.hold;
    const end = fadeOutStart + L.fadeOut;
    if (end > sceneDurationSec + 0.01) {
      throw new Error(`${tmpId}: overlay #${i} window ends at ${end.toFixed(2)}s, exceeds scene duration ${sceneDurationSec.toFixed(2)}s`);
    }
    const pngPath = path.join(TMP_DIR, `${tmpId}_ov${i}.png`);
    await page.setContent(L.html);
    await page.screenshot({ path: pngPath, omitBackground: true });
    rendered.push({ ...L, pngPath, end });
  }

  const inputArgs = ["-i", srcFile];
  const filterParts = [];
  trimParts.forEach((p, i) => {
    filterParts.push(`[0:v]trim=start=${p.start}:end=${p.end},setpts=PTS-STARTPTS[p${i}]`);
  });
  let rawLabel;
  if (trimParts.length > 1) {
    filterParts.push(`${trimParts.map((_, i) => `[p${i}]`).join("")}concat=n=${trimParts.length}:v=1:a=0[raw]`);
    rawLabel = "raw";
  } else {
    rawLabel = "p0";
  }
  filterParts.push(`[${rawLabel}]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p[base0]`);

  let current = "base0";
  let inputIndex = 1;
  rendered.forEach((L, i) => {
    inputArgs.push("-loop", "1", "-r", "30", "-t", sceneDurationSec.toFixed(3), "-i", L.pngPath);
    const idx = inputIndex++;
    const label = `ov${i}`;
    filterParts.push(`[${idx}:v]format=rgba,fade=t=in:st=${L.start}:d=${L.fadeIn}:alpha=1,fade=t=out:st=${(L.end - L.fadeOut).toFixed(3)}:d=${L.fadeOut}:alpha=1[${label}]`);
    filterParts.push(`[${current}][${label}]overlay=0:0:enable='between(t,${L.start},${L.end})'[base${i + 1}]`);
    current = `base${i + 1}`;
  });

  filterParts.push(`[${current}]format=yuv420p[vout]`);

  await ffmpeg([
    ...inputArgs,
    "-filter_complex", filterParts.join(";"),
    "-map", "[vout]",
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-crf", "19",
    "-an",
    destPath,
  ], `class final scene ${tmpId}`);

  return { destPath, sceneDurationSec };
}

// Layer timing per scene (seconds, scene-local). Role labels are shown ONLY
// on the first appearance of each screen (COACH CONSOLE on c02, the first
// scene where the classAdmin console is the featured subject - the
// OPENING's brief c01 B-roll is background under its own title card, not a
// labeled subject; CLASS DISPLAY on c06, the first moment the dashboard
// cuts into frame) and never repeated on c03/c04/c05/c07/c08/c09/c10, per
// the job spec. Key-message text is the job spec's own corrected wording -
// see the file-level "THREE FACTUAL CORRECTIONS" notes this job was given.
const CLASS_FINAL_LAYER_TIMING = {
  c02: [
    { kind: "roleLabel", start: 0.4, fadeIn: 0.3, hold: 2.4, fadeOut: 0.3, html: finalFeatureLabelHtml({ category: "COACH CONSOLE", feature: "Build & control the class" }) },
    { kind: "keyMessage", start: 3.7, fadeIn: 0.3, hold: 3.2, fadeOut: 0.3, html: finalKeyMessageHtml("Build every interval your way.") },
  ],
  c03: [
    { kind: "keyMessage", start: 1.0, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3, html: finalKeyMessageHtml("Turn your plan into a complete circuit.") },
  ],
  c05: [
    { kind: "keyMessage", start: 1.0, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3, html: finalKeyMessageHtml("Build once. Run it again.") },
  ],
  c06: [
    { kind: "keyMessage", start: 0.3, fadeIn: 0.3, hold: 1.6, fadeOut: 0.3, html: finalKeyMessageHtml("Press start. The room follows.") },
    { kind: "roleLabel", start: 3.2, fadeIn: 0.3, hold: 2.4, fadeOut: 0.3, html: finalFeatureLabelHtml({ category: "CLASS DISPLAY", feature: "Guide the room in real time" }) },
  ],
  c07: [
    { kind: "keyMessage", start: 9.5, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3, html: finalKeyMessageHtml("Targets update automatically with every block.") },
  ],
  c08: [
    { kind: "keyMessage", start: 1.0, fadeIn: 0.3, hold: 3.4, fadeOut: 0.3, html: finalKeyMessageHtml("The screen guides the timing. You coach the people.") },
  ],
  c09: [
    { kind: "keyMessage", start: 7.5, fadeIn: 0.3, hold: 3.2, fadeOut: 0.3, html: finalKeyMessageHtml("Every block sets its own target. The board re-scores the room instantly.") },
  ],
  c10: [
    { kind: "keyMessage", start: 1.0, fadeIn: 0.3, hold: 3.6, fadeOut: 0.3, html: classFinalTwoLineMessageHtml("The coach ends it, not the clock.", "Stay in control from start to finish.") },
  ],
};

// Segment order: OPENING (2 parts), CARD 01, c02+c03, CARD 02, c04+c05,
// CARD 03, c06+c07+c08, CARD 04, the Chapter 04 intensity-emphasis montage
// (card + footage), c09+c10, then the new 3-part OUTRO. Matches the job
// spec's OPENING -> BUILD -> SAVE -> RUN -> ADAPT/CONTROL -> OUTRO
// narrative exactly. c01 (beyond the OPENING's B-roll) and c11 are never
// referenced.
const CLASS_FINAL_TIMELINE = [
  { kind: "openingA" },
  { kind: "openingB" },
  { kind: "card", index: 0 },
  { kind: "scene", id: "c02" },
  { kind: "scene", id: "c03" },
  { kind: "card", index: 1 },
  { kind: "scene", id: "c04" },
  { kind: "scene", id: "c05" },
  { kind: "card", index: 2 },
  { kind: "scene", id: "c06" },
  { kind: "scene", id: "c07" },
  { kind: "scene", id: "c08" },
  { kind: "card", index: 3 },
  { kind: "emphasisCard" },
  { kind: "emphasisFootage" },
  { kind: "scene", id: "c09" },
  { kind: "scene", id: "c10" },
  { kind: "outroPart", index: 0 },
  { kind: "outroPart", index: 1 },
  { kind: "outroPart", index: 2 },
];

function chooseLoudnessWindowsClassFinal(narrationLines, segments, totalSec) {
  const sceneById = new Map(segments.filter((s) => s.kind === "scene").map((s) => [s.id, s]));
  const longestLine = narrationLines.slice().sort((a, b) => b.lineDurationSec - a.lineDurationSec)[0];
  const narrationWindow = {
    label: `${longestLine.id} narration`,
    startSec: longestLine.absoluteOffsetSec + 0.15,
    durationSec: Math.min(3.5, longestLine.lineDurationSec - 0.3),
  };
  let best = null;
  for (const line of narrationLines) {
    const scene = sceneById.get(line.id);
    const tailStart = line.absoluteOffsetSec + line.lineDurationSec + 0.3;
    const tailEnd = scene.startSec + scene.durationSec - 0.2;
    const tailLen = tailEnd - tailStart;
    if (tailLen > 3 && (!best || tailLen > best.durationSec)) {
      best = { label: `${line.id} music-only tail`, startSec: tailStart, durationSec: Math.min(4, tailLen) };
    }
  }
  if (!best) {
    const outro = segments.find((s) => s.kind === "outroPart");
    best = { label: "outro", startSec: outro.startSec + 0.2, durationSec: Math.max(0.5, outro.durationSec - 0.4) };
  }
  return { narrationWindow, musicOnlyWindow: best };
}

async function buildClassFinal() {
  await rm(TMP_DIR, { recursive: true, force: true });
  await mkdir(TMP_DIR, { recursive: true });

  console.log("Loading class-mode narration from DEMO_SCRIPT.md ...");
  const allRows = await loadClassCutNarration(); // 10 rows, c01..c10
  const narrationRows = allRows.filter((r) => r.id !== "c01"); // c01 dropped; OPENING replaces it, silent

  console.log("Launching headless Chromium for card/label/message/scene rendering ...");
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

  const segments = [];
  try {
    for (const entry of CLASS_FINAL_TIMELINE) {
      if (entry.kind === "openingA") {
        const destPath = path.join(TMP_DIR, "opening_a.mp4");
        console.log("  building opening part A (c01 B-roll + title) ...");
        const overlay = { html: classFinalOpeningTitleHtml(), start: 0.2, fadeIn: 0.3, hold: 2.0, fadeOut: 0.3 };
        const { sceneDurationSec } = await buildClassFinalSceneClip(page, "opening_a", "c01", CLASS_FINAL_TRIM_PLAN.c01, [overlay], destPath);
        segments.push({ kind: "openingA", id: "opening_a", file: destPath, durationSec: sceneDurationSec });
      } else if (entry.kind === "openingB") {
        const destPath = path.join(TMP_DIR, "opening_b.mp4");
        console.log("  building opening part B (title card) ...");
        const html = classFinalTitleCardHtml(["One platform for race days and training days."], { size: 58 });
        const { sceneDurationSec } = await buildClassFinalStaticClip(page, html, CLASS_FINAL_OPENING_PART_SEC, destPath);
        segments.push({ kind: "openingB", id: "opening_b", file: destPath, durationSec: sceneDurationSec });
      } else if (entry.kind === "card") {
        const card = CLASS_FINAL_CARDS[entry.index];
        const destPath = path.join(TMP_DIR, `card_${entry.index}.mp4`);
        console.log(`  building card clip ${entry.index} (${card.kicker}) ...`);
        const { sceneDurationSec } = await buildFinalCardClip(page, card, destPath);
        segments.push({ kind: "card", id: `card_${entry.index}`, file: destPath, durationSec: sceneDurationSec });
      } else if (entry.kind === "scene") {
        const destPath = path.join(TMP_DIR, `${entry.id}.mp4`);
        console.log(`  building scene clip ${entry.id} ...`);
        const overlays = CLASS_FINAL_LAYER_TIMING[entry.id] || [];
        const { sceneDurationSec } = await buildClassFinalSceneClip(page, entry.id, entry.id, CLASS_FINAL_TRIM_PLAN[entry.id], overlays, destPath);
        segments.push({ kind: "scene", id: entry.id, file: destPath, durationSec: sceneDurationSec });
      } else if (entry.kind === "emphasisCard") {
        const destPath = path.join(TMP_DIR, "emphasis_card.mp4");
        console.log("  building intensity-emphasis card (SAME CLASS / 3 INTENSITY BLOCKS) ...");
        const html = cardHtml({ kicker: "SAME CLASS", headline: "3 Intensity Blocks", sub: "" });
        const { sceneDurationSec } = await buildClassFinalStaticClip(page, html, CLASS_FINAL_EMPHASIS_CARD_SEC, destPath);
        segments.push({ kind: "emphasisCard", id: "emphasis_card", file: destPath, durationSec: sceneDurationSec });
      } else if (entry.kind === "emphasisFootage") {
        const destPath = path.join(TMP_DIR, "emphasis_footage.mp4");
        console.log("  building intensity-emphasis footage (120W -> 175W -> 210W over real board footage) ...");
        const overlay = { html: finalKeyMessageHtml("120 W → 175 W → 210 W"), start: 0.3, fadeIn: 0.3, hold: 1.8, fadeOut: 0.3 };
        const { sceneDurationSec } = await buildClassFinalSceneClip(page, "emphasis_footage", "c07", CLASS_FINAL_EMPHASIS_FOOTAGE_TRIM, [overlay], destPath);
        segments.push({ kind: "emphasisFootage", id: "emphasis_footage", file: destPath, durationSec: sceneDurationSec });
      } else if (entry.kind === "outroPart") {
        const destPath = path.join(TMP_DIR, `outro_${entry.index}.mp4`);
        console.log(`  building outro part ${entry.index} ...`);
        let html;
        if (entry.index === 0) html = classFinalTitleCardHtml(["Race days.", "Training days.", "One platform."], { kicker: "FITRACE STUDIO", size: 56 });
        else if (entry.index === 1) html = classFinalTitleCardHtml(["One venue. One system.", "More ways to train."], { size: 52 });
        else html = classFinalWordmarkHtml();
        const { sceneDurationSec } = await buildClassFinalStaticClip(page, html, CLASS_FINAL_OUTRO_PART_SEC[entry.index], destPath);
        segments.push({ kind: "outroPart", id: `outro_${entry.index}`, file: destPath, durationSec: sceneDurationSec });
      }
    }
  } finally {
    await browser.close();
  }

  let cursor = 0;
  for (const seg of segments) {
    seg.startSec = cursor;
    cursor += seg.durationSec;
  }
  const totalSec = cursor;
  console.log(`Timeline built: ${segments.length} segments, total ${totalSec.toFixed(2)}s`);
  for (const seg of segments) {
    console.log(`  ${seg.id.padEnd(16)} start=${seg.startSec.toFixed(2)}s dur=${seg.durationSec.toFixed(2)}s`);
  }

  console.log("Concatenating normalized segments (-c:v copy) ...");
  const videoOnlyPath = await buildFinalVideoOnly(segments);
  const videoOnlyDurationSec = await ffprobeDurationSec(videoOnlyPath);
  console.log(`  video_only.mp4 duration = ${videoOnlyDurationSec.toFixed(3)}s`);

  console.log("Synthesizing narration (say -v \"Evan (Enhanced)\") for c02..c10 ...");
  const narrationLines = await synthesizeFinalNarration(narrationRows, segments);
  for (const l of narrationLines) {
    console.log(`  ${l.id}: "${l.narration.slice(0, 60)}${l.narration.length > 60 ? "..." : ""}" -> ${l.lineDurationSec.toFixed(2)}s (scene ${l.sceneDurationSec.toFixed(2)}s, margin ${l.marginSec.toFixed(2)}s)`);
  }

  console.log("Building narration track (no SFX in the class cut) ...");
  const voiceSfxPath = await buildVoiceSfxTrack(narrationLines, [], videoOnlyDurationSec);

  console.log("Building looped/crossfaded music bed ...");
  const { outPath: bedPath, crossfadeSec, bedDurationSec } = await buildBedTrack(videoOnlyDurationSec);
  console.log(`  bed=${bedDurationSec.toFixed(2)}s crossfade=${crossfadeSec.toFixed(2)}s`);

  console.log("Sidechain-ducking the bed under narration ...");
  const bedDuckedPath = await duckBedUnderVoice(bedPath, voiceSfxPath);

  console.log("Final audio mix ...");
  const finalAudioPath = await mixFinalAudio(bedDuckedPath, voiceSfxPath);

  console.log("Muxing final video (-c:v copy) ...");
  await mux(videoOnlyPath, finalAudioPath);

  const loudnessWindows = chooseLoudnessWindowsClassFinal(narrationLines, segments, videoOnlyDurationSec);
  const manifest = {
    totalSec: videoOnlyDurationSec,
    segments: segments.map(({ id, kind, startSec, durationSec }) => ({ id, kind, startSec, durationSec })),
    narration: narrationLines.map(({ aiffPath, ...rest }) => rest),
    sfx: [],
    loudnessWindows,
    captionTexts: CLASS_FINAL_CAPTION_TEXTS,
  };
  await writeFile(MANIFEST_PATH, JSON.stringify(manifest, null, 2), "utf8");

  const finalStat = await stat(OUTPUT_PATH);
  console.log(`\nWrote ${path.relative(ROOT, OUTPUT_PATH)} (${(finalStat.size / 1024 / 1024).toFixed(2)} MB)`);
  return manifest;
}

// ---------------------------------------------------------------------------
// Class-final-cut-specific acceptance checks (reuses checkStreamsPresent/
// checkAvSync/checkVideoFormat/checkFaststart/checkTotalDuration/
// checkNarrationFits/checkLoudnessDucking as-is above - they're generic
// over OUTPUT_PATH/manifest already).
// ---------------------------------------------------------------------------

function checkTotalDurationInRangeClassFinal(totalSec) {
  const ok = totalSec >= 135 && totalSec <= 155;
  const mm = Math.floor(totalSec / 60);
  const ss = (totalSec % 60).toFixed(1).padStart(4, "0");
  return {
    name: "duration budget: total within 2:15-2:35 (135-155s)",
    ok,
    detail: `actual=${totalSec.toFixed(2)}s (${mm}:${ss})`,
  };
}

// Forbidden-phrase guard: the job's three factual corrections require that
// none of these ever appear in the built caption set - "The screen coaches
// for you" (the old C8 key message, replaced because it overstates what
// the product does), "one click" (Repeat Selected needs From/To/Times, not
// one click), and "LEVEL 1/2/3" (ClassSegment carries exactly one
// target_watts - see hub_server/domain/class_models.py:23 - there is no
// per-athlete "level" concept in the product).
const CLASS_FINAL_FORBIDDEN_PATTERNS = [
  { name: '"The screen coaches for you"', re: /screen coaches for you/i },
  { name: '"one click"', re: /\bone click\b/i },
  { name: "LEVEL 1/2/3", re: /\blevel\s*[123]\b/i },
];

function checkForbiddenCaptionText(manifest) {
  const hits = [];
  for (const text of manifest.captionTexts) {
    for (const pat of CLASS_FINAL_FORBIDDEN_PATTERNS) {
      if (pat.re.test(text)) hits.push(`${pat.name} found in "${text}"`);
    }
  }
  const ok = hits.length === 0;
  return {
    name: 'forbidden caption text absent ("the screen coaches for you" / "one click" / LEVEL 1/2/3)',
    ok,
    detail: ok ? `scanned ${manifest.captionTexts.length} caption strings, no hits` : hits.join("\n"),
  };
}

async function runClassFinalChecks(manifest) {
  const results = [];
  results.push(await checkStreamsPresent());
  results.push(await checkAvSync());
  results.push(await checkVideoFormat());
  results.push(await checkFaststart());
  results.push(await checkTotalDuration(manifest.totalSec));
  results.push(checkTotalDurationInRangeClassFinal(manifest.totalSec));
  results.push(checkNarrationFits(manifest));
  results.push(await checkLoudnessDucking(manifest));
  results.push(checkForbiddenCaptionText(manifest));

  console.log("\n================ CLASS FINAL CUT ACCEPTANCE CHECKS ================\n");
  for (const r of results) {
    console.log(`[${r.ok ? "PASS" : "FAIL"}] ${r.name}`);
    console.log(`  ${r.detail.split("\n").join("\n  ")}`);
    console.log("");
  }
  const failed = results.filter((r) => !r.ok);
  if (failed.length > 0) {
    console.log(`FAIL: ${failed.length}/${results.length} checks failed.`);
    process.exitCode = 1;
  } else {
    console.log(`PASS: all ${results.length} checks passed.`);
  }
  return results;
}


async function main() {
  console.log(`cut=${CUT}  dir=${path.relative(ROOT, EN_DIR)}  output=${path.relative(ROOT, OUTPUT_PATH)}`);
  if (CUT === "final") {
    let finalManifest;
    if (SKIP_BUILD) {
      console.log("--skip-build: reusing previous output + manifest.");
      finalManifest = JSON.parse(await readFile(MANIFEST_PATH, "utf8"));
    } else {
      finalManifest = await buildFinal();
    }
    await runFinalChecks(finalManifest);
    return;
  }
  if (CUT === "class_final") {
    let classFinalManifest;
    if (SKIP_BUILD) {
      console.log("--skip-build: reusing previous output + manifest.");
      classFinalManifest = JSON.parse(await readFile(MANIFEST_PATH, "utf8"));
    } else {
      classFinalManifest = await buildClassFinal();
    }
    await runClassFinalChecks(classFinalManifest);
    return;
  }
  let manifest;
  if (SKIP_BUILD) {
    console.log("--skip-build: reusing previous output + manifest.");
    manifest = JSON.parse(await readFile(MANIFEST_PATH, "utf8"));
  } else {
    manifest = await build();
  }
  await runAllChecks(manifest);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
