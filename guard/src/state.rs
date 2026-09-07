use serde::{Deserialize, Serialize};
use std::io;
use std::path::Path;

use crate::fsutil::write_json_atomic;

/// Guard's own memory of a pending marker across boots, persisted to
/// `guard-state.json`.
#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct GuardState {
    pub boot_id: String,
    pub first_seen_uptime_sec: f64,
    pub boot_count: u32,
}

pub fn load_state(path: &Path) -> Option<GuardState> {
    let contents = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&contents).ok()
}

/// Persists `state` to `path` only when it actually differs from what's
/// already there (G-18). SD-card corruption is this product's #1 field
/// failure; a recovery component that writes on every tick manufactures
/// the very failure it exists to prevent. Returns whether a write happened.
pub fn save_state_if_changed(path: &Path, state: &GuardState) -> io::Result<bool> {
    if load_state(path).as_ref() == Some(state) {
        return Ok(false);
    }
    write_json_atomic(path, state)?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn sample() -> GuardState {
        GuardState {
            boot_id: "boot-a".to_string(),
            first_seen_uptime_sec: 12.5,
            boot_count: 1,
        }
    }

    #[test]
    fn writes_state_that_does_not_exist_yet() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("guard-state.json");

        let wrote = save_state_if_changed(&path, &sample()).unwrap();

        assert!(wrote);
        assert_eq!(load_state(&path), Some(sample()));
    }

    #[test]
    fn does_not_touch_disk_when_state_is_unchanged() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("guard-state.json");
        save_state_if_changed(&path, &sample()).unwrap();
        let mtime_before = fs::metadata(&path).unwrap().modified().unwrap();

        // Give the filesystem clock a moment to move, so a spurious rewrite
        // would show up as a changed mtime.
        std::thread::sleep(std::time::Duration::from_millis(20));

        let wrote = save_state_if_changed(&path, &sample()).unwrap();
        let mtime_after = fs::metadata(&path).unwrap().modified().unwrap();

        assert!(!wrote, "must not report a write when content is identical");
        assert_eq!(
            mtime_before, mtime_after,
            "file must not be rewritten when content is unchanged"
        );
    }

    #[test]
    fn writes_again_when_state_actually_changes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("guard-state.json");
        save_state_if_changed(&path, &sample()).unwrap();

        let mut changed = sample();
        changed.boot_count = 2;
        let wrote = save_state_if_changed(&path, &changed).unwrap();

        assert!(wrote);
        assert_eq!(load_state(&path), Some(changed));
    }

    #[test]
    fn unparseable_existing_file_is_treated_as_no_state() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("guard-state.json");
        fs::write(&path, "not json").unwrap();

        assert_eq!(load_state(&path), None);
    }
}
