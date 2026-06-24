"""
ErrorHandlingMiner — Detects error-handling conventions:
  - try/except vs Result types vs callbacks vs promises
  - Custom exception class naming
  - Error propagation style (raise vs return vs log)
  - Logging framework used
  - Panic/recover (Go), ? operator (Rust), etc.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from patchwork.miners.ast_base import parse_bytes, walk, node_text


@dataclass
class ErrorResult:
    primary_pattern: str        # 'try/except' | 'Result<T>' | 'callbacks' | 'async/await' | 'Either'
    exception_naming: str | None  # 'Error' suffix | 'Exception' suffix | 'mixed'
    logging_framework: str | None
    custom_exceptions: list[str]
    propagation_style: str | None  # 'raise' | 'return' | 'log-and-continue' | 'mixed'
    notes: list[str] = field(default_factory=list)


_PY_RAISE = re.compile(r'\braise\b')
_PY_TRY = re.compile(r'\btry\s*:')
_PY_EXCEPT = re.compile(r'\bexcept\s+\w')
_PY_CUSTOM_EX = re.compile(r'class\s+(\w+(?:Error|Exception))\s*\(')
_PY_LOGGING = re.compile(r'\b(logging|structlog|loguru|logbook)\b')
_PY_LOGGER_VAR = re.compile(r'logger\s*=\s*(logging|structlog|loguru)')

_JS_TRY = re.compile(r'\btry\s*\{')
_JS_CATCH = re.compile(r'\bcatch\s*\(')
_JS_THROW = re.compile(r'\bthrow\s+new\s+\w+')
_JS_PROMISE_CATCH = re.compile(r'\.catch\(')
_JS_ASYNC_AWAIT = re.compile(r'\bawait\b')
_JS_CUSTOM_ERROR = re.compile(r'class\s+(\w+Error)\s+extends\s+\w*Error')

_GO_ERR = re.compile(r',\s*err\s*:?=')
_GO_ERR_NIL = re.compile(r'if\s+err\s*!=\s*nil')
_GO_CUSTOM_ERR = re.compile(r'type\s+(\w+Error)\s+struct')

_RUST_RESULT = re.compile(r'Result<[^,\n]+,')
_RUST_QUESTION = re.compile(r'\?;')
_RUST_UNWRAP = re.compile(r'\.unwrap\(\)')
_RUST_EXPECT = re.compile(r'\.expect\(')

_LOGGING_FRAMEWORKS = {
    "python": ["logging", "structlog", "loguru", "logbook"],
    "javascript": ["winston", "pino", "bunyan", "loglevel", "debug", "console"],
    "typescript": ["winston", "pino", "bunyan", "tslog", "pino-pretty"],
    "go": ["log", "zap", "zerolog", "logrus", "slog"],
    "rust": ["log", "tracing", "env_logger", "slog"],
}


def _detect_py_errors(paths: list[Path]) -> ErrorResult:
    try_count = 0
    raise_count = 0
    promise_count = 0
    custom_excs: list[str] = []
    logging_counts: Counter[str] = Counter()

    for path in paths[:150]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        try_count += len(_PY_TRY.findall(text))
        raise_count += len(_PY_RAISE.findall(text))
        custom_excs.extend(_PY_CUSTOM_EX.findall(text))
        for fw in _LOGGING_FRAMEWORKS.get("python", []):
            if fw in text:
                logging_counts[fw] += 1

    pattern = "try/except"
    exc_naming = None
    if custom_excs:
        error_suffix = sum(1 for e in custom_excs if e.endswith("Error"))
        exc_suffix = sum(1 for e in custom_excs if e.endswith("Exception"))
        exc_naming = "Error suffix" if error_suffix >= exc_suffix else "Exception suffix"

    prop = "raise" if raise_count > try_count * 0.5 else "log-and-continue"
    logging_fw = logging_counts.most_common(1)[0][0] if logging_counts else None

    return ErrorResult(
        primary_pattern=pattern,
        exception_naming=exc_naming,
        logging_framework=logging_fw,
        custom_exceptions=list(dict.fromkeys(custom_excs))[:8],
        propagation_style=prop,
    )


def _detect_js_errors(paths: list[Path], lang: str) -> ErrorResult:
    try_count = 0
    throw_count = 0
    promise_catch = 0
    async_await = 0
    custom_errors: list[str] = []
    logging_counts: Counter[str] = Counter()

    for path in paths[:150]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        try_count += len(_JS_TRY.findall(text))
        throw_count += len(_JS_THROW.findall(text))
        promise_catch += len(_JS_PROMISE_CATCH.findall(text))
        async_await += len(_JS_ASYNC_AWAIT.findall(text))
        custom_errors.extend(_JS_CUSTOM_ERROR.findall(text))
        for fw in _LOGGING_FRAMEWORKS.get(lang, []):
            if fw in text:
                logging_counts[fw] += 1

    total = try_count + promise_catch + async_await
    if total == 0:
        pattern = "try/catch"
    elif async_await > promise_catch:
        pattern = "async/await + try/catch"
    else:
        pattern = "Promise chains"

    logging_fw = logging_counts.most_common(1)[0][0] if logging_counts else None

    return ErrorResult(
        primary_pattern=pattern,
        exception_naming="Error suffix" if custom_errors else None,
        logging_framework=logging_fw,
        custom_exceptions=list(dict.fromkeys(custom_errors))[:8],
        propagation_style="throw" if throw_count > 5 else "return",
    )


def _detect_go_errors(paths: list[Path]) -> ErrorResult:
    err_check = 0
    custom_errs: list[str] = []

    for path in paths[:150]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        err_check += len(_GO_ERR_NIL.findall(text))
        custom_errs.extend(_GO_CUSTOM_ERR.findall(text))

    notes = []
    if err_check > 5:
        notes.append("Idiomatic Go error handling: check `err != nil` after each call")

    return ErrorResult(
        primary_pattern="if err != nil",
        exception_naming=None,
        logging_framework=None,
        custom_exceptions=list(dict.fromkeys(custom_errs))[:5],
        propagation_style="return",
        notes=notes,
    )


def _detect_rust_errors(paths: list[Path]) -> ErrorResult:
    result_count = 0
    question_count = 0
    unwrap_count = 0

    for path in paths[:150]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        result_count += len(_RUST_RESULT.findall(text))
        question_count += len(_RUST_QUESTION.findall(text))
        unwrap_count += len(_RUST_UNWRAP.findall(text))

    pattern = "Result<T,E> + ? operator" if question_count > unwrap_count else "Result<T,E> + unwrap/expect"
    notes = []
    if unwrap_count > question_count and question_count > 0:
        notes.append("Mix of ? operator and .unwrap() — prefer ? in production code")

    return ErrorResult(
        primary_pattern=pattern,
        exception_naming=None,
        logging_framework=None,
        custom_exceptions=[],
        propagation_style="return",
        notes=notes,
    )


class ErrorHandlingMiner:
    def mine(self, by_lang: dict[str, list[Path]]) -> dict[str, ErrorResult]:
        results: dict[str, ErrorResult] = {}
        for lang, paths in by_lang.items():
            if lang == "python":
                results[lang] = _detect_py_errors(paths)
            elif lang in ("javascript", "typescript"):
                results[lang] = _detect_js_errors(paths, lang)
            elif lang == "go":
                results[lang] = _detect_go_errors(paths)
            elif lang == "rust":
                results[lang] = _detect_rust_errors(paths)
        return results
