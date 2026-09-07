#!/usr/bin/env bash
# Set REVIEW_SURFACE secrets on all 11 fleet repos.
# Run once the review-surface service is deployed and you have the real URL + token.
#
# Usage:
#   export REVIEW_SURFACE_URL="https://review.phenotype.internal"
#   export REVIEW_SURFACE_TOKEN="$(openssl rand -hex 32)"
#   bash set-fleet-secrets.sh
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

: "${REVIEW_SURFACE_URL:?REVIEW_SURFACE_URL must be exported}"
: "${REVIEW_SURFACE_TOKEN:?REVIEW_SURFACE_TOKEN must be exported (e.g. openssl rand -hex 32)}"

# Refuse to push the placeholder example URL.
if [[ "$REVIEW_SURFACE_URL" == *"example"* || "$REVIEW_SURFACE_URL" == *"PLACEHOLDER"* ]]; then
  echo "ERROR: REVIEW_SURFACE_URL still has placeholder text; refusing to push to 11 repos." >&2
  exit 1
fi
if [[ ${#REVIEW_SURFACE_TOKEN} -lt 32 ]]; then
  echo "ERROR: REVIEW_SURFACE_TOKEN looks too short (<32 chars); refusing to push." >&2
  exit 1
fi

for repo in "${REPOS[@]}"; do
  echo "==> $repo"
  gh secret set REVIEW_SURFACE_URL      --repo "KooshaPari/$repo" --body "$REVIEW_SURFACE_URL"
  gh secret set REVIEW_SURFACE_TOKEN    --repo "KooshaPari/$repo" --body "$REVIEW_SURFACE_TOKEN"
done

echo "Done. REVIEW_SURFACE_URL + REVIEW_SURFACE_TOKEN set on ${#REPOS[@]} repos."
