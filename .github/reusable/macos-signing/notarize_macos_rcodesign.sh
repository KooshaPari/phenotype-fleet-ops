#!/usr/bin/env bash
# notarize_macos_rcodesign.sh
#
# Linux-portable Apple notarization via Cisco's `rcodesign`. Standalone
# binaries cannot carry a stapled ticket, so they're submitted in a ZIP and
# the successful notarization log is retained. For .app and .dmg targets,
# prefer notarize_macos_native.sh --staple on a Mac runner, but this script
# can also staple when given a .dmg.
#
# Required env: APPLE_NOTARIZATION_KEY_P8_FILE, APPLE_NOTARIZATION_KEY_ID,
#               APPLE_NOTARIZATION_ISSUER_ID
# Optional env: APPLE_TEAM_ID (defaults to inferring from issuer)

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: notarize_macos_rcodesign.sh --target PATH [--staple] [--report-dir PATH] [--max-wait-seconds SECONDS]

Options:
  --target PATH                .app, .dmg, or .zip (required)
  --staple                    staple the ticket (only .dmg; .app requires macOS)
  --report-dir PATH            default: $RUNNER_TEMP/macos-notarization
  --max-wait-seconds SECONDS   default: 1800
EOF
}

target=""
staple=false
report_dir="${RUNNER_TEMP:-/tmp}/macos-notarization"
max_wait=1800

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)            target="${2:-}"; shift 2 ;;
    --staple)            staple=true; shift ;;
    --report-dir)        report_dir="${2:-}"; shift 2 ;;
    --max-wait-seconds)  max_wait="${2:-}"; shift 2 ;;
    -h|--help)           usage; exit 0 ;;
    *)                   echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "$target" ]] && { echo "--target is required." >&2; usage; exit 2; }
[[ ! -e "$target" ]] && { echo "Target not found: $target" >&2; exit 1; }

: "${APPLE_NOTARIZATION_KEY_P8_FILE:?APPLE_NOTARIZATION_KEY_P8_FILE must point to a .p8 file}"
: "${APPLE_NOTARIZATION_KEY_ID:?APPLE_NOTARIZATION_KEY_ID is required}"
: "${APPLE_NOTARIZATION_ISSUER_ID:?APPLE_NOTARIZATION_ISSUER_ID is required}"

command -v rcodesign >/dev/null 2>&1 || { echo "rcodesign not on PATH." >&2; exit 1; }

mkdir -p "$report_dir"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# Encode the API key into the JSON form rcodesign expects
api_key_json="$tmpdir/api-key.json"
rcodesign encode-app-store-connect-api-key \
  --output-path "$api_key_json" \
  "$APPLE_NOTARIZATION_ISSUER_ID" \
  "$APPLE_NOTARIZATION_KEY_ID" \
  "$APPLE_NOTARIZATION_KEY_P8_FILE" \
  >"$report_dir/encode-api-key.log" 2>&1

# Standalone binaries need to be zipped first
case "$target" in
  *.zip)
    archive="$target"
    ;;
  *.app|*.dmg)
    archive="$target"
    ;;
  *)
    archive="$tmpdir/$(basename "$target").zip"
    (cd "$(dirname "$target")" && zip -q "$archive" "$(basename "$target")")
    ;;
esac

notary_log="$report_dir/$(basename "$target").rcodesign.notarize.log"
rcodesign notarize \
  --api-key-file "$api_key_json" \
  --max-wait-seconds "$max_wait" \
  --wait \
  "$archive" \
  2>&1 | tee "$notary_log"

# Staple (only for .dmg; .app bundles require macOS for stapler)
if [[ "$staple" == "true" && "$target" == *.dmg ]]; then
  # rcodesign can staple DMGs natively
  rcodesign staple "$target" 2>&1 | tee "$report_dir/$(basename "$target").rcodesign.staple.log"
fi

target_sha="$(shasum -a 256 "$target" | awk '{print $1}')"
{
  echo "target=$target"
  echo "target_sha256=$target_sha"
  echo "key_id=$APPLE_NOTARIZATION_KEY_ID"
  echo "issuer_id=$APPLE_NOTARIZATION_ISSUER_ID"
  echo "stapled=$staple"
  echo "completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$report_dir/$(basename "$target").notarization-summary.txt"
