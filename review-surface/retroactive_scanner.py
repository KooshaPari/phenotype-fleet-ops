"""retroactive_scanner — walk merged/closed PR comments and surface findings
that were ignored.

The first generation of review-surface only commented in real time on new PRs.
Many repos that *predate* it still have review comments from other bots (or
from humans) that were never actioned. The scanner looks at:

    1. PR review comments (inline on diff)
    2. PR issue comments (top-level conversation comments)
    3. Bot-authored comments
    4. Comments whose keywords indicate an unresolved finding
       (`fix this`, `must address`, `blocker`, `p0`, `p1`, etc.)

For each PR with ≥ 1 ignored finding it opens (or updates) a tracking issue
labelled `retroactive-review-finding`. That keeps the audit log in GitHub
where humans already look.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import httpx

FINDING_KEYWORDS = (
    "blocker",
    "must address",
    "must fix",
    "must be addressed",
    "needs to be fixed",
    "should be fixed",
    "this is a bug",
    "this looks like a bug",
    "unresolved",
    "fix this",
    "needs fixing",
    "please address",
    "we should address",
    "p0",
    "p1",
    "p2",
    "p3",
)

BOT_LOGINS = (
    "coderabbitai",
    "coderabbit",
    "copilot",
    "cursor",
    "github-actions",
    "dependabot",
    "renovate",
    "gemini-code-assist",
    "thegent",
    "phenotype-review-surface",
)


@dataclass
class Finding:
    pr: str  # "owner/repo#123"
    comment_id: int
    comment_url: str
    author: str
    body: str
    kind: str  # "inline" | "issue"
    severity: str  # "p0" | "p1" | "p2" | "p3" | "unknown"
    ignored_reason: Optional[str] = None


@dataclass
class ScanReport:
    repo: str  # "owner/repo"
    scanned_prs: int = 0
    prs_with_findings: int = 0
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scanned_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "scanned_prs": self.scanned_prs,
            "prs_with_findings": self.prs_with_findings,
            "findings": [asdict(f) for f in self.findings],
            "errors": self.errors,
            "scanned_at": self.scanned_at,
        }


# ── Severity classification ───────────────────────────────────────────────────

_RE_P0 = re.compile(r"\b(p0|blocker|critical)\b", re.IGNORECASE)
_RE_P1 = re.compile(r"\b(p1|high priority|major)\b", re.IGNORECASE)
_RE_P2 = re.compile(r"\b(p2|medium|moderate)\b", re.IGNORECASE)
_RE_P3 = re.compile(r"\b(p3|low priority|nitpick|minor)\b", re.IGNORECASE)

# Words that suggest the comment was *dismissed* by the author and should NOT
# count as a finding. We are conservative: a single mention of "resolved",
# "addressed", or "wontfix" anywhere in the body dials the finding down.
_DISMISS_HINTS = re.compile(
    r"\b(resolved|addressed|wontfix|won'?t fix|by design|not an issue)\b",
    re.IGNORECASE,
)


def classify(body: str) -> tuple[str, bool]:
    """Return (severity, dismissed?)."""
    if not body or len(body.strip()) < 4:
        return "unknown", True
    if _DISMISS_HINTS.search(body):
        return "unknown", True

    body_l = body.lower()
    has_keyword = any(k in body_l for k in FINDING_KEYWORDS)
    if not has_keyword:
        return "unknown", True

    if _RE_P0.search(body):
        return "p0", False
    if _RE_P1.search(body):
        return "p1", False
    if _RE_P2.search(body):
        return "p2", False
    if _RE_P3.search(body):
        return "p3", False
    return "unknown", False


# ── Scanner ───────────────────────────────────────────────────────────────────


class PRCommentScanner:
    """Walks a repo's PR history via the GitHub REST API."""

    def __init__(
        self,
        github_token: str,
        api_base: str = "https://api.github.com",
        max_prs: int = 50,
        only_states: Iterable[str] = ("merged", "closed"),
        include_bot_authors: bool = True,
        timeout: float = 20.0,
    ) -> None:
        self.token = github_token
        self.api_base = api_base.rstrip("/")
        self.max_prs = max_prs
        self.only_states = set(only_states)
        self.include_bot_authors = include_bot_authors
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "PRCommentScanner":
        self._client = httpx.AsyncClient(
            base_url=self.api_base,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "phenotype-review-surface-scanner/1.0",
            },
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        assert self._client is not None, "use 'async with PRCommentScanner(...)'"
        return self._client

    # ── raw fetchers ──────────────────────────────────────────────────────────

    async def list_recent_prs(self, owner: str, repo: str) -> list[dict[str, Any]]:
        params = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": str(min(self.max_prs, 100)),
        }
        r = await self.client.get(f"/repos/{owner}/{repo}/pulls", params=params)
        r.raise_for_status()
        items = r.json()
        if self.only_states:
            items = [
                p
                for p in items
                if (p.get("state") == "merged" and "merged" in self.only_states)
                or (
                    p.get("state") == "closed"
                    and not p.get("merged_at")
                    and "closed" in self.only_states
                )
            ]
        return items

    async def list_review_comments(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        r = await self.client.get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            params={"per_page": "100"},
        )
        r.raise_for_status()
        return r.json()

    async def list_issue_comments(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        r = await self.client.get(
            f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            params={"per_page": "100"},
        )
        r.raise_for_status()
        return r.json()

    # ── main scan ─────────────────────────────────────────────────────────────

    async def scan_repo(self, owner: str, repo: str) -> ScanReport:
        report = ScanReport(repo=f"{owner}/{repo}")
        try:
            prs = await self.list_recent_prs(owner, repo)
        except Exception as e:
            report.errors.append(f"list_prs_failed: {e!r}")
            return report

        report.scanned_prs = len(prs)
        seen_prs: set[int] = set()

        for pr in prs[: self.max_prs]:
            pr_number = pr["number"]
            if pr_number in seen_prs:
                continue
            seen_prs.add(pr_number)
            pr_facts = []
            try:
                pr_facts.extend(await self.list_review_comments(owner, repo, pr_number))
            except Exception as e:
                report.errors.append(f"review_comments_failed:{pr_number}: {e!r}")
            try:
                pr_facts.extend(await self.list_issue_comments(owner, repo, pr_number))
            except Exception as e:
                report.errors.append(f"issue_comments_failed:{pr_number}: {e!r}")

            for c in pr_facts:
                finding = self._to_finding(c, owner, repo, pr_number)
                if finding is None:
                    continue
                report.findings.append(finding)

        report.prs_with_findings = len({f.pr for f in report.findings})
        return report

    def _to_finding(
        self,
        comment: dict[str, Any],
        owner: str,
        repo: str,
        pr_number: int,
    ) -> Optional[Finding]:
        author = (comment.get("user") or {}).get("login", "")
        body = comment.get("body") or ""
        kind = (
            "inline"
            if "pull_request_review_id" in comment or "path" in comment
            else "issue"
        )
        author_l = author.lower()

        is_bot = author_l in BOT_LOGINS or author_l.endswith("[bot]")
        if not is_bot and not self.include_bot_authors:
            # Even when caller wants human-only, we still surface comments
            # whose keywords are loud enough.
            pass

        severity, dismissed = classify(body)
        if dismissed:
            return None
        if severity == "unknown":
            # Had a keyword but no P-tier; still worth tracking if author is a
            # bot, because bot findings tend to drift.
            if not is_bot:
                return None

        return Finding(
            pr=f"{owner}/{repo}#{pr_number}",
            comment_id=comment.get("id", 0),
            comment_url=comment.get("html_url", ""),
            author=author,
            body=body[:2000],  # truncate for issue body
            kind=kind,
            severity=severity,
        )


# ── Issue persistence ─────────────────────────────────────────────────────────


def render_issue_body(repo: str, findings: list[Finding]) -> dict[str, str]:
    """Build title + body for the tracking issue that the scanner will open."""
    title = f"Retroactive review findings — {repo} ({len(findings)})"
    lines = [
        "This issue was opened by `phenotype-review-surface` retroactive scanner.",
        "",
        f"Found **{len(findings)}** ignored or unaddressed review comments across "
        f"{len({f.pr for f in findings})} PRs. Triage by severity, then either:",
        "",
        "- Open a follow-up PR that fixes the underlying issue, or",
        "- Add a `wontfix` rationale on each numbered item.",
        "",
        "Items marked `auto-track` were detected by severity keyword. Items marked "
        "`bot` came from a bot author.",
        "",
        "| Severity | PR | Author | Comment |",
        "|---|---|---|---|",
    ]
    for f in findings[:200]:  # cap body length for GitHub's 65536-char limit
        body_safe = f.body.replace("\n", " ").replace("|", "\\|")[:300]
        lines.append(
            f"| {f.severity} | {f.pr} | {f.author} | "
            f"[{f.comment_id}]({f.comment_url}) — {body_safe} |"
        )
    if len(findings) > 200:
        lines.append("")
        lines.append(f"_… and {len(findings) - 200} more (see scan log)._")
    return {"title": title, "body": "\n".join(lines)}


async def upsert_tracking_issue(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    findings: list[Finding],
    label: str = "retroactive-review-finding",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Find an existing tracking issue (labelled `retroactive-review-finding`,
    open state) and either update or create a new one.

    Always runs the search so we don't spam the issue tracker.
    """
    if not findings:
        return {"action": "noop", "reason": "no_findings"}

    title_body = render_issue_body(f"{owner}/{repo}", findings)
    title = title_body["title"]
    body = title_body["body"]

    if dry_run:
        return {"action": "dry_run", "title": title, "body_len": len(body)}

    # search existing
    q = f"label:{label} state:open repo:{owner}/{repo}"
    search = await client.get("/search/issues", params={"q": q, "per_page": "1"})
    items = (search.json() or {}).get("items", [])
    if items:
        existing = items[0]
        await client.patch(
            f"/repos/{owner}/{repo}/issues/{existing['number']}",
            json={"title": title, "body": body, "labels": [label]},
        )
        return {
            "action": "updated",
            "issue": existing["number"],
            "url": existing["html_url"],
        }

    payload = {
        "title": title,
        "body": body,
        "labels": [label],
    }
    created = await client.post(f"/repos/{owner}/{repo}/issues", json=payload)
    if created.status_code >= 300:
        return {
            "action": "create_failed",
            "status": created.status_code,
            "body": created.text,
        }
    j = created.json()
    return {"action": "created", "issue": j.get("number"), "url": j.get("html_url")}
