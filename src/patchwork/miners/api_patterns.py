"""
APIPatternMiner — Detects API / data-layer conventions:
  - REST response shape: {data, error} | {result} | {success, data} | raw
  - HTTP method naming pattern
  - Route parameter style: :id vs {id} vs <id>
  - ORM in use and query style
  - Async patterns: async/await vs callbacks vs coroutines
  - GraphQL presence
  - gRPC presence
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class APIResult:
    response_shape: str | None      # e.g. '{data, error}' | '{success, data}' | 'raw'
    route_param_style: str | None   # ':id' | '{id}' | '<id>'
    async_pattern: str | None       # 'async/await' | 'callbacks' | 'coroutines'
    orm: str | None
    has_graphql: bool
    has_grpc: bool
    api_frameworks: list[str]
    http_client: str | None
    notes: list[str] = field(default_factory=list)


# Response shape patterns
_RESP_DATA_ERROR = re.compile(r'["\'](?:data|error)["\']', re.IGNORECASE)
_RESP_SUCCESS_DATA = re.compile(r'["\']success["\'].*["\']data["\']', re.DOTALL | re.IGNORECASE)
_RESP_RESULT = re.compile(r'["\']result["\']', re.IGNORECASE)

# Route parameter styles
_ROUTE_COLON = re.compile(r'(?:app|router)\.\w+\(["\'][^"\']*:\w+')   # Express :id
_ROUTE_BRACE = re.compile(r'(?:path|url)\s*=\s*["\'][^"\']*\{[a-zA-Z_]+\}')  # FastAPI {id}
_ROUTE_ANGLE = re.compile(r'(?:app|blueprint)\.\w+\(["\'][^"\']*<[a-zA-Z_:]+>')   # Flask <id>

# ORM signals
_ORM_SIGNALS = {
    "SQLAlchemy": [r"\bsessionmaker\b", r"Column\(", r"declarative_base", r"db\.session"],
    "Prisma": [r"prisma\.\w+\.find", r"prisma\.\w+\.create", r"from ['\"]@prisma"],
    "Sequelize": [r"Sequelize\b", r"\.define\(", r"sequelize\.query"],
    "TypeORM": [r"@Entity\(\)", r"getRepository\(", r"createQueryBuilder"],
    "Django ORM": [r"models\.Model\b", r"\.objects\.filter\(", r"\.objects\.get\("],
    "GORM": [r"\bgorm\b.*\.Find\(", r"db\.Where\(", r"AutoMigrate\("],
    "Mongoose": [r"mongoose\.model\(", r"new Schema\(", r"\.populate\("],
    "Drizzle": [r"from ['\"]drizzle-orm", r"drizzle\("],
    "Hibernate": [r"@Entity\b.*@Table\b", r"SessionFactory\b"],
}

# Web framework signals
_FRAMEWORK_SIGNALS = {
    "FastAPI": [r"from fastapi import", r"@app\.get\(", r"@router\."],
    "Flask": [r"from flask import", r"@app\.route\(", r"Blueprint\("],
    "Django": [r"from django", r"urlpatterns\s*=", r"HttpResponse"],
    "Express": [r"require\(['\"]express['\"]", r"app\.use\(", r"router\.get\("],
    "Fastify": [r"require\(['\"]fastify['\"]", r"fastify\.register"],
    "Hono": [r"from ['\"]hono['\"]", r"new Hono\("],
    "Gin": [r"\bgin\b.*\bDefault\(\)", r"r\.GET\(", r"c\.JSON\("],
    "Echo": [r"\becho\b.*\bNew\(\)", r"e\.GET\("],
    "Actix": [r"use actix_web", r"HttpServer::new"],
    "Axum": [r"use axum::", r"Router::new\(\)"],
    "NestJS": [r"@Controller\(", r"@Injectable\(\)", r"@Module\("],
    "Spring": [r"@RestController\b", r"@GetMapping", r"@SpringBootApplication"],
}

_ASYNC_SIGNALS = {
    "python": {
        "async/await": [r"\basync def\b", r"\bawait\b"],
        "coroutines": [r"asyncio\.run\(", r"@asyncio\.coroutine"],
        "callbacks": [r"\.add_done_callback\(", r"concurrent\.futures"],
    },
    "javascript": {
        "async/await": [r"\basync\s+function\b", r"\bawait\b"],
        "promises": [r"\.then\(", r"new Promise\(", r"Promise\.all"],
        "callbacks": [r"callback\s*\)", r"cb\s*\)"],
    },
    "typescript": {
        "async/await": [r"\basync\s+function\b", r"\bawait\b"],
        "promises": [r"Promise<", r"\.then\("],
    },
    "go": {
        "goroutines": [r"\bgo\s+\w+\(", r"\bchan\b"],
    },
    "rust": {
        "async/await": [r"\basync\s+fn\b", r"\.await\b"],
        "futures": [r"Box<dyn Future", r"impl Future"],
    },
}

_HTTP_CLIENTS = {
    "python": ["requests", "httpx", "aiohttp", "urllib3", "pycurl"],
    "javascript": ["axios", "fetch", "got", "node-fetch", "superagent", "ky"],
    "typescript": ["axios", "fetch", "got", "ky", "ofetch"],
    "go": ["net/http", "resty", "fasthttp"],
    "rust": ["reqwest", "hyper", "ureq"],
}


def _detect_apis(paths: list[Path], lang: str) -> APIResult:
    data_error = 0
    success_data = 0
    result_shape = 0
    colon_routes = 0
    brace_routes = 0
    angle_routes = 0
    # Track per-file hits (not per-match) to avoid false positives from
    # code that merely mentions framework names in strings, comments, or docs.
    orm_files: Counter[str] = Counter()
    fw_files: Counter[str] = Counter()
    async_counts: Counter[str] = Counter()
    http_client_counts: Counter[str] = Counter()
    gql_files = 0
    grpc_files = 0

    for path in paths[:200]:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue

        data_error += len(_RESP_DATA_ERROR.findall(text))
        success_data += len(_RESP_SUCCESS_DATA.findall(text))
        result_shape += len(_RESP_RESULT.findall(text))

        colon_routes += len(_ROUTE_COLON.findall(text))
        brace_routes += len(_ROUTE_BRACE.findall(text))
        angle_routes += len(_ROUTE_ANGLE.findall(text))

        # Skip files that are themselves pattern-definition files (e.g. this miner)
        # to avoid self-matching on our own regex strings.
        if "_ORM_SIGNALS" in text or "_FRAMEWORK_SIGNALS" in text:
            continue

        # Count each ORM/framework once per file (not per pattern match)
        for orm_name, patterns in _ORM_SIGNALS.items():
            if any(re.search(pat, text) for pat in patterns):
                orm_files[orm_name] += 1

        for fw, patterns in _FRAMEWORK_SIGNALS.items():
            if any(re.search(pat, text) for pat in patterns):
                fw_files[fw] += 1

        for style, patterns in _ASYNC_SIGNALS.get(lang, {}).items():
            if any(re.search(pat, text) for pat in patterns):
                async_counts[style] += 1

        for client in _HTTP_CLIENTS.get(lang, []):
            # Match actual import/usage, not just the string appearing in comments or dicts
            if re.search(rf'(?:import\s+{re.escape(client)}|from\s+{re.escape(client)}\s|{re.escape(client)}\.)', text):
                http_client_counts[client] += 1

        if re.search(r'\bgraphql\b|gql`', text, re.IGNORECASE):
            gql_files += 1
        if re.search(r'\bgrpc\b|\bprotobuf\b|\.proto["\']', text, re.IGNORECASE):
            grpc_files += 1

    # Require signal in ≥3 files to filter out false positives.
    # Single or double-file mentions are often: config files, the tool's own
    # pattern dictionaries, test fixtures, or README-like docstrings.
    min_files = 3
    orm_files = Counter({k: v for k, v in orm_files.items() if v >= min_files})
    fw_files = Counter({k: v for k, v in fw_files.items() if v >= min_files})

    # Response shape
    response_shape = None
    if success_data > 3:
        response_shape = "{success, data}"
    elif data_error > 3:
        response_shape = "{data, error}"
    elif result_shape > 3:
        response_shape = "{result}"

    # Route param style
    route_total = colon_routes + brace_routes + angle_routes
    route_style = None
    if route_total > 0:
        best = max(colon_routes, brace_routes, angle_routes)
        if colon_routes == best:
            route_style = ":id (Express style)"
        elif brace_routes == best:
            route_style = "{id} (FastAPI style)"
        else:
            route_style = "<id> (Flask style)"

    return APIResult(
        response_shape=response_shape,
        route_param_style=route_style,
        async_pattern=async_counts.most_common(1)[0][0] if async_counts else None,
        orm=orm_files.most_common(1)[0][0] if orm_files else None,
        has_graphql=gql_files >= 3,
        has_grpc=grpc_files >= 3,
        api_frameworks=[fw for fw, _ in fw_files.most_common(3)],
        http_client=http_client_counts.most_common(1)[0][0] if http_client_counts else None,
    )


class APIPatternMiner:
    def mine(self, by_lang: dict[str, list[Path]]) -> dict[str, APIResult]:
        results: dict[str, APIResult] = {}
        for lang, paths in by_lang.items():
            result = _detect_apis(paths, lang)
            # Only include if we found something meaningful
            if result.api_frameworks or result.orm or result.async_pattern:
                results[lang] = result
        return results
