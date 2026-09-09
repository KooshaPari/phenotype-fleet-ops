"""Integration tests for the smart provider dispatcher.

Verifies the actual public API of `review-surface/smart_dispatcher.py`:
- `default_dispatcher()` factory
- `SmartDispatcher.pick_or_fallback(excluded=set)` → `PickResult`
- `RateLimitTracker.record_call(provider)` / `record_failure(provider, error_kind)`
- `RateLimitTracker.in_cooldown(provider)` / `slots_available(provider)`
- Safety net behavior (forge + thegent) when all priority providers exhausted
- Negative cases: unknown providers, all-blocked path, non-rate failure

These tests guard the live fleet-review flow:
  PR open -> webhook -> pick_or_fallback -> coderabbit/copilot/cursor -> forge
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smart_dispatcher import (  # noqa: E402  (path-injected above)
    PROVIDER_CAPS,
    PickResult,
    RateLimitTracker,
    SmartDispatcher,
    default_dispatcher,
)


def _build_dispatcher() -> SmartDispatcher:
    """Real dispatcher using the same default priority the fleet relies on."""
    return default_dispatcher()


# ---------- priority ordering ----------

def test_priority_ordering_matches_fleet_default() -> None:
    """Fleet expects: coderabbit, copilot, gemini, cursor, thegent, forge (forge last as safety net)."""
    d = _build_dispatcher()
    assert d.priority[:4] == ("coderabbit", "copilot", "gemini", "cursor")
    # Last two = safety net (always available, never cooldowned by priority pass)
    assert "thegent" in d.priority
    assert "forge" in d.priority


def test_first_pick_returns_first_priority_provider() -> None:
    d = _build_dispatcher()
    pr = d.pick_or_fallback()
    assert isinstance(pr, PickResult)
    assert pr.provider == "coderabbit"
    assert pr.reason == "available"


def test_excluding_first_priority_falls_through() -> None:
    d = _build_dispatcher()
    pr = d.pick_or_fallback(excluded={"coderabbit"})
    assert pr.provider == "copilot"
    assert pr.reason == "available"


def test_excluding_top_n_falls_through_to_n_plus_1() -> None:
    d = _build_dispatcher()
    pr = d.pick_or_fallback(excluded={"coderabbit", "copilot", "gemini"})
    assert pr.provider == "cursor"
    assert pr.reason == "available"


# ---------- rotation + safety net ----------

def test_excluding_all_priority_falls_back_to_safety_net() -> None:
    """When every top-tier provider is excluded, the dispatcher still
    reaches a safety-net provider (thegent or forge) so reviews never
    silently stall. The exact reason depends on whether the safety net
    was hit during priority traversal (reason='available') or only after
    every ranked provider was exhausted (reason='all_blocked_fallback')."""
    d = _build_dispatcher()
    top_tier = [p for p in d.priority if p not in ("forge", "thegent")]
    pr = d.pick_or_fallback(excluded=set(top_tier))
    assert pr.provider in ("forge", "thegent"), (
        f"expected safety-net provider, got {pr.provider!r} reason={pr.reason!r}"
    )
    assert pr.reason in ("available", "all_blocked_fallback")


def test_sticky_assignment_across_calls_when_unconstrained() -> None:
    """Without exclusion, the dispatcher's deterministic priority walk
    yields the same first-pick provider for every fresh request. There
    is no per-PR stickiness in the current API (sticky-by-pr_key is a
    future extension, see review-surface/main.py webhook handler).
    """
    d = _build_dispatcher()
    prs = [d.pick_or_fallback() for _ in range(3)]
    # All 3 calls are unconstrained, so each yields priority[0]
    for pr in prs:
        assert pr.provider == "coderabbit"
        assert pr.reason == "available"


# ---------- cooldown + slot mechanics ----------

def test_record_call_consumes_slot() -> None:
    d = _build_dispatcher()
    tracker = d.tracker
    # coderabbit cap is 3/hr by default; consume 3 slots
    for _ in range(PROVIDER_CAPS["coderabbit"]["per_hour"]):
        tracker.record_call("coderabbit")
    assert tracker.slots_available("coderabbit") == 0


def test_record_failure_sets_cooldown() -> None:
    """A failure with error_kind='rate_limit' must mark the provider as
    in cooldown so subsequent picks skip it for at least 5 minutes."""
    d = _build_dispatcher()
    tracker = d.tracker
    tracker.record_failure("coderabbit", error_kind="rate_limit")
    assert tracker.in_cooldown("coderabbit") is True


def test_cooldown_is_per_provider() -> None:
    """A cooldown on coderabbit must NOT affect copilot picks."""
    d = _build_dispatcher()
    tracker = d.tracker
    tracker.record_failure("coderabbit", error_kind="rate_limit")
    assert tracker.in_cooldown("coderabbit") is True
    assert tracker.in_cooldown("copilot") is False


def test_excluding_cooldown_provider_forces_fallback() -> None:
    """cooldown(coderabbit, 'rate_limit') -> pick_or_fallback picks copilot (not coderabbit)."""
    d = _build_dispatcher()
    d.cooldown("coderabbit", error_kind="rate_limit")
    assert d.tracker.in_cooldown("coderabbit") is True
    pr = d.pick_or_fallback()
    assert pr.provider == "copilot", f"expected copilot, got {pr.provider}"


# ---------- provider caps registry sanity ----------

def test_provider_caps_cover_all_priority_providers() -> None:
    """Every priority provider must have an entry in PROVIDER_CAPS (else
    defaults fall back to 'forge', silently — we want explicit caps)."""
    d = _build_dispatcher()
    for name in d.priority:
        assert name in PROVIDER_CAPS, f"missing PROVIDER_CAPS entry for {name}"


def test_provider_caps_include_per_hour_and_window() -> None:
    """Each cap entry must specify per_hour (used by RateLimitTracker) and
    cooldown_seconds (used by SmartDispatcher.cooldown())."""
    for name, cap in PROVIDER_CAPS.items():
        assert "per_hour" in cap, f"{name} missing per_hour"
        assert "cooldown_seconds" in cap, f"{name} missing cooldown_seconds"


# ---------- negative cases ----------

def test_unknown_provider_in_excluded_is_ignored() -> None:
    """Excluding a name the dispatcher has never heard of must not crash
    and must not change the picked provider."""
    d = _build_dispatcher()
    pr = d.pick_or_fallback(excluded={"nonsense-provider", "also-nonsense"})
    assert pr.provider == "coderabbit"
    assert pr.reason == "available"


def test_all_providers_excluded_returns_all_blocked_fallback() -> None:
    """When every priority AND safety-net provider is explicitly excluded
    by the caller, the dispatcher must still return a PickResult (so the
    webhook can surface the failure) rather than raising."""
    d = _build_dispatcher()
    pr = d.pick_or_fallback(excluded=set(d.priority))
    assert pr.provider == "forge"
    assert pr.reason == "all_blocked_fallback"


def test_record_failure_without_rate_kind_does_not_set_cooldown() -> None:
    """Negative path: a non-rate-limit failure (auth, server) must NOT
    trigger a provider cooldown — only rate_limit/429/timeout errors
    suppress the provider for the cooldown window."""
    d = _build_dispatcher()
    tracker = d.tracker
    tracker.record_failure("coderabbit", error_kind="server_error")
    assert tracker.in_cooldown("coderabbit") is False
    assert tracker.slots_available("coderabbit") == PROVIDER_CAPS["coderabbit"]["per_hour"]


# ---------- runner ----------

def _run() -> int:
    failed = 0
    cases = [
        ("test_priority_ordering_matches_fleet_default", test_priority_ordering_matches_fleet_default),
        ("test_first_pick_returns_first_priority_provider", test_first_pick_returns_first_priority_provider),
        ("test_excluding_first_priority_falls_through", test_excluding_first_priority_falls_through),
        ("test_excluding_top_n_falls_through_to_n_plus_1", test_excluding_top_n_falls_through_to_n_plus_1),
        ("test_excluding_all_priority_falls_back_to_safety_net", test_excluding_all_priority_falls_back_to_safety_net),
        ("test_sticky_assignment_across_calls_when_unconstrained", test_sticky_assignment_across_calls_when_unconstrained),
        ("test_record_call_consumes_slot", test_record_call_consumes_slot),
        ("test_record_failure_sets_cooldown", test_record_failure_sets_cooldown),
        ("test_cooldown_is_per_provider", test_cooldown_is_per_provider),
        ("test_excluding_cooldown_provider_forces_fallback", test_excluding_cooldown_provider_forces_fallback),
        ("test_provider_caps_cover_all_priority_providers", test_provider_caps_cover_all_priority_providers),
        ("test_provider_caps_include_per_hour_and_window", test_provider_caps_include_per_hour_and_window),
        ("test_unknown_provider_in_excluded_is_ignored", test_unknown_provider_in_excluded_is_ignored),
        ("test_all_providers_excluded_returns_all_blocked_fallback", test_all_providers_excluded_returns_all_blocked_fallback),
        ("test_record_failure_without_rate_kind_does_not_set_cooldown", test_record_failure_without_rate_kind_does_not_set_cooldown),
    ]
    for name, fn in cases:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            failed += 1
        else:
            print(f"OK   {name}")
    passed = len(cases) - failed
    print(f"\nSUMMARY: {passed}/{len(cases)} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(_run())
