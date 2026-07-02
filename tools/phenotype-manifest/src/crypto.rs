//! Cryptographic signing and verification for manifests
use crate::manifest::{Manifest, VerificationResult};
use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};
use rand::rngs::OsRng;
use rand::RngCore;
use std::fs;
use std::path::Path;

pub fn load_signing_key(path: &Path) -> Result<SigningKey> {
    let content = fs::read_to_string(path)
        .with_context(|| format!("Failed to read key file: {}", path.display()))?;
    if content.contains("-----BEGIN") {
        let pem_doc = pem::parse(&content).map_err(|e| anyhow!("Invalid PEM: {}", e))?;
        if pem_doc.tag.contains("PRIVATE") {
            if pem_doc.contents.len() < 32 {
                return Err(anyhow!("PEM key too short"));
            }
            let mut bytes = [0u8; 32];
            bytes.copy_from_slice(&pem_doc.contents[..32]);
            return Ok(SigningKey::from_bytes(&bytes));
        }
    }
    if let Ok(bytes) = STANDARD.decode(content.trim()) {
        if bytes.len() == 32 {
            let mut arr = [0u8; 32];
            arr.copy_from_slice(&bytes);
            return Ok(SigningKey::from_bytes(&arr));
        }
    }
    if let Ok(bytes) = hex::decode(content.trim()) {
        if bytes.len() == 32 {
            let mut arr = [0u8; 32];
            arr.copy_from_slice(&bytes);
            return Ok(SigningKey::from_bytes(&arr));
        }
    }
    Err(anyhow!("Key must be 32 bytes (Ed25519)"))
}

pub fn load_verifying_key(path: &Path) -> Result<VerifyingKey> {
    let content = fs::read_to_string(path)
        .with_context(|| format!("Failed to read public key: {}", path.display()))?;
    if content.contains("-----BEGIN") {
        let pem_doc = pem::parse(&content).map_err(|e| anyhow!("Invalid PEM: {}", e))?;
        if pem_doc.tag.contains("PUBLIC") {
            if pem_doc.contents.len() < 32 {
                return Err(anyhow!("PEM key too short"));
            }
            let mut bytes = [0u8; 32];
            bytes.copy_from_slice(&pem_doc.contents[..32]);
            return VerifyingKey::from_bytes(&bytes)
                .map_err(|e| anyhow!("Invalid Ed25519 public key: {}", e));
        }
    }
    if let Ok(bytes) = STANDARD.decode(content.trim()) {
        if bytes.len() == 32 {
            let mut arr = [0u8; 32];
            arr.copy_from_slice(&bytes);
            return VerifyingKey::from_bytes(&arr)
                .map_err(|e| anyhow!("Invalid Ed25519 public key: {}", e));
        }
    }
    if let Ok(bytes) = hex::decode(content.trim()) {
        if bytes.len() == 32 {
            let mut arr = [0u8; 32];
            arr.copy_from_slice(&bytes);
            return VerifyingKey::from_bytes(&arr)
                .map_err(|e| anyhow!("Invalid Ed25519 public key: {}", e));
        }
    }
    Err(anyhow!("Public key must be 32 bytes (Ed25519)"))
}

pub fn sign_manifest(manifest: &mut Manifest, key: &SigningKey) -> Result<()> {
    let canonical = manifest.canonical_json()?;
    let signature = key.sign(&canonical);
    manifest.signature = STANDARD.encode(signature.to_bytes());
    manifest.public_key = STANDARD.encode(key.verifying_key().to_bytes());
    Ok(())
}

pub fn verify_manifest(
    manifest: &Manifest,
    pubkey: &VerifyingKey,
    require_all_pillars: bool,
    min_health_score: f64,
    max_age_hours: u64,
) -> VerificationResult {
    let mut errors = Vec::new();
    let mut warnings = Vec::new();
    if let Err(e) = manifest.validate() {
        errors.push(format!("Manifest validation failed: {}", e));
        return VerificationResult::failure(errors);
    }
    let canonical = match manifest.canonical_json() {
        Ok(c) => c,
        Err(e) => {
            errors.push(format!("Canonical serialization failed: {}", e));
            return VerificationResult::failure(errors);
        }
    };
    let signature = match STANDARD.decode(manifest.signature.as_bytes()) {
        Ok(bytes) if bytes.len() == 64 => {
            let mut arr = [0u8; 64];
            arr.copy_from_slice(&bytes);
            Signature::from_bytes(&arr)
        }
        _ => {
            errors.push("Invalid signature encoding".into());
            return VerificationResult::failure(errors);
        }
    };
    if pubkey.verify(&canonical, &signature).is_err() {
        errors.push("Signature verification failed".into());
        return VerificationResult::failure(errors);
    }
    let now = chrono::Utc::now();
    if now > manifest.expires_at {
        errors.push(format!(
            "Manifest expired at {} (now {})",
            manifest.expires_at, now
        ));
    }
    let age_hours = (now - manifest.generated_at).num_hours();
    if age_hours > max_age_hours as i64 {
        warnings.push(format!(
            "Manifest age {}h exceeds max {}h",
            age_hours, max_age_hours
        ));
    }
    if manifest.health_score < min_health_score {
        errors.push(format!(
            "Health score {:.2} below minimum {:.2}",
            manifest.health_score, min_health_score
        ));
    }
    if require_all_pillars {
        let pillars = [
            ("quality", &manifest.pillars.quality),
            ("security", &manifest.pillars.security),
            ("perf", &manifest.pillars.perf),
            ("compliance", &manifest.pillars.compliance),
            ("docs", &manifest.pillars.docs),
        ];
        for (name, pillar) in pillars {
            if pillar.checks.is_empty() && pillar.skip_reason.is_none() {
                errors.push(format!(
                    "Pillar '{}' has no checks and no skip reason",
                    name
                ));
            }
        }
    }
    if !errors.is_empty() {
        return VerificationResult::failure(errors);
    }
    VerificationResult::success(manifest.clone())
}

pub fn generate_keypair() -> (SigningKey, VerifyingKey) {
    let mut csprng = OsRng;
    let mut bytes = [0u8; 32];
    csprng.fill_bytes(&mut bytes);
    let signing_key = SigningKey::from_bytes(&bytes);
    let verifying_key = signing_key.verifying_key();
    (signing_key, verifying_key)
}

pub fn save_keypair(
    signing_key: &SigningKey,
    verifying_key: &VerifyingKey,
    private_path: &Path,
    public_path: &Path,
) -> Result<()> {
    let private_pem = pem::encode(&pem::Pem {
        tag: "PRIVATE KEY".into(),
        contents: signing_key.to_bytes().to_vec(),
    });
    fs::write(private_path, private_pem)?;
    let public_pem = pem::encode(&pem::Pem {
        tag: "PUBLIC KEY".into(),
        contents: verifying_key.to_bytes().to_vec(),
    });
    fs::write(public_path, public_pem)?;
    Ok(())
}
