# Control-plane architecture

## Hexagonal layers

```
┌─────────────────────────────────────────────────────────┐
│  Dashboard (bench-cockpit / Grafana) — read-only        │
└───────────────────────┬─────────────────────────────────┘
                        │ DashboardQueryPort
┌───────────────────────▼─────────────────────────────────┐
│  Domain: Experiment, Device, RunManifest, TraceLink     │
└─┬───────────────┬───────────────┬───────────────┬───────┘
  │               │               │               │
  ▼               ▼               ▼               ▼
ExperimentStore  BenchRunner   TraceExporter   EnginePort
  │               │               │               │
  ▼               ▼               ▼               ▼
Postgres+MinIO   fleet/harness  Langfuse SDK    hwledger/omlx
  │               │               │
  ▼               ▼               ▼
NATS subjects: pheno.bench.*, pheno.device.*, pheno.trace.*
```

## Event subjects (NATS)

| Subject | Payload | Publisher |
|---------|---------|-----------|
| `pheno.bench.result` | bench_result.v1 | FleetBenchAdapter, Mac bridge |
| `pheno.device.heartbeat` | device yaml + inventory | hwledger_probe, Mac bridge |
| `pheno.horizon.tick` | horizon-log row | horizon-tick.ps1 |
| `pheno.cockpit.kpi` | cockpit summary | bench-cockpit |
| `pheno.trace.link` | langfuse trace id | LangfuseTraceAdapter |

## ETL / lake

1. **Land** — raw JSONL/CSV → MinIO `s3://pheno-lake/raw/{device}/{date}/`
2. **Catalog** — bridge writer upserts run rows into Postgres `runs`, `experiments`, `devices`
3. **Enrich** — attach Langfuse trace URLs, git SHA, model aliases from `pheno-research`
4. **Serve** — control-plane HTTP API + cockpit hydrate endpoints
5. **Analytics (phase 2)** — ClickHouse materialized views for t/s histograms

## Federation model

- **Hub-and-spoke**, not full mesh: one durable store on Windows.
- Mac is a **client publisher** (bridge) + optional local Langfuse/cockpit for offline work.
- Discovery: Tailscale IPs in `pheno-research/devices/*.yaml` + NATS interest-based subjects.
- Atomic view: Postgres transactions for catalog; MinIO versioning for objects; JetStream ack for events.

## Maturity gates

- No new `*.ps1` business logic — CLI shells call ports only.
- CI: `audit_hexagonal_boundaries.py` (pheno-harness).
- Secrets: env / OS keychain only; never in git.
