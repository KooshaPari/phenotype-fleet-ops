# Absorption manifest — pheno-control-plane → phenotype-fleet-ops/agent-devops-setups/tailnet-control-plane/

**Source repo**: `KooshaPari/pheno-control-plane` (archived 2026-08-08, private, 20 KB)
**Target repo**: `KooshaPari/phenotype-fleet-ops`
**Branch**: `chore/absorb-pheno-control-plane-2026-09-01`
**Worktree**: `/Users/kooshapari/CodeProjects/Phenotype/repos/worktrees/fleet-ops-absorb-pheno-control-plane-2026-09-01`
**Absorb technique**: history-preserving subtree (cherry-pick of source `main`) with manifest under `agent-devops-setups/tailnet-control-plane/`
**Date**: 2026-09-01
**Audit reference**: `audits/absorption-justifications/pheno-control-plane-2026-09-01.md`

## Files absorbed (7)

| Source path | Target path | Notes |
|---|---|---|
| `README.md` | `agent-devops-setups/tailnet-control-plane/README.md` | SSOT intent doc |
| `compose/docker-compose.yml` | `agent-devops-setups/tailnet-control-plane/compose/docker-compose.yml` | NATS + Dragonfly + MinIO + Postgres stack |
| `compose/init-postgres.sql` | `agent-devops-setups/tailnet-control-plane/compose/init-postgres.sql` | devices/experiments/runs/sync_manifest schema |
| `bridge/publish_status.py` | `agent-devops-setups/tailnet-control-plane/bridge/publish_status.py` | Mac spoke → Windows hub publisher |
| `bridge/requirements.txt` | `agent-devops-setups/tailnet-control-plane/bridge/requirements.txt` | nats-py dep |
| `docs/ARCHITECTURE.md` | `agent-devops-setups/tailnet-control-plane/docs/ARCHITECTURE.md` | hexagonal layers + ETL |
| `docs/fix-ssh-config-acl.ps1` | `agent-devops-setups/tailnet-control-plane/docs/fix-ssh-config-acl.ps1` | one-shot ACL fix script |

## Files NOT absorbed (intentionally excluded)

These belong to the *source repo's* CI/dev environment, not the absorbed artifact, and would conflict with the target's CI infra:

- `.mergify.yml` (target has its own `.mergify.yml`)
- `renovate.json` (target has its own `renovate.json`)
- `trunk.yaml` (target has its own `trunk.yaml`)
- `.circleci/config.yml` (target uses github workflows + circleci config of its own)
- `.github/workflows/infisical.yml` (infisical secret manager — staged for separate PR if needed)
- `.github/workflows/ci.yml`
- `.github/workflows/trunk-check.yml`
- `.github/workflows/scorecard.yml`

## Why this manifest

pheno-control-plane was the "Unified Tailnet hub" prototype (Windows desk) — discover, ingest, surface R&D from Mac + Windows via NATS + MinIO + Postgres. The target `phenotype-fleet-ops` is the canonical home for *agent-devops-setups* (already hosts `llama-cpp`). The hexagonal architecture (DashboardQueryPort / ExperimentStore / BenchRunner / TraceExporter / EnginePort) matches the existing pillars in phenotype-fleet-ops (governance/, review-surface/, templates/, etc.).

After absorb:
- Source repo content lives at `agent-devops-setups/tailnet-control-plane/` and is tracked by `phenotype-fleet-ops` repo.
- Source repo can be deleted (`gh repo delete KooshaPari/pheno-control-plane --yes`) once this branch is pushed + merged.
- Cross-repo references (e.g. `pheno-research/devices/*.yaml` referenced from ARCHITECTURE.md) still resolve: `pheno-research` is being absorbed into `pheno` monorepo in a parallel absorb PR.

## Gates (per `04-polyrepo-ecosystem-consolidation.md`)

- [x] Provenance manifest written in target
- [x] History-preserving (this branch is cherry-pickable from source; tree's `git subtree split` against source remains possible until source is deleted)
- [x] No CI config conflicts (excluded intentionally)
- [x] Soft-delete contract: `gh repo delete` queued, not executed
- [x] Registry row updated: `repo-pheno-control-plane-audit20260901` disposition=ABSORB fsm=deleted (in `phenotype-registry/registry/disposition-index.json`)
- [x] Project JSON written: `phenotype-registry/projects/pheno-control-plane-2026-09-01.json`
- [x] Justification audit written: `phenotype-registry/audits/absorption-justifications/pheno-control-plane-2026-09-01.md`

## Identity provenance

When the source is later deleted, this manifest is the only surviving provenance link. Document the source repo's final HEAD before deletion:

```bash
gh api repos/KooshaPari/pheno-control-plane | jq '.pushed_at, .default_branch'
# → record here before deletion: pushed_at=2026-08-08, default_branch=main, last commit HEAD=<recorded>
```
