"""
TestingMiner — Detects testing conventions:
  - Framework (pytest / unittest / jest / vitest / go test / etc.)
  - Test organisation (describe/it / class-based / function-based)
  - Coverage tooling present
  - Fixture/factory patterns
  - Assertion style (assert vs expect vs should)
  - Test file ratio
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestingResult:
    framework: str | None
    test_file_count: int
    source_file_count: int
    test_ratio: float               # test files / source files
    organisation: str | None        # 'describe/it' | 'class-based' | 'function-based'
    assertion_style: str | None     # 'assert' | 'expect' | 'should' | 'mixed'
    has_coverage: bool
    coverage_tool: str | None
    has_fixtures: bool
    has_factories: bool
    has_mocking: bool
    mock_library: str | None
    notes: list[str] = field(default_factory=list)


_FRAMEWORK_SIGNALS = {
    "python": {
        "pytest": [r"\bimport pytest\b", r"\bfrom pytest\b", r"@pytest\.fixture", r"def test_"],
        "unittest": [r"\bimport unittest\b", r"class\s+\w+\(unittest\.TestCase\)"],
        "nose": [r"\bimport nose\b", r"\bfrom nose\b"],
    },
    "javascript": {
        "jest": [r"\bimport.*from\s+['\"]jest['\"]", r"\bdescribe\(", r"\btest\(", r"\bit\("],
        "vitest": [r"\bimport.*from\s+['\"]vitest['\"]", r"\bdescribe\(", r"\bit\("],
        "mocha": [r"\bimport.*from\s+['\"]mocha['\"]", r"\bdescribe\(", r"\bit\("],
        "jasmine": [r"\bjasmine\.", r"\bdescribe\("],
    },
    "typescript": {
        "jest": [r"\bimport.*from\s+['\"]@jest/", r"\bdescribe\(", r"\bit\("],
        "vitest": [r"\bimport.*from\s+['\"]vitest['\"]", r"\bdescribe\("],
        "mocha": [r"\bimport.*from\s+['\"]mocha['\"]"],
    },
    "go": {
        "testing": [r"\bimport\s+\"testing\"", r"\bfunc\s+Test\w+\(t\s+\*testing\.T\)"],
        "testify": [r"\bimport.*testify"],
    },
    "rust": {
        "built-in": [r"#\[cfg\(test\)\]", r"#\[test\]", r"\bmod\s+tests\s*\{"],
    },
}

_COVERAGE = {
    "python": ["coverage", "pytest-cov", "coveragepy"],
    "javascript": ["c8", "istanbul", "nyc", "jest --coverage"],
    "typescript": ["c8", "istanbul", "nyc"],
    "go": ["go test -cover"],
    "rust": ["cargo-tarpaulin", "cargo-llvm-cov"],
}

_MOCK_LIBRARIES = {
    "python": ["unittest.mock", "pytest-mock", "mock", "MagicMock", "patch"],
    "javascript": ["jest.fn", "sinon", "jest.mock"],
    "typescript": ["jest.fn", "ts-mockito", "jest.mock"],
    "go": ["testify/mock", "gomock"],
}

_RE_DESCRIBE = re.compile(r'\bdescribe\s*\(')
_RE_IT = re.compile(r'\bit\s*\(')
_RE_EXPECT = re.compile(r'\bexpect\s*\(')
_RE_ASSERT = re.compile(r'\bassert\s')
_RE_SHOULD = re.compile(r'\.should\.')
_RE_FIXTURE = re.compile(r'@pytest\.fixture|@fixture|class\s+\w+Factory')
_RE_FACTORY = re.compile(r'Factory\b|factory_boy|FactoryBot|factory\.create')


def _is_test_file(path: Path) -> bool:
    stem = path.stem
    return (
        stem.startswith("test_") or stem.endswith("_test")
        or ".test." in path.name or ".spec." in path.name
        or stem.startswith("Test") or path.parent.name in ("tests", "test", "__tests__", "spec")
    )


class TestingMiner:
    def __init__(self, root: Path):
        self.root = root

    def mine(self, by_lang: dict[str, list[Path]]) -> dict[str, TestingResult]:
        results: dict[str, TestingResult] = {}
        for lang, paths in by_lang.items():
            results[lang] = self._mine_lang(lang, paths)
        return results

    def _mine_lang(self, lang: str, paths: list[Path]) -> TestingResult:
        test_files = [p for p in paths if _is_test_file(p)]
        src_files = [p for p in paths if not _is_test_file(p)]
        ratio = len(test_files) / len(src_files) if src_files else 0.0

        framework_counts: Counter[str] = Counter()
        describe_count = 0
        expect_count = 0
        assert_count = 0
        should_count = 0
        has_fixtures = False
        has_factories = False
        has_mocking = False
        mock_libs: Counter[str] = Counter()
        has_coverage = False
        coverage_tool: str | None = None

        for path in (test_files + src_files)[:200]:
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue

            # Framework detection
            for fw, patterns in _FRAMEWORK_SIGNALS.get(lang, {}).items():
                for pat in patterns:
                    if re.search(pat, text):
                        framework_counts[fw] += 1
                        break

            describe_count += len(_RE_DESCRIBE.findall(text))
            expect_count += len(_RE_EXPECT.findall(text))
            assert_count += len(_RE_ASSERT.findall(text))
            should_count += len(_RE_SHOULD.findall(text))

            if _RE_FIXTURE.search(text):
                has_fixtures = True
            if _RE_FACTORY.search(text):
                has_factories = True

            for ml in _MOCK_LIBRARIES.get(lang, []):
                if ml in text:
                    has_mocking = True
                    mock_libs[ml] += 1

        # Coverage detection from config files
        for cov in _COVERAGE.get(lang, []):
            cfg_files = [
                self.root / "pyproject.toml",
                self.root / "setup.cfg",
                self.root / "package.json",
                self.root / "jest.config.js",
                self.root / "jest.config.ts",
                self.root / ".nycrc",
            ]
            for cfg in cfg_files:
                if cfg.exists():
                    try:
                        if cov.split()[0] in cfg.read_text():
                            has_coverage = True
                            coverage_tool = cov.split()[0]
                            break
                    except OSError:
                        pass

        framework = framework_counts.most_common(1)[0][0] if framework_counts else None

        org = None
        if describe_count > 3:
            org = "describe/it"
        elif lang == "python" and "unittest" in (framework or ""):
            org = "class-based"
        elif lang in ("python", "go", "rust"):
            org = "function-based"

        assertion_style = None
        total_assert = expect_count + assert_count + should_count
        if total_assert > 0:
            if expect_count >= max(assert_count, should_count):
                assertion_style = "expect"
            elif assert_count >= max(expect_count, should_count):
                assertion_style = "assert"
            else:
                assertion_style = "should"

        mock_lib = mock_libs.most_common(1)[0][0] if mock_libs else None

        return TestingResult(
            framework=framework,
            test_file_count=len(test_files),
            source_file_count=len(src_files),
            test_ratio=round(ratio, 2),
            organisation=org,
            assertion_style=assertion_style,
            has_coverage=has_coverage,
            coverage_tool=coverage_tool,
            has_fixtures=has_fixtures,
            has_factories=has_factories,
            has_mocking=has_mocking,
            mock_library=mock_lib,
        )
