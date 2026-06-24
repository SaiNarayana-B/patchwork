"""
Core scanner: discovers files, dispatches language miners, aggregates results.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import pathspec

from patchwork.miners.naming import NamingMiner
from patchwork.miners.imports import ImportMiner
from patchwork.miners.structure import StructureMiner
from patchwork.miners.error_handling import ErrorHandlingMiner
from patchwork.miners.testing import TestingMiner
from patchwork.miners.api_patterns import APIPatternMiner
from patchwork.miners.git_patterns import GitPatternMiner
from patchwork.miners.config_detector import ConfigDetector
from patchwork.output.report import ConventionReport  # noqa: E402 — keep at top

# File extensions → language tags
LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
}

DEFAULT_IGNORE_PATTERNS = [
    "node_modules/", ".git/", "__pycache__/", ".venv/", "venv/",
    "dist/", "build/", ".next/", ".nuxt/", "target/",
    "*.min.js", "*.min.css", "*.bundle.js",
    "*.lock", "package-lock.json", "yarn.lock",
    ".mypy_cache/", ".pytest_cache/", ".ruff_cache/",
    "*.egg-info/", "site-packages/",
    "vendor/", "third_party/",
    "*.pb.go", "*.generated.*", "*_gen.*",
]


@dataclass
class ScanOptions:
    root: Path
    max_files: int = 500
    max_file_size_kb: int = 500
    include_git: bool = True
    languages: list[str] = field(default_factory=list)  # empty = all
    extra_ignore: list[str] = field(default_factory=list)
    verbose: bool = False


def _build_ignore_spec(root: Path, extra: list[str]) -> pathspec.PathSpec:
    patterns = list(DEFAULT_IGNORE_PATTERNS) + extra
    gitignore = root / ".gitignore"
    if gitignore.exists():
        with open(gitignore) as f:
            patterns.extend(f.read().splitlines())
    return pathspec.PathSpec.from_lines("gitignore", patterns)


def _iter_source_files(
    root: Path,
    spec: pathspec.PathSpec,
    languages: list[str],
    max_files: int,
    max_file_size_kb: int,
) -> Iterator[tuple[Path, str]]:
    """Yield (path, language) for every scannable source file."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        # Prune ignored directories in-place
        dirnames[:] = [
            d for d in dirnames
            if not spec.match_file(str(rel_dir / d) + "/")
        ]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            rel = fpath.relative_to(root)
            if spec.match_file(str(rel)):
                continue
            lang = LANGUAGE_MAP.get(fpath.suffix.lower())
            if lang is None:
                continue
            if languages and lang not in languages:
                continue
            if fpath.stat().st_size > max_file_size_kb * 1024:
                continue
            yield fpath, lang
            count += 1
            if count >= max_files:
                return


def scan(opts: ScanOptions) -> ConventionReport:
    """
    Full pipeline: discover → mine → aggregate → return ConventionReport.
    """
    t0 = time.perf_counter()
    root = opts.root.resolve()

    # Detect project config/stack first (no AST needed)
    config = ConfigDetector(root).detect()

    # Discover all source files
    spec = _build_ignore_spec(root, opts.extra_ignore)
    files: list[tuple[Path, str]] = list(
        _iter_source_files(root, spec, opts.languages, opts.max_files, opts.max_file_size_kb)
    )

    if not files:
        return ConventionReport(root=root, config=config, elapsed=time.perf_counter() - t0)

    # Group by language for efficient miner dispatch
    by_lang: dict[str, list[Path]] = {}
    for fpath, lang in files:
        by_lang.setdefault(lang, []).append(fpath)

    # Run all miners
    naming = NamingMiner().mine(by_lang)
    imports = ImportMiner().mine(by_lang)
    structure = StructureMiner(root).mine(files)
    errors = ErrorHandlingMiner().mine(by_lang)
    testing = TestingMiner(root).mine(by_lang)
    api = APIPatternMiner().mine(by_lang)
    git = GitPatternMiner(root).mine() if opts.include_git else None

    elapsed = time.perf_counter() - t0

    return ConventionReport(
        root=root,
        config=config,
        file_count=len(files),
        by_lang={lang: len(paths) for lang, paths in by_lang.items()},
        naming=naming,
        imports=imports,
        structure=structure,
        errors=errors,
        testing=testing,
        api=api,
        git=git,
        elapsed=elapsed,
    )
