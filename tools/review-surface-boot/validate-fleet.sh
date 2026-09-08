#!/usr/bin/env bash
#
# validate-fleet.sh — verify the review-surface automation is wired and
# consistent across the 11 fleet repos.
#
# Usage:
#   ./validate-fleet.sh                  # full check, all repos
#   ./validate-fleet.sh --repo=hfscope  # single repo
#   ./validate-fleet.sh --skip-lefthook # skip the lefthook hook presence check
#   ./validate-fleet.sh --strict        # exit non-zero on any FAIL
#
# Exit codes:
#   0  — all green
#   1  — at least one FAIL (with --strict, or if any repo is unreachable)
#   2  — usage error
#
set -uo pipefail

REPOS_ROOT="${REPOS_ROOT:-/Users/kooshapari/CodeProjects/Phenotype/repos}"
MASTER="phenotype-fleet-ops"

TARGETS_ALL=(
  "hfscope main"
  "local-ops main"
  "PhenoPlugins main"
  "phenotype-go-sdk main"
  "phenotype-python-sdk main"
  "phenotype-traceability-spine main"
  "PlayCua main"
  "RepoLedger main"
  "ResearchLedger master"
  "PhenoObservability main"
  "phenotype-journeys main"
)

FILES_V1=(
  ".github/workflows/review-fanout.yml"
  ".github/workflows/retroactive-sweep.yml"
  ".github/ISSUE_TEMPLATE/review_finding.md"
)

FILES_V2=(
  ".github/workflows/auto-merge.yml"
  ".github/workflows/sonarcloud.yml"           # .trunk/trunk.yaml equivalent; lives at repo root here
  "review-surface/config_loader.py"
)

# legacy alias for backward compatibility with prior docs
ANCHOR_FILES=(
  ".github/workflows/review-fanout.yml"
  ".github/workflows/retroactive-sweep.yml"
  "lefthook.yml"
  ".github/ISSUE_TEMPLATE/review_finding.md"
  ".github/auto-merge.yml"
  ".github/workflows/sonarcloud.yml"
  "review-surface/config_loader.py"
)

ANCHORS=(
  ".github/workflows/review-fanout.yml:REVIEW_SURFACE_URL"
  ".github/workflows/review-fanout.yml:REVIEW_SURFACE_TOKEN"
  ".github/workflows/review-fanout.yml:\"owner\""
  ".github/workflows/review-fanout.yml:\"repo\""
  ".github/workflows/review-fanout.yml:\"number\""
  ".github/workflows/review-fanout.yml:Bearer"
  ".github/workflows/review-fanout.yml:Authorization"
  ".github/workflows/retroactive-sweep.yml:on:"
  ".github/workflows/retroactive-sweep.yml:schedule:"
  ".github/workflows/retroactive-sweep.yml:workflow_dispatch:"
  ".github/workflows/retroactive-sweep.yml:\"owner\""
  ".github/workflows/retroactive-sweep.yml:\"repo\""
  ".github/workflows/retroactive-sweep.yml:dry_run"
  ".github/workflows/retroactive-sweep.yml:lookback"
  "lefthook.yml:no_tty: true"
  "lefthook.yml:env:"
  ".github/ISSUE_TEMPLATE/review_finding.md:name:"
  ".github/ISSUE_TEMPLATE/review_finding.md:labels:"
  ".github/auto-merge.yml:auto-merge"
  ".github/workflows/sonarcloud.yml:sonarcloud"
  "review-surface/config_loader.py:def load_yaml"
  "review-surface/config_loader.py:def apply_to_settings"
  "review-surface/config_loader.py:def apply_to_dispatcher_caps"
)

ANTI_ANCHORS=(
  ".github/workflows/review-fanout.yml:review.phenotype.internal"
)

# ── CLI ──────────────────────────────────────────────────────────────────
TARGETS=()
SKIP_LEFTHOOK=0
STRICT=0
ONLY_REPO=""

usage() {
  sed -n '2,15p' "$0"
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo=*)     ONLY_REPO="${1#*=}"; shift ;;
    --skip-lefthook) SKIP_LEFTHOOK=1; shift ;;
    --strict)     STRICT=1; shift ;;
    --help|-h)    usage ;;
    *)            echo "unknown arg: $1" >&2; usage ;;
  esac
done

if [ -n "$ONLY_REPO" ]; then
  TARGETS=("$ONLY_REPO")
else
  TARGETS=("${TARGETS_ALL[@]}")
fi

# ── preflight ─────────────────────────────────────────────────────────────
if [ ! -d "$REPOS_ROOT/$MASTER" ]; then
  echo "ERR: master repo not found at $REPOS_ROOT/$MASTER" >&2
  exit 1
fi

# ── checks ────────────────────────────────────────────────────────────────
PASS=0
FAIL=0
WARN=0

check_repo() {
  local repo="$1" branch="$2"
  local repo_dir="$REPOS_ROOT/$repo"
  local ok=1
  echo "── $repo (branch=$branch)"

  # presence on disk
  if [ ! -d "$repo_dir/.git" ]; then
    echo "  WARN not on disk (skipped)"
    WARN=$((WARN+1))
    return
  fi

  local actual_branch
  actual_branch="$(git -C "$repo_dir" branch --show-current 2>/dev/null)"
  if [ "$actual_branch" != "$branch" ]; then
    echo "  WARN on '$actual_branch', expected '$branch'"
    WARN=$((WARN+1))
  fi

  # v1 + v2 file presence
  local f
  for f in "${ANCHOR_FILES[@]}"; do
    if [ -f "$repo_dir/$f" ]; then
      echo "  ok    $f"
    else
      echo "  FAIL  $f (missing)"
      ok=0
    fi
  done

  # byte-for-byte parity with fleet-ops master for the 4 propagated files
  for f in "${FILES_V1[@]}" lefthook.yml "${FILES_V2[@]}"; do
    if ! diff -q "$repo_dir/$f" "$REPOS_ROOT/$MASTER/$f" >/dev/null 2>&1; then
      echo "  FAIL  $f (drift from $MASTER master)"
      ok=0
    fi
  done

  # correctness anchors
  for spec in "${ANCHORS[@]}"; do
    local file="${spec%%:*}"
    local needle="${spec#*:}"
    if [ -f "$repo_dir/$file" ] && ! grep -qF "$needle" "$repo_dir/$file"; then
      echo "  FAIL  $file missing anchor: $needle"
      ok=0
    fi
  done

  # anti-anchors (must NOT appear)
  for spec in "${ANTI_ANCHORS[@]}"; do
    local file="${spec%%:*}"
    local needle="${spec#*:}"
    if [ -f "$repo_dir/$file" ] && grep -qF "$needle" "$repo_dir/$file"; then
      echo "  FAIL  $file contains forbidden anchor: $needle"
      ok=0
    fi
  done

  # lefthook hook installation (git hook present)
  if [ "$SKIP_LEFTHOOK" -eq 0 ] && [ -f "$repo_dir/lefthook.yml" ]; then
    if [ -f "$repo_dir/.git/hooks/pre-commit" ] && grep -q lefthook "$repo_dir/.git/hooks/pre-commit" 2>/dev/null; then
      echo "  ok    lefthook hooks installed"
    else
      echo "  WARN  lefthook hooks NOT installed (run: cd $repo && lefthook install)"
      WARN=$((WARN+1))
    fi
  fi

  if [ "$ok" -eq 1 ]; then
    PASS=$((PASS+1))
    echo "  PASS"
  else
    FAIL=$((FAIL+1))
    echo "  FAIL"
  fi
}

echo "=== validate-fleet ==="
echo "master: $MASTER"
echo "repos:  ${#TARGETS[@]}"
echo

for entry in "${TARGETS[@]}"; do
  check_repo $entry
  echo
done

echo "=== summary ==="
echo "PASS: $PASS"
echo "FAIL: $FAIL"
echo "WARN: $WARN"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
