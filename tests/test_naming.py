"""Tests for NamingMiner."""
import tempfile
from pathlib import Path

import pytest

from patchwork.miners.naming import NamingMiner, _classify, _majority_convention


# ── Unit tests for classifier ─────────────────────────────────────────────────

class TestClassify:
    def test_snake_case(self):
        assert _classify("get_user_by_id") == "snake_case"
        assert _classify("parse_json") == "snake_case"

    def test_camel_case(self):
        assert _classify("getUserById") == "camelCase"
        assert _classify("parseJson") == "camelCase"

    def test_pascal_case(self):
        assert _classify("UserService") == "PascalCase"
        assert _classify("HttpClient") == "PascalCase"

    def test_screaming_snake(self):
        assert _classify("MAX_RETRIES") == "SCREAMING_SNAKE"
        assert _classify("API_BASE_URL") == "SCREAMING_SNAKE"

    def test_kebab(self):
        assert _classify("my-component") == "kebab-case"

    def test_single_word(self):
        # single lowercase word — could be snake or camel; both accept
        result = _classify("user")
        assert result in ("snake_case", "camelCase")


class TestMajorityConvention:
    def test_unanimous(self):
        names = ["get_user", "parse_data", "fetch_all", "create_item"]
        conv = _majority_convention(names)
        assert conv.style == "snake_case"
        assert conv.confidence == 1.0

    def test_majority(self):
        names = ["getUser", "parseData", "fetchAll", "create_item"]
        conv = _majority_convention(names)
        assert conv.style == "camelCase"
        assert conv.confidence == 0.75

    def test_empty(self):
        conv = _majority_convention([])
        assert conv.style == "mixed"
        assert conv.confidence == 0.0


# ── Integration test with real files ─────────────────────────────────────────

class TestNamingMiner:
    def test_python_snake_case(self, tmp_path):
        py_file = tmp_path / "service.py"
        py_file.write_text("""
def get_user_by_id(user_id: int):
    pass

def parse_json_response(data: dict):
    pass

def create_new_item(name: str, value: int):
    pass

class UserService:
    pass

class HttpClient:
    pass
""")
        miner = NamingMiner()
        result = miner.mine({"python": [py_file]})
        nr = result.get("python")
        assert nr is not None
        assert nr.functions is not None
        assert nr.functions.style == "snake_case"
        assert nr.functions.confidence >= 0.9
        assert nr.classes is not None
        assert nr.classes.style == "PascalCase"

    def test_typescript_camel_case(self, tmp_path):
        ts_file = tmp_path / "service.ts"
        ts_file.write_text("""
function getUserById(userId: string) {}
function parseResponse(data: object) {}
const fetchUserData = async () => {};

class UserService {}
class HttpClient {}
""")
        miner = NamingMiner()
        result = miner.mine({"typescript": [ts_file]})
        nr = result.get("typescript")
        assert nr is not None

    def test_test_prefix_detection(self, tmp_path):
        py_file = tmp_path / "test_service.py"
        py_file.write_text("""
def test_get_user():
    pass

def test_parse_json():
    pass

def test_create_item():
    pass
""")
        miner = NamingMiner()
        result = miner.mine({"python": [py_file]})
        nr = result.get("python")
        assert nr is not None
        assert nr.test_prefix == "test_"

    def test_multiple_files(self, tmp_path):
        for i in range(5):
            f = tmp_path / f"module_{i}.py"
            f.write_text(f"""
def get_item_{i}():
    pass

def process_data_{i}():
    pass

class Model{i}:
    pass
""")
        miner = NamingMiner()
        result = miner.mine({"python": list(tmp_path.glob("*.py"))})
        nr = result.get("python")
        assert nr is not None
        assert nr.functions.style == "snake_case"
        assert nr.classes.style == "PascalCase"
