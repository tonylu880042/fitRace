mod config;
mod decision;
mod fsutil;
mod marker;
mod rollback;
mod state;
mod sysinfo;

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use decision::{decide, Decision, DecisionInput};

const DEFAULT_BASE_DIR: &str = "/opt/fitracestudio";
const BOOT_ID_PATH: &str = "/proc/sys/kernel/random/boot_id";
const UPTIME_PATH: &str = "/proc/uptime";
const SERVICE_NAME: &str = "fitracestudio-hub.service";

fn run_command(args: &[String]) {
    if let Some((program, rest)) = args.split_first() {
        let _ = Command::new(program).args(rest).status();
    }
}

fn epoch_ms_now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

fn main() {
    let base = PathBuf::from(
        std::env::var("FITRACE_GUARD_BASE_DIR").unwrap_or_else(|_| DEFAULT_BASE_DIR.to_string()),
    );
    let marker_path = base.join("pending-verify.json");
    let state_path = base.join("guard-state.json");
    let config_path = base.join("guard-config.json");
    let current_link = base.join("current");

    let config = config::load_config(&std::fs::read_to_string(&config_path).unwrap_or_default());
    let marker = marker::load_marker(&marker_path);
    let stored_state = state::load_state(&state_path);
    let boot_id = sysinfo::read_boot_id(Path::new(BOOT_ID_PATH)).unwrap_or_default();
    let uptime_sec = sysinfo::read_uptime_sec(Path::new(UPTIME_PATH)).unwrap_or(0.0);

    // TODO(phase 2, network): wire a real /health probe. Phase 2 supplies
    // the real probe; until then guard deliberately takes no action rather
    // than guessing -- decide() treats unknown health as NoAction, never as
    // healthy or unhealthy.
    let health: Option<bool> = None;

    let input = DecisionInput {
        marker: marker.as_ref(),
        state: stored_state.as_ref(),
        current_boot_id: &boot_id,
        current_uptime_sec: uptime_sec,
        health,
        t_verify_sec: config.t_verify_sec,
    };

    match decide(&input) {
        Decision::NoAction => {}
        Decision::HealthyClearMarker => {
            let _ = std::fs::remove_file(&marker_path);
        }
        Decision::Wait { next_state } => {
            let _ = state::save_state_if_changed(&state_path, &next_state);
        }
        Decision::RollBack { next_state, .. } => {
            let _ = state::save_state_if_changed(&state_path, &next_state);
            if let Some(m) = &marker {
                let _ = rollback::roll_back(
                    &current_link,
                    SERVICE_NAME,
                    &base,
                    m,
                    epoch_ms_now(),
                    run_command,
                );
            }
        }
    }
}
