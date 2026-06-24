"""Integration tests for the full scanner pipeline."""
import tempfile
from pathlib import Path

import pytest

from patchwork.scanner import scan, ScanOptions


@pytest.fixture
def python_project(tmp_path):
    """Create a minimal Python project for testing."""
    # Source files
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("")
    (tmp_path / "src" / "service.py").write_text("""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class UserService:
    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        try:
            return {"id": user_id, "name": "Test"}
        except Exception as e:
            logger.error("Failed to get user: %s", e)
            raise

    def create_user(self, name: str, email: str) -> dict:
        if not name:
            raise ValueError("Name is required")
        return {"name": name, "email": email}

class AuthService:
    def verify_token(self, token: str) -> bool:
        try:
            return len(token) > 0
        except Exception:
            return False
""")

    (tmp_path / "src" / "models.py").write_text("""
from dataclasses import dataclass

@dataclass
class User:
    id: int
    name: str
    email: str

@dataclass
class Session:
    token: str
    user_id: int
    MAX_DURATION = 3600
""")

    # Tests
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("")
    (tmp_path / "tests" / "test_service.py").write_text("""
import pytest
from src.service import UserService

def test_get_user_by_id():
    svc = UserService()
    result = svc.get_user_by_id(1)
    assert result["id"] == 1

def test_create_user_raises():
    svc = UserService()
    with pytest.raises(ValueError):
        svc.create_user("", "test@example.com")
""")

    # Config
    (tmp_path / "pyproject.toml").write_text("""
[project]
name = "test-project"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = ["fastapi", "sqlalchemy"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
""")

    return tmp_path


class TestFullScan:
    def test_scan_returns_report(self, python_project):
        opts = ScanOptions(root=python_project, include_git=False)
        report = scan(opts)
        assert report is not None
        assert report.file_count > 0
        assert "python" in report.by_lang

    def test_naming_detected(self, python_project):
        opts = ScanOptions(root=python_project, include_git=False)
        report = scan(opts)
        assert report.naming
        py = report.naming.get("python")
        assert py is not None
        # Functions should be snake_case
        if py.functions:
            assert py.functions.style == "snake_case"
        # Classes should be PascalCase
        if py.classes:
            assert py.classes.style == "PascalCase"

    def test_structure_detected(self, python_project):
        opts = ScanOptions(root=python_project, include_git=False)
        report = scan(opts)
        assert report.structure is not None
        assert report.structure.test_layout is not None

    def test_config_detected(self, python_project):
        opts = ScanOptions(root=python_project, include_git=False)
        report = scan(opts)
        assert report.config is not None
        assert report.config.language == "python"
        assert "fastapi" in (report.config.frameworks or [])

    def test_to_markdown(self, python_project):
        opts = ScanOptions(root=python_project, include_git=False)
        report = scan(opts)
        md = report.to_markdown()
        assert "CONVENTIONS.md" in md
        assert "Tech Stack" in md
        assert "Naming Conventions" in md

    def test_to_json(self, python_project):
        import json
        opts = ScanOptions(root=python_project, include_git=False)
        report = scan(opts)
        data = json.loads(report.to_json())
        assert "file_count" in data
        assert "naming" in data
        assert "config" in data

    def test_empty_project(self, tmp_path):
        opts = ScanOptions(root=tmp_path, include_git=False)
        report = scan(opts)
        assert report is not None
        assert report.file_count == 0

    def test_agents_md_format(self, python_project):
        opts = ScanOptions(root=python_project, include_git=False)
        report = scan(opts)
        md = report.to_markdown(agents_md=True)
        assert "AGENTS.md" in md

    def test_elapsed_positive(self, python_project):
        opts = ScanOptions(root=python_project, include_git=False)
        report = scan(opts)
        assert report.elapsed > 0

    def test_testing_detected(self, python_project):
        opts = ScanOptions(root=python_project, include_git=False)
        report = scan(opts)
        if report.testing and "python" in report.testing:
            tr = report.testing["python"]
            assert tr.test_file_count >= 1
