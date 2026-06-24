"""
Tree-sitter parser cache + helpers shared across all miners.
Lazy-loads language parsers on first use.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterator

try:
    from tree_sitter import Language, Parser, Node
    TS_AVAILABLE = True
except ImportError:
    TS_AVAILABLE = False

# Registry of (language_tag → loader function)
_LANGUAGE_LOADERS: dict[str, str] = {
    "python": "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "go": "tree_sitter_go",
    "rust": "tree_sitter_rust",
    "java": "tree_sitter_java",
}


@lru_cache(maxsize=None)
def get_parser(lang: str) -> "Parser | None":
    """Return a cached tree-sitter Parser for the given language, or None."""
    if not TS_AVAILABLE:
        return None
    module_name = _LANGUAGE_LOADERS.get(lang)
    if module_name is None:
        return None
    try:
        mod = __import__(module_name)
        language = Language(mod.language())
        parser = Parser(language)
        return parser
    except Exception:
        return None


def parse_file(path: Path, lang: str) -> "Node | None":
    """Parse a source file and return the root AST node, or None on failure."""
    parser = get_parser(lang)
    if parser is None:
        return None
    try:
        source = path.read_bytes()
        tree = parser.parse(source)
        return tree.root_node
    except Exception:
        return None


def parse_bytes(source: bytes, lang: str) -> "Node | None":
    """Parse raw bytes and return the root AST node."""
    parser = get_parser(lang)
    if parser is None:
        return None
    try:
        tree = parser.parse(source)
        return tree.root_node
    except Exception:
        return None


def walk(node: "Node") -> Iterator["Node"]:
    """DFS walk over all nodes in the AST."""
    cursor = node.walk()
    reached_root = False
    while not reached_root:
        yield cursor.node
        if cursor.goto_first_child():
            continue
        if cursor.goto_next_sibling():
            continue
        retracing = True
        while retracing:
            if not cursor.goto_parent():
                reached_root = True
                retracing = False
            elif cursor.goto_next_sibling():
                retracing = False


def nodes_of_type(root: "Node", *types: str) -> list["Node"]:
    """Collect all descendant nodes matching any of the given type names."""
    return [n for n in walk(root) if n.type in types]


def node_text(node: "Node", source: bytes) -> str:
    """Extract the UTF-8 text of a node from the raw source bytes."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


# ── Regex-based fallbacks (used when tree-sitter is unavailable or for langs
#    without a grammar installed) ──────────────────────────────────────────────

def regex_extract_identifiers(source: str, lang: str) -> list[str]:
    """Very rough identifier extraction via regex (fallback path)."""
    if lang == "python":
        pattern = r"(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"
    elif lang in ("javascript", "typescript"):
        pattern = r"(?:function|class|const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    elif lang == "go":
        pattern = r"(?:func|type|var|const)\s+([A-Z][A-Za-z0-9]*|[a-z][A-Za-z0-9]*)"
    else:
        pattern = r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b"
    return re.findall(pattern, source)
