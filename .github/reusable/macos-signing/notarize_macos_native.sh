#!/usr/bin/env bash
# notarize_macos_native.sh — wraps `xcrun notarytool` for self-hosted Mac runners.
set -euo pipefail
usage() {
  cat >&2 <<'EOF'
Usage: notarize_macos_native.sh --target PATH [--staple] [--report-dir PATH] [--max-wait-seconds SECONDS]

Required env:
  APPLE_NOTARIZATION_KEY_ID, APPLE_NOTARIZATION_ISSUER_ID, APPLE_TEAM_ID
  APPLE_NOTARIZATION_KEY_P8_FILE  (path to a .p8 file already decoded by caller)
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
[[ -z "$target" ]] && { echo "--target is required." >&2; exit 2; }
[[ ! -e "$target" ]] && { echo "Target not found: $target" >&2; exit 1; }
: "${APPLE_NOTARIZATION_KEY_ID:?missing}"; : "${APPLE_NOTARIZATION_ISSUER_ID:?missing}"
: "${APPLE_NOTARIZATION_KEY_P8_FILE:?missing}"; : "${APPLE_TEAM_ID:?missing}"
command -v xcrun >/dev/null 2>&1 || { echo "xcrun not on PATH." >&2; exit 1; }
mkdir -p "$report_dir"
key_args=(--key "$APPLE_NOTARIZATION_KEY_P8_FILE" --key-id "$APPLE_NOTARIZATION_KEY_ID" --issuer "$APPLE_NOTARIZATION_ISSUER_ID")
xcrun notarytool submit "$target" "${key_args[@]}" --team-id "$APPLE_TEAM_ID" --no-progress --wait --timeout "$max_wait" \
  2>&1 | tee "$report_dir/$(basename "$target").notarytool.submit.log"
if [[ "$staple" == "true" && ("$target" == *.app || "$target" == *.dmg) ]]; then
  xcrun stapler staple "$target" 2>&1 | tee "$report_dir/$(basename "$target").stapler.log"
  xcrun stapler validate "$target" 2>&1 | tee -a "$report_dir/$(basename "$target").stapler.log"
fi
target_sha="$(shasum -a 256 "$target" | awk '{print $1}')"
{
  echo "target=$target"; echo "target_sha256=$target_sha"
  echo "key_id=$APPLE_NOTARIZATION_KEY_ID"; echo "issuer_id=$APPLE_NOTARIZATION_ISSUER_ID"
  echo "team_id=$APPLE_TEAM_ID"; echo "stapled=$staple"
  echo "completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$report_dir/$(basename "$target").notarization-summary.txt"
