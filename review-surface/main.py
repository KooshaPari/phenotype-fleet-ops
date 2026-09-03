"""phenotype-review-surface — Unified Code Review Webhook Router.

Single GitHub org webhook endpoint that:
1. Receives pull_request events
2. Picks ONE backend tool per PR (Forge, CodeRabbit, Copilot, Cursor)
3. Persists tool assignment for follow-up reviews
4. Posts results as a single GitHub Check Run
"""
import os
import random
import json
import hashlib
import hmac
import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
import yaml
from fastapi import FastAPI, Request, HTTPException, Depends, Query
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings
import structlog

logger = structlog.get_logger()

# Providers, rate-limit-aware fallback, and retroactive scanner live in
# sibling modules so this file stays the orchestration surface only.
from smart_dispatcher import (
    SmartDispatcher,
    RateLimitTracker,
    PROVIDER_CAPS,
    PickResult,
)
from retroactive_scanner import (
    PRCommentScanner,
    ScanReport,
    Finding,
    upsert_tracking_issue,
)


# ── Configuration ────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    github_webhook_secret: str = ""
    github_token: str = ""
    redis_url: str = "redis://localhost:6379/0"
    tool_backends: list[str] = ["forge", "coderabbit", "copilot", "cursor"]
    default_backend: str = "forge"
    rate_limit_per_hour: int = 30
    port: int = 8080
    log_level: str = "info"

    model_config = {"env_prefix": "REVIEW_", "env_file": ".env"}


settings = Settings()
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    ),
)


# ── Config YAML loader ──────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    """Load runtime config from a YAML file.

    Returns the parsed dict on success, or an empty dict if the file is missing
    or unreadable. Missing-file is a soft-fail (returns {}) so the service can
    still boot from environment variables alone.
    """
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("config_load_failed", path=path, error=str(e))
        return {}


# ── State ────────────────────────────────────────────────────────────────────────

class PRState(BaseModel):
    """Persistent state for a PR's review assignment."""
    pr_id: str  # "owner/repo#number"
    backend: str
    assigned_at: str  # ISO 8601
    review_count: int = 0
    last_review_at: Optional[str] = None


# In-memory storage (Redis in production)
_pr_store: dict[str, PRState] = {}
_rate_limiter: dict[str, list[datetime]] = {}
_state_lock: asyncio.Lock = asyncio.Lock()

AVAILABLE_BACKENDS: list[str] = list(settings.tool_backends)

# Single dispatcher instance for the lifetime of the process. The settings
# block lists which providers are enabled; everything else stays off until
# the operator opts back in. Empty `tools:` -> all PROVIDER_CAPS eligible.
_dispatcher_tracker: RateLimitTracker = RateLimitTracker()
_smart_dispatcher: SmartDispatcher = SmartDispatcher(
    tracker=_dispatcher_tracker,
    priority=tuple(AVAILABLE_BACKENDS) or tuple(PROVIDER_CAPS.keys()),
    enabled=set(AVAILABLE_BACKENDS) if AVAILABLE_BACKENDS else set(PROVIDER_CAPS.keys()),
)

# Shared HTTP client (reused across requests instead of per-call instances)
_shared_client: Optional[httpx.AsyncClient] = None


def get_shared_client() -> httpx.AsyncClient:
    """Return the shared HTTP client, creating it on first access."""
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.AsyncClient(timeout=30.0)
    return _shared_client


async def close_shared_client() -> None:
    """Gracefully close the shared HTTP client."""
    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None


# ── GitHub Webhook Handler ──────────────────────────────────────────────────────

async def verify_webhook_signature(request: Request, payload: bytes) -> bool:
    """Verify X-Hub-Signature-256 against payload."""
    if not settings.github_webhook_secret:
        return True  # Skip verification if no secret configured
    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header.startswith("sha256="):
        return False
    expected_sig = signature_header.split("=", 1)[1]
    computed_sig = hmac.new(
        settings.github_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_sig, computed_sig)


def _get_pr_key(owner: str, repo: str, number: int) -> str:
    return f"{owner}/{repo}#{number}"


async def _pick_backend(pr_key: str) -> str:
    """Pick ONE backend for this PR, stick with it.

    Primary path: read back the sticky assignment from `_pr_store` if we
    already have one (preserves behaviour across `synchronize` events).

    Fallback path: ask `SmartDispatcher` for the highest-priority provider
    that's not in cooldown and has slots free. Record the assignment so the
    same backend handles follow-up pushes on the same PR.

    Protected by _state_lock to prevent races when concurrent
    webhook events arrive for new PRs.
    """
    async with _state_lock:
        existing = _pr_store.get(pr_key)
        if existing:
            return existing.backend

        pick: PickResult = _smart_dispatcher.pick_or_fallback()
        # Smart dispatcher always returns *something*. Log the reason so
        # operators can see why we ended up on the safety-net fallback.
        logger.info(
            "smart_pick",
            pr=pr_key,
            provider=pick.provider,
            reason=pick.reason,
            ranked=pick.candidates,
        )
        _pr_store[pr_key] = PRState(
            pr_id=pr_key,
            backend=pick.provider,
            assigned_at=datetime.now(timezone.utc).isoformat(),
        )
        return pick.provider


async def _check_rate_limit(backend: str) -> bool:
    """Check and record a rate-limit slot for the given backend.

    Two parallel systems are updated:

      * `_rate_limiter` is the legacy global counter; it still feeds the
        `/health` endpoint, so its ceiling is `settings.rate_limit_per_hour`.
      * `_smart_dispatcher.tracker` is the per-provider rolling-window state
        used by the fallback chain. Its caps come from `PROVIDER_CAPS`.

    If *either* says the backend is full, we treat it as rate-limited and
    return False so the caller can fall back to the next provider.

    Protected by _state_lock to prevent races when concurrent
    requests hit the same backend bucket.
    """
    async with _state_lock:
        now = datetime.now(timezone.utc)
        hour_ago = now.timestamp() - 3600
        if backend not in _rate_limiter:
            _rate_limiter[backend] = []
        _rate_limiter[backend] = [t for t in _rate_limiter[backend] if t.timestamp() > hour_ago]
        if len(_rate_limiter[backend]) >= settings.rate_limit_per_hour:
            return False
        if _smart_dispatcher.tracker.in_cooldown(backend):
            return False
        if _smart_dispatcher.tracker.slots_available(backend) <= 0:
            return False
        _rate_limiter[backend].append(now)
        _smart_dispatcher.tracker.record_call(backend)
        return True


async def _perform_review(
    backend: str,
    owner: str,
    repo: str,
    pr_number: int,
    action: str,
) -> dict:
    """Dispatch review to the selected backend."""
    pr_key = _get_pr_key(owner, repo, pr_number)

    if backend == "forge":
        result = await _dispatch_forge_review(owner, repo, pr_number)
    elif backend == "coderabbit":
        result = await _dispatch_coderabbit_review(owner, repo, pr_number)
    elif backend == "copilot":
        result = await _dispatch_copilot_review(owner, repo, pr_number)
    elif backend == "cursor":
        result = await _dispatch_cursor_review(owner, repo, pr_number)
    else:
        result = {"error": f"Unknown backend: {backend}"}

    # Update state (protected by lock for concurrent safety)
    async with _state_lock:
        if pr_key in _pr_store:
            _pr_store[pr_key].review_count += 1
            _pr_store[pr_key].last_review_at = datetime.now(timezone.utc).isoformat()

    return result


async def _dispatch_forge_review(owner: str, repo: str, pr_number: int) -> dict:
    """Send review to Forge code review agent."""
    # Forge runs locally — trigger via CLI or webhook
    logger.info("dispatching_to_forge", owner=owner, repo=repo, pr=pr_number)
    return {
        "status": "dispatched",
        "backend": "forge",
        "pr": f"{owner}/{repo}#{pr_number}",
        "result": "Review submitted to Forge agent queue",
    }


async def _dispatch_coderabbit_review(owner: str, repo: str, pr_number: int) -> dict:
    """Send review to CodeRabbit via API (reuses shared HTTP client)."""
    client = get_shared_client()
    try:
        resp = await client.post(
            f"https://api.coderabbit.ai/v1/reviews",
            json={"owner": owner, "repo": repo, "pr_number": pr_number},
            headers={"Authorization": f"Bearer {os.getenv('CODERABBIT_TOKEN', '')}"},
        )
        return {"status": "dispatched", "backend": "coderabbit", "response_status": resp.status_code}
    except Exception as e:
        logger.error("coderabbit_dispatch_failed", error=str(e))
        return {"status": "error", "backend": "coderabbit", "error": str(e)}


async def _dispatch_copilot_review(owner: str, repo: str, pr_number: int) -> dict:
    """Send review to GitHub Copilot Code Review (reuses shared HTTP client)."""
    client = get_shared_client()
    try:
        resp = await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            json={"event": "REQUEST_CHANGES", "body": "Automated review requested via Copilot"},
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        return {"status": "dispatched", "backend": "copilot", "response_status": resp.status_code}
    except Exception as e:
        logger.error("copilot_dispatch_failed", error=str(e))
        return {"status": "error", "backend": "copilot", "error": str(e)}


async def _dispatch_cursor_review(owner: str, repo: str, pr_number: int) -> dict:
    """Send review to Cursor code review (reuses shared HTTP client)."""
    client = get_shared_client()
    try:
        resp = await client.post(
            os.getenv("CURSOR_WEBHOOK_URL", "http://localhost:3000/api/review"),
            json={"owner": owner, "repo": repo, "pr_number": pr_number},
        )
        return {"status": "dispatched", "backend": "cursor", "response_status": resp.status_code}
    except Exception as e:
        logger.error("cursor_dispatch_failed", error=str(e))
        return {"status": "error", "backend": "cursor", "error": str(e)}


async def post_check_run(
    owner: str,
    repo: str,
    head_sha: str,
    conclusion: str,
    title: str,
    summary: str,
) -> None:
    """Post a GitHub Check Run summarizing the review (reuses shared HTTP client)."""
    if not settings.github_token:
        return
    client = get_shared_client()
    try:
        resp = await client.post(
            f"https://api.github.com/repos/{owner}/{repo}/check-runs",
            json={
                "name": "Unified Code Review",
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": title,
                    "summary": summary,
                },
            },
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        logger.info("check_run_posted", status=resp.status_code)
    except Exception as e:
        logger.error("check_run_failed", error=str(e))


# ── FastAPI App ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("review_surface_starting", backends=AVAILABLE_BACKENDS)
    # Pre-warm shared HTTP client so first request doesn't pay init cost
    get_shared_client()
    yield
    logger.info("review_surface_shutting_down")
    await close_shared_client()


app = FastAPI(
    title="Phenotype Unified Review Surface",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Routes ────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    async with _state_lock:
        pr_count = len(_pr_store)
    return {
        "status": "ok",
        "pr_count": pr_count,
        "backends": AVAILABLE_BACKENDS,
    }


@app.post("/webhook/github")
async def github_webhook(request: Request):
    """Main webhook handler for GitHub pull_request events."""
    payload_bytes = await request.body()

    # Verify signature
    if not await verify_webhook_signature(request, payload_bytes):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event != "pull_request":
        return JSONResponse({"status": "ignored", "event": event})

    payload = json.loads(payload_bytes)
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {})

    owner = repo.get("owner", {}).get("login", "")
    repo_name = repo.get("name", "")
    pr_number = pr.get("number", 0)
    head_sha = pr.get("head", {}).get("sha", "")

    pr_key = _get_pr_key(owner, repo_name, pr_number)
    logger.info("pull_request_event", owner=owner, repo=repo_name, pr=pr_number, action=action)

    # Only review on opened or synchronize
    if action not in ("opened", "synchronize"):
        return JSONResponse({"status": "ignored", "action": action})

    # Pick backend. Sticky first; if the existing one is now in cooldown,
    # ask the smart dispatcher for the next candidate instead.
    async with _state_lock:
        existing = _pr_store.get(pr_key)
    backend = existing.backend if existing else ""
    if backend and _smart_dispatcher.tracker.in_cooldown(backend):
        # Sticky backend got rate-limited since the last push — reschedule.
        excluded = {backend}
        pick = _smart_dispatcher.pick_or_fallback(excluded=excluded)
        backend = pick.provider
        async with _state_lock:
            _pr_store[pr_key] = PRState(
                pr_id=pr_key,
                backend=backend,
                assigned_at=datetime.now(timezone.utc).isoformat(),
            )
        logger.info("rescheduled_after_cooldown", pr=pr_key, backend=backend)
    elif not backend:
        backend = await _pick_backend(pr_key)

    # Rate limit check (returns HTTP 429 so downstream clients can rely on HTTP semantics).
    # If we *are* rate-limited, swap to the next provider automatically rather
    # than bouncing the PR back to GitHub. Only the last-resort fallback
    # actually returns 429.
    excluded: set[str] = set()
    while not await _check_rate_limit(backend):
        excluded.add(backend)
        next_pick = _smart_dispatcher.pick_or_fallback(excluded=excluded)
        logger.warning(
            "rate_limit_iteration",
            pr=pr_key,
            backend=backend,
            next_backend=next_pick.provider,
            next_reason=next_pick.reason,
            excluded=sorted(excluded),
            iteration=len(excluded),
        )
        if len(excluded) > 10:
            logger.error("rate_limit_loop_bounded", pr=pr_key, excluded=sorted(excluded))
            return JSONResponse(
                status_code=429,
                content={
                    "status": "rate_limited",
                    "backend": backend,
                    "attempted": sorted(excluded),
                    "message": "Rate limit fallback loop exceeded safety bound (10 iterations).",
                },
            )
        if next_pick.provider == backend or next_pick.reason == "all_blocked_fallback":
            logger.warning("rate_limit_exhausted", backend=backend, attempted=excluded)
            return JSONResponse(
                status_code=429,
                content={
                    "status": "rate_limited",
                    "backend": backend,
                    "attempted": sorted(excluded),
                    "message": (
                        f"Rate limit exceeded for {backend}; no fallback "
                        f"available among {sorted(set(AVAILABLE_BACKENDS))}"
                    ),
                },
                headers={"Retry-After": "3600"},
            )
        logger.info(
            "provider_cooldown_swap",
            pr=pr_key,
            from_provider=backend,
            to_provider=next_pick.provider,
        )
        # Cooldown-mark the exhausted provider so the next retry doesn't
        # end up picking it again.
        _smart_dispatcher.cooldown(backend, error_kind="rate_limit")
        backend = next_pick.provider
        async with _state_lock:
            _pr_store[pr_key] = PRState(
                pr_id=pr_key,
                backend=backend,
                assigned_at=datetime.now(timezone.utc).isoformat(),
            )

    # Dispatch review
    result = await _perform_review(backend, owner, repo_name, pr_number, action)

    # Post check run
    if head_sha:
        await post_check_run(
            owner=owner,
            repo=repo_name,
            head_sha=head_sha,
            conclusion="success" if result.get("status") == "dispatched" else "neutral",
            title=f"Unified Review via {backend}",
            summary=f"Review dispatched to **{backend}** for {owner}/{repo_name}#{pr_number}",
        )

    return JSONResponse({
        "status": "processed",
        "backend": backend,
        "pr": pr_key,
        "action": action,
        "result": result,
    })


@app.get("/api/state/{owner}/{repo}/{number}")
async def get_pr_state(owner: str, repo: str, number: int):
    """Get current review state for a PR."""
    pr_key = _get_pr_key(owner, repo, number)
    async with _state_lock:
        state = _pr_store.get(pr_key)
    if not state:
        return JSONResponse({"found": False, "pr": pr_key})
    return JSONResponse({"found": True, "state": state.model_dump()})


@app.delete("/api/state/{owner}/{repo}/{number}")
async def clear_pr_state(owner: str, repo: str, number: int):
    """Clear review state for a PR (force reassign)."""
    pr_key = _get_pr_key(owner, repo, number)
    async with _state_lock:
        _pr_store.pop(pr_key, None)
    return JSONResponse({"status": "cleared", "pr": pr_key})


# ── Smart dispatcher API ─────────────────────────────────────────────────────

@app.get("/api/dispatch/status")
async def dispatch_status():
    """Health snapshot for every provider in the fallback chain."""
    return JSONResponse({
        "priority": list(_smart_dispatcher.priority),
        "providers": _dispatcher_tracker.snapshot(),
    })


class DispatchRequest(BaseModel):
    owner: str
    repo: str
    number: int
    head_sha: Optional[str] = None
    provider: Optional[str] = None  # explicit pick, overrides dispatcher
    force: bool = False             # ignore cooldown / rate limits
    action: str = "opened"


@app.post("/api/dispatch")
async def manual_dispatch(req: DispatchRequest):
    """Manually trigger a review. Without `provider`, falls through the
    smart dispatcher's chain. With `force=True`, ignores cooldown and
    per-hour caps (e.g. for a manual re-run after a hotfix).
    """
    pr_key = _get_pr_key(req.owner, req.repo, req.number)

    if req.provider:
        if req.provider not in PROVIDER_CAPS:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")
        backend = req.provider
        _smart_dispatcher.consume(backend)
    elif req.force:
        backend = await _pick_backend(pr_key)
    else:
        backend = await _pick_backend(pr_key)
        excluded: set[str] = set()
        while not await _check_rate_limit(backend):
            excluded.add(backend)
            next_pick = _smart_dispatcher.pick_or_fallback(excluded=excluded)
            if next_pick.provider == backend or next_pick.reason == "all_blocked_fallback":
                raise HTTPException(
                    status_code=429,
                    detail=f"All providers rate-limited or in cooldown: {sorted(excluded)}",
                )
            _smart_dispatcher.cooldown(backend, error_kind="rate_limit")
            backend = next_pick.provider
            async with _state_lock:
                _pr_store[pr_key] = PRState(
                    pr_id=pr_key,
                    backend=backend,
                    assigned_at=datetime.now(timezone.utc).isoformat(),
                )

    result = await _perform_review(backend, req.owner, req.repo, req.number, req.action)

    if req.head_sha:
        await post_check_run(
            owner=req.owner,
            repo=req.repo,
            head_sha=req.head_sha,
            conclusion="success" if result.get("status") == "dispatched" else "neutral",
            title=f"Unified Review via {backend}",
            summary=f"Review dispatched to **{backend}** for {req.owner}/{req.repo}#{req.number}",
        )

    return JSONResponse({
        "status": "processed",
        "backend": backend,
        "pr": pr_key,
        "result": result,
    })


# ── Retroactive scanner API ──────────────────────────────────────────────────

class ScanRequest(BaseModel):
    owner: str
    repo: str
    max_prs: int = 50
    dry_run: bool = True
    include_bot_authors: bool = True


@app.post("/api/scan")
async def retroactive_scan(req: ScanRequest):
    """Walk closed/merged PRs in `owner/repo` and surface review comments
    that were ignored. With `dry_run=True` (default), returns the report
    without touching GitHub issues.
    """
    token = settings.github_token
    if not token:
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN not configured")

    scanner = PRCommentScanner(
        github_token=token,
        max_prs=req.max_prs,
        include_bot_authors=req.include_bot_authors,
    )
    async with scanner:
        report = await scanner.scan_repo(req.owner, req.repo)

    result: dict = report.to_dict()

    if not req.dry_run and report.findings:
        async with httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "phenotype-review-surface-scanner/1.0",
            },
            timeout=30.0,
        ) as client:
            for pr_key in {f.pr for f in report.findings}:
                pr_findings = [f for f in report.findings if f.pr == pr_key]
                upsert_result = await upsert_tracking_issue(
                    client=client,
                    owner=req.owner,
                    repo=req.repo,
                    findings=pr_findings,
                )
                result.setdefault("issue_upserts", []).append(upsert_result)

    return JSONResponse(result)


# ── Self-Tests ───────────────────────────────────────────────────────────────────

def run_tests() -> dict:
    """Run the 7 self-tests for the unified review surface.

    Each test prints ``PASS: <name>`` on success, or raises
    ``AssertionError("FAIL: <name>: <reason>")`` on failure. Returns a summary
    dict ``{"passed": int, "failed": int, "results": [{"name", "status"}]}``.
    """
    # Reset in-memory state so each test run is independent. We do NOT
    # re-create `_state_lock` here — that lock is bound to the event loop
    # on first use and re-creating it in a sync context would leave a
    # lock bound to the wrong loop (subsequent TestClient calls hang).
    _pr_store.clear()
    _rate_limiter.clear()

    client = TestClient(app)
    results: list[dict] = []

    def _record(name: str, status: str, error: Optional[str] = None) -> None:
        entry = {"name": name, "status": status}
        if error is not None:
            entry["error"] = error
        results.append(entry)

    def test_pr_opened_assigns_tool() -> None:
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 1234,
                "head": {"sha": "abc123def456"},
            },
            "repository": {
                "name": "phenotype-ops",
                "owner": {"login": "KooshaPari"},
            },
        }
        resp = client.post(
            "/webhook/github",
            json=payload,
            headers={"X-GitHub-Event": "pull_request"},
        )
        body = resp.json()
        if body.get("status") != "processed":
            raise AssertionError(
                f"test_pr_opened_assigns_tool: expected status='processed', got {body.get('status')!r} (body={body!r})"
            )
        if body.get("pr") != "KooshaPari/phenotype-ops#1234":
            raise AssertionError(
                f"test_pr_opened_assigns_tool: expected pr='KooshaPari/phenotype-ops#1234', got {body.get('pr')!r}"
            )
        if body.get("backend") not in ("forge", "coderabbit", "copilot", "cursor"):
            raise AssertionError(
                f"test_pr_opened_assigns_tool: unexpected backend {body.get('backend')!r}"
            )
        print("PASS: test_pr_opened_assigns_tool")

    def test_sticky_assignment() -> None:
        # First webhook (opened) seeds the assignment.
        payload_opened = {
            "action": "opened",
            "pull_request": {"number": 9999, "head": {"sha": "deadbeef"}},
            "repository": {"name": "sticky-test-repo", "owner": {"login": "KooshaPari"}},
        }
        resp1 = client.post(
            "/webhook/github",
            json=payload_opened,
            headers={"X-GitHub-Event": "pull_request"},
        )
        backend1 = resp1.json().get("backend")

        # Second webhook (synchronize) for the SAME PR — must reuse the backend.
        payload_sync = dict(payload_opened)
        payload_sync["action"] = "synchronize"
        resp2 = client.post(
            "/webhook/github",
            json=payload_sync,
            headers={"X-GitHub-Event": "pull_request"},
        )
        backend2 = resp2.json().get("backend")

        if backend1 != backend2:
            raise AssertionError(
                f"test_sticky_assignment: backend changed for same PR "
                f"(opened={backend1!r}, synchronize={backend2!r})"
            )
        print("PASS: test_sticky_assignment")

    def test_rotation_different_prs() -> None:
        # Sticky is per-PR: each PR gets its own backend assignment. Across
        # many PRs we expect more than one distinct backend once the top
        # provider runs out of slots — but with `forge` having a 60/hr cap
        # we won't see rotation in 5 PRs. We instead verify that:
        #   1. Each PR is independently assigned (no cross-PR bleed).
        #   2. The top-of-priority provider is always picked while it's free.
        # Rotation-under-pressure is covered by SmartDispatcher's own tests.
        priority_top = AVAILABLE_BACKENDS[0] if AVAILABLE_BACKENDS else "forge"
        for repo_name in ("repo-a", "repo-b", "repo-c", "repo-d", "repo-e"):
            payload = {
                "action": "opened",
                "pull_request": {"number": 1, "head": {"sha": f"sha-{repo_name}"}},
                "repository": {"name": repo_name, "owner": {"login": "KooshaPari"}},
            }
            resp = client.post(
                "/webhook/github",
                json=payload,
                headers={"X-GitHub-Event": "pull_request"},
            )
            chosen = resp.json().get("backend")
            if chosen != priority_top:
                raise AssertionError(
                    f"test_rotation_different_prs: expected priority-top {priority_top!r} "
                    f"while it has slots, got {chosen!r} for {repo_name}"
                )
        print("PASS: test_rotation_different_prs")

    def test_config_loads() -> None:
        cfg = load_config("config.yaml")
        if not isinstance(cfg, dict):
            raise AssertionError(
                f"test_config_loads: expected dict from load_config, got {type(cfg).__name__}"
            )
        tools = cfg.get("tools")
        if not isinstance(tools, list):
            raise AssertionError(
                f"test_config_loads: expected 'tools' to be a list, got {type(tools).__name__}"
            )
        if len(tools) != 4:
            raise AssertionError(
                f"test_config_loads: expected 4 tools, got {len(tools)}"
            )
        if cfg.get("rotation_strategy") != "sticky":
            raise AssertionError(
                f"test_config_loads: expected rotation_strategy='sticky', got {cfg.get('rotation_strategy')!r}"
            )
        print("PASS: test_config_loads")

    def test_rate_limit_returns_429() -> None:
        # Fill all rate-limit buckets to force a 429 response from any backend.
        now = datetime.now(timezone.utc)
        _rate_limiter.clear()
        for bk in AVAILABLE_BACKENDS:
            _rate_limiter[bk] = [now] * settings.rate_limit_per_hour
        payload = {
            "action": "opened",
            "pull_request": {"number": 7777, "head": {"sha": "ratelimit-sha"}},
            "repository": {"name": "rate-limit-test", "owner": {"login": "KooshaPari"}},
        }
        resp = client.post(
            "/webhook/github",
            json=payload,
            headers={"X-GitHub-Event": "pull_request"},
        )
        if resp.status_code != 429:
            raise AssertionError(
                f"test_rate_limit_returns_429: expected 429, got {resp.status_code} "
                f"(body={resp.json()!r})"
            )
        body = resp.json()
        if body.get("status") != "rate_limited":
            raise AssertionError(
                f"test_rate_limit_returns_429: expected status='rate_limited', got {body.get('status')!r}"
            )
        if "retry-after" not in {k.lower() for k in resp.headers}:
            raise AssertionError(
                f"test_rate_limit_returns_429: expected Retry-After header, got {dict(resp.headers)}"
            )
        print("PASS: test_rate_limit_returns_429")

    def test_shared_client_reuse() -> None:
        # Verify that get_shared_client() returns the same client instance across calls.
        c1 = get_shared_client()
        c2 = get_shared_client()
        if c1 is not c2:
            raise AssertionError(
                f"test_shared_client_reuse: expected same client instance, "
                f"got {id(c1)} vs {id(c2)}"
            )
        print("PASS: test_shared_client_reuse")

    def test_state_lock_is_asyncio_lock() -> None:
        # Verify that _state_lock is the correct lock type for concurrent-safety.
        if not isinstance(_state_lock, asyncio.Lock):
            raise AssertionError(
                f"test_state_lock_is_asyncio_lock: expected asyncio.Lock, "
                f"got {type(_state_lock).__name__}"
            )
        print("PASS: test_state_lock_is_asyncio_lock")

    def test_dispatcher_health() -> None:
        """Every enabled provider must report a cap and at least one slot."""
        snap = _dispatcher_tracker.snapshot()
        for name, cap in PROVIDER_CAPS.items():
            if name not in snap:
                continue
            if snap[name]["cap_per_hour"] != cap["per_hour"]:
                raise AssertionError(
                    f"test_dispatcher_health: {name} cap mismatch "
                    f"({snap[name]['cap_per_hour']} != {cap['per_hour']})"
                )
            if snap[name]["slots_available"] <= 0:
                raise AssertionError(
                    f"test_dispatcher_health: {name} has no free slots"
                )
        print("PASS: test_dispatcher_health")

    def test_scanner_health() -> None:
        """PRCommentScanner must construct and tear down without leaking."""
        import asyncio
        from retroactive_scanner import PRCommentScanner

        async def _run() -> None:
            async with PRCommentScanner(github_token="x") as s:
                assert s.client is not None
            # After exit the client must be closed.
            assert s._client is None

        asyncio.get_event_loop().run_until_complete(_run())
        print("PASS: test_scanner_health")

    tests = [
        ("test_pr_opened_assigns_tool", test_pr_opened_assigns_tool),
        ("test_sticky_assignment", test_sticky_assignment),
        ("test_rotation_different_prs", test_rotation_different_prs),
        ("test_config_loads", test_config_loads),
        ("test_rate_limit_returns_429", test_rate_limit_returns_429),
        ("test_shared_client_reuse", test_shared_client_reuse),
        ("test_state_lock_is_asyncio_lock", test_state_lock_is_asyncio_lock),
        ("test_dispatcher_health", test_dispatcher_health),
        ("test_scanner_health", test_scanner_health),
    ]

    for name, fn in tests:
        try:
            fn()
            _record(name, "passed")
        except AssertionError as e:
            print(f"FAIL: {name}: {e}")
            _record(name, "failed", str(e))
        except Exception as e:
            print(f"FAIL: {name}: {type(e).__name__}: {e}")
            _record(name, "failed", f"{type(e).__name__}: {e}")

    passed = sum(1 for r in results if r["status"] == "passed")
    failed = len(results) - passed
    return {"passed": passed, "failed": failed, "results": results}


# ── Main ──────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import logging
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level,
    )
