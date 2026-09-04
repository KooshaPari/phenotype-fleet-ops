#!/usr/bin/env python3
"""
review-surface-boot — Propagate the fleet review-surface automation to a target repo.

Installs the proactive review-fanout workflow, retroactive-sweep workflow,
the review_finding issue template, and the fleet-ops root lefthook.yml into a
target repository so the agent fleet can pick up ignored code-review findings
automatically and stay rate-limit-aware across providers.

Usage:
  review-surface-boot.py <target-repo-dir> [--dry-run] [--skip-lefthook] [--skip-templates] [--skip-workflows]

Exit codes:
  0  success
  1  generic failure
  2  invalid target (no .git, not in a working tree)
  3  partial install (some files existed and were skipped)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = (
    Path(__file__).resolve().parents[2]
)  # tools/review-surface-boot/review-surface-boot.py -> tools -> phenotype-fleet-ops
SOURCE_WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SOURCE_TEMPLATES = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
SOURCE_LEFTHOOK = REPO_ROOT / "lefthook.yml"

# Files we ship
WORKFLOW_FILES = ("review-fanout.yml", "retroactive-sweep.yml")
TEMPLATE_FILES = ("review_finding.md",)
LEFTHOOK_FILE = "lefthook.yml"


def info(msg: str) -> None:
    print(f"  [boot] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"  [boot][warn] {msg}", file=sys.stderr, flush=True)


def err(msg: str) -> None:
    print(f"  [boot][error] {msg}", file=sys.stderr, flush=True)


def is_git_repo(target: Path) -> bool:
    return (target / ".git").exists() or (target / ".git").is_file()


def copy_file(src: Path, dest: Path, dry_run: bool) -> bool:
    if dest.exists():
        warn(f"skip (exists): {dest.relative_to(dest.parents[2])}")
        return False
    info(f"install: {dest.name} -> {dest.relative_to(dest.parents[2])}")
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    return True


def install_workflows(target: Path, dry_run: bool) -> int:
    dest = target / ".github" / "workflows"
    partial = 0
    for fname in WORKFLOW_FILES:
        src = SOURCE_WORKFLOWS / fname
        if not src.exists():
            err(f"missing source: {src}")
            return 1
        if not copy_file(src, dest / fname, dry_run):
            partial += 1
    return partial


def install_templates(target: Path, dry_run: bool) -> int:
    dest = target / ".github" / "ISSUE_TEMPLATE"
    partial = 0
    for fname in TEMPLATE_FILES:
        src = SOURCE_TEMPLATES / fname
        if not src.exists():
            err(f"missing source: {src}")
            return 1
        if not copy_file(src, dest / fname, dry_run):
            partial += 1
    return partial


def install_lefthook(target: Path, dry_run: bool) -> int:
    if not SOURCE_LEFTHOOK.exists():
        err(f"missing source: {SOURCE_LEFTHOOK}")
        return 1
    return 0 if copy_file(SOURCE_LEFTHOOK, target / LEFTHOOK_FILE, dry_run) else 1


def git_status_clean(target: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(target), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
        return r.stdout.strip() == ""
    except subprocess.CalledProcessError as e:
        err(f"git status failed: {e.stderr}")
        return False


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("target", help="Path to target repository (must contain .git)")
    p.add_argument(
        "--dry-run", action="store_true", help="Report what would change, don't write"
    )
    p.add_argument("--skip-lefthook", action="store_true")
    p.add_argument("--skip-templates", action="store_true")
    p.add_argument("--skip-workflows", action="store_true")
    p.add_argument("--commit", action="store_true", help="git add + commit the changes")
    args = p.parse_args()

    target = Path(args.target).resolve()
    if not target.exists():
        err(f"target does not exist: {target}")
        return 2
    if not is_git_repo(target):
        err(f"target is not a git repository: {target}")
        return 2

    if not git_status_clean(target):
        warn(
            "target has uncommitted changes; proceeding but review the diff before commit"
        )

    print(f"review-surface-boot -> {target}")
    partial_total = 0

    if not args.skip_workflows:
        partial_total += install_workflows(target, args.dry_run)
    if not args.skip_templates:
        partial_total += install_templates(target, args.dry_run)
    if not args.skip_lefthook:
        partial_total += install_lefthook(target, args.dry_run)

    if args.commit and not args.dry_run:
        try:
            subprocess.run(
                ["git", "-C", str(target), "add"]
                + [
                    str(target / ".github" / "workflows" / f)
                    for f in WORKFLOW_FILES
                    if not args.skip_workflows
                ]
                + [
                    str(target / ".github" / "ISSUE_TEMPLATE" / f)
                    for f in TEMPLATE_FILES
                    if not args.skip_templates
                ]
                + ([str(target / LEFTHOOK_FILE)] if not args.skip_lefthook else []),
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(target),
                    "commit",
                    "-m",
                    "chore(ci): install review-surface fanout, sweep, and lefthook\n\n"
                    "Adds proactive CodeRabbit/Copilot/Cursor/Forge review fanout on PR\n"
                    "open/synchronize, weekly retroactive sweep over closed PRs, and a\n"
                    "fleet-ops root lefthook for local pre-commit + dispatch.\n\n"
                    "Sourced from phenotype-fleet-ops/tools/review-surface-boot.",
                ],
                check=True,
            )
            info("commit created")
        except subprocess.CalledProcessError as e:
            err(f"git commit failed: {e.stderr}")
            return 1

    if partial_total:
        info(f"install complete with {partial_total} file(s) skipped (already existed)")
        return 3
    info("install complete (all files fresh)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
