use std::ffi::OsString;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// Writes `value` to `path` atomically: serialize to a `.tmp` sibling, then
/// rename over the real path. Mirrors the Python applier's
/// `tmp + os.replace` pattern (race_settings_store.py) so there's never a
/// half-written file on disk.
pub fn write_json_atomic<T: serde::Serialize>(path: &Path, value: &T) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp_path = tmp_sibling(path);
    fs::write(&tmp_path, serde_json::to_vec(value)?)?;
    fs::rename(&tmp_path, path)
}

fn tmp_sibling(path: &Path) -> PathBuf {
    let mut name: OsString = path.as_os_str().to_owned();
    name.push(".tmp");
    PathBuf::from(name)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn writes_and_leaves_no_tmp_file_behind() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("nested").join("thing.json");

        write_json_atomic(&path, &json!({"a": 1})).unwrap();

        assert_eq!(
            fs::read_to_string(&path).unwrap(),
            serde_json::to_string(&json!({"a": 1})).unwrap()
        );
        assert!(!tmp_sibling(&path).exists());
    }
}
