#!/usr/bin/env bash
# sign_macos_native.sh
#
# Native codesign wrapper for self-hosted macOS runners. Pulls the Developer ID
# Application .p12 from $APPLE_DEVELOPER_ID_P12_BASE64 (already decoded to disk
# by the caller via fetch_apple_secrets.sh --env-file + base64 -d) and signs the
# target with hardened-runtime.
#
# This is the recommended path when the runner is a real Mac (codesign runs
# natively). The rcodesign fallback (sign_macos_rcodesign.sh) is for Linux
# portability.
#
# Usage:
#   sign_macos_native.sh --target PATH --identity IDENTITY [options]
#
# Options:
#   --deep true|false         recurse into nested bundles (default: true)
#   --entitlements PATH       entitlements plist
#   --identifier IDENTIFIER   bundle identifier
#   --options FLAGS           comma-separated: runtime,hard,kill,expires,restrict,
#                             library,linker-signed,host
#   --target PATH             file or .app bundle to sign (required)
#   --timestamp true|false    enable secure timestamp (default: true)

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: sign_macos_native.sh --target PATH --identity IDENTITY [options]

Required env:
  APPLE_DEVELOPER_ID_P12_FILE     path to a .p12 file on disk (decoded by caller)
  APPLE_DEVELOPER_ID_P12_PASSWORD password for the .p12

Options:
  --deep true|false
  --entitlements PATH
  --identifier IDENTIFIER
  --identity IDENTITY            "Developer ID Application: Name (TEAMID)"
  --options FLAGS
  --target PATH
  --timestamp true|false
EOF
}

target=""
identity=""
options=""
entitlements_file=""
identifier=""
deep="true"
timestamp="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep)          deep="${2:-}"; shift 2 ;;
    --entitlements)  entitlements_file="${2:-}"; shift 2 ;;
    --identifier)    identifier="${2:-}"; shift 2 ;;
    --identity)      identity="${2:-}"; shift 2 ;;
    --options)       options="${2:-}"; shift 2 ;;
    --target)        target="${2:-}"; shift 2 ;;
    --timestamp)     timestamp="${2:-}"; shift 2 ;;
    -h|--help)       usage; exit 0 ;;
    *)               echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "$target" ]]         && { echo "--target is required." >&2; usage; exit 2; }
[[ ! -e "$target" ]]       && { echo "Target not found: $target" >&2; exit 1; }
[[ -z "$identity" ]]       && { echo "--identity is required." >&2; usage; exit 2; }

: "${APPLE_DEVELOPER_ID_P12_FILE:?APPLE_DEVELOPER_ID_P12_FILE must point to a .p12 file}"
: "${APPLE_DEVELOPER_ID_P12_PASSWORD:?APPLE_DEVELOPER_ID_P12_PASSWORD is required}"

# Import the .p12 into a temporary keychain so codesign can use it.
keychain_dir="$(mktemp -d)"
trap 'rm -rf "$keychain_dir"; security delete-keychain "$keychain_dir/tmp.keychain" 2>/dev/null || true' EXIT
keychain="$keychain_dir/tmp.keychain"
keychain_pw="$(openssl rand -hex 16)"

security create-keychain -p "$keychain_pw" "$keychain" >/dev/null
security set-keychain-settings -lut 21600 "$keychain" >/dev/null
security unlock-keychain -p "$keychain_pw" "$keychain" >/dev/null

# Prepend the temp keychain to the search list so codesign finds the cert
# without exposing other keychains to the runner.
existing_search="$(security list-keychains -d user | tr -d '"' | xargs)"
security list-keychains -d user -s "$keychain" $existing_search >/dev/null

security import "$APPLE_DEVELOPER_ID_P12_FILE" \
  -k "$keychain" \
  -P "$APPLE_DEVELOPER_ID_P12_PASSWORD" \
  -T /usr/bin/codesign \
  -T /usr/bin/security >/dev/null

# Allow codesign to use the imported identity without an interactive prompt
security set-key-partition-list \
  -S apple-tool:,apple:,codesign: \
  -s -k "$keychain_pw" "$keychain" >/dev/null

args=(--force --sign "$identity")

[[ "$deep" == "true" ]] && args+=(--deep)
[[ -n "$options" ]]     && args+=(--options "$options")
case "$timestamp" in
  true)            args+=(--timestamp) ;;
  false|none)      args+=(--timestamp=none) ;;
esac
[[ -n "$entitlements_file" ]] && args+=(--entitlements "$entitlements_file")
[[ -n "$identifier" ]]         && args+=(--identifier "$identifier")

args+=("$target")

codesign "${args[@]}"

# Verify
codesign --verify --deep --strict --verbose=2 "$target"
