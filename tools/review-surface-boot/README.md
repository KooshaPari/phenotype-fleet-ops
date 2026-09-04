# review-surface-boot

Propagates the fleet review-surface automation to a target repo:

- `.github/workflows/review-fanout.yml` — proactive review fanout on `pull_request` open/synchronize.
- `.github/workflows/retroactive-sweep.yml` — weekly sweep over closed PRs that opens issues for ignored findings.
- `.github/ISSUE_TEMPLATE/review_finding.md` — the intake template agents and humans use to file review findings.
- `lefthook.yml` — local pre-commit + dispatch wiring (smart-provider fallback aware).

## Why

CodeRabbit's free tier has a small rate limit. We want the smart-provider
dispatcher in `phenotype-fleet-ops/review-surface/` to fall back through
coderabbit -> copilot -> cursor -> forge as quotas fill, and for any
findings that slip through (or were ignored in earlier PRs) to be opened
as a real issue + draft PR an agent can pick up.

## Install

```bash
python3 tools/review-surface-boot/review-surface-boot.py /path/to/target-repo
```

Add `--dry-run` to preview, `--commit` to create a single commit with all
installed files, or `--skip-lefthook` / `--skip-templates` / `--skip-workflows`
to control which layers are applied.

## Source of truth

All files are copied from `phenotype-fleet-ops/` at the matching paths. Update
those, then re-run this tool on every fleet repo to keep the automation in
sync.
