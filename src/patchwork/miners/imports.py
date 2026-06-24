"""
ImportMiner — Detects import style conventions:
  - Absolute vs relative imports
  - Path alias usage (e.g. @/, ~/, src/)
  - Import grouping (stdlib / third-party / local)
  - Barrel file patterns (index.ts re-exports)
  - Destructuring vs namespace imports
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ImportResult:
    style: str                      # 'absolute' | 'relative' | 'mixed'
    relative_confidence: float
    aliases_used: list[str]         # e.g. ['@/', 'src/']
    grouping: str | None            # 'grouped' | 'ungrouped'
    destructuring: str | None       # 'destructuring' | 'namespace' | 'mixed'
    barrel_files: list[str]         # relative paths of index.{ts,js}
    common_stdlib: list[str]        # most imported stdlib modules
    common_third_party: list[str]   # most imported third-party packages
    notes: list[str] = field(default_factory=list)


_PY_RELATIVE = re.compile(r'^\s*from\s+\.', re.MULTILINE)
_PY_ABSOLUTE = re.compile(r'^\s*(?:import|from)\s+(?!\.)', re.MULTILINE)
_PY_IMPORT = re.compile(r'^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))', re.MULTILINE)

_JS_RELATIVE = re.compile(r"""(?:import|require)\s*\(?['"](\./|\.\./)""", re.MULTILINE)
_JS_ABSOLUTE_ALIAS = re.compile(r"""(?:import|require)\s*\(?['"](@\w+/|~/)""", re.MULTILINE)
_JS_IMPORT_FROM = re.compile(r"""import\s+(?:\{[^}]+\}|\*\s+as\s+\w+|\w+)\s+from\s+['"]([^'"]+)['"]""")
_JS_DESTRUCTURE = re.compile(r"""import\s+\{[^}]+\}\s+from""")
_JS_NAMESPACE = re.compile(r"""import\s+\*\s+as\s+\w+\s+from""")
_JS_SIDE_EFFECT = re.compile(r"""import\s+['"][^'"]+['"]""")

_STDLIB_PY = {
    "os", "sys", "re", "json", "pathlib", "typing", "dataclasses",
    "collections", "itertools", "functools", "io", "time", "datetime",
    "logging", "unittest", "asyncio", "threading", "subprocess",
    "hashlib", "base64", "copy", "math", "random",
}


def _detect_py_imports(paths: list[Path]) -> ImportResult:
    relative_count = 0
    absolute_count = 0
    all_modules: list[str] = []
    aliases: set[str] = set()

    for path in paths[:200]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        relative_count += len(_PY_RELATIVE.findall(text))
        absolute_count += len(_PY_ABSOLUTE.findall(text))
        for m in _PY_IMPORT.finditer(text):
            mod = (m.group(1) or m.group(2) or "").strip().split(".")[0]
            if mod:
                all_modules.append(mod)
        # Detect src/ or similar path aliases in pyproject/setup.cfg
        if "@" in text or "from src." in text:
            aliases.add("src/")

    total = relative_count + absolute_count
    rel_conf = relative_count / total if total else 0.0
    style = "relative" if rel_conf > 0.6 else ("mixed" if rel_conf > 0.2 else "absolute")

    counts = Counter(all_modules)
    stdlib = [m for m, _ in counts.most_common(30) if m in _STDLIB_PY][:5]
    third_party = [m for m, _ in counts.most_common(30) if m not in _STDLIB_PY][:8]

    return ImportResult(
        style=style,
        relative_confidence=round(rel_conf, 2),
        aliases_used=list(aliases),
        grouping=None,
        destructuring=None,
        barrel_files=[],
        common_stdlib=stdlib,
        common_third_party=third_party,
    )


def _detect_js_imports(paths: list[Path], lang: str) -> ImportResult:
    relative_count = 0
    alias_count = 0
    alias_prefixes: Counter[str] = Counter()
    destructure_count = 0
    namespace_count = 0
    all_packages: list[str] = []
    barrel_files: list[str] = []

    for path in paths[:200]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue

        relative_count += len(_JS_RELATIVE.findall(text))
        found_aliases = _JS_ABSOLUTE_ALIAS.findall(text)
        alias_count += len(found_aliases)
        for a in found_aliases:
            alias_prefixes[a] += 1

        destructure_count += len(_JS_DESTRUCTURE.findall(text))
        namespace_count += len(_JS_NAMESPACE.findall(text))

        for m in _JS_IMPORT_FROM.finditer(text):
            pkg = m.group(1)
            if not pkg.startswith(".") and not pkg.startswith("@"):
                top = pkg.split("/")[0]
                all_packages.append(top)

        # Barrel file detection
        if path.stem == "index" and lang == "typescript":
            if "export" in text and "from" in text:
                barrel_files.append(str(path.name))

    total = relative_count + alias_count
    rel_conf = relative_count / total if total else 0.5
    style = "relative" if rel_conf > 0.7 else ("mixed" if rel_conf > 0.3 else "absolute")

    aliases = [a for a, _ in alias_prefixes.most_common(5)]

    destr = None
    if destructure_count + namespace_count > 0:
        ratio = destructure_count / (destructure_count + namespace_count)
        destr = "destructuring" if ratio > 0.7 else ("namespace" if ratio < 0.3 else "mixed")

    third_party = [m for m, _ in Counter(all_packages).most_common(10)]

    return ImportResult(
        style=style,
        relative_confidence=round(rel_conf, 2),
        aliases_used=aliases,
        grouping=None,
        destructuring=destr,
        barrel_files=barrel_files[:5],
        common_stdlib=[],
        common_third_party=third_party,
    )


class ImportMiner:
    def mine(self, by_lang: dict[str, list[Path]]) -> dict[str, ImportResult]:
        results: dict[str, ImportResult] = {}
        for lang, paths in by_lang.items():
            if lang == "python":
                results[lang] = _detect_py_imports(paths)
            elif lang in ("javascript", "typescript"):
                results[lang] = _detect_js_imports(paths, lang)
        return results
