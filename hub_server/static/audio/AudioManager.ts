/**
 * AudioManager — FitRaceStudio
 *
 * Manages race audio via Howler.js audio sprite for low-latency,
 * cross-browser playback.  Satisfies the Web Audio API autoplay
 * policy by requiring an explicit user gesture before initialising
 * the AudioContext.
 *
 * Usage:
 *   1. Render <audio-unlock-overlay> so the user can “Click to
 *      Enter Race Mode”.
 *   2. On click → AudioManager.unlock() → AudioManager is ready.
 *   3. Call AudioManager.playRaceStart(), .checkMilestone(), etc.
 */

// ─── Sprite definition (matches generated race-sprites.mp3) ───────

interface SpriteMap {
  [name: string]: [number, number]; // [startSec, durationSec]
}

// ─── Public API ────────────────────────────────────────────────────

export class AudioManager {
  private static howl: Howl | null = null;
  private static unlocked = false;
  private static sprintLoopId: number | null = null;

  // Sprite offsets are loaded from /static/audio/race-sprites.json
  private static sprite: SpriteMap | null = null;

  // ── Initialisation ──────────────────────────────────────────────

  /** Must be called from a user-gesture handler (click / touch). */
  static async unlock(): Promise<void> {
    if (this.unlocked) return;

    const res = await fetch("/static/audio/race-sprites.json");
    this.sprite = await res.json();

    this.howl = new Howl({
      src: ["/static/audio/race-sprites.mp3"],
      sprite: this.sprite,
      format: ["mp3"],
      preload: true,
      onloaderror: (_id: number, msg: unknown) =>
        console.warn("AudioManager: load error", msg),
    });

    // Play a silent snippet so the AudioContext is officially resumed
    this.howl.play("start_horn");
    // Immediately stop it — the browser now considers AudioContext
    // "allowed to start"
    this.howl.stop();

    this.unlocked = true;
  }

  // ── Event triggers ──────────────────────────────────────────────

  /** Race start horn / chime. */
  static playRaceStart(): void {
    if (!this.ready()) return;
    this.play("start_horn");
  }

  /**
   * Called on every telemetry tick with 0–100.
   * Fires milestone_ping + vo_halfway when crossing 50 %.
   */
  static checkMilestone(pct: number): void {
    if (!this.ready() || this.milestoneFired) return;
    if (pct >= 50) {
      this.milestoneFired = true;
      this.play("milestone_ping");
      // VO plays slightly after the ping so they don't clash
      setTimeout(() => this.play("vo_halfway"), 400);
    }
  }

  /** Fade in the sprint tension loop and play the VO callout. */
  static triggerFinalSprint(): void {
    if (!this.ready() || this.sprintLoopId !== null) return;
    this.sprintLoopId = this.howl!.play("sprint_loop");
    this.howl!.loop(true, this.sprintLoopId);
    this.howl!.fade(0, 0.6, 800, this.sprintLoopId);
    setTimeout(() => this.play("vo_final"), 600);
  }

  /** Brief swoosh / alert for a position overtake. */
  static playOvertake(): void {
    if (!this.ready()) return;
    this.play("overtake_swoosh");
  }

  // ── Internals ───────────────────────────────────────────────────

  private static milestoneFired = false;

  /** Reset milestone + sprint state when a new race begins. */
  static reset(): void {
    this.milestoneFired = false;
    if (this.sprintLoopId !== null && this.howl) {
      this.howl.fade(0.6, 0, 400, this.sprintLoopId);
      const id = this.sprintLoopId;
      setTimeout(() => { this.howl?.stop(id); }, 450);
      this.sprintLoopId = null;
    }
    this.howl?.stop();
  }

  private static ready(): boolean {
    if (!this.unlocked || !this.howl) {
      console.warn("AudioManager: not unlocked yet");
      return false;
    }
    return true;
  }

  private static play(name: string): void {
    this.howl!.play(name);
  }
}
