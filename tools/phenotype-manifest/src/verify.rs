//! Manifest verification logic

use crate::cli::{OutputFormat, VerifyArgs};
use crate::config::AppConfig;
use crate::crypto::{load_verifying_key, verify_manifest};
use crate::manifest::{Manifest, VerificationResult};
use crate::schema::validate_manifest as schema_validate_manifest;
use anyhow::{anyhow, Context, Result};
use std::fs;
use tracing::info;

pub fn verify_manifest_cmd(args: VerifyArgs, cfg: &AppConfig) -> Result<()> {
    let manifest_path = args
        .manifest
        .clone()
        .unwrap_or_else(|| cfg.manifest_path.clone());
    let pubkey_path = args
        .pubkey
        .clone()
        .unwrap_or_else(|| AppConfig::expand_home(&cfg.public_key_path));
    let pubkey_path = AppConfig::expand_home(&pubkey_path);

    info!("Verifying manifest: {}", manifest_path.display());

    // 1. Read manifest
    let content = fs::read_to_string(&manifest_path)
        .with_context(|| format!("Failed to read manifest: {}", manifest_path.display()))?;
    let json: serde_json::Value =
        serde_json::from_str(&content).context("Failed to parse manifest JSON")?;

    // 2. Schema validation (optional, uses configured URL)
    if let Err(errs) = schema_validate_manifest(&json, &cfg.schema_url) {
        eprintln!("Schema validation warnings: {}", errs.join("; "));
    }

    let manifest: Manifest =
        serde_json::from_value(json).context("Failed to deserialize manifest")?;

    // 3. Load public key
    let pubkey = load_verifying_key(&pubkey_path)
        .with_context(|| format!("Failed to load public key: {}", pubkey_path.display()))?;

    // 4. Verify
    let require_all = args.require_all_pillars.unwrap_or(cfg.require_all_pillars);
    let min_health = args.min_health_score.unwrap_or(cfg.fail_below);
    let max_age = args.max_age_hours.unwrap_or(cfg.max_age_hours);
    let result = verify_manifest(&manifest, &pubkey, require_all, min_health, max_age);

    // 5. Output
    match args.format {
        OutputFormat::Human => print_human(&result),
        OutputFormat::Json => println!("{}", serde_json::to_string_pretty(&result)?),
        OutputFormat::Github => print_github(&result),
    }

    // 6. Exit code
    if !result.valid {
        return Err(anyhow!("Manifest verification failed"));
    }

    if result.health_score.unwrap_or(0.0) < min_health {
        return Err(anyhow!("Health score below threshold"));
    }

    Ok(())
}

fn print_human(result: &VerificationResult) {
    if result.valid {
        println!("Manifest VALID");
    } else {
        println!("Manifest INVALID");
    }

    if let Some(health) = result.health_score {
        println!("Health: {:.1}%", health * 100.0);
    }

    println!(
        "Pillars: {}/{} passed",
        result.pillars_passed, result.pillars_total
    );
    println!(
        "Checks:  {}/{} passed",
        result.checks_passed, result.checks_total
    );

    if !result.errors.is_empty() {
        println!("\nErrors:");
        for e in &result.errors {
            println!("  - {}", e);
        }
    }

    if !result.warnings.is_empty() {
        println!("\nWarnings:");
        for w in &result.warnings {
            println!("  - {}", w);
        }
    }

    if let Some(manifest) = &result.manifest {
        println!("\n{}", manifest.format_human());
    }
}

fn print_github(result: &VerificationResult) {
    for error in &result.errors {
        println!("::error::{}", error);
    }
    for warning in &result.warnings {
        println!("::warning::{}", warning);
    }

    if let Some(health) = result.health_score {
        println!("::notice::Health score: {:.1}%", health * 100.0);
    }
    println!(
        "::notice::Pillars passed: {}/{}",
        result.pillars_passed, result.pillars_total
    );
    println!(
        "::notice::Checks passed: {}/{}",
        result.checks_passed, result.checks_total
    );
}
