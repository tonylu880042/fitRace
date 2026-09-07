use serde::Deserialize;

/// How long a pending update is given to prove itself healthy before guard
/// rolls it back, absent the boot-count escape hatch.
pub const DEFAULT_T_VERIFY_SEC: f64 = 600.0;

#[derive(Debug, Deserialize, Clone, PartialEq)]
#[serde(default)]
pub struct Config {
    pub t_verify_sec: f64,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            t_verify_sec: DEFAULT_T_VERIFY_SEC,
        }
    }
}

/// Parses `contents` as a `Config`. Any parse failure -- missing file
/// content, malformed JSON, wrong types -- falls back to compiled-in
/// defaults (G-3): a recovery component must never refuse to start because
/// its config file is corrupt.
pub fn load_config(contents: &str) -> Config {
    serde_json::from_str(contents).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_valid_config() {
        let config = load_config(r#"{"t_verify_sec": 120.0}"#);
        assert_eq!(config.t_verify_sec, 120.0);
    }

    #[test]
    fn falls_back_to_defaults_on_garbage() {
        let config = load_config("{ this is not json");
        assert_eq!(config, Config::default());
        assert_eq!(config.t_verify_sec, DEFAULT_T_VERIFY_SEC);
    }

    #[test]
    fn falls_back_to_defaults_on_empty_contents() {
        let config = load_config("");
        assert_eq!(config, Config::default());
    }

    #[test]
    fn missing_field_falls_back_to_its_default() {
        let config = load_config("{}");
        assert_eq!(config.t_verify_sec, DEFAULT_T_VERIFY_SEC);
    }
}
