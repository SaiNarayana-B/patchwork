"""
patchwork — Mine your codebase. Generate CONVENTIONS.md.
Stop AI agents from making up your style.
"""

__version__ = "0.1.0"
__all__ = ["scan", "ConventionReport"]

from patchwork.scanner import scan
from patchwork.output.report import ConventionReport
