"""
NamingMiner — Detects naming conventions across functions, classes, variables,
constants, files. Uses tree-sitter AST when available; falls back to regex.

Detects:
  - camelCase / PascalCase / snake_case / SCREAMING_SNAKE / kebab-case
  - Consistency score per category
  - Language-specific override rules (e.g. Go unexported = lowercase)
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from patchwork.miners.ast_base import parse_file, walk, node_text


@dataclass
class NamingConvention:
    style: str           # 'snake_case' | 'camelCase' | 'PascalCase' | 'SCREAMING_SNAKE' | 'mixed'
    confidence: float    # 0.0 – 1.0
    examples: list[str] = field(default_factory=list)
    counter_examples: list[str] = field(default_factory=list)


@dataclass
class NamingResult:
    functions: NamingConvention | None = None
    classes: NamingConvention | None = None
    variables: NamingConvention | None = None
    constants: NamingConvention | None = None
    files: NamingConvention | None = None
    private_prefix: str | None = None   # e.g. '_' or '__'
    test_prefix: str | None = None      # e.g. 'test_' or 'Test'
    notes: list[str] = field(default_factory=list)


# ── Naming style detection ────────────────────────────────────────────────────

_RE_SNAKE = re.compile(r'^[a-z][a-z0-9]*(_[a-z0-9]+)*$')
_RE_CAMEL = re.compile(r'^[a-z][a-zA-Z0-9]*$')
_RE_PASCAL = re.compile(r'^[A-Z][a-zA-Z0-9]*$')
_RE_SCREAMING = re.compile(r'^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$')
_RE_KEBAB = re.compile(r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$')
_RE_PRIVATE_SINGLE = re.compile(r'^_[a-z]')
_RE_PRIVATE_DOUBLE = re.compile(r'^__[a-z]')


def _classify(name: str) -> str:
    if _RE_SCREAMING.match(name):
        return "SCREAMING_SNAKE"
    if _RE_SNAKE.match(name):
        return "snake_case"
    if _RE_CAMEL.match(name):
        return "camelCase"
    if _RE_PASCAL.match(name):
        return "PascalCase"
    if _RE_KEBAB.match(name):
        return "kebab-case"
    return "mixed"


def _majority_convention(names: list[str]) -> NamingConvention:
    if not names:
        return NamingConvention(style="mixed", confidence=0.0)
    counts: Counter[str] = Counter(_classify(n) for n in names)
    top_style, top_count = counts.most_common(1)[0]
    confidence = top_count / len(names)
    examples = [n for n in names if _classify(n) == top_style][:5]
    counter_examples = [n for n in names if _classify(n) != top_style][:3]
    return NamingConvention(
        style=top_style,
        confidence=round(confidence, 2),
        examples=examples,
        counter_examples=counter_examples,
    )


# ── Language-specific AST extraction ─────────────────────────────────────────

_FUNCTION_TYPES = {
    "python": ("function_definition", "async_function_definition"),
    "javascript": ("function_declaration", "arrow_function", "method_definition"),
    "typescript": ("function_declaration", "arrow_function", "method_definition"),
    "go": ("function_declaration", "method_declaration"),
    "rust": ("function_item",),
    "java": ("method_declaration", "constructor_declaration"),
}

_CLASS_TYPES = {
    "python": ("class_definition",),
    "javascript": ("class_declaration",),
    "typescript": ("class_declaration",),
    "go": ("type_declaration",),
    "rust": ("struct_item", "enum_item", "trait_item"),
    "java": ("class_declaration", "interface_declaration", "enum_declaration"),
}

_VAR_TYPES = {
    "python": ("assignment",),
    "javascript": ("variable_declarator",),
    "typescript": ("variable_declarator",),
    "go": ("short_var_decl", "var_spec"),
    "rust": ("let_declaration",),
    "java": ("variable_declarator",),
}


def _extract_function_names(root, source: bytes, lang: str) -> list[str]:
    types = _FUNCTION_TYPES.get(lang, ())
    names = []
    for node in walk(root):
        if node.type in types:
            name_node = node.child_by_field_name("name")
            if name_node:
                names.append(node_text(name_node, source))
    return names


def _extract_class_names(root, source: bytes, lang: str) -> list[str]:
    types = _CLASS_TYPES.get(lang, ())
    names = []
    for node in walk(root):
        if node.type in types:
            name_node = node.child_by_field_name("name")
            if name_node:
                names.append(node_text(name_node, source))
    return names


def _extract_var_names(root, source: bytes, lang: str) -> list[str]:
    types = _VAR_TYPES.get(lang, ())
    names = []
    for node in walk(root):
        if node.type in types:
            name_node = node.child_by_field_name("name")
            if name_node:
                t = node_text(name_node, source)
                if len(t) > 1 and not t.startswith("_"):
                    names.append(t)
    return names[:200]  # cap to avoid noise


# ── Regex fallback ────────────────────────────────────────────────────────────

_FALLBACK_PATTERNS = {
    "python": {
        "functions": re.compile(r'^def\s+([A-Za-z_][A-Za-z0-9_]*)', re.MULTILINE),
        "classes": re.compile(r'^class\s+([A-Za-z_][A-Za-z0-9_]*)', re.MULTILINE),
        "constants": re.compile(r'^([A-Z][A-Z0-9_]{2,})\s*=', re.MULTILINE),
    },
    "javascript": {
        "functions": re.compile(r'(?:function\s+|const\s+|let\s+|var\s+)([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:=\s*(?:async\s+)?(?:function|\()|{|\()'),
        "classes": re.compile(r'class\s+([A-Za-z_$][A-Za-z0-9_$]*)'),
        "constants": re.compile(r'const\s+([A-Z_]{2,}[A-Z0-9_]*)\s*='),
    },
    "typescript": {
        "functions": re.compile(r'(?:function\s+|const\s+|let\s+|async\s+function\s+)([A-Za-z_$][A-Za-z0-9_$]*)'),
        "classes": re.compile(r'class\s+([A-Za-z_$][A-Za-z0-9_$]*)'),
        "constants": re.compile(r'const\s+([A-Z_]{2,}[A-Z0-9_]*)\s*='),
    },
    "go": {
        "functions": re.compile(r'^func\s+(?:\([^)]+\)\s+)?([A-Za-z][A-Za-z0-9]*)', re.MULTILINE),
        "classes": re.compile(r'^type\s+([A-Za-z][A-Za-z0-9]*)\s+struct', re.MULTILINE),
        "constants": re.compile(r'const\s+([A-Z][A-Za-z0-9]*)\s', re.MULTILINE),
    },
}


def _regex_mine(paths: list[Path], lang: str) -> dict[str, list[str]]:
    patterns = _FALLBACK_PATTERNS.get(lang, {})
    result: dict[str, list[str]] = defaultdict(list)
    for path in paths[:100]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for category, pat in patterns.items():
            result[category].extend(pat.findall(text))
    return result


# ── Public miner ─────────────────────────────────────────────────────────────

class NamingMiner:
    def mine(self, by_lang: dict[str, list[Path]]) -> dict[str, NamingResult]:
        """Return a NamingResult per language."""
        results: dict[str, NamingResult] = {}

        for lang, paths in by_lang.items():
            all_funcs: list[str] = []
            all_classes: list[str] = []
            all_vars: list[str] = []
            all_consts: list[str] = []

            for path in paths[:150]:  # cap for performance
                try:
                    source = path.read_bytes()
                except OSError:
                    continue

                from patchwork.miners.ast_base import parse_bytes
                root = parse_bytes(source, lang)

                if root is not None:
                    src_str = source.decode("utf-8", errors="replace")
                    all_funcs.extend(_extract_function_names(root, source, lang))
                    all_classes.extend(_extract_class_names(root, source, lang))
                    all_vars.extend(_extract_var_names(root, source, lang))
                    # Detect constants: names that are SCREAMING_SNAKE in assignments
                    all_consts.extend(
                        n for n in all_vars if _RE_SCREAMING.match(n)
                    )
                else:
                    # Regex fallback
                    mined = _regex_mine([path], lang)
                    all_funcs.extend(mined.get("functions", []))
                    all_classes.extend(mined.get("classes", []))
                    all_consts.extend(mined.get("constants", []))

            # Deduplicate and build result
            all_funcs = list(dict.fromkeys(all_funcs))
            all_classes = list(dict.fromkeys(all_classes))
            all_vars = list(dict.fromkeys(all_vars))
            all_consts = list(dict.fromkeys(all_consts))

            # Detect private naming convention
            private_prefix = None
            if lang == "python":
                dunder = [f for f in all_funcs if f.startswith("__") and not f.endswith("__")]
                single = [f for f in all_funcs if f.startswith("_") and not f.startswith("__")]
                if dunder:
                    private_prefix = "__"
                elif single:
                    private_prefix = "_"

            # Detect test prefix
            test_prefix = None
            test_funcs = [f for f in all_funcs if f.startswith("test_")]
            Test_funcs = [f for f in all_funcs if f.startswith("Test")]
            if test_funcs:
                test_prefix = "test_"
            elif Test_funcs:
                test_prefix = "Test"

            notes = []
            if lang == "go":
                exported = [f for f in all_funcs if f and f[0].isupper()]
                unexported = [f for f in all_funcs if f and f[0].islower()]
                if exported and unexported:
                    notes.append(
                        "Go convention: PascalCase for exported identifiers, camelCase for unexported"
                    )

            results[lang] = NamingResult(
                functions=_majority_convention(
                    [f for f in all_funcs if not f.startswith("_")]
                ) if all_funcs else None,
                classes=_majority_convention(all_classes) if all_classes else None,
                variables=_majority_convention(
                    [v for v in all_vars if not _RE_SCREAMING.match(v)]
                ) if all_vars else None,
                constants=_majority_convention(all_consts) if all_consts else None,
                files=_file_naming(paths),
                private_prefix=private_prefix,
                test_prefix=test_prefix,
                notes=notes,
            )

        return results


def _file_naming(paths: list[Path]) -> NamingConvention:
    stems = [p.stem for p in paths if p.stem and p.stem not in ("__init__", "index", "main")]
    return _majority_convention(stems)
