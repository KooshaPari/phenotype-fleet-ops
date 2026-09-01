# pheno-control-plane

Unified Tailnet **hub** (Windows desk) that discovers, ingests, and surfaces R&D from Mac + Windows without agent babysitting.

## Technology choices (and why)

| Concern | Chosen | Alternatives considered | Why this |
|---------|--------|---------------------------|----------|
| Event bus | **NATS JetStream** | Kafka, Redis Streams, MQTT | Tiny footprint, excellent Tailscale fit, durable consumers, no ZK |
| Object / lake | **MinIO** | SeaweedFS, local FS, S3 | S3 API, local-first, easy retention policies |
| Catalog / meta | **Postgres 16** | SQLite, Surreal, Neo4j, Arango, ClickHouse | Mature ACID catalog; ClickHouse later for analytics |
| Analytics (phase 2) | **ClickHouse** | Timescale, DuckDB | High-volume bench + span aggregates |
| Graph (phase 2) | **Neo4j** or **Arango** | Memgraph | Device↔experiment↔trace lineage; defer until needed |
| Cache / KV | **Dragonfly** | Redis, KeyDB | Drop-in Redis protocol, higher throughput |
| Trace UI | **Langfuse** (existing Mac/cloud) | Phoenix, Helicone | Already built by Mac agents |
| Dashboard | **bench-cockpit** + control-plane API | Grafana-only | Domain UI already exists; Grafana as ops overlay |

**Not chosen now:** Surreal/Arango as primary store (too many hats early). SQLite OK for offline edge cache only.

## Topology

```
Mac (spoke)                         Windows desk (hub)
───────────                         ──────────────────
bridge → NATS (publish)  ──TS──►    NATS JetStream
bench-cockpit / omlx                MinIO (raw artifacts)
Langfuse (cloud or local)           Postgres (catalog)
                                    ClickHouse (phase 2)
                                    Unified dashboard :8090/:3100
```

Hub owns durable storage. Spokes are eventually-consistent publishers.

## Quick start

```powershell
# Never Docker Engine — Podman only (WSL machine).
cd D:\koosh\pheno-control-plane\compose
podman compose up -d
# Or NATS alone:
#   podman run -d --name pheno-nats -p 4222:4222 -p 8222:8222 docker.io/library/nats:2.10-alpine -js -m 8222
# Mac bridge:
#   ssh-mac 'cd .../bridge && python publish_status.py'
```

See `docs/ARCHITECTURE.md` for hexagonal ports and ETL.
