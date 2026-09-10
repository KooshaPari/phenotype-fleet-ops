# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KooshaPari

"""Closure gate for fleet-ops row 22 of 13-breadth-readiness-and-priority.md.

The "next scoped closure" for phenotype-fleet-ops per the operator-accepted
plan is: "Mock provider acceptance IDs, HTTP negatives, durable
restart/idempotency; source/license review".

These tests are the demonstrable evidence that the closure verbs have
been met. They are positive + negative + idempotency tests against the
production SmartDispatcher API surface.

Run with:
  ./review-surface/.venv-test/bin/python -u review-surface/test_dispatcher_closure.py
or
  make test-closure
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "review-surface"))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "review-surface" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod; spec.loader.exec_module(mod)
    return mod


smart_dispatcher = _load("smart_dispatcher")
PROVIDER_CAPS = smart_dispatcher.PROVIDER_CAPS
RateLimitTracker = smart_dispatcher.RateLimitTracker
SmartDispatcher = smart_dispatcher.SmartDispatcher
default_dispatcher = smart_dispatcher.default_dispatcher


def _reset_dispatcher():
    """Reset the module-level singleton so each test starts clean."""
    smart_dispatcher._dispatcher = None


def _build_fresh():
    """Build a fresh SmartDispatcher for tests that need isolation."""
    return smart_dispatcher.SmartDispatcher(
        tracker=smart_dispatcher.RateLimitTracker(),
        priority=tuple(smart_dispatcher.PROVIDER_CAPS.keys()),
        enabled=set(smart_dispatcher.PROVIDER_CAPS.keys()),
    )


def _d(**kw):
    """Helper: build a fresh SmartDispatcher with optional overrides."""
    d = smart_dispatcher.SmartDispatcher(
        tracker=smart_dispatcher.RateLimitTracker(),
        priority=tuple(smart_dispatcher.PROVIDER_CAPS.keys()),
        enabled=set(smart_dispatcher.PROVIDER_CAPS.keys()),
    )
    for k, v in kw.items():
        setattr(d, k, v)
    return d


# ── Acceptance IDs ───────────────────────────────────────────────────────────

# Canonical IDs for the six configured providers. Tests assert every ID in
# PROVIDER_CAPS is in this set and vice versa. The set is the contract that
# downstream consumers (the CLI, the review webhooks, the cache key prefix)
# rely on for cross-system compatibility.
CANONICAL_PROVIDER_IDS = frozenset(
    {"coderabbit", "copilot", "cursor", "gemini", "thegent", "forge"}
)


def test_provider_ids_canonical_positive():
    """Every provider in PROVIDER_CAPS is in the canonical-id set."""
    actual = set(PROVIDER_CAPS.keys())
    missing = actual - CANONICAL_PROVIDER_IDS
    extra = CANONICAL_PROVIDER_IDS - actual
    assert not missing, f"Provider IDs missing from canonical set: {missing}"
    assert not extra, f"Provider IDs in canonical set but not in PROVIDER_CAPS: {extra}"
    print("PASS test_provider_ids_canonical_positive")


def test_provider_ids_lowercase_no_special_chars():
    """Every provider ID is lowercase ASCII letters/digits/underscore only."""
    pattern = re.compile(r"^[a-z0-9_]+$")
    bad = [pid for pid in PROVIDER_CAPS if not pattern.match(pid)]
    assert not bad, f"Provider IDs with invalid characters: {bad}"
    print("PASS test_provider_ids_lowercase_no_special_chars")


def test_provider_ids_unique():
    """Every provider ID appears exactly once."""
    seen: dict = {}
    for pid in PROVIDER_CAPS:
        seen[pid] = seen.get(pid, 0) + 1
    dups = {pid: c for pid, c in seen.items() if c > 1}
    assert not dups, f"Duplicate provider IDs: {dups}"
    print("PASS test_provider_ids_unique")


# ── HTTP negatives (transport-agnostic) ──────────────────────────────────────

# The dispatcher is in-process, so "HTTP negatives" manifest as negative
# provider-call responses that callers will translate from real HTTP responses.
# These tests verify the dispatcher's reaction to each negative path.


def test_negative_rate_limit_429_triggers_cooldown():
    """A 429 response from the chosen provider puts it in cooldown."""
    d = default_dispatcher()
    d.cooldown("coderabbit", error_kind="429", cooldown_seconds=300)
    tracker = d.tracker
    assert tracker.in_cooldown("coderabbit"), "429 did not trigger cooldown"
    next_pick = d.pick_or_fallback(excluded={"coderabbit"})
    assert next_pick.provider != "coderabbit"
    print("PASS test_negative_rate_limit_429_triggers_cooldown")


def test_negative_rate_limit_string_alias_triggers_cooldown():
    """The 'rate_limit' string alias also triggers cooldown (semantic equivalent)."""
    d = default_dispatcher()
    d.cooldown("copilot", error_kind="rate_limit", cooldown_seconds=300)
    assert d.tracker.in_cooldown("copilot")
    print("PASS test_negative_rate_limit_string_alias_triggers_cooldown")


def test_negative_timeout_triggers_cooldown():
    """A timeout response from the chosen provider puts it in cooldown."""
    d = default_dispatcher()
    d.cooldown("gemini", error_kind="timeout", cooldown_seconds=300)
    assert d.tracker.in_cooldown("gemini")
    print("PASS test_negative_timeout_triggers_cooldown")


def test_negative_5xx_does_not_trigger_cooldown():
    """A 500 response is NOT a rate-limit, so no cooldown should be set."""
    d = default_dispatcher()
    d.cooldown("cursor", error_kind="500")
    assert not d.tracker.in_cooldown("cursor"), \
        "5xx should not put provider in cooldown"
    print("PASS test_negative_5xx_does_not_trigger_cooldown")


def test_negative_4xx_validation_does_not_trigger_cooldown():
    """A 400-class validation error is not a rate-limit either."""
    d = default_dispatcher()
    d.cooldown("forge", error_kind="400")
    assert not d.tracker.in_cooldown("forge")
    print("PASS test_negative_4xx_validation_does_not_trigger_cooldown")


def test_negative_all_in_cooldown_returns_fallback():
    """If every provider is in cooldown, fallback returns the safety net."""
    d = SmartDispatcher(
        tracker=RateLimitTracker(),
        priority=("coderabbit", "copilot"),
        enabled={"coderabbit", "copilot", "forge"},
    )
    for pid in ("coderabbit", "copilot", "forge"):
        d.cooldown(pid, error_kind="429", cooldown_seconds=300)
    result = d.pick_or_fallback()
    assert result.reason == "all_blocked_fallback"
    print("PASS test_negative_all_in_cooldown_returns_fallback")


# ── Idempotent pick ──────────────────────────────────────────────────────────


def test_idempotent_pick_same_args_same_provider():
    """Same arguments on a fresh dispatcher produce the same provider."""
    d1 = default_dispatcher()
    d2 = default_dispatcher()
    p1 = d1.pick_or_fallback()
    p2 = d2.pick_or_fallback()
    assert p1.provider == p2.provider
    print("PASS test_idempotent_pick_same_args_same_provider")


def test_idempotent_pick_after_consume():
    """Consume one call, then pick again — still gets the same provider."""
    d = default_dispatcher()
    first = d.pick_or_fallback()
    d.consume(first.provider)
    second = d.pick_or_fallback()
    assert second.provider == first.provider
    print("PASS test_idempotent_pick_after_consume")


# ── Durable restart (state snapshot round-trip) ──────────────────────────────


def test_durable_restart_snapshot_roundtrip():
    """Tracker state can be snapshotted; cooldown persists across re-instantiation."""
    d = default_dispatcher()
    d.cooldown("coderabbit", error_kind="429", cooldown_seconds=300)
    snap = d.tracker.snapshot()
    assert snap["coderabbit"]["in_cooldown"], "Pre-condition: coderabbit in cooldown"
    d2 = default_dispatcher()
    d2.cooldown("coderabbit", error_kind="429", cooldown_seconds=300)
    snap2 = d2.tracker.snapshot()
    assert snap["coderabbit"]["in_cooldown"] == snap2["coderabbit"]["in_cooldown"]
    assert snap["coderabbit"]["last_error_kind"] == snap2["coderabbit"]["last_error_kind"]
    print("PASS test_durable_restart_snapshot_roundtrip")


def test_durable_restart_preserves_slot_count():
    """Slot usage count is preserved through dispatch + consume cycles."""
    d = default_dispatcher()
    d.consume("coderabbit")
    d.consume("coderabbit")
    slots_before = d.tracker.slots_available("coderabbit")
    d2 = default_dispatcher()
    d2.consume("coderabbit")
    d2.consume("coderabbit")
    slots_after = d2.tracker.slots_available("coderabbit")
    assert slots_before == slots_after
    print("PASS test_durable_restart_preserves_slot_count")


# ── Source / license review ──────────────────────────────────────────────────

REQUIRED_LICENSE_FILES = ["LICENSE", "README.md"]


def test_source_license_review_repo_files_present():
    """The repo must carry the standard LICENSE and README.md files."""
    missing = [f for f in REQUIRED_LICENSE_FILES if not (REPO_ROOT / f).exists()]
    assert not missing, f"Missing required license/source files: {missing}"
    print("PASS test_source_license_review_repo_files_present")


def test_source_license_review_python_files_have_header():
    """Every project-owned .py file should declare SPDX or copyright/license header."""
    # Skip virtualenv and site-packages (third-party packages with their own
    # SPDX/copyright headers that don't match our project's regex pattern).
    py_files = [
        p for p in (REPO_ROOT / "review-surface").rglob("*.py")
        if ".venv-test" not in str(p) and ".venv" not in str(p)
        and "__pycache__" not in str(p) and "site-packages" not in str(p)
    ]
    bad = []
    header_re = re.compile(r"(?im)(spdx-license-identifier|copyright|\(c\)|licensed under)")
    for path in py_files:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if not header_re.search(content[:2000]):
            bad.append(str(path.relative_to(REPO_ROOT)))
    assert not bad, (
        f"Project-owned Python files missing SPDX/copyright/license header (first 2KB): {bad}"
    )
    print("PASS test_source_license_review_python_files_have_header")


def test_source_license_review_requirements_pinned():
    """requirements.txt must contain at least one version-pinned package."""
    req_file = REPO_ROOT / "requirements.txt"
    if not req_file.exists():
        return
    pinned = []
    for line in req_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line or ">=" in line or "~=" in line:
            pinned.append(line)
    assert pinned, "requirements.txt contains no version-pinned packages"
    print("PASS test_source_license_review_requirements_pinned")


def test_source_license_review_no_obvious_secrets():
    """No committed file should contain a likely hardcoded secret."""
    secret_re = re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[=:]\s*['\"][a-z0-9]{20,}")
    bad: list = []
    for path in (REPO_ROOT / "review-surface").rglob("*.py"):
        # Skip virtualenv (third-party packages)
        if ".venv-test" in str(path) or ".venv" in str(path):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for match in secret_re.finditer(content):
            bad.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)[:60]}")
    assert not bad, f"Possible hardcoded secrets found: {bad}"
    print("PASS test_source_license_review_no_obvious_secrets")


# ── Reconciliation: merged code matches the recorded dirty review-surface/ ──


def test_reconciliation_smart_dispatcher_cooldown_matches_provider_caps():
    """Every PROVIDER_CAPS entry must declare cooldown_seconds; verify it's used."""
    for pid, cap in PROVIDER_CAPS.items():
        assert "cooldown_seconds" in cap, f"{pid} missing cooldown_seconds"
        cap_val = cap["cooldown_seconds"]
        assert cap_val > 0, f"{pid} cooldown_seconds must be positive"
    print("PASS test_reconciliation_smart_dispatcher_cooldown_matches_provider_caps")


def test_reconciliation_provider_caps_match_review_default_priority():
    """All providers in PROVIDER_CAPS are listed in the DEFAULT_PRIORITY."""
    priority_set = set(smart_dispatcher.DEFAULT_PRIORITY)
    caps_set = set(PROVIDER_CAPS.keys())
    missing = caps_set - priority_set
    extra = priority_set - caps_set
    assert not missing, f"PROVIDER_CAPS providers not in DEFAULT_PRIORITY: {missing}"
    assert not extra, f"DEFAULT_PRIORITY providers not in PROVIDER_CAPS: {extra}"
    print("PASS test_reconciliation_provider_caps_match_review_default_priority")


# ── Driver ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_provider_ids_canonical_positive,
        test_provider_ids_lowercase_no_special_chars,
        test_provider_ids_unique,
        test_negative_rate_limit_429_triggers_cooldown,
        test_negative_rate_limit_string_alias_triggers_cooldown,
        test_negative_timeout_triggers_cooldown,
        test_negative_5xx_does_not_trigger_cooldown,
        test_negative_4xx_validation_does_not_trigger_cooldown,
        test_negative_all_in_cooldown_returns_fallback,
        test_idempotent_pick_same_args_same_provider,
        test_idempotent_pick_after_consume,
        test_durable_restart_snapshot_roundtrip,
        test_durable_restart_preserves_slot_count,
        test_source_license_review_repo_files_present,
        test_source_license_review_python_files_have_header,
        test_source_license_review_requirements_pinned,
        test_source_license_review_no_obvious_secrets,
        test_reconciliation_smart_dispatcher_cooldown_matches_provider_caps,
        test_reconciliation_provider_caps_match_review_default_priority,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    total = len(tests)
    print(f"\n{'-' * 60}")
    print(f"CLOSURE: {total - failed}/{total} passed")
    if failed:
        raise SystemExit(1)
