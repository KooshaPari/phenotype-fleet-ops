"""config_loader — wire `config.yaml` into runtime Settings + PROVIDER_CAPS.

Why a dedicated module:
- `Settings.tool_backends` (pydantic-settings) had a hardcoded default that
  ignored `config.yaml` entirely — config drift between the file and the
  running service was invisible.
- `PROVIDER_CAPS` in `smart_dispatcher.py` had hardcoded rate-limit ceilings
  per provider that ignored `config.yaml`'s `rate_limit_per_hour` per tool.

Resolution:
- One source of truth: `config.yaml`
- Env-var override per field (e.g. `REVIEW_TOOL_BACKENDS=forge,coderabbit`)
- On startup, `load_config_into_settings()` mutates the Settings singleton
  AND `PROVIDER_CAPS` (per-provider per-hour cap) in place.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file. Returns {} on missing/empty file."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# Public alias — main.py and tests import `load_config`.
load_config = load_yaml


def enabled_tools(cfg: dict[str, Any]) -> list[str]:
    """Return the ordered list of enabled tool names from config.yaml."""
    tools = cfg.get("tools") or []
    out = [t["name"] for t in tools if t.get("enabled", True) and "name" in t]
    return out


def per_provider_caps(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract per-provider rate-limit caps from config.yaml.

    Returns a dict keyed by tool name, each value being
    {"per_hour": int, "cost_per_review": float}.
    """
    out: dict[str, dict[str, Any]] = {}
    for t in cfg.get("tools") or []:
        if "name" not in t:
            continue
        out[t["name"]] = {
            "per_hour": int(t.get("rate_limit_per_hour", 30)),
            "cost_per_review": float(t.get("cost_per_review", 0.0)),
        }
    return out


def apply_to_settings(settings: Any, cfg: dict[str, Any]) -> None:
    """Mutate `Settings` in-place from config.yaml + env overrides.

    Env vars take precedence (so a container/k8s deployment can override
    `config.yaml` without editing the file).
    """
    # ── tool_backends ──────────────────────────────────────────────────────
    env_backends = os.getenv("REVIEW_TOOL_BACKENDS")
    if env_backends:
        backends = [b.strip() for b in env_backends.split(",") if b.strip()]
    else:
        backends = enabled_tools(cfg) or list(settings.tool_backends)
    if backends:
        settings.tool_backends = backends

    # ── default_backend ───────────────────────────────────────────────────
    env_default = os.getenv("REVIEW_DEFAULT_BACKEND")
    if env_default:
        settings.default_backend = env_default
    elif backends:
        # First enabled tool wins as default (stable across restarts)
        settings.default_backend = backends[0]

    # ── rate_limit_per_hour (global fallback) ────────────────────────────
    env_rl = os.getenv("REVIEW_RATE_LIMIT_PER_HOUR")
    if env_rl and env_rl.isdigit():
        settings.rate_limit_per_hour = int(env_rl)


def apply_to_dispatcher_caps(provider_caps: dict[str, dict[str, Any]],
                             cfg: dict[str, Any]) -> None:
    """Mutate PROVIDER_CAPS in-place from config.yaml caps.

    Provider caps in config.yaml override the hardcoded `PROVIDER_CAPS`
    defaults in `smart_dispatcher.py`. Unknown tools get a sane default
    (per_hour=30, cost=0.0) so they remain usable.
    """
    cfg_caps = per_provider_caps(cfg)
    for name, cap in cfg_caps.items():
        if name in provider_caps:
            provider_caps[name]["per_hour"] = cap["per_hour"]
            provider_caps[name]["cost_per_review"] = cap["cost_per_review"]
        else:
            # New provider not in PROVIDER_CAPS — register it so the
            # dispatcher can pick it up without a code change.
            provider_caps[name] = {
                "tier": "free",
                "per_hour": cap["per_hour"],
                "cost_per_review": cap["cost_per_review"],
                "label": f"{name} (config-driven)",
            }


def load_config_into_settings(settings: Any,
                              provider_caps: dict[str, dict[str, Any]],
                              config_path: str | Path = "config.yaml") -> None:
    """One-shot helper: load config.yaml + apply to settings + PROVIDER_CAPS.

    Called from `main.py` at process startup, after `Settings()` is
    instantiated and AFTER `smart_dispatcher.PROVIDER_CAPS` is imported.
    """
    cfg = load_yaml(config_path)
    if not cfg:
        return
    apply_to_settings(settings, cfg)
    apply_to_dispatcher_caps(provider_caps, cfg)
