"""deploy_config.json: the handler, layer pins and environment the operator-gated deploy applies (Decision 272)."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_CONFIG = _ROOT / "src/lambdas/ducklake_telemetry_content_reader/deploy_config.json"
_TELEMETRY_READER_HCL = _ROOT / "terraform/personal/ducklake_telemetry_reader.tf"
_CONTENT_HCL = _ROOT / "terraform/bootstrap/telemetry_content_reader.tf"


def _config() -> dict:
    return json.loads(_CONFIG.read_text(encoding="utf-8"))


def test_shape_is_three_keys_and_two_pinned_layers() -> None:
    cfg = _config()
    assert set(cfg) == {"handler", "layers", "environment"}
    assert cfg["handler"] == "src.lambdas.ducklake_telemetry_content_reader.handler.handler"
    assert [layer["name"] for layer in cfg["layers"]] == ["ducklake-deps", "ducklake-extensions"]
    for layer in cfg["layers"]:
        assert set(layer) == {"name", "version", "code_sha256"}
        assert isinstance(layer["version"], int) and layer["version"] > 0
        assert len(base64.b64decode(layer["code_sha256"], validate=True)) == 32
        assert not re.fullmatch(r"[0-9a-f]{64}", layer["code_sha256"])


def test_environment_matches_the_telemetry_reader_plus_the_blob_root() -> None:
    env = _config()["environment"]
    hcl = _TELEMETRY_READER_HCL.read_text(encoding="utf-8")
    declared = set(re.findall(r"^\s{6}([A-Z_]+)\s*=", hcl, re.M))
    assert declared - {"TELEMETRY_DSN_SECRET_ID"} <= set(env)
    assert "TELEMETRY_DSN_SECRET_ID" not in env
    assert env["TELEMETRY_META_SCHEMA"] == "ducklake_telemetry"
    assert env["TELEMETRY_DATA_PATH"] == "s3://agent-platform-data-lake/ducklake-telemetry/"
    assert env["DUCKLAKE_EXTENSION_DIRECTORY"] == "/opt/duckdb_extensions"
    assert env["TELEMETRY_DSN_PARAMETER"] == "/secret/agent-platform-ducklake-telemetry-reader-dsn"
    prefix = re.search(r'telemetry_content_blob_prefix\s*=\s*"([^"]+)"', _CONTENT_HCL.read_text(encoding="utf-8"))
    assert prefix is not None
    assert env["TELEMETRY_BLOB_ROOT"] == f"s3://agent-platform-data-lake/{prefix.group(1)}/"


def test_no_twelve_digit_number_anywhere() -> None:
    assert not re.search(r"\d{12}", _CONFIG.read_text(encoding="utf-8"))
