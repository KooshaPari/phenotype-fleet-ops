#!/usr/bin/env bash
# Set REVIEW_SURFACE secrets on all 11 fleet repos.
# Run once the review-surface service is deployed and you have the real URL + token.
#
# Usage:
#   export REVIEW_SURFACE_URL="https://review.phenotype.internal"
#   export REVIEW_SURFACE_TOKEN="$(openssl rand -hex 32)"
#   bash set-fleet-secrets.sh
#
# Optional flags:
#   --dry-run    Print every `gh secret set` invocation without running it.
#                Works even without real values (shows what would run).
#   --repos=a,b  Override the default 11-repo target list.
#
# Requires: gh CLI authenticated as KooshaPari (or a maintainer of each repo).
set -euo pipefail

REPOS=(
  hfscope
  local-ops
  PhenoPlugins
  phenotype-go-sdk
  phenotype-python-sdk
  phenotype-traceability-spine
  PlayCua
  RepoLedger
  ResearchLedger
  PhenoObservability
  phenotype-journeys
)

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --repos=*) IFS=',' read -r -a REPOS <<< "${arg#--repos=}" ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

if [[ $DRY_RUN -eq 0 ]]; then
  : "${REVIEW_SURFACE_URL:?REVIEW_SURFACE_URL must be exported}"
  : "${REVIEW_SURFACE_TOKEN:?REVIEW_SURFACE_TOKEN must be exported (e.g. openssl rand -hex 32)}"

  # Refuse to push the placeholder example URL.
  if [[ "$REVIEW_SURFACE_URL" == *"example"* || "$REVIEW_SURFACE_URL" == *"PLACEHOLDER"* ]]; then
    echo "ERROR: REVIEW_SURFACE_URL still has placeholder text; refusing to push to ${#REPOS[@]} repos." >&2
    exit 1
  fi
  if [[ ${#REVIEW_SURFACE_TOKEN} -lt 32 ]]; then
    echo "ERROR: REVIEW_SURFACE_TOKEN looks too short (<32 chars); refusing to push." >&2
    exit 1
  fi
else
  # In dry-run, use safe placeholder strings so the printout is realistic
  # but obviously NOT the values we'd push.
  REVIEW_SURFACE_URL="${REVIEW_SURFACE_URL:-https://review.phenotype.internal.example.invalid}"
  REVIEW_SURFACE_TOKEN="${REVIEW_SURFACE_TOKEN:-$(printf '%064d' 0 | tr '0' 'X')}"
  echo "==> DRY RUN — no `gh secret set` calls will be made."
fi

for repo in "${REPOS[@]}"; do
  echo "==> $repo"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "    gh secret set REVIEW_SURFACE_URL   --repo KooshaPari/$repo --body <REDACTED_URL>"
    echo "    gh secret set REVIEW_SURFACE_TOKEN --repo KooshaPari/$repo --body <REDACTED_TOKEN>"
  else
    gh secret set REVIEW_SURFACE_URL      --repo "KooshaPari/$repo" --body "$REVIEW_SURFACE_URL"
    gh secret set REVIEW_SURFACE_TOKEN    --repo "KooshaPari/$repo" --body "$REVIEW_SURFACE_TOKEN"
  fi
done

echo "Done. REVIEW_SURFACE_URL + REVIEW_SURFACE_TOKEN set on ${#REPOS[@]} repos."
