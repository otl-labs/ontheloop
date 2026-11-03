"""The telemetry_joins projection of config/lambda/ducklake/field_semantics.yaml (PLAN-telemetry-verification-verbs)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from scripts import schema_to_field_semantics as generator
from scripts.telemetry_verify.joins import load_telemetry_joins

ROOT = Path(__file__).resolve().parents[1]


def test_telemetry_joins_projected_from_the_contract() -> None:
    oracle = [
        dict(zip(("join_id", "child_table", "child_column", "parent_table", "parent_column"), join, strict=True))
        for join in sorted(load_telemetry_joins())
    ]
    assert generator._telemetry_joins() == oracle and oracle
    committed = yaml.safe_load((ROOT / "config" / "lambda" / "ducklake" / "field_semantics.yaml").read_text("utf-8"))
    assert committed["telemetry_joins"] == oracle


def test_the_generator_does_not_import_the_verification_package() -> None:
    code = "import sys; import scripts.schema_to_field_semantics; print(any('telemetry_verify' in m for m in sys.modules))"
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.stdout.strip() == "False", done.stderr[-300:]
