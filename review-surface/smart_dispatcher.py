"""smart_dispatcher — Rate-limit-aware provider fallback chain.

Each review provider (CodeRabbit, Copilot, Cursor, Forge) has a different rate
limit ceiling. This module ranks all *enabled* providers by current health and
returns the first usable one. When the picked provider later 429s, the
outer layer re-invokes `pick_or_fallback()` to swap to the next healthy
provider.

Decisions are made purely from in-memory state so the dispatcher stays cheap
(no extra network calls per request). Persistent state is owned by the
caller's RateLimitTracker.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Iterable, Optional


# ── Provider metadata ──────────────────────────────────────────────────────────
#
# Free tier ceilings observed late-2025 / early-2026. Update these whenever a
# vendor changes terms. Values are deliberately conservative so the dispatcher
# falls back *before* the vendor actually 429s us, avoiding one wasted retry.

PROVIDER_CAPS: dict[str, dict[str, Any]] = {
    "coderabbit": {
        "tier": "free",
        "per_hour": 3,
        "cost_per_review": 0.0,
        "label": "CodeRabbit (free)",
    },
    "copilot": {
        "tier": "free",
        "per_hour": 5,
        "cost_per_review": 0.0,
        "label": "Copilot PR review",
    },
    "cursor": {
        "tier": "free",
        "per_hour": 4,
        "cost_per_review": 0.0,
        "label": "Cursor background",
    },
    "gemini": {
        "tier": "free",
        "per_hour": 10,
        "cost_per_review": 0.0,
        "label": "Gemini Code Assist",
    },
    "thegent": {
        "tier": "self",
        "per_hour": 30,
        "cost_per_review": 0.0,
        "label": "thegent (self-hosted)",
    },
    "forge": {
        "tier": "self",
        "per_hour": 60,
        "cost_per_review": 0.0,
        "label": "Forge local agent",
    },
}


# ── Provider capability matrix ─────────────────────────────────────────────────
#
# Not all providers can handle all review surfaces. We model capabilities as
# simple strings so the dispatcher can match without instantiating anything.
# * means default-capable (no special filter applied).

_PROVIDER_CAPABILITIES: dict[str, set[str]] = {
    "coderabbit": {"diff", "files", "lang:any", "comment:inline", "comment:summary"},
    "copilot": {"diff", "files", "lang:any", "comment:inline"},
    "cursor": {"diff", "files", "lang:ts,js,py,go,rust", "comment:inline"},
    "gemini": {"diff", "files", "lang:any", "comment:summary"},
    "thegent": {
        "diff",
        "files",
        "lang:any",
        "comment:inline",
        "comment:summary",
        "ollama:local",
    },
    "forge": {
        "diff",
        "files",
        "lang:any",
        "comment:inline",
        "comment:summary",
        "ollama:local",
        "agent:tools",
    },
}


# ── Runtime health state ───────────────────────────────────────────────────────


@dataclass
class ProviderStats:
    """Mutable rolling-window stats for one provider."""

    recent_calls: list[float] = field(default_factory=list)  # UNIX timestamps
    recent_failures: int = 0
    last_error_at: Optional[float] = None
    last_error_kind: Optional[str] = None
    cooldown_until: Optional[float] = None  # set when a 429 happens

    def reset(self) -> None:
        self.recent_calls.clear()
        self.recent_failures = 0
        self.last_error_at = None
        self.last_error_kind = None
        self.cooldown_until = None


class RateLimitTracker:
    """In-memory tracker with optional sharing across worker processes.

    For a single-replica deployment the in-process dict is the source of truth.
    For multi-replica you would back this with Redis (state already keyed).
    """

    def __init__(self, window_seconds: int = 3600) -> None:
        self.window_seconds = window_seconds
        self._stats: dict[str, ProviderStats] = {}
        self._lock = Lock()

    def _get(self, provider: str) -> ProviderStats:
        if provider not in self._stats:
            self._stats[provider] = ProviderStats()
        return self._stats[provider]

    def prune(self, provider: str, now: float) -> ProviderStats:
        s = self._get(provider)
        cutoff = now - self.window_seconds
        s.recent_calls = [t for t in s.recent_calls if t >= cutoff]
        return s

    def record_call(self, provider: str, now: Optional[float] = None) -> None:
        with self._lock:
            now = now or time.time()
            s = self.prune(provider, now)
            s.recent_calls.append(now)

    def record_success(self, provider: str) -> None:
        with self._lock:
            self._get(provider).recent_failures = max(
                0, self._get(provider).recent_failures - 1
            )

    def record_failure(
        self,
        provider: str,
        error_kind: str,
        cooldown_seconds: int = 300,
        now: Optional[float] = None,
    ) -> None:
        with self._lock:
            now = now or time.time()
            s = self.prune(provider, now)
            s.recent_failures += 1
            s.last_error_at = now
            s.last_error_kind = error_kind
            # If we hit a rate-limit cooldown, mark it so pick_or_fallback skips
            # until cooldown_until. 5 minutes is a safe middle value.
            if error_kind in ("rate_limit", "429", "timeout"):
                s.cooldown_until = now + cooldown_seconds

    def in_cooldown(self, provider: str, now: Optional[float] = None) -> bool:
        with self._lock:
            now = now or time.time()
            s = self.prune(provider, now)
            if s.cooldown_until is None:
                return False
            if s.cooldown_until <= now:
                s.cooldown_until = None
                return False
            return True

    def slots_available(self, provider: str, now: Optional[float] = None) -> int:
        """Estimated free slots before the provider will hit its limit."""
        with self._lock:
            now = now or time.time()
            s = self.prune(provider, now)
            cap = PROVIDER_CAPS.get(provider, {}).get("per_hour", 5)
            return max(0, cap - len(s.recent_calls))

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Read-only snapshot for /health endpoints."""
        with self._lock:
            out: dict[str, dict[str, Any]] = {}
            now = time.time()
            for name, cap in PROVIDER_CAPS.items():
                if name not in self._stats:
                    out[name] = {
                        "enabled": True,
                        "cap_per_hour": cap["per_hour"],
                        "in_cooldown": False,
                        "slots_available": cap["per_hour"],
                        "recent_failures": 0,
                        "last_error_kind": None,
                    }
                    continue
                s = self.prune(name, now)
                # Compute cooldown inline rather than calling
                # self.in_cooldown(), which would re-acquire the
                # non-reentrant lock and self-deadlock.
                if s.cooldown_until is not None and s.cooldown_until <= now:
                    s.cooldown_until = None
                out[name] = {
                    "enabled": True,
                    "cap_per_hour": cap["per_hour"],
                    "in_cooldown": s.cooldown_until is not None
                    and s.cooldown_until > now,
                    "slots_available": max(0, cap["per_hour"] - len(s.recent_calls)),
                    "recent_failures": s.recent_failures,
                    "last_error_kind": s.last_error_kind,
                }
            return out


# ── Dispatcher ────────────────────────────────────────────────────────────────

DEFAULT_PRIORITY: tuple[str, ...] = (
    "coderabbit",
    "copilot",
    "gemini",
    "cursor",
    "thegent",
    "forge",
)


@dataclass
class PickResult:
    provider: str
    reason: str
    candidates: tuple[str, ...]


class SmartDispatcher:
    """Selects the next healthy provider, in configured priority order."""

    def __init__(
        self,
        tracker: RateLimitTracker,
        priority: Iterable[str] = DEFAULT_PRIORITY,
        enabled: Optional[set[str]] = None,
    ) -> None:
        self.tracker = tracker
        self.priority = tuple(priority)
        self.enabled = enabled or set(self.priority)
        # Always include self-hosted tiers even if user disables them — they are
        # the safety net. Callers can override via `safety_net=...`.
        self._safety_net_order: tuple[str, ...] = ("forge", "thegent")

    def rank(self, capability: str = "diff") -> list[str]:
        """All eligible providers, sorted by priority, capped to enabled set."""
        candidates = [p for p in self.priority if p in self.enabled]
        # Append safety-net providers not already in the priority list.
        for net in self._safety_net_order:
            if net not in candidates and net in self.enabled:
                candidates.append(net)
        return [p for p in candidates if self._supports(p, capability)]

    def _supports(self, provider: str, capability: str) -> bool:
        caps = _PROVIDER_CAPABILITIES.get(provider, set())
        if caps and not caps:
            return False
        # lang:* is wild-carded.
        wanted = capability
        if wanted.startswith("lang:"):
            tag = "lang:any"
        else:
            tag = wanted
        if tag in caps:
            return True
        # Fallback: any provider that declares lang:any satisfies any lang: tag.
        return tag.startswith("lang:") and "lang:any" in caps

    def pick_or_fallback(
        self,
        excluded: Optional[set[str]] = None,
        capability: str = "diff",
        now: Optional[float] = None,
    ) -> PickResult:
        """Return the next provider. `excluded` skips ones already attempted
        in this dispatch round (so the dispatcher can't loop on the same name).
        """
        excluded = set(excluded or set())
        ranked = self.rank(capability=capability)
        now = now if now is not None else time.time()

        for p in ranked:
            if p in excluded:
                continue
            if self.tracker.in_cooldown(p, now=now):
                excluded.add(p)
                continue
            if self.tracker.slots_available(p, now=now) <= 0:
                excluded.add(p)
                continue
            return PickResult(provider=p, reason="available", candidates=tuple(ranked))

        # Nothing in the priority chain has a free slot — last resort.
        return PickResult(
            provider="forge",
            reason="all_blocked_fallback",
            candidates=tuple(ranked),
        )

    def cooldown(
        self,
        provider: str,
        error_kind: str,
        cooldown_seconds: int = 300,
    ) -> None:
        """Mark a provider as in cooldown after a 429 / timeout."""
        self.tracker.record_failure(
            provider,
            error_kind=error_kind,
            cooldown_seconds=cooldown_seconds,
        )

    def consume(self, provider: str) -> None:
        """Increment per-window usage. Call only when a review is actually dispatched."""
        self.tracker.record_call(provider)


def default_dispatcher() -> SmartDispatcher:
    """Factory used by main.py and tests."""
    return SmartDispatcher(tracker=RateLimitTracker())
