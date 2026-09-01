#!/usr/bin/env bash
# sign_macos_rcodesign.sh
#
# Linux-portable Apple code-signing via Cisco's `rcodesign` (Rust, no macOS
# needed). Reads the Developer ID .p12 from disk and signs the target with
# hardened-runtime. Use this when the runner is Linux.
#
# If the runner IS a Mac, prefer sign_macos_native.sh — native `codesign`
# is faster, better-tested, and supports more options.
#
# Usage:
#   sign_macos_rcodesign.sh --target PATH [options]
#
# Options:
#   --deep true|false
#   --entitlements PATH
#   --identifier IDENTIFIER
#   --options FLAGS
#   --target PATH
#   --timestamp true|false

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: sign_macos_rcodesign.sh --target PATH [options]

Required env:
  APPLE_DEVELOPER_ID_P12_FILE     path to a .p12 file (decoded by caller)
  APPLE_DEVELOPER_ID_P12_PASSWORD password for the .p12

Options: see header (codesign-compatible subset)
EOF
}

target=""
options=""
entitlements_file=""
identifier=""
deep="true"
timestamp="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep)         deep="${2:-}"; shift 2 ;;
    --entitlements) entitlements_file="${2:-}"; shift 2 ;;
    --identifier)   identifier="${2:-}"; shift 2 ;;
    --options)      options="${2:-}"; shift 2 ;;
    --target)       target="${2:-}"; shift 2 ;;
    --timestamp)    timestamp="${2:-}"; shift 2 ;;
    -h|--help)      usage; exit 0 ;;
    *)              echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "$target" ]]   && { echo "--target is required." >&2; usage; exit 2; }
[[ ! -e "$target" ]] && { echo "Target not found: $target" >&2; exit 1; }

: "${APPLE_DEVELOPER_ID_P12_FILE:?APPLE_DEVELOPER_ID_P12_FILE must point to a .p12 file}"
: "${APPLE_DEVELOPER_ID_P12_PASSWORD:?APPLE_DEVELOPER_ID_P12_PASSWORD is required}"

command -v rcodesign >/dev/null 2>&1 || {
  echo "rcodesign not on PATH. Install: cargo install rcodesign --locked OR brew install rcodesign" >&2
  exit 1
}

args=(sign --p12-file "$APPLE_DEVELOPER_ID_P12_FILE" --p12-password "$APPLE_DEVELOPER_ID_P12_PASSWORD")

[[ "$deep" == "false" ]] && args+=(--shallow)
case "$timestamp" in
  false|none) args+=(--timestamp-url none) ;;
esac

# Map native codesign options to rcodesign flags
if [[ -n "$options" ]]; then
  IFS=',' read -ra split <<< "$options"
  for opt in "${split[@]}"; do
    opt="${opt//[[:space:]]/}"
    [[ -z "$opt" ]] && continue
    case "$opt" in
      host|hard|kill|expires|restrict|library|runtime|linker-signed)
        args+=(--code-signature-flags "$opt") ;;
      *)
        echo "Unsupported rcodesign flag: $opt (skipping)" >&2 ;;
    esac
  done
fi

[[ -n "$entitlements_file" ]] && args+=(--entitlements-xml-file "$entitlements_file")
[[ -n "$identifier" ]]         && args+=(--binary-identifier "$identifier")

# rcodesign requires --for-notarization when hardened-runtime is enabled,
# so notarization can attach a ticket.
if [[ "$options" == *runtime* && "$timestamp" == "true" ]]; then
  args+=(--for-notarization)
fi

args+=("$target")
rcodesign "${args[@]}"

# Verify
rcodesign verify "$target"
