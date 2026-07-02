//! Centralized configuration for phenotype-manifest.
//!
//! Configuration is loaded (in order of increasing precedence) from:
//! 1. Compiled-in defaults
//! 2. `~/.config/phenotype-manifest/config.toml` (user-wide)
//! 3. `.phenotype-manifest.toml` in CWD or nearest ancestor (project-local)
//! 4. Environment variables with prefix `PM_` (e.g. `PM_PRIVATE_KEY_PATH`)
//!
//! Every field has a sensible default so no config file is required.

use figment::providers::{Env, Format, Serialized, Toml};
use figment::Figment;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

// ── Default helpers ────────────────────────────────────────────────────────

fn default_private_key_path() -> PathBuf {
    PathBuf::from("~/.ssh/manifest")
}
fn default_public_key_path() -> PathBuf {
    PathBuf::from(".github/manifest.pubkey.pem")
}
fn default_manifest_path() -> PathBuf {
    PathBuf::from(".manifest.signed.json")
}
fn default_fail_below() -> f64 {
    0.90
}
fn default_max_age_hours() -> u64 {
    24
}
fn default_require_all_pillars() -> bool {
    true
}
fn default_schema_url() -> String {
    "https://phenotype.dev/schemas/manifest-1.0.json".into()
}
fn default_pillar_schema_url() -> String {
    "https://phenotype.dev/schemas/pillar-1.0.json".into()
}
fn default_lefthook_template() -> PathBuf {
    PathBuf::from("../../governance/lefthook.yml")
}
fn default_log_level() -> String {
    "info".into()
}

// ── Check definition (overridable pillar command) ──────────────────────────

/// A single check definition — mirrors `pillar::Check` but is serializable.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CheckDef {
    pub name: String,
    pub command: String,
    #[serde(default)]
    pub globs: Vec<String>,
}

/// Optional pillar check overrides.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PillarCheckConfig {
    pub quality: Vec<CheckDef>,
    pub security: Vec<CheckDef>,
    pub perf: Vec<CheckDef>,
    pub compliance: Vec<CheckDef>,
    pub docs: Vec<CheckDef>,
}

// ── Top-level config ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    // ── Paths ──────────────────────────────────────────────────────────
    #[serde(default = "default_private_key_path")]
    pub private_key_path: PathBuf,

    #[serde(default = "default_public_key_path")]
    pub public_key_path: PathBuf,

    #[serde(default = "default_manifest_path")]
    pub manifest_path: PathBuf,

    // ── Thresholds ─────────────────────────────────────────────────────
    #[serde(default = "default_fail_below")]
    pub fail_below: f64,

    #[serde(default = "default_max_age_hours")]
    pub max_age_hours: u64,

    #[serde(default = "default_require_all_pillars")]
    pub require_all_pillars: bool,

    // ── Schema ─────────────────────────────────────────────────────────
    #[serde(default = "default_schema_url")]
    pub schema_url: String,

    #[serde(default = "default_pillar_schema_url")]
    pub pillar_schema_url: String,

    // ── DevOps ─────────────────────────────────────────────────────────
    #[serde(default = "default_lefthook_template")]
    pub lefthook_template: PathBuf,

    // ── Runtime ────────────────────────────────────────────────────────
    #[serde(default = "default_log_level")]
    pub log_level: String,

    // ── Pillar overrides (optional) ────────────────────────────────────
    #[serde(default)]
    pub pillar_checks: Option<PillarCheckConfig>,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            private_key_path: default_private_key_path(),
            public_key_path: default_public_key_path(),
            manifest_path: default_manifest_path(),
            fail_below: default_fail_below(),
            max_age_hours: default_max_age_hours(),
            require_all_pillars: default_require_all_pillars(),
            schema_url: default_schema_url(),
            pillar_schema_url: default_pillar_schema_url(),
            lefthook_template: default_lefthook_template(),
            log_level: default_log_level(),
            pillar_checks: None,
        }
    }
}

impl AppConfig {
    /// Load configuration from filesystem + environment.
    ///
    /// Precedence (lowest → highest):
    /// 1. Compiled defaults
    /// 2. `~/.config/phenotype-manifest/config.toml`
    /// 3. `.phenotype-manifest.toml` in CWD
    /// 4. Environment variables prefixed `PM_`
    pub fn load() -> Self {
        let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
        let user_cfg: PathBuf = [
            home.as_str(),
            ".config",
            "phenotype-manifest",
            "config.toml",
        ]
        .iter()
        .collect();

        Figment::from(Serialized::defaults(Self::default()))
            .merge(Toml::file(user_cfg).nested())
            .merge(Toml::file(".phenotype-manifest.toml").nested())
            .merge(Env::prefixed("PM_").ignore(&["HOME"]))
            .extract()
            .expect("Failed to parse phenotype-manifest config")
    }

    /// Expand `~` in a path to `$HOME`.
    pub fn expand_home(path: &Path) -> PathBuf {
        let s = path.to_string_lossy().to_string();
        if let Some(stripped) = s.strip_prefix("~/") {
            let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
            PathBuf::from(format!("{}/{}", home, stripped))
        } else {
            path.to_path_buf()
        }
    }
}
