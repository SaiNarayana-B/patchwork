"""
GitPatternMiner — Mines git history for workflow conventions:
  - Commit message style (conventional commits / semantic / free-form)
  - Branch naming convention
  - PR/merge frequency
  - Average commit size (files changed)
  - Co-change pairs (files that always change together)
"""
from __future__ import annotations

import re
import subprocess  # nosec B404 — used only for 'git' with a hardcoded arg list, no shell=True
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GitResult:
    commit_style: str | None        # 'conventional' | 'semantic' | 'free-form'
    commit_examples: list[str]
    branch_style: str | None        # 'feature/name' | 'feat/name' | 'JIRA-123' | 'free-form'
    avg_files_per_commit: float
    total_commits_sampled: int
    cochange_pairs: list[tuple[str, str, int]]  # (fileA, fileB, count)
    notes: list[str] = field(default_factory=list)


_CONVENTIONAL_RE = re.compile(
    r'^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\(.+\))?!?:\s+.+'
)
_SEMANTIC_RE = re.compile(
    r'^(add|update|remove|fix|change|bump|improve|rename|move|delete|merge)\s+',
    re.IGNORECASE
)
_BRANCH_FEATURE = re.compile(r'^(feature|feat)/[\w/-]+$')
_BRANCH_JIRA = re.compile(r'^[A-Z]+-\d+')
_BRANCH_FIX = re.compile(r'^(fix|hotfix|bugfix)/[\w/-]+$')


def _run_git(args: list[str], cwd: Path, max_bytes: int = 500_000) -> str:
    try:
        result = subprocess.run(  # nosec B603 — list args, no shell=True, cwd is a validated Path
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            timeout=10,
        )
        return result.stdout[:max_bytes].decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _is_git_repo(root: Path) -> bool:
    return (root / ".git").exists()


class GitPatternMiner:
    def __init__(self, root: Path):
        self.root = root

    def mine(self) -> GitResult | None:
        if not _is_git_repo(self.root):
            return None

        # Sample last 200 commit messages
        log_out = _run_git(
            ["log", "--oneline", "-200", "--pretty=format:%s"],
            self.root,
        )
        messages = [m.strip() for m in log_out.splitlines() if m.strip()]

        if not messages:
            return None

        # Commit style detection
        conventional = sum(1 for m in messages if _CONVENTIONAL_RE.match(m))
        semantic = sum(1 for m in messages if _SEMANTIC_RE.match(m))
        total = len(messages)

        if conventional / total > 0.5:
            style = "conventional commits"
        elif semantic / total > 0.4:
            style = "semantic prefixes"
        else:
            style = "free-form"

        examples = messages[:5]

        # Branch names
        branches_out = _run_git(
            ["branch", "-a", "--format=%(refname:short)"],
            self.root,
        )
        branches = [b.strip() for b in branches_out.splitlines() if b.strip()]
        branch_style = _detect_branch_style(branches)

        # Average files per commit
        diff_stat = _run_git(
            ["log", "--oneline", "-100", "--stat"],
            self.root,
        )
        file_counts = re.findall(r'(\d+) files? changed', diff_stat)
        avg_files = (
            round(sum(int(x) for x in file_counts) / len(file_counts), 1)
            if file_counts else 0.0
        )

        # Co-change analysis (files that often change together)
        co_change = _detect_cochange(self.root)

        notes: list[str] = []
        if style == "conventional commits":
            notes.append(
                "Uses Conventional Commits — always prefix messages with type(scope): description"
            )

        return GitResult(
            commit_style=style,
            commit_examples=examples,
            branch_style=branch_style,
            avg_files_per_commit=avg_files,
            total_commits_sampled=total,
            cochange_pairs=co_change[:5],
            notes=notes,
        )


def _detect_branch_style(branches: list[str]) -> str | None:
    if not branches:
        return None
    feature = sum(1 for b in branches if _BRANCH_FEATURE.match(b))
    fix = sum(1 for b in branches if _BRANCH_FIX.match(b))
    jira = sum(1 for b in branches if _BRANCH_JIRA.match(b))
    total = len(branches)
    if (feature + fix) / total > 0.4:
        return "feature/name + fix/name"
    if jira / total > 0.4:
        return "JIRA-123 ticket keys"
    return "free-form"


def _detect_cochange(root: Path) -> list[tuple[str, str, int]]:
    """Find file pairs that change together frequently."""
    log_out = _run_git(
        ["log", "--name-only", "--pretty=format:--COMMIT--", "-100"],
        root,
        max_bytes=200_000,
    )
    commits: list[list[str]] = []
    current: list[str] = []
    for line in log_out.splitlines():
        if line == "--COMMIT--":
            if current:
                commits.append(current)
            current = []
        elif line.strip() and not line.startswith("diff"):
            current.append(line.strip())
    if current:
        commits.append(current)

    pair_counts: Counter[tuple[str, str]] = Counter()
    for commit_files in commits:
        files = sorted(set(commit_files))
        for i, f1 in enumerate(files):
            for f2 in files[i + 1:]:
                pair_counts[(f1, f2)] += 1

    return [(a, b, count) for (a, b), count in pair_counts.most_common(10) if count >= 2]
