"""
StructureMiner — Detects project layout conventions:
  - Source root (src/, lib/, app/)
  - Test layout (tests/, __tests__/, *.test.ts co-location)
  - Feature vs layer organisation
  - Monorepo vs single-package
  - Key directories and their roles
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StructureResult:
    source_root: str | None             # e.g. 'src', 'lib', None (flat)
    test_layout: str | None             # 'colocated' | 'separate' | 'both'
    test_dirs: list[str]
    organisation: str | None            # 'feature' | 'layer' | 'flat'
    is_monorepo: bool
    monorepo_packages: list[str]        # e.g. ['packages/api', 'packages/web']
    key_dirs: dict[str, str]            # dir → role description
    depth_avg: float                    # average nesting depth
    notes: list[str] = field(default_factory=list)


_KNOWN_SOURCE_ROOTS = ["src", "lib", "app", "source", "core", "pkg"]
_KNOWN_TEST_DIRS = ["tests", "test", "__tests__", "spec", "specs", "e2e"]
_LAYER_DIRS = {"controllers", "services", "models", "views", "routes",
               "middleware", "utils", "helpers", "handlers", "repositories",
               "components", "pages", "hooks", "stores", "api"}
_FEATURE_SIGNAL = 4   # if a dir has ≥4 of the above names as siblings, it's layer-based


def _top_dirs(root: Path) -> list[str]:
    try:
        return [
            d.name for d in root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
    except PermissionError:
        return []


def _detect_source_root(top_dirs: list[str]) -> str | None:
    for name in _KNOWN_SOURCE_ROOTS:
        if name in top_dirs:
            return name
    return None


def _detect_test_layout(files: list[tuple[Path, str]]) -> tuple[str | None, list[str]]:
    colocated = 0
    separate_dirs: set[str] = set()

    for path, _ in files:
        parts = path.parts
        stem = path.stem
        # Co-located: *.test.ts / *.spec.js / test_*.py next to source
        if any(x in stem for x in (".test", ".spec", "test_")) or (
            stem.startswith("test_") or stem.endswith("_test")
        ):
            # Check if it's inside a dedicated test dir
            if any(p in _KNOWN_TEST_DIRS for p in parts):
                separate_dirs.add(next(p for p in parts if p in _KNOWN_TEST_DIRS))
            else:
                colocated += 1

    layout = None
    if colocated > 0 and separate_dirs:
        layout = "both"
    elif colocated > 0:
        layout = "colocated"
    elif separate_dirs:
        layout = "separate"

    return layout, sorted(separate_dirs)


def _detect_organisation(root: Path, source_root: str | None) -> str | None:
    """Heuristic: if the immediate children of src/ look like layers, it's layer-based."""
    check_root = root / source_root if source_root else root
    try:
        children = {d.name.lower() for d in check_root.iterdir() if d.is_dir()}
    except (PermissionError, OSError):
        return None
    layer_hits = children & _LAYER_DIRS
    if len(layer_hits) >= 3:
        return "layer"
    # Feature-based: children are domain names, each containing index/types
    feature_signals = 0
    for child in check_root.iterdir():
        if child.is_dir():
            sub = {d.name for d in child.iterdir() if d.is_dir() or d.is_file()}
            if any(n in sub for n in ("index.ts", "index.js", "types.ts", "types.py")):
                feature_signals += 1
    if feature_signals >= 3:
        return "feature"
    return "flat"


def _detect_monorepo(root: Path, top_dirs: list[str]) -> tuple[bool, list[str]]:
    monorepo_roots = ["packages", "apps", "services", "libs", "modules"]
    for mr in monorepo_roots:
        if mr in top_dirs:
            mr_path = root / mr
            try:
                pkgs = [
                    f"{mr}/{d.name}"
                    for d in mr_path.iterdir()
                    if d.is_dir() and (d / "package.json").exists()
                    or (d / "pyproject.toml").exists()
                    or (d / "go.mod").exists()
                ]
                if len(pkgs) >= 2:
                    return True, pkgs[:10]
            except (PermissionError, OSError):
                pass
    return False, []


def _avg_depth(files: list[tuple[Path, str]], root: Path) -> float:
    if not files:
        return 0.0
    return round(
        sum(len(p.relative_to(root).parts) for p, _ in files) / len(files), 1
    )


_DIR_ROLES = {
    "src": "source root",
    "lib": "source root / shared libraries",
    "app": "application source",
    "tests": "test suite",
    "test": "test suite",
    "__tests__": "co-located Jest test suite",
    "spec": "test suite (RSpec/Jasmine style)",
    "docs": "documentation",
    "scripts": "build / utility scripts",
    "config": "configuration files",
    "public": "static assets served publicly",
    "static": "static assets",
    "assets": "static assets",
    "migrations": "database migrations",
    "db": "database-related code",
    "api": "API layer",
    "models": "data models",
    "views": "view layer / templates",
    "controllers": "controller layer",
    "services": "service layer",
    "utils": "utility functions",
    "helpers": "helper functions",
    "hooks": "React / lifecycle hooks",
    "components": "UI components",
    "pages": "page components (Next.js / Nuxt style)",
    "types": "TypeScript type definitions",
    "interfaces": "TypeScript interfaces",
    "constants": "shared constants",
    "middleware": "middleware layer",
    "routes": "route definitions",
    "plugins": "plugin definitions",
    "store": "state management store",
    "stores": "state management stores",
    "i18n": "internationalisation strings",
    "locales": "locale/translation files",
}


class StructureMiner:
    def __init__(self, root: Path):
        self.root = root

    def mine(self, files: list[tuple[Path, str]]) -> StructureResult:
        top = _top_dirs(self.root)
        source_root = _detect_source_root(top)
        test_layout, test_dirs = _detect_test_layout(files)
        organisation = _detect_organisation(self.root, source_root)
        is_mono, mono_pkgs = _detect_monorepo(self.root, top)
        depth = _avg_depth(files, self.root)

        key_dirs = {
            name: role
            for name, role in _DIR_ROLES.items()
            if name in top
        }

        notes: list[str] = []
        if is_mono:
            notes.append(f"Monorepo with {len(mono_pkgs)} packages")

        return StructureResult(
            source_root=source_root,
            test_layout=test_layout,
            test_dirs=test_dirs,
            organisation=organisation,
            is_monorepo=is_mono,
            monorepo_packages=mono_pkgs,
            key_dirs=key_dirs,
            depth_avg=depth,
            notes=notes,
        )
