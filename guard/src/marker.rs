use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;

/// The applier's `pending-verify.json`, written before it repoints the
/// `current` symlink at a new release. `applied_at_epoch_ms` is carried
/// for diagnostics only -- decision logic must never read it (D-3): the
/// Pi 5 has no RTC, so wall-clock deltas are garbage after a clock jump.
#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct PendingVerifyMarker {
    pub previous: Option<String>,
    pub applied: String,
    pub applied_at_epoch_ms: i64,
}

/// Reads and parses the marker at `path`. Returns `None` if the file is
/// missing or its contents don't parse -- both are treated identically by
/// the decision function (nothing safe to act on).
pub fn load_marker(path: &Path) -> Option<PendingVerifyMarker> {
    let contents = fs::read_to_string(path).ok()?;
    serde_json::from_str(&contents).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_well_formed_marker() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("pending-verify.json");
        fs::write(
            &path,
            r#"{"previous":"/opt/fitracestudio/releases/hub-0.2.1","applied":"hub-0.2.2","applied_at_epoch_ms":1757212800000}"#,
        )
        .unwrap();

        let marker = load_marker(&path).expect("marker should parse");
        assert_eq!(
            marker.previous.as_deref(),
            Some("/opt/fitracestudio/releases/hub-0.2.1")
        );
        assert_eq!(marker.applied, "hub-0.2.2");
        assert_eq!(marker.applied_at_epoch_ms, 1757212800000);
    }

    #[test]
    fn parses_a_marker_with_null_previous() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("pending-verify.json");
        fs::write(
            &path,
            r#"{"previous":null,"applied":"hub-0.1.0","applied_at_epoch_ms":1}"#,
        )
        .unwrap();

        let marker = load_marker(&path).expect("marker should parse");
        assert_eq!(marker.previous, None);
    }

    #[test]
    fn missing_file_yields_none() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("does-not-exist.json");
        assert_eq!(load_marker(&path), None);
    }

    #[test]
    fn unparseable_contents_yield_none() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("pending-verify.json");
        fs::write(&path, "not json at all {{{").unwrap();
        assert_eq!(load_marker(&path), None);
    }
}
