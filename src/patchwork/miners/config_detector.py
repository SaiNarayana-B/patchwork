"""
ConfigDetector — Reads config files to detect tech stack without AST parsing.
Inspects: package.json, pyproject.toml, go.mod, Cargo.toml, Makefile, etc.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore


@dataclass
class ProjectConfig:
    name: str | None = None
    version: str | None = None
    language: str | None = None          # primary language
    runtime: str | None = None           # e.g. 'Node.js 20', 'Python 3.11'
    package_manager: str | None = None   # npm/yarn/pnpm/pip/uv/cargo/go
    frameworks: list[str] = field(default_factory=list)
    linters: list[str] = field(default_factory=list)
    formatters: list[str] = field(default_factory=list)
    type_checker: str | None = None
    build_tool: str | None = None
    scripts: dict[str, str] = field(default_factory=dict)
    has_docker: bool = False
    has_ci: bool = False
    ci_platform: str | None = None
    notes: list[str] = field(default_factory=list)


_JS_FRAMEWORKS = [
    "react", "next", "vue", "nuxt", "svelte", "sveltekit", "angular",
    "solid", "remix", "astro", "qwik", "hono", "express", "fastify",
    "nestjs", "@nestjs/core", "koa", "elysia",
]
_JS_LINTERS = ["eslint", "oxlint", "biome"]
_JS_FORMATTERS = ["prettier", "biome", "@biomejs/biome"]
_JS_TYPE_CHECKERS = ["typescript", "flow"]
_JS_BUILD = ["vite", "webpack", "turbopack", "rollup", "esbuild", "bun", "parcel", "rspack"]

_PY_FRAMEWORKS = [
    "fastapi", "flask", "django", "starlette", "litestar", "tornado",
    "aiohttp", "sanic", "falcon",
]
_PY_LINTERS = ["ruff", "flake8", "pylint", "pyflakes"]
_PY_FORMATTERS = ["black", "ruff", "autopep8", "yapf"]
_PY_TYPE = ["mypy", "pyright", "pytype"]

_CI_PLATFORMS = {
    ".github/workflows": "GitHub Actions",
    ".gitlab-ci.yml": "GitLab CI",
    ".circleci": "CircleCI",
    "Jenkinsfile": "Jenkins",
    ".travis.yml": "Travis CI",
    "azure-pipelines.yml": "Azure Pipelines",
    ".drone.yml": "Drone CI",
    "bitbucket-pipelines.yml": "Bitbucket Pipelines",
}


class ConfigDetector:
    def __init__(self, root: Path):
        self.root = root

    def detect(self) -> ProjectConfig:
        cfg = ProjectConfig()

        # Detect CI
        for ci_path, ci_name in _CI_PLATFORMS.items():
            if (self.root / ci_path).exists():
                cfg.has_ci = True
                cfg.ci_platform = ci_name
                break

        # Docker
        if (self.root / "Dockerfile").exists() or (self.root / "docker-compose.yml").exists():
            cfg.has_docker = True

        # Node.js
        pkg_json = self.root / "package.json"
        if pkg_json.exists():
            self._read_package_json(pkg_json, cfg)

        # Python
        pyproject = self.root / "pyproject.toml"
        if pyproject.exists():
            self._read_pyproject(pyproject, cfg)
        elif (self.root / "setup.py").exists() or (self.root / "setup.cfg").exists():
            cfg.language = "python"
            cfg.package_manager = "pip"

        # Go
        go_mod = self.root / "go.mod"
        if go_mod.exists():
            self._read_go_mod(go_mod, cfg)

        # Rust
        cargo = self.root / "Cargo.toml"
        if cargo.exists():
            self._read_cargo(cargo, cfg)

        # Package manager detection (node)
        if (self.root / "pnpm-lock.yaml").exists():
            cfg.package_manager = "pnpm"
        elif (self.root / "yarn.lock").exists():
            cfg.package_manager = "yarn"
        elif (self.root / "bun.lockb").exists() or (self.root / "bun.lock").exists():
            cfg.package_manager = "bun"
        elif (self.root / "package-lock.json").exists() and cfg.package_manager is None:
            cfg.package_manager = "npm"

        # Python package manager
        if (self.root / "uv.lock").exists():
            cfg.package_manager = "uv"
        elif (self.root / "poetry.lock").exists():
            cfg.package_manager = "poetry"
        elif (self.root / "Pipfile").exists():
            cfg.package_manager = "pipenv"

        return cfg

    def _read_package_json(self, path: Path, cfg: ProjectConfig) -> None:
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return

        cfg.language = "javascript/typescript"
        cfg.name = data.get("name")
        cfg.version = data.get("version")

        all_deps = {
            **data.get("dependencies", {}),
            **data.get("devDependencies", {}),
        }

        for fw in _JS_FRAMEWORKS:
            if fw in all_deps:
                cfg.frameworks.append(fw)

        for linter in _JS_LINTERS:
            if linter in all_deps:
                cfg.linters.append(linter)

        for fmt in _JS_FORMATTERS:
            if fmt in all_deps:
                if fmt not in cfg.formatters:
                    cfg.formatters.append(fmt)

        for tc in _JS_TYPE_CHECKERS:
            if tc in all_deps:
                cfg.type_checker = tc

        for bt in _JS_BUILD:
            if bt in all_deps:
                cfg.build_tool = bt
                break

        scripts = data.get("scripts", {})
        important = {k: v for k, v in scripts.items()
                     if k in ("dev", "start", "build", "test", "lint", "format", "typecheck")}
        cfg.scripts.update(important)

        # Runtime from engines field
        engines = data.get("engines", {})
        if "node" in engines:
            cfg.runtime = f"Node.js {engines['node']}"

    def _read_pyproject(self, path: Path, cfg: ProjectConfig) -> None:
        cfg.language = "python"
        if tomllib is None:
            return
        try:
            data = tomllib.loads(path.read_text())
        except Exception:
            return

        project = data.get("project", {})
        tool = data.get("tool", {})

        cfg.name = project.get("name")
        cfg.version = project.get("version")

        requires_python = project.get("requires-python", "")
        if requires_python:
            cfg.runtime = f"Python {requires_python}"

        all_deps = list(project.get("dependencies", [])) + [
            str(dep) for group in project.get("optional-dependencies", {}).values()
            for dep in group
        ]
        dep_names = [re.split(r"[>=<!;[\s]", d)[0].lower() for d in all_deps]

        for fw in _PY_FRAMEWORKS:
            if fw in dep_names:
                cfg.frameworks.append(fw)

        for linter in _PY_LINTERS:
            if linter in dep_names or linter in tool:
                if linter not in cfg.linters:
                    cfg.linters.append(linter)

        for fmt in _PY_FORMATTERS:
            if fmt in dep_names or fmt in tool:
                if fmt not in cfg.formatters:
                    cfg.formatters.append(fmt)

        for tc in _PY_TYPE:
            if tc in dep_names or tc in tool:
                cfg.type_checker = tc
                break

        scripts_section = project.get("scripts", {})
        cfg.scripts.update(scripts_section)

    def _read_go_mod(self, path: Path, cfg: ProjectConfig) -> None:
        cfg.language = "go"
        cfg.package_manager = "go"
        try:
            text = path.read_text()
        except OSError:
            return
        m = re.search(r'^module\s+(\S+)', text, re.MULTILINE)
        if m:
            cfg.name = m.group(1)
        m2 = re.search(r'^go\s+([\d.]+)', text, re.MULTILINE)
        if m2:
            cfg.runtime = f"Go {m2.group(1)}"

        # Detect popular Go frameworks from dependencies
        go_frameworks = {
            "gin-gonic/gin": "gin",
            "labstack/echo": "echo",
            "gofiber/fiber": "fiber",
            "go-chi/chi": "chi",
            "gorilla/mux": "gorilla/mux",
        }
        for dep, name in go_frameworks.items():
            if dep in text:
                cfg.frameworks.append(name)

    def _read_cargo(self, path: Path, cfg: ProjectConfig) -> None:
        cfg.language = "rust"
        cfg.package_manager = "cargo"
        if tomllib is None:
            return
        try:
            data = tomllib.loads(path.read_text())
        except Exception:
            return
        pkg = data.get("package", {})
        cfg.name = pkg.get("name")
        cfg.version = pkg.get("version")
        cfg.runtime = f"Rust {pkg.get('edition', '2021')}"

        # Detect web frameworks
        rust_frameworks = {
            "axum": "axum", "actix-web": "actix-web", "warp": "warp",
            "rocket": "rocket", "tide": "tide",
        }
        all_deps = {**data.get("dependencies", {}), **data.get("dev-dependencies", {})}
        for dep, name in rust_frameworks.items():
            if dep in all_deps:
                cfg.frameworks.append(name)
