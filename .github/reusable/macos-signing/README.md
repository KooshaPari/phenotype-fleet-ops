# macOS Signing + Notarization Template

Reusable scripts and workflows for shipping signed, notarized macOS builds
on **self-hosted runners** (zero GitHub-hosted minutes).

## Why this exists

All 18 `kooshapari` repos that ship a macOS app were either:
- Missing `Entitlements.plist` entirely (`OmniRoute`, `sharecli`, `Tracera`,
  `phenotype-tooling`, `phenotype-omlx`, `phenotype-registry`, `phenotype-journeys`,
  `phenotype-fleet-ops`, `phenotype-traceability-spine`, `argis-extensions`,
  `cliproxyapi-plusplus`, `phenoAI`, `Agentora`, `phenotype-apps`,
  `phenotype-go-sdk`, `phenotype-python-sdk`, `pheno-harness`, `Melosviz`,
  `cockpit`, `pheno`)
- Using GitHub-hosted `macos-15-xlarge` runners (billed ~$0.08/min × N builds)
- Using `helios-cli`-style AKV PKCS#11 flow that needs Azure Key Vault (which
  this org uses only for Windows EV code-signing — not Apple)

This template removes all three blockers:
- Provides minimal `Entitlements.plist` templates for Tauri / Electron / Swift
- Targets self-hosted Mac runners (e.g. `Kooshas-Laptop.local-hwledger`)
- Pulls secrets from **Infisical** (the org's existing secret store; see
  `phenotype-fleet-ops/.github/workflows/infisical.yml`)

## Files

```
macos-signing/
├── fetch_apple_secrets.sh            Infisical → env (one source of truth)
├── sign_macos_native.sh              codesign + temp keychain (Mac runner)
├── notarize_macos_native.sh          xcrun notarytool (Mac runner)
├── sign_macos_rcodesign.sh           rcodesign fallback (Linux runner)
├── notarize_macos_rcodesign.sh       rcodesign notarize fallback (Linux runner)
├── entitlements/
│   ├── tauri-base.plist              minimal Tauri 2 entitlements
│   ├── electron-base.plist           minimal Electron entitlements
│   └── swift-base.plist              minimal Swift/native entitlements
└── workflows/
    ├── release-macos.yml             sample reusable workflow
    └── fetch-secrets.yml             composable secret-fetch workflow
```

## Required Infisical secrets

Set these in **Infisical → project `8efe392e-56a6-4c3c-89f9-8141183dd7e8` →
path `/apple`** (the `phenotype-fleet-ops` default project):

| Key | Source | Notes |
|---|---|---|
| `APPLE_TEAM_ID` | Apple Developer account | 10-char |
| `APPLE_DEVELOPER_ID_P12_BASE64` | `base64 -i DeveloperID.p12 \| tr -d '\n'` | one-time, password-protected |
| `APPLE_DEVELOPER_ID_P12_PASSWORD` | password you set when exporting | |
| `APPLE_NOTARIZATION_ISSUER_ID` | App Store Connect → Users → Keys | UUID |
| `APPLE_NOTARIZATION_KEY_ID` | App Store Connect API key id | e.g. `ABC123XYZ` |
| `APPLE_NOTARIZATION_KEY_P8_BASE64` | `base64 -i AuthKey_XXX.p8 \| tr -d '\n'` | one-time |

Optional per-repo overrides (set in GH secrets or Infisical path
`/apple/<repo-slug>`):

| Key | Purpose |
|---|---|
| `APPLE_BUNDLE_ID` | bundle identifier in `tauri.conf.json` / `Info.plist` |
| `APPLE_SIGNING_IDENTITY` | `"Developer ID Application: Name (TEAMID)"` |

## Step-by-step: adopt in a new repo

```bash
# 1. Vendor the template into your repo
cp -R templates/macos-signing/ .github/scripts/

# 2. Add the entitlements to your build config
#    Tauri:    apps/<desktop>/src-tauri/Entitlements.plist (use tauri-base.plist)
#    Electron: build/entitlements.mac.plist (use electron-base.plist) + package.json:
#      "mac": { "entitlements": "build/entitlements.mac.plist", "hardenedRuntime": true }
#    Swift:    apps/<app>/<App>.entitlements (use swift-base.plist)

# 3. Add the release workflow
cp templates/workflows/release-macos.yml .github/workflows/release-macos.yml
# Edit: bundle identifier, app name, target paths

# 4. Provision Infisical secrets (one-time, per org)

# 5. Register the self-hosted runner at org level:
gh api -X POST /orgs/kooshapari/actions/runners/registration-token \
  -q '.token' -f runner_name=kooshapari-mac-sign-01
# then: ./config.sh --url https://github.com/kooshapari --token <t> --labels mac-signing,self-hosted,macos,arm64

# 6. Test with a tag push
git tag v0.1.0-rc1 && git push --tags
```

## Why two signing backends?

| Path | Runner | Use when |
|---|---|---|
| `sign_macos_native.sh` + `notarize_macos_native.sh` | macOS (arm64 or x86_64) | Preferred. Native tools, faster, fully featured. |
| `sign_macos_rcodesign.sh` + `notarize_macos_rcodesign.sh` | Linux or macOS | Fallback. Cisco `rcodesign` is cross-platform. Use when the Mac runner is unavailable. |

Both pull secrets from Infisical identically. Workflows can switch via
`runs-on` matrix or a `runner-os` matrix dimension.

## Open follow-ups (recorded for tracking)

1. **Re-register the existing self-hosted Mac runner
   (`/Users/kooshapari/.github-actions-runner/`, currently scoped to
   `KooshaPari/hwLedger`) at the `kooshapari` org level** with labels
   `mac-signing,self-hosted,macos,arm64`. This is the cheapest path to
   "zero billed runners" — the runner is already running.
2. **Add a `release-macos.yml` to every NEEDS-AUDIT repo** (see
   `per_repo_pr_stubs.md` for 18 ready-to-paste PR descriptions).
3. **Decide on `vibeproxy`** — explicit "Deprecated fork of automaze.io
   VibeProxy"; either archive or migrate to `phenotype-apps/macos-proxy`.
4. **Document `homebrew-omniroute` formula path** — needs to consume signed
   `.dmg` URLs from `OmniRoute` releases.
5. **Re-run failing `helios-cli` CI run `33480672788`** — Apple-job failures
   need investigation; may need to switch the macOS leg from
   `macos-15-xlarge` to the self-hosted runner.

## Reference

- helios-cli reference: `.github/scripts/macos-signing/` (AKV PKCS#11 +
  rcodesign). The template here is the AKV-free successor.
- fleet-ops Infisical pattern: `.github/workflows/infisical.yml`
- Apple docs: <https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution>
