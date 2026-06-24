"""
patchwork MCP server — exposes convention mining as MCP tools.

Tools:
  patchwork_scan         Full scan → returns CONVENTIONS.md text
  patchwork_naming       Naming conventions for a specific language
  patchwork_structure    Project structure summary
  patchwork_errors       Error handling conventions
  patchwork_testing      Testing conventions
  patchwork_stack        Tech stack detection
  patchwork_git          Git workflow conventions
  patchwork_check        Check a symbol/path against detected conventions
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from patchwork.scanner import scan as do_scan, ScanOptions
from patchwork.output.report import ConventionReport

# Simple in-process cache keyed by root path + mtime of CONVENTIONS.md
_CACHE: dict[str, ConventionReport] = {}


def _get_or_scan(root: Path) -> ConventionReport:
    key = str(root)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    opts = ScanOptions(root=root, max_files=500)
    report = do_scan(opts)
    _CACHE[key] = report
    return report


def _invalidate(root: Path) -> None:
    _CACHE.pop(str(root), None)


async def run_server(root: Path, port: int = 3742, stdio: bool = True) -> None:
    if not MCP_AVAILABLE:
        raise RuntimeError(
            "MCP package not installed. Run: pip install 'patchwork-conventions[mcp]'"
        )

    server = Server("patchwork")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="patchwork_scan",
                description=(
                    "Scan the codebase and return full CONVENTIONS.md content. "
                    "Use this when you need a complete picture of project conventions."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Absolute path to scan (default: current project root)",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "json"],
                            "description": "Output format",
                            "default": "markdown",
                        },
                        "refresh": {
                            "type": "boolean",
                            "description": "Force re-scan even if cached",
                            "default": False,
                        },
                    },
                },
            ),
            types.Tool(
                name="patchwork_naming",
                description=(
                    "Return naming conventions for a specific language detected in this project. "
                    "Use before writing new functions, classes, or variables."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "language": {
                            "type": "string",
                            "description": "Language to query (python, typescript, go, etc.)",
                        },
                    },
                    "required": ["language"],
                },
            ),
            types.Tool(
                name="patchwork_structure",
                description=(
                    "Return project structure conventions: source root, test layout, "
                    "organisation style, key directories. Use before creating new files."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            ),
            types.Tool(
                name="patchwork_stack",
                description=(
                    "Return the detected tech stack: frameworks, package manager, "
                    "linters, formatters, build tools, scripts."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            ),
            types.Tool(
                name="patchwork_errors",
                description=(
                    "Return error-handling conventions for this project. "
                    "Use before writing new error handling code."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "language": {"type": "string"},
                    },
                },
            ),
            types.Tool(
                name="patchwork_testing",
                description=(
                    "Return testing conventions: framework, assertion style, "
                    "test layout, mocking approach."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "language": {"type": "string"},
                    },
                },
            ),
            types.Tool(
                name="patchwork_git",
                description=(
                    "Return git workflow conventions: commit message style, "
                    "branch naming, co-change file pairs."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            ),
            types.Tool(
                name="patchwork_check",
                description=(
                    "Check whether a proposed symbol name or file path follows "
                    "this project's conventions. Returns 'ok' or a specific violation."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "name": {
                            "type": "string",
                            "description": "Symbol or file name to check",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["function", "class", "variable", "constant", "file"],
                            "description": "What kind of name to check",
                        },
                        "language": {"type": "string"},
                    },
                    "required": ["name", "kind", "language"],
                },
            ),
        ]

    def _resolve_scan_root(raw_path: str | None) -> Path:
        """Resolve and validate the scan path.

        Rejects paths that escape the filesystem root or point to sensitive
        system directories, and normalises symlinks so containment checks
        are reliable.  The MCP server is a read-only tool, but we still
        validate inputs at the boundary to follow least-privilege.
        """
        candidate = Path(raw_path).resolve() if raw_path else root.resolve()

        # Reject obviously dangerous system paths
        _BLOCKED = {"/etc", "/proc", "/sys", "/dev", "/private/etc"}
        for blocked in _BLOCKED:
            if str(candidate).startswith(blocked):
                raise ValueError(f"Scanning system path '{candidate}' is not allowed.")

        # Must be an existing directory (not a file or a socket etc.)
        if not candidate.is_dir():
            raise ValueError(f"Path '{candidate}' is not a directory.")

        return candidate

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        try:
            scan_root = _resolve_scan_root(arguments.get("path"))
        except ValueError as exc:
            return [types.TextContent(type="text", text=f"Error: {exc}")]

        if arguments.get("refresh"):
            _invalidate(scan_root)

        report = _get_or_scan(scan_root)

        if name == "patchwork_scan":
            fmt = arguments.get("format", "markdown")
            text = report.to_json() if fmt == "json" else report.to_markdown()
            return [types.TextContent(type="text", text=text)]

        elif name == "patchwork_naming":
            lang = arguments.get("language", "")
            nr = (report.naming or {}).get(lang)
            if nr is None:
                return [types.TextContent(
                    type="text",
                    text=f"No naming data for '{lang}'. Available: {list(report.naming or {})}"
                )]
            lines = [f"Naming conventions for {lang}:"]
            if nr.functions:
                lines.append(
                    f"- functions: {nr.functions.style} "
                    f"({int(nr.functions.confidence * 100)}% consistent)"
                )
                if nr.functions.examples:
                    lines.append(f"  examples: {', '.join(nr.functions.examples[:4])}")
            if nr.classes:
                lines.append(f"- classes: {nr.classes.style}")
                if nr.classes.examples:
                    lines.append(f"  examples: {', '.join(nr.classes.examples[:4])}")
            if nr.variables:
                lines.append(f"- variables: {nr.variables.style}")
            if nr.constants and nr.constants.examples:
                lines.append(f"- constants: {nr.constants.style}")
            if nr.files:
                lines.append(f"- files: {nr.files.style}")
            if nr.private_prefix:
                lines.append(f"- private prefix: {nr.private_prefix}")
            for note in nr.notes:
                lines.append(f"note: {note}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "patchwork_structure":
            s = report.structure
            if s is None:
                return [types.TextContent(type="text", text="No structure data available.")]
            lines = ["Project structure:"]
            if s.source_root:
                lines.append(f"- source root: {s.source_root}/")
            if s.test_layout:
                lines.append(f"- test layout: {s.test_layout}")
                if s.test_dirs:
                    lines.append(f"  directories: {', '.join(s.test_dirs)}")
            if s.organisation:
                lines.append(f"- organisation: {s.organisation}")
            if s.is_monorepo:
                lines.append(f"- monorepo: {len(s.monorepo_packages)} packages")
                for pkg in s.monorepo_packages[:5]:
                    lines.append(f"  - {pkg}/")
            if s.key_dirs:
                lines.append("- key directories:")
                for d, role in s.key_dirs.items():
                    lines.append(f"  {d}/ = {role}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "patchwork_stack":
            cfg = report.config
            if cfg is None:
                return [types.TextContent(type="text", text="No config detected.")]
            lines = ["Tech stack:"]
            if cfg.name:
                lines.append(f"- project: {cfg.name}")
            if cfg.language:
                lines.append(f"- language: {cfg.language}")
            if cfg.runtime:
                lines.append(f"- runtime: {cfg.runtime}")
            if cfg.package_manager:
                lines.append(f"- package manager: {cfg.package_manager}")
            if cfg.frameworks:
                lines.append(f"- frameworks: {', '.join(cfg.frameworks)}")
            if cfg.linters:
                lines.append(f"- linters: {', '.join(cfg.linters)}")
            if cfg.formatters:
                lines.append(f"- formatters: {', '.join(cfg.formatters)}")
            if cfg.type_checker:
                lines.append(f"- type checker: {cfg.type_checker}")
            if cfg.scripts:
                lines.append("- scripts:")
                for k, v in cfg.scripts.items():
                    lines.append(f"  {k}: {v}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "patchwork_errors":
            lang = arguments.get("language")
            errors = report.errors or {}
            if lang:
                er = errors.get(lang)
                if er is None:
                    return [types.TextContent(
                        type="text",
                        text=f"No error data for '{lang}'. Available: {list(errors)}"
                    )]
                data = {lang: er}
            else:
                data = errors
            lines = []
            for lng, er in data.items():
                lines.append(f"{lng}: {er.primary_pattern}")
                if er.logging_framework:
                    lines.append(f"  logging: {er.logging_framework}")
                if er.propagation_style:
                    lines.append(f"  propagation: {er.propagation_style}")
                if er.custom_exceptions:
                    lines.append(f"  custom exceptions: {', '.join(er.custom_exceptions[:6])}")
                for note in er.notes:
                    lines.append(f"  note: {note}")
            return [types.TextContent(type="text", text="\n".join(lines) or "No error patterns found.")]

        elif name == "patchwork_testing":
            lang = arguments.get("language")
            testing = report.testing or {}
            if lang:
                tr = testing.get(lang)
                if tr is None:
                    return [types.TextContent(
                        type="text",
                        text=f"No testing data for '{lang}'."
                    )]
                data = {lang: tr}
            else:
                data = testing
            lines = []
            for lng, tr in data.items():
                lines.append(f"{lng}:")
                if tr.framework:
                    lines.append(f"  framework: {tr.framework}")
                lines.append(f"  test files: {tr.test_file_count}")
                if tr.organisation:
                    lines.append(f"  organisation: {tr.organisation}")
                if tr.assertion_style:
                    lines.append(f"  assertions: {tr.assertion_style}(...)")
                if tr.mock_library:
                    lines.append(f"  mocking: {tr.mock_library}")
            return [types.TextContent(type="text", text="\n".join(lines) or "No testing data.")]

        elif name == "patchwork_git":
            g = report.git
            if g is None:
                return [types.TextContent(type="text", text="No git data (not a git repo or no commits).")]
            lines = [f"Git conventions ({g.total_commits_sampled} commits sampled):"]
            if g.commit_style:
                lines.append(f"- commit style: {g.commit_style}")
            if g.commit_examples:
                lines.append("- examples:")
                for ex in g.commit_examples[:3]:
                    lines.append(f"  {ex}")
            if g.branch_style:
                lines.append(f"- branch naming: {g.branch_style}")
            if g.avg_files_per_commit:
                lines.append(f"- avg files/commit: {g.avg_files_per_commit}")
            if g.cochange_pairs:
                lines.append("- files that change together:")
                for a, b, count in g.cochange_pairs[:3]:
                    lines.append(f"  {a} <-> {b} ({count}x)")
            for note in g.notes:
                lines.append(f"note: {note}")
            return [types.TextContent(type="text", text="\n".join(lines))]

        elif name == "patchwork_check":
            sym = arguments.get("name")
            kind = arguments.get("kind")
            if not sym or not kind:
                return [types.TextContent(
                    type="text",
                    text="Error: 'name' and 'kind' are required arguments.",
                )]
            lang = arguments.get("language", "")
            return [types.TextContent(
                type="text",
                text=_check_convention(sym, kind, lang, report),
            )]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    # Transport
    if stdio:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    else:
        # SSE transport for HTTP mode
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        import uvicorn

        sse = SseServerTransport("/messages")

        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())

        app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages", app=sse.handle_post_message),
        ])
        uvicorn.run(app, host="127.0.0.1", port=port)


def _check_convention(name: str, kind: str, lang: str, report: ConventionReport) -> str:
    """Check if a name follows detected conventions. Returns descriptive verdict."""
    import re

    naming = (report.naming or {}).get(lang)
    if naming is None:
        return f"ok (no convention data for {lang})"

    convention_map = {
        "function": naming.functions,
        "class": naming.classes,
        "variable": naming.variables,
        "constant": naming.constants,
        "file": naming.files,
    }
    conv = convention_map.get(kind)
    if conv is None or conv.confidence < 0.6:
        return f"ok (no strong convention detected for {lang} {kind}s)"

    expected = conv.style
    actual = _classify_name(name)

    if actual == expected:
        return f"✓ ok — `{name}` follows `{expected}` convention for {lang} {kind}s"
    else:
        example = conv.examples[0] if conv.examples else "N/A"
        return (
            f"⚠ violation — `{name}` looks like `{actual}` "
            f"but this project uses `{expected}` for {lang} {kind}s. "
            f"Example: `{example}`"
        )


def _classify_name(name: str) -> str:
    import re
    if re.match(r'^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$', name):
        return "SCREAMING_SNAKE"
    if re.match(r'^[a-z][a-z0-9]*(_[a-z0-9]+)*$', name):
        return "snake_case"
    if re.match(r'^[a-z][a-zA-Z0-9]*$', name):
        return "camelCase"
    if re.match(r'^[A-Z][a-zA-Z0-9]*$', name):
        return "PascalCase"
    if re.match(r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$', name):
        return "kebab-case"
    return "mixed"
