"""Run one persona in a locked-down container from the persona image and return its result.

Never mounts the workspace, .git, home or the Docker socket; never --privileged, host PID or an --env-file. The key
is passed by variable name only: docker inherits the value from this process's environment.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_IMAGE_REF = re.compile(r"^(?:[a-z0-9][a-z0-9./_-]*@)?sha256:[0-9a-f]{64}$")
_PERSONA = re.compile(r"^[a-z][a-z0-9_]*$")
_KEY_ROW_PREFIX = "ports.run_persona.litellm.api_key"
_IN_MOUNT = "/run/persona/in"
_EVENTS_MOUNT = "/run/persona/events"


@dataclass(frozen=True)
class RunImageResult:
    exit_code: int
    result: dict[str, Any] | None
    events: dict[str, Any] | None
    stderr: str


def _load(relative: str) -> dict[str, Any]:
    data = yaml.safe_load((_ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{relative} is not a YAML mapping")
    return data


def _key_variables() -> set[str]:
    rows = _load("docs/contracts/project-configuration.yaml").get("settings") or []
    return {r["variable"] for r in rows if str(r.get("id", "")).startswith(_KEY_ROW_PREFIX) and r.get("variable")}


def _base_url_variables() -> set[str]:
    tiers = _load("docs/contracts/inference-provider.yaml")["model_id_formats"]["litellm_tier_models"]
    names = set()
    for row in tiers.values():
        model = row.get("default_model") if isinstance(row, dict) else None
        if isinstance(model, str) and "/" in model:
            names.add(model.split("/", 1)[0].upper() + "_API_BASE")
    return names


def run_argv(
    image_ref: str,
    persona: str,
    in_dir: str,
    events_dir: str,
    *,
    key_variable: str | None,
    base_url_variable: str | None = None,
    network: str = "bridge",
    docker: str = "docker",
    entrypoint: list[str] | None = None,
) -> list[str]:
    if not _IMAGE_REF.match(image_ref):
        raise ValueError(f"image_ref must be <name>@sha256:<64 hex> or a sha256 image id, not a tag: {image_ref!r}")
    if not _PERSONA.match(persona):
        raise ValueError(f"persona id {persona!r} does not match {_PERSONA.pattern}")
    if key_variable is not None and key_variable not in _key_variables():
        raise ValueError(f"{key_variable!r} is not a variable the run_persona api_key rows name")
    if base_url_variable is not None and base_url_variable not in _base_url_variables():
        raise ValueError(f"{base_url_variable!r} is not <PREFIX>_API_BASE for a litellm tier default_model")
    argv = [
        docker, "run", "--rm", "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "10001:10001", "--network", network,
        "-v", f"{in_dir}:{_IN_MOUNT}:ro", "-v", f"{events_dir}:{_EVENTS_MOUNT}",
    ]  # fmt: skip
    for variable in (key_variable, base_url_variable):
        if variable is not None:
            argv += ["-e", variable]
    if entrypoint:
        return [*argv, "--entrypoint", entrypoint[0], image_ref, *entrypoint[1:]]
    return [
        *argv, image_ref, "--contract", f"/app/docs/contracts/personas/{persona}.yaml",
        "--input", f"{_IN_MOUNT}/input.json", "--events", f"{_EVENTS_MOUNT}/events.json",
    ]  # fmt: skip


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def run_image(
    image_ref: str,
    persona: str,
    materialised_input: dict[str, Any],
    *,
    key_variable: str | None,
    base_url_variable: str | None = None,
    network: str = "bridge",
    timeout_s: int = 3700,
    docker: str = "docker",
) -> RunImageResult:
    in_dir = tempfile.mkdtemp(prefix="persona-in-")
    events_dir = tempfile.mkdtemp(prefix="persona-events-")
    try:
        os.chmod(in_dir, 0o755)
        input_path = Path(in_dir) / "input.json"
        input_path.write_text(json.dumps(materialised_input), encoding="utf-8")
        os.chmod(input_path, 0o644)
        os.chmod(events_dir, 0o777)
        argv = run_argv(
            image_ref, persona, in_dir, events_dir,
            key_variable=key_variable, base_url_variable=base_url_variable, network=network, docker=docker,
        )  # fmt: skip
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
        except FileNotFoundError as exc:
            raise RuntimeError(f"docker unreachable: {exc}") from exc
        if proc.returncode == 125 and "daemon" in proc.stderr.lower():
            raise RuntimeError(f"docker unreachable: {proc.stderr.strip()}")
        events_file = Path(events_dir) / "events.json"
        events = _parse_json(events_file.read_text(encoding="utf-8")) if events_file.exists() else None
        return RunImageResult(proc.returncode, _parse_json(proc.stdout), events, proc.stderr)
    finally:
        shutil.rmtree(in_dir, ignore_errors=True)
        shutil.rmtree(events_dir, ignore_errors=True)
