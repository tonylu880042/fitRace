// Acceptance checker for the demo-video recording pipeline.
//
// Verifies that every expected scene clip (s01_intro.webm .. s14_outro.webm)
// plus the concatenated demo_full_4min.mp4 exist, are non-trivially sized,
// and have an actual duration (read via ffprobe) within +/-25% of the
// target duration declared in DEMO_SCRIPT.md's "自動化錄製分鏡表" shot
// table. Exits non-zero and prints a PASS/FAIL table if anything is
// missing, empty, or out of tolerance.
//
// FITRACE_DEMO_LANG=en checks the English cut in output/videos/en/ instead
// of the default zh-TW cut in output/videos/. Target durations are
// language-independent (scene order/timing is identical for both cuts), so
// both language modes read the same DEMO_SCRIPT.md shot table - it is not
// duplicated per language.

import { spawn } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";

const ROOT = process.cwd();
const DEMO_SCRIPT_PATH = path.join(ROOT, "DEMO_SCRIPT.md");
const LANG = process.env.FITRACE_DEMO_LANG === "en" ? "en" : "zh";
const OUTPUT_DIR = LANG === "en"
  ? path.join(ROOT, "output/videos/en")
  : path.join(ROOT, "output/videos");
const FFPROBE = "/opt/homebrew/bin/ffprobe";
const TOLERANCE = 0.25;
const MIN_BYTES = 10 * 1024;

// S1..S14 map to these filenames in table order (fixed by the pipeline spec;
// DEMO_SCRIPT.md's table has no filename column, only scene id/name/seconds).
const SCENE_FILENAMES = [
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
];

function parseTableRow(line) {
  // | S1 | 開場 | `/` | 靜態推近 | 12 | 場館級即時競賽系統 |
  const cells = line
    .split("|")
    .map((cell) => cell.trim())
    .filter((_, index, array) => index > 0 && index < array.length - 1);
  if (cells.length < 6) return null;
  const idMatch = cells[0].match(/^S(\d+)$/);
  if (!idMatch) return null;
  const seconds = Number(cells[4]);
  if (!Number.isFinite(seconds) || seconds <= 0) return null;
  return {
    id: cells[0],
    index: Number(idMatch[1]),
    name: cells[1],
    seconds,
    subtitle: cells[5],
  };
}

async function loadScenesFromDemoScript() {
  const text = await readFile(DEMO_SCRIPT_PATH, "utf8");
  const lines = text.split("\n");
  const headingIndex = lines.findIndex((line) => line.includes("自動化錄製分鏡表"));
  if (headingIndex === -1) {
    throw new Error(`Could not find "自動化錄製分鏡表" heading in ${DEMO_SCRIPT_PATH}`);
  }
  const scenes = [];
  for (const line of lines.slice(headingIndex)) {
    if (!line.trim().startsWith("|")) {
      if (scenes.length > 0) break; // left the table
      continue;
    }
    const row = parseTableRow(line);
    if (row) scenes.push(row);
  }
  if (scenes.length !== 14) {
    throw new Error(`Expected 14 scenes (S1-S14) in DEMO_SCRIPT.md's shot table, found ${scenes.length}`);
  }
  scenes.sort((a, b) => a.index - b.index);
  return scenes;
}

function ffprobeDuration(filePath) {
  return new Promise((resolve, reject) => {
    const child = spawn(FFPROBE, [
      "-v", "error",
      "-show_entries", "format=duration",
      "-of", "csv=p=0",
      filePath,
    ]);
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk.toString()));
    child.stderr.on("data", (chunk) => (stderr += chunk.toString()));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`ffprobe failed for ${filePath} (exit ${code}): ${stderr.trim()}`));
        return;
      }
      const value = Number(stdout.trim());
      if (!Number.isFinite(value)) {
        reject(new Error(`ffprobe returned no duration for ${filePath}: "${stdout.trim()}"`));
        return;
      }
      resolve(value);
    });
  });
}

async function checkFile(label, filePath, targetSeconds) {
  const result = { label, filePath, targetSeconds, ok: false, reasons: [] };

  let fileStat;
  try {
    fileStat = await stat(filePath);
  } catch (_) {
    result.reasons.push("missing");
    return result;
  }
  if (!fileStat.isFile()) {
    result.reasons.push("not a regular file");
    return result;
  }
  result.bytes = fileStat.size;
  if (fileStat.size < MIN_BYTES) {
    result.reasons.push(`too small (${fileStat.size} bytes < ${MIN_BYTES})`);
  }

  try {
    const duration = await ffprobeDuration(filePath);
    result.actualSeconds = duration;
    const low = targetSeconds * (1 - TOLERANCE);
    const high = targetSeconds * (1 + TOLERANCE);
    if (duration < low || duration > high) {
      result.reasons.push(
        `duration ${duration.toFixed(1)}s outside +/-25% of target ${targetSeconds}s (${low.toFixed(1)}-${high.toFixed(1)}s)`,
      );
    }
  } catch (error) {
    result.reasons.push(error.message);
  }

  result.ok = result.reasons.length === 0;
  return result;
}

function formatRow(result) {
  const status = result.ok ? "PASS" : "FAIL";
  const size = result.bytes != null ? `${(result.bytes / 1024).toFixed(0)}KB` : "--";
  const actual = result.actualSeconds != null ? `${result.actualSeconds.toFixed(1)}s` : "--";
  const target = `${result.targetSeconds}s`;
  const reasons = result.reasons.length ? result.reasons.join("; ") : "-";
  return { status, label: result.label, target, actual, size, reasons };
}

function printTable(rows) {
  const cols = ["status", "label", "target", "actual", "size", "reasons"];
  const headers = { status: "STATUS", label: "SCENE", target: "TARGET", actual: "ACTUAL", size: "SIZE", reasons: "NOTES" };
  const widths = {};
  for (const col of cols) {
    widths[col] = Math.max(
      headers[col].length,
      ...rows.map((row) => String(row[col]).length),
    );
  }
  const line = (row) => cols.map((col) => String(row[col]).padEnd(widths[col])).join("  ");
  console.log(line(headers));
  console.log(cols.map((col) => "-".repeat(widths[col])).join("  "));
  for (const row of rows) console.log(line(row));
}

async function main() {
  console.log(`lang=${LANG}  dir=${path.relative(ROOT, OUTPUT_DIR)}`);
  const scenes = await loadScenesFromDemoScript();
  const totalTargetSeconds = scenes.reduce((sum, scene) => sum + scene.seconds, 0);

  const checks = [];
  for (let i = 0; i < scenes.length; i += 1) {
    const scene = scenes[i];
    const filename = SCENE_FILENAMES[i];
    const filePath = path.join(OUTPUT_DIR, filename);
    checks.push(await checkFile(`${scene.id} ${filename}`, filePath, scene.seconds));
  }

  const fullVideoPath = path.join(OUTPUT_DIR, "demo_full_4min.mp4");
  checks.push(await checkFile("demo_full_4min.mp4", fullVideoPath, totalTargetSeconds));

  const rows = checks.map(formatRow);
  printTable(rows);

  const failed = checks.filter((c) => !c.ok);
  console.log("");
  if (failed.length) {
    console.log(`FAIL: ${failed.length}/${checks.length} checks failed.`);
    process.exit(1);
  } else {
    console.log(`PASS: all ${checks.length} checks passed.`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
