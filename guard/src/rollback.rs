use std::ffi::OsString;
use std::fs;
use std::io;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::fsutil::write_json_atomic;
use crate::marker::PendingVerifyMarker;

/// Atomically repoints `link_path` at `target`: create a `.tmp` symlink,
/// then rename it over the real link -- mirrors the Python applier's
/// `_point_symlink` (hub_update_applier.py) so there's never a moment with
/// no symlink at all.
pub fn point_symlink(link_path: &Path, target: &Path) -> io::Result<()> {
    let tmp_link = tmp_sibling(link_path);
    if fs::symlink_metadata(&tmp_link).is_ok() {
        fs::remove_file(&tmp_link)?;
    }
    symlink(target, &tmp_link)?;
    fs::rename(&tmp_link, link_path)
}

fn tmp_sibling(link_path: &Path) -> PathBuf {
    let file_name = link_path.file_name().unwrap_or_default();
    let mut name = OsString::from(".");
    name.push(file_name);
    name.push(".tmp");
    link_path.with_file_name(name)
}

#[derive(Debug, Serialize, Clone, PartialEq)]
struct RolledBackRecord {
    previous: String,
    applied: String,
    rolled_back_at_epoch_ms: i64,
}

/// Performs a rollback: repoint the symlink, restart the service through
/// the injected `runner`, then rewrite the marker as `rolled-back.json`
/// (removing `pending-verify.json`). `now_epoch_ms` is recorded on the new
/// marker for diagnostics only.
pub fn roll_back(
    current_link: &Path,
    service_name: &str,
    marker_dir: &Path,
    marker: &PendingVerifyMarker,
    now_epoch_ms: i64,
    runner: impl FnOnce(&[String]),
) -> io::Result<()> {
    let previous = marker
        .previous
        .as_ref()
        .expect("roll_back requires a marker with a previous release");
    point_symlink(current_link, Path::new(previous))?;

    runner(&[
        "systemctl".to_string(),
        "restart".to_string(),
        service_name.to_string(),
    ]);

    let record = RolledBackRecord {
        previous: previous.clone(),
        applied: marker.applied.clone(),
        rolled_back_at_epoch_ms: now_epoch_ms,
    };
    write_json_atomic(&marker_dir.join("rolled-back.json"), &record)?;

    let pending_path = marker_dir.join("pending-verify.json");
    if pending_path.exists() {
        fs::remove_file(&pending_path)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn point_symlink_creates_a_fresh_link() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("releases").join("hub-0.1.0");
        fs::create_dir_all(&target).unwrap();
        let link = dir.path().join("current");

        point_symlink(&link, &target).unwrap();

        assert_eq!(fs::read_link(&link).unwrap(), target);
    }

    #[test]
    fn point_symlink_repoints_an_existing_link() {
        let dir = tempfile::tempdir().unwrap();
        let old_target = dir.path().join("releases").join("hub-0.1.0");
        let new_target = dir.path().join("releases").join("hub-0.2.0");
        fs::create_dir_all(&old_target).unwrap();
        fs::create_dir_all(&new_target).unwrap();
        let link = dir.path().join("current");
        symlink(&old_target, &link).unwrap();

        point_symlink(&link, &new_target).unwrap();

        assert_eq!(fs::read_link(&link).unwrap(), new_target);
    }

    #[test]
    fn roll_back_points_real_symlink_at_previous_release_on_disk() {
        let dir = tempfile::tempdir().unwrap();
        let previous = dir.path().join("releases").join("hub-0.1.0");
        let broken = dir.path().join("releases").join("hub-0.2.0");
        fs::create_dir_all(&previous).unwrap();
        fs::create_dir_all(&broken).unwrap();
        let link = dir.path().join("current");
        symlink(&broken, &link).unwrap();

        let marker = PendingVerifyMarker {
            previous: Some(previous.to_string_lossy().to_string()),
            applied: "hub-0.2.0".to_string(),
            applied_at_epoch_ms: 1,
        };
        fs::write(dir.path().join("pending-verify.json"), "{}").unwrap();

        let mut calls: Vec<Vec<String>> = Vec::new();
        roll_back(
            &link,
            "fitracestudio-hub.service",
            dir.path(),
            &marker,
            42,
            |cmd| calls.push(cmd.to_vec()),
        )
        .unwrap();

        // The real filesystem symlink target, not just the recorded
        // command list -- a rollback that never touched disk would still
        // pass a commands-only assertion.
        assert_eq!(fs::read_link(&link).unwrap(), previous);
        assert_eq!(
            calls,
            vec![vec![
                "systemctl".to_string(),
                "restart".to_string(),
                "fitracestudio-hub.service".to_string()
            ]]
        );
    }

    #[test]
    fn roll_back_rewrites_marker_as_rolled_back_and_removes_pending() {
        let dir = tempfile::tempdir().unwrap();
        let previous = dir.path().join("releases").join("hub-0.1.0");
        fs::create_dir_all(&previous).unwrap();
        let link = dir.path().join("current");
        symlink(dir.path().join("releases").join("hub-0.2.0"), &link).unwrap();
        let pending_path = dir.path().join("pending-verify.json");
        fs::write(&pending_path, "{}").unwrap();

        let marker = PendingVerifyMarker {
            previous: Some(previous.to_string_lossy().to_string()),
            applied: "hub-0.2.0".to_string(),
            applied_at_epoch_ms: 1,
        };

        roll_back(
            &link,
            "fitracestudio-hub.service",
            dir.path(),
            &marker,
            42,
            |_| {},
        )
        .unwrap();

        assert!(!pending_path.exists());
        let rolled_back: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(dir.path().join("rolled-back.json")).unwrap())
                .unwrap();
        assert_eq!(rolled_back["applied"], "hub-0.2.0");
        assert_eq!(
            rolled_back["previous"],
            previous.to_string_lossy().to_string()
        );
        assert_eq!(rolled_back["rolled_back_at_epoch_ms"], 42);
    }
}
