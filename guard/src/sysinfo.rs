use std::fs;
use std::io;
use std::path::Path;

/// Reads the kernel boot id (normally `/proc/sys/kernel/random/boot_id`,
/// injectable so tests can point at a fixture file instead).
pub fn read_boot_id(path: &Path) -> io::Result<String> {
    Ok(fs::read_to_string(path)?.trim().to_string())
}

/// Reads monotonic uptime in seconds from the first field of
/// `/proc/uptime`-formatted content (injectable path for tests).
pub fn read_uptime_sec(path: &Path) -> io::Result<f64> {
    let contents = fs::read_to_string(path)?;
    let first = contents
        .split_whitespace()
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "empty uptime file"))?;
    first
        .parse::<f64>()
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_and_trims_boot_id() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("boot_id");
        fs::write(&path, "550e8400-e29b-41d4-a716-446655440000\n").unwrap();

        assert_eq!(
            read_boot_id(&path).unwrap(),
            "550e8400-e29b-41d4-a716-446655440000"
        );
    }

    #[test]
    fn reads_first_field_of_uptime_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("uptime");
        fs::write(&path, "12345.67 54321.89\n").unwrap();

        assert_eq!(read_uptime_sec(&path).unwrap(), 12345.67);
    }

    #[test]
    fn missing_uptime_file_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("does-not-exist");
        assert!(read_uptime_sec(&path).is_err());
    }
}
