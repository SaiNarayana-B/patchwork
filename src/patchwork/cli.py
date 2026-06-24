"""
patchwork CLI — entry point for all commands.

Commands:
  scan     Scan a codebase and generate CONVENTIONS.md
  update   Re-scan, preserving any manual edits (merge mode)
  diff     Show what changed since last scan (exit 1 if changed)
  watch    Auto-regenerate on file changes
  show     Print detected conventions to terminal (no file write)
  serve    Start MCP server
"""
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax

console = Console()


@click.group()
@click.version_option(package_name="patchwork-conventions")
def main() -> None:
    """patchwork — mine your codebase, generate CONVENTIONS.md."""


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--output", "-o", default=None, help="Output file (default: CONVENTIONS.md in PATH)")
@click.option("--agents-md", is_flag=True, help="Write AGENTS.md instead of CONVENTIONS.md")
@click.option("--claude-md", is_flag=True, help="Append to CLAUDE.md instead")
@click.option("--json", "as_json", is_flag=True, help="Output JSON instead of Markdown")
@click.option("--no-git", is_flag=True, help="Skip git history analysis")
@click.option("--max-files", default=500, show_default=True, help="Max files to scan")
@click.option("--lang", multiple=True, help="Only scan these languages (e.g. --lang python)")
@click.option("--quiet", "-q", is_flag=True, help="Suppress all output except errors")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress")
@click.option("--stdout", is_flag=True, help="Print to stdout instead of writing file")
def scan(
    path: str,
    output: str | None,
    agents_md: bool,
    claude_md: bool,
    as_json: bool,
    no_git: bool,
    max_files: int,
    lang: tuple[str, ...],
    quiet: bool,
    verbose: bool,
    stdout: bool,
) -> None:
    """Scan a codebase and generate CONVENTIONS.md."""
    from patchwork.scanner import scan as do_scan, ScanOptions

    root = Path(path).resolve()

    if not quiet:
        console.print(
            Panel.fit(
                f"[bold cyan]patchwork[/bold cyan] scanning [green]{root}[/green]",
                border_style="cyan",
            )
        )

    opts = ScanOptions(
        root=root,
        max_files=max_files,
        include_git=not no_git,
        languages=list(lang),
        verbose=verbose,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        disable=quiet,
    ) as progress:
        progress.add_task("Mining conventions...", total=None)
        report = do_scan(opts)

    if as_json:
        text = report.to_json()
    else:
        text = report.to_markdown(agents_md=agents_md)

    if stdout:
        click.echo(text)
        return

    # Determine output path
    if output:
        out_path = Path(output)
    elif claude_md:
        out_path = root / "CLAUDE.md"
    elif agents_md:
        out_path = root / "AGENTS.md"
    elif as_json:
        out_path = root / ".patchwork" / "conventions.json"
    else:
        out_path = root / "CONVENTIONS.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if claude_md and out_path.exists():
        # Append patchwork section to existing CLAUDE.md
        existing = out_path.read_text()
        marker = "<!-- patchwork:start -->"
        end_marker = "<!-- patchwork:end -->"
        if marker in existing:
            # Replace existing patchwork section
            import re
            text = re.sub(
                rf"{re.escape(marker)}.*?{re.escape(end_marker)}",
                f"{marker}\n{text}\n{end_marker}",
                existing,
                flags=re.DOTALL,
            )
        else:
            text = existing.rstrip() + f"\n\n{marker}\n{text}\n{end_marker}\n"

    out_path.write_text(text)

    if not quiet:
        _print_summary(report, out_path)


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--output", "-o", default=None, help="Output file (default: CONVENTIONS.md)")
def update(path: str, output: str | None) -> None:
    """Re-scan and update CONVENTIONS.md, preserving manual edits."""
    from patchwork.scanner import scan as do_scan, ScanOptions

    root = Path(path).resolve()
    out_path = Path(output) if output else root / "CONVENTIONS.md"

    # Load any existing manual annotations
    manual_sections: dict[str, str] = {}
    if out_path.exists():
        manual_sections = _extract_manual_sections(out_path.read_text())

    opts = ScanOptions(root=root)
    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True) as p:
        p.add_task("Updating conventions...", total=None)
        report = do_scan(opts)

    text = report.to_markdown()

    # Re-inject manual sections
    for heading, content in manual_sections.items():
        text += f"\n\n## {heading} (manual)\n\n{content}"

    out_path.write_text(text)
    console.print(f"[green]✓[/green] Updated [bold]{out_path}[/bold]")
    if manual_sections:
        console.print(f"  Preserved {len(manual_sections)} manual section(s)")


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
def diff(path: str) -> None:
    """Show what would change in CONVENTIONS.md (exit 1 if changes detected)."""
    import difflib
    from patchwork.scanner import scan as do_scan, ScanOptions

    root = Path(path).resolve()
    out_path = root / "CONVENTIONS.md"

    opts = ScanOptions(root=root)
    report = do_scan(opts)
    new_text = report.to_markdown()

    if not out_path.exists():
        console.print("[yellow]No existing CONVENTIONS.md — run `patchwork scan` first[/yellow]")
        sys.exit(1)

    old_text = out_path.read_text()
    diffs = list(difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile="CONVENTIONS.md (current)",
        tofile="CONVENTIONS.md (updated)",
        lineterm="",
    ))

    if not diffs:
        console.print("[green]✓ CONVENTIONS.md is up to date[/green]")
        sys.exit(0)

    console.print(Syntax("\n".join(diffs[:100]), "diff"))
    sys.exit(1)


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--interval", default=5.0, show_default=True, help="Seconds between rescans")
def watch(path: str, interval: float) -> None:
    """Watch for changes and auto-regenerate CONVENTIONS.md."""
    import time
    from patchwork.scanner import scan as do_scan, ScanOptions

    root = Path(path).resolve()
    out_path = root / "CONVENTIONS.md"
    opts = ScanOptions(root=root)

    console.print(f"[cyan]Watching[/cyan] {root} (every {interval}s) — Ctrl+C to stop")

    last_mtime: dict[str, float] = {}

    def _get_mtimes() -> dict[str, float]:
        mtimes = {}
        for p in root.rglob("*"):
            if p.is_file() and not any(
                part.startswith(".") or part in ("node_modules", "__pycache__", "dist")
                for part in p.parts
            ):
                try:
                    mtimes[str(p)] = p.stat().st_mtime
                except OSError:
                    pass
        return mtimes

    last_mtime = _get_mtimes()

    try:
        while True:
            time.sleep(interval)
            current = _get_mtimes()
            changed = (
                set(current.keys()) != set(last_mtime.keys())
                or any(current.get(k) != last_mtime.get(k) for k in current)
            )
            if changed:
                last_mtime = current
                report = do_scan(opts)
                out_path.write_text(report.to_markdown())
                console.print(f"[green]↺[/green] CONVENTIONS.md updated")
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped[/dim]")


@main.command()
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option("--lang", multiple=True, help="Filter to specific languages")
def show(path: str, lang: tuple[str, ...]) -> None:
    """Print detected conventions to terminal without writing any file."""
    from patchwork.scanner import scan as do_scan, ScanOptions

    root = Path(path).resolve()
    opts = ScanOptions(root=root, languages=list(lang))

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), transient=True) as p:
        p.add_task("Analysing...", total=None)
        report = do_scan(opts)

    _print_full_report(report)


@main.command()
@click.option("--port", default=3742, show_default=True, help="MCP server port")
@click.option("--stdio", is_flag=True, help="Use stdio transport (for Claude Code)")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
def serve(port: int, stdio: bool, path: str) -> None:
    """Start the patchwork MCP server."""
    import asyncio
    from patchwork.mcp.server import run_server

    root = Path(path).resolve()
    asyncio.run(run_server(root=root, port=port, stdio=stdio))


# ── Rich terminal output helpers ──────────────────────────────────────────────

def _print_summary(report, out_path: Path) -> None:
    """Print a compact summary after scan."""
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("", style="dim")
    table.add_column("")

    table.add_row("Files scanned", str(report.file_count))
    if report.by_lang:
        langs = ", ".join(f"{lang} ({count})" for lang, count in sorted(report.by_lang.items()))
        table.add_row("Languages", langs)
    table.add_row("Time", f"{report.elapsed:.2f}s")
    table.add_row("Output", str(out_path))

    console.print(table)

    # Highlight key findings
    findings: list[str] = []
    for lang, nr in (report.naming or {}).items():
        if nr.functions:
            findings.append(
                f"{lang} functions: [cyan]{nr.functions.style}[/cyan] "
                f"({int(nr.functions.confidence * 100)}%)"
            )
    if report.structure and report.structure.organisation:
        findings.append(f"structure: [cyan]{report.structure.organisation}-based[/cyan]")
    if report.git and report.git.commit_style:
        findings.append(f"commits: [cyan]{report.git.commit_style}[/cyan]")

    if findings:
        console.print("\n[bold]Key findings:[/bold]")
        for f in findings[:8]:
            console.print(f"  [green]✓[/green] {f}")

    console.print(f"\n[bold green]✓[/bold green] Written to [bold]{out_path.name}[/bold]")


def _print_full_report(report) -> None:
    """Full rich terminal output."""
    md = report.to_markdown()
    from rich.markdown import Markdown
    console.print(Markdown(md))


def _extract_manual_sections(text: str) -> dict[str, str]:
    """Extract sections marked with <!-- manual --> from existing file."""
    import re
    sections = {}
    pattern = re.compile(r'## ([^\n]+) \(manual\)\n\n(.*?)(?=\n## |\Z)', re.DOTALL)
    for m in pattern.finditer(text):
        sections[m.group(1)] = m.group(2).strip()
    return sections


if __name__ == "__main__":
    main()
