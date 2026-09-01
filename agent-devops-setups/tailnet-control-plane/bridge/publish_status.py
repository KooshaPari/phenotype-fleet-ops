"""Publish Mac spoke status into the Windows hub (NATS + optional HTTP).

Env:
  PHENO_NATS_URL=nats://100.96.135.160:4222
  PHENO_DEVICE_ID=mac.m1_pro
  PHENO_HOST=kooshas-laptop
"""
from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import nats
except ImportError:
    nats = None


SUBJECT_HEARTBEAT = "pheno.device.heartbeat"
SUBJECT_KPI = "pheno.cockpit.kpi"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_status() -> dict:
    home = Path.home()
    omlx = home / "CodeProjects/Phenotype/repos/phenotype-omlx-temp"
    cockpit = omlx / "apps/bench-cockpit"
    return {
        "device_id": os.environ.get("PHENO_DEVICE_ID", "mac.m1_pro"),
        "host": os.environ.get("PHENO_HOST", socket.gethostname()),
        "timestamp": utc_now(),
        "paths": {
            "omlx": str(omlx) if omlx.exists() else None,
            "cockpit": str(cockpit) if cockpit.exists() else None,
        },
        "role": "spoke",
        "stacks": ["phenotype-omlx-temp", "bench-cockpit"],
    }


async def publish_nats(payload: dict) -> None:
    if nats is None:
        raise SystemExit("pip install nats-py")
    url = os.environ.get("PHENO_NATS_URL", "nats://100.96.135.160:4222")
    nc = await nats.connect(url)
    try:
        await nc.publish(SUBJECT_HEARTBEAT, json.dumps(payload).encode("utf-8"))
        await nc.flush()
        print(f"published {SUBJECT_HEARTBEAT} -> {url}")
    finally:
        await nc.drain()


def main() -> None:
    payload = collect_status()
    print(json.dumps(payload, indent=2))
    if os.environ.get("PHENO_DRY_RUN") == "1":
        return
    import asyncio

    asyncio.run(publish_nats(payload))


if __name__ == "__main__":
    main()
