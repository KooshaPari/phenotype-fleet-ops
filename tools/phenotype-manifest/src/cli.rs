//! CLI argument definitions

use clap::{Args, ValueEnum};
use std::path::PathBuf;

#[derive(Args, Debug, Clone)]
pub struct GenerateArgs {
    /// Path to Ed25519 private key (PEM or raw)
    #[arg(long, short)]
    pub key: Option<PathBuf>,

    /// Output manifest path
    #[arg(long, short)]
    pub output: Option<PathBuf>,

    /// Require all 5 pillars to pass
    #[arg(long)]
    pub require_all_pillars: Option<bool>,

    /// Minimum health score 0.0-1.0 (fail if below)
    #[arg(long)]
    pub fail_below: Option<f64>,

    /// Maximum manifest age in hours
    #[arg(long)]
    pub max_age_hours: Option<u64>,

    /// Skip specific pillars (comma-separated)
    #[arg(long, value_delimiter = ',')]
    pub skip_pillars: Vec<String>,

    /// Output JSON to stdout instead of file
    #[arg(long)]
    pub stdout: bool,

    /// Show human-readable output
    #[arg(long, default_value = "true")]
    pub human: bool,
}

#[derive(Args, Debug, Clone)]
pub struct VerifyArgs {
    /// Path to signed manifest
    #[arg(long, short)]
    pub manifest: Option<PathBuf>,

    /// Path to Ed25519 public key (PEM or raw)
    #[arg(long, short = 'p')]
    pub pubkey: Option<PathBuf>,

    /// Require all 5 pillars to be present
    #[arg(long)]
    pub require_all_pillars: Option<bool>,

    /// Minimum health score 0.0-1.0
    #[arg(long)]
    pub min_health_score: Option<f64>,

    /// Maximum manifest age in hours
    #[arg(long)]
    pub max_age_hours: Option<u64>,

    /// Output format
    #[arg(long, value_enum, default_value = "human")]
    pub format: OutputFormat,

    /// Fail on warnings
    #[arg(long)]
    pub strict: bool,
}

#[derive(Args, Debug, Clone)]
pub struct ShowArgs {
    /// Path to signed manifest
    #[arg(long, short)]
    pub manifest: Option<PathBuf>,

    /// Output as JSON
    #[arg(long)]
    pub json: bool,
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum OutputFormat {
    Human,
    Json,
    Github, // GitHub Actions annotations
}
