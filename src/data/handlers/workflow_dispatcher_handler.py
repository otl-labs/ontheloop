"""Lambda handler: agent-platform-workflow-dispatcher (Decision 267, rec-4233).

An EventBridge rule per register row invokes this with {"schedule": "<row name>"}. The handler mints a short-lived
token from the dispatcher's GitHub App, skips when the workflow already started inside the row's skip window, else
starts it on main through workflow_dispatch, and revokes the token.

Exemption from src/data/handlers/CLAUDE.md's force_{param} rule: a force field that skipped the window check would
start a workflow inside the skip window, which Decision 267 clause 1 excludes; a re-run is simply the next tick. An
event key other than "schedule" is ignored.

Environment: WORKFLOW_DISPATCH_ENABLED must be "true" or the handler returns "disabled" before reading anything
(the SIT copy, Decision 267 clause 4).
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from src.data.handlers import github_app_token

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_BUNDLE_ROOT = Path(__file__).resolve().parents[3]
REGISTER_PATH = _BUNDLE_ROOT / "config" / "lambda" / "scheduled_workflow_dispatcher" / "schedules.yaml"
TOKEN_PERMISSIONS = {"actions": "write", "metadata": "read"}
_WORKFLOW_RE = re.compile(r"^[a-z0-9-]+\.yml$")
_CRON_RE = re.compile(r"^cron\(.+\)$")


def load_register(path: Path | None = None) -> dict[str, Any]:
    register = yaml.safe_load((path or REGISTER_PATH).read_text(encoding="utf-8"))
    if not isinstance(register, dict):
        raise ValueError("register is not a mapping")
    schedules = register.get("schedules")
    if not isinstance(schedules, dict) or not schedules:
        raise ValueError("register has no schedules")
    if register.get("ref") != "main":
        raise ValueError("register ref must be main")
    if not str(register.get("key_parameter", "")).startswith("/secret/"):
        raise ValueError("register key_parameter must be under /secret/")
    app = register.get("app") or {}
    if not str(app.get("bot_login", "")).endswith("[bot]"):
        raise ValueError("register app.bot_login must end in [bot]")
    for name, row in schedules.items():
        if not _WORKFLOW_RE.match(str(row.get("workflow", ""))):
            raise ValueError(f"schedule {name}: workflow must match {_WORKFLOW_RE.pattern}")
        window = row.get("skip_window_seconds")
        if not isinstance(window, int) or not 60 <= window <= 3600:
            raise ValueError(f"schedule {name}: skip_window_seconds must be 60-3600")
        if not _CRON_RE.match(str(row.get("aws_cron", ""))):
            raise ValueError(f"schedule {name}: aws_cron must be cron(...)")
    return register


def _read_key(parameter: str) -> str:
    if not parameter.startswith("/secret/"):
        raise ValueError("key parameter must be under /secret/")
    import boto3

    response = boto3.client("ssm").get_parameter(Name=parameter, WithDecryption=True)
    return str(response["Parameter"]["Value"])


def _log(status: str, schedule: str, workflow: str, run_id: Any) -> None:
    logger.info("status=%s schedule=%s workflow=%s run_id=%s", status, schedule, workflow, run_id or "none")


def handler(event: dict[str, Any], context: Any = None, api: github_app_token.GitHubApi | None = None) -> dict[str, Any]:
    if "schedule" not in event:
        import jwt  # noqa: F401
        from cryptography.hazmat.primitives import serialization  # noqa: F401

        return {"status": "smoke", "schedules": sorted(load_register()["schedules"])}
    if os.environ.get("WORKFLOW_DISPATCH_ENABLED") != "true":
        return {"status": "disabled"}
    register = load_register()
    name = event["schedule"]
    if name not in register["schedules"]:
        raise ValueError(f"unknown schedule {name!r}")
    row = register["schedules"][name]
    repository = register["repository"]
    api = api or github_app_token.GitHubApi()
    pem = _read_key(register["key_parameter"])
    app_token = github_app_token.app_jwt(register["app"]["client_id"], pem, int(time.time()))
    token = github_app_token.installation_token(api, app_token, repository, dict(TOKEN_PERMISSIONS))
    try:
        result = _skip_or_dispatch(api, token, register, name, row)
    except BaseException:
        try:
            github_app_token.revoke(api, token)
        except Exception as exc:  # noqa: BLE001
            logger.warning("status=revoke_failed schedule=%s error=%s", name, type(exc).__name__)
        raise
    github_app_token.revoke(api, token)
    return result


def _skip_or_dispatch(
    api: github_app_token.GitHubApi, token: str, register: dict[str, Any], name: str, row: dict[str, Any]
) -> dict[str, Any]:
    workflow = row["workflow"]
    repository = register["repository"]
    since = datetime.now(timezone.utc) - timedelta(seconds=row["skip_window_seconds"])
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    base = f"/repos/{repository}/actions/workflows/{workflow}"
    _, runs = api.call("GET", f"{base}/runs?created=>={stamp}&per_page=1", token)
    if runs.get("total_count"):
        recent = runs["workflow_runs"][0]["id"] if runs.get("workflow_runs") else None
        _log("skipped", name, workflow, recent)
        return {"status": "skipped", "schedule": name, "workflow": workflow, "recent_run_id": recent}
    _, dispatched = api.call("POST", f"{base}/dispatches", token, {"ref": register["ref"], "return_run_details": True})
    run_id = (dispatched or {}).get("workflow_run_id")
    _log("dispatched", name, workflow, run_id)
    return {"status": "dispatched", "schedule": name, "workflow": workflow, "workflow_run_id": run_id}
