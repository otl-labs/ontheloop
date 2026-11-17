"""The execute verb (run job): run each route's persona on its materialised input and record the result as data.

Inputs are read from prepared/, validated against the route's input schema (jsonschema) before anything runs, and the
persona runs in a subprocess with a minimal environment that holds no token. Results are written to results/<route>.json
as {route, exit_code, timed_out, stdout} and never parsed here; output validation is post's job. Later add plans
register their schemas in SCHEMAS.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
PROBE_PREFIX = "verdict_probe."
PROBE_SCHEMA = "tests/fixtures/persona_verdict/probe_schema.json"
PROBE_SCRIPT = "tests/fixtures/persona_verdict/probe.py"
SCHEMAS: dict[str, dict[str, Any]] = {}
DEFAULT_TIMEOUT = 180.0


def schema_for(root: Path, route_id: str, kind: str) -> dict[str, Any] | None:
    if route_id.startswith(PROBE_PREFIX):
        return json.loads((root / PROBE_SCHEMA).read_text(encoding="utf-8"))[kind]
    registered = SCHEMAS.get(route_id)
    return registered[kind] if registered else None


def _env() -> dict[str, str]:
    return {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ}


def _command(root: Path, route_id: str, input_path: Path) -> list[str]:
    if route_id.startswith(PROBE_PREFIX):
        return [sys.executable, "-I", str(root / PROBE_SCRIPT), str(input_path)]
    raise ValueError(f"no runner for {route_id}")


def run_route(root: Path, work: Path, route_id: str, timeout: float) -> dict[str, Any]:
    prepared = work / "prepared"
    failure_file = prepared / f"failure-{route_id}.json"
    if failure_file.exists():
        data = json.loads(failure_file.read_text(encoding="utf-8"))
        return {"route": route_id, "failure": data.get("failure", "input_unusable")}
    input_path = prepared / f"input-{route_id}.json"
    schema = schema_for(root, route_id, "input")
    try:
        body = json.loads(input_path.read_text(encoding="utf-8"))
        if schema is None:
            raise ValueError("no input schema")
        jsonschema.validate(body, schema)
    except (OSError, ValueError, jsonschema.ValidationError):
        return {"route": route_id, "failure": "input_unusable"}
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            _command(root, route_id, input_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env(),
            timeout=timeout,
            cwd=str(work),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"route": route_id, "exit_code": None, "timed_out": True, "stdout": ""}
    return {"route": route_id, "exit_code": done.returncode, "timed_out": False, "stdout": done.stdout}


def execute(root: Path, work: Path, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    routes = json.loads((work / "prepared" / "routes.json").read_text(encoding="utf-8"))["routes"]
    results_dir = work / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for route_id in routes:
        result = run_route(root, work, route_id, timeout)
        (results_dir / f"{route_id}.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
        results.append(result)
    return results


def main(argv: list[str]) -> int:
    execute(ROOT, Path.cwd())
    return 0
