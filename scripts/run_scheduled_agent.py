"""Scheduled agent manifest tooling and Lambda trigger/smoke CLI.

Reads .github/agents/schedule.yaml for manifest listing, cron evaluation,
and dry-run validation. Live LOCAL inference was retired per CD.28 (the
direct Bedrock path is gone): ``--agent NAME`` without ``--dry-run`` and
``--due`` now fail loudly; run agents via the deployed dispatcher with
``--trigger-lambda NAME`` instead. The LiteLLM dispatch rebuild lands with
PLAN-resolve-scheduled-agent-provider.

Usage
-----
python -m scripts.run_scheduled_agent --list
python -m scripts.run_scheduled_agent --agent doc-freshness --dry-run
python -m scripts.run_scheduled_agent --trigger-lambda doc-freshness
python -m scripts.run_scheduled_agent --smoke-test doc-freshness
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent
_MANIFEST_PATH = _REPO_ROOT / ".github" / "agents" / "schedule.yaml"

# Optional model override (e.g. set in CI to force a specific model)
_MODEL_OVERRIDE = os.getenv("SCHEDULED_AGENT_MODEL")


# ---------------------------------------------------------------------------
# Cron helpers
# ---------------------------------------------------------------------------


def _match_cron_field(field: str, value: int, min_val: int, max_val: int) -> bool:
    """Return True if *value* matches the cron *field* expression.

    Supports ``*`` (wildcard), exact integers, and comma-separated lists only.
    Step syntax (``*/N``) and range syntax (``1-5``) are NOT implemented.

    Args:
        field: Cron field expression string.
        value: The actual time value to test.
        min_val: Minimum valid value for this field (inclusive).
        max_val: Maximum valid value for this field (inclusive).
    """
    if value < min_val or value > max_val:
        return False
    if field == "*":
        return True
    parts = field.split(",")
    for part in parts:
        try:
            if int(part) == value:
                return True
        except ValueError:
            pass
    return False


def is_agent_due(agent: dict[str, Any], now: datetime) -> bool:
    """Return True if the agent's cron expression matches *now* (UTC, minute precision).

    Cron expression order: minute hour day-of-month month day-of-week
    GitHub Actions uses 1-based day-of-week (1=Monday ... 7=Sunday).
    Python's weekday() returns 0=Monday ... 6=Sunday, so map: isoweekday() gives 1-7.
    """
    cron = agent.get("cron", "")
    parts = cron.strip().split()
    if len(parts) != 5:
        logger.warning("Agent %r has malformed cron %r — skipping", agent.get("name"), cron)
        return False

    minute_field, hour_field, dom_field, month_field, dow_field = parts

    return (
        _match_cron_field(minute_field, now.minute, 0, 59)
        and _match_cron_field(hour_field, now.hour, 0, 23)
        and _match_cron_field(dom_field, now.day, 1, 31)
        and _match_cron_field(month_field, now.month, 1, 12)
        and _match_cron_field(dow_field, now.isoweekday(), 1, 7)
    )


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def load_manifest(path: Path = _MANIFEST_PATH) -> list[dict[str, Any]]:
    """Load and return the list of agent definitions from the schedule manifest.

    Returns an empty list if the file does not exist, to allow --list on fresh
    installs without crashing.
    """
    if not path.exists():
        logger.warning("Manifest not found at %s", path)
        return []
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    agents: list[dict[str, Any]] = data.get("agents", [])
    return agents


# ---------------------------------------------------------------------------
# Agent execution
# ---------------------------------------------------------------------------


def parse_findings(output: str) -> list[dict[str, Any]]:
    """Parse agent output text into a list of finding dicts.

    Expects the output to be a JSON array. If parsing fails or the output is
    not an array, wraps the raw text as a single ``{"raw": output}`` entry.

    Args:
        output: Raw text output from the agent.

    Returns:
        List of finding dicts. Empty list if output is empty.
    """
    if not output.strip():
        return []
    try:
        parsed = json.loads(output)
        if isinstance(parsed, list):
            return parsed
        return [{"raw": output}]
    except (json.JSONDecodeError, ValueError):
        return [{"raw": output}]


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------


def run_agent(agent: dict[str, Any], *, dry_run: bool = False) -> bool:
    """Validate *agent* and report on the retired local invocation path.

    The local direct-inference path invoked the Bedrock Converse API, which
    was retired per CD.28 (T1.15 sweep). Live local invocation now fails
    loudly; use ``--trigger-lambda NAME`` to run an agent via the deployed
    dispatcher, or ``--dry-run`` to validate manifest entries. The dispatch
    rebuild onto LiteLLM lands with PLAN-resolve-scheduled-agent-provider.

    Returns True for the disabled-skip and dry-run paths, False otherwise.
    """
    name: str = agent["name"]

    if not agent.get("enabled", True):
        logger.info("Agent '%s' is disabled, skipping", name)
        print(f"Agent '{name}' is disabled, skipping")
        return True

    prompt_path_str: str = agent["prompt_path"]
    model: str = _MODEL_OVERRIDE or agent.get("model", "gpt-5-mini")

    prompt_file = (_REPO_ROOT / prompt_path_str).resolve()
    if not prompt_file.is_relative_to(_REPO_ROOT.resolve()):
        logger.error("Prompt path escapes repo root for agent %r: %s", name, prompt_path_str)
        return False
    if not prompt_file.exists():
        logger.error("Prompt file not found for agent %r: %s", name, prompt_file)
        return False

    prompt_text = prompt_file.read_text(encoding="utf-8")

    if dry_run:
        print(f"[dry-run] Would invoke agent '{name}' with model={model}")
        print(f"[dry-run] Prompt path: {prompt_file}")
        print(f"[dry-run] Prompt length: {len(prompt_text)} chars")
        return True

    logger.error(
        "Agent '%s': local direct invocation retired per CD.28 (Bedrock left the architecture). "
        "Use --trigger-lambda %s to run via the deployed dispatcher, or --dry-run to validate.",
        name,
        name,
    )
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _trigger_lambda(agent_name: str) -> int:
    """Invoke the scheduled-agent dispatcher Lambda ad-hoc for a given agent.

    Constructs a ``aws lambda invoke`` command with a ``force_agent`` payload,
    executes it via subprocess.run(), and prints a summary of the response.

    Args:
        agent_name: Name of the agent to force-trigger (e.g. ``"rec-curator"``).

    Returns:
        0 on success, 1 on failure.
    """
    function_name = "agent-platform-scheduled-agent-dispatcher"
    payload = json.dumps({"force_agent": agent_name})
    from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

    profile = resolve_aws_profile()

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json")
    os.close(tmp_fd)
    try:
        cmd = [
            "aws",
            "lambda",
            "invoke",
            "--function-name",
            function_name,
            "--payload",
            payload,
            "--cli-binary-format",
            "raw-in-base64-out",
            "--cli-read-timeout",
            "900",
            *(["--profile", profile] if profile else []),
            tmp_path,
        ]
        logger.info("Triggering Lambda %s for agent '%s'", function_name, agent_name)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            logger.error("Lambda invoke failed (exit %d): %s", result.returncode, result.stderr)
            return 1
        # Read Lambda response payload
        try:
            with open(tmp_path, encoding="utf-8", errors="replace") as fh:
                response_body = fh.read().strip()
            if response_body:
                try:
                    parsed = json.loads(response_body)
                    print(json.dumps(parsed, indent=2))
                except json.JSONDecodeError:
                    print(response_body)
            else:
                print("(empty Lambda response body)")
        except OSError as exc:
            logger.warning("Could not read Lambda response file: %s", exc)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("Lambda invocation error: %s", exc)
        return 1
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _smoke_test(agent_name: str) -> int:
    """Run a full deploy-invoke-verify smoke test for a scheduled agent.

    Sequence:
      1. Build and deploy Lambda via ``scripts.build_lambda --deploy``.
      2. Invoke the dispatcher Lambda with ``{"force_agent": agent_name}``.
      3. Verify a fresh object exists in the agent log bucket under
         ``agents/<name>/`` with LastModified within 60 seconds.

    Delivery contracts:
      - docs/contracts/inference-provider.yaml: post-deploy verification
      - scripts/build_lambda.py: supported deploy mechanism

    Returns 0 on success, non-zero on any failure.
    """
    # Step 1: Build and deploy
    logger.info("Smoke test: building and deploying Lambda package...")
    deploy_cmd = [sys.executable, "-m", "scripts.build_lambda", "--deploy"]
    deploy_result = subprocess.run(
        deploy_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if deploy_result.returncode != 0:
        logger.error(
            "Smoke test: build/deploy failed (exit %d): %s",
            deploy_result.returncode,
            deploy_result.stderr,
        )
        return 1

    logger.info("Smoke test: deploy succeeded.")

    # Step 2: Invoke the dispatcher Lambda
    invoke_rc = _trigger_lambda(agent_name)
    if invoke_rc != 0:
        logger.error("Smoke test: Lambda invocation failed.")
        return 2

    logger.info("Smoke test: Lambda invocation succeeded.")

    # Step 3: Verify fresh S3 log object
    bucket = "agent-platform-agent-logs"
    prefix = f"agents/{agent_name}/"
    from scripts.aws_profile import resolve_aws_profile  # noqa: PLC0415

    profile = resolve_aws_profile()
    region = "eu-west-2"

    verify_cmd = [
        "aws",
        "s3api",
        "list-objects-v2",
        "--bucket",
        bucket,
        "--prefix",
        prefix,
        "--query",
        "sort_by(Contents, &LastModified)[-1]",
        *(["--profile", profile] if profile else []),
        "--region",
        region,
        "--output",
        "json",
    ]
    verify_result = subprocess.run(
        verify_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if verify_result.returncode != 0:
        logger.error(
            "Smoke test: S3 verification failed (exit %d): %s",
            verify_result.returncode,
            verify_result.stderr,
        )
        return 3

    # Parse the latest object and check freshness
    try:
        obj = json.loads(verify_result.stdout)
    except (json.JSONDecodeError, ValueError):
        logger.error(
            "Smoke test: could not parse S3 response: %s",
            verify_result.stdout[:200],
        )
        return 3

    if not obj or not isinstance(obj, dict):
        logger.error(
            "Smoke test: no objects found in s3://%s/%s",
            bucket,
            prefix,
        )
        return 3

    last_modified_str = obj.get("LastModified", "")
    if not last_modified_str:
        logger.error("Smoke test: LastModified missing from S3 object.")
        return 3

    try:
        last_modified = datetime.fromisoformat(last_modified_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        logger.error(
            "Smoke test: could not parse LastModified: %s",
            last_modified_str,
        )
        return 3

    now = datetime.now(timezone.utc)
    age_seconds = (now - last_modified).total_seconds()
    if age_seconds > 60:
        logger.error(
            "Smoke test: latest log object is %.0fs old (max 60s). Key: %s",
            age_seconds,
            obj.get("Key", "?"),
        )
        return 3

    logger.info(
        "Smoke test PASSED: fresh log object (%.0fs old) at %s",
        age_seconds,
        obj.get("Key", "?"),
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_scheduled_agent",
        description="Scheduled agent dispatcher. Reads schedule.yaml and runs agents.",
    )
    p.add_argument("--list", action="store_true", help="Print all agents from the manifest")
    p.add_argument("--agent", metavar="NAME", help="Run a specific agent by name")
    p.add_argument(
        "--due",
        action="store_true",
        help="Run all agents whose cron expression matches the current UTC time",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be invoked without making any calls",
    )
    p.add_argument(
        "--trigger-lambda",
        metavar="AGENT",
        help="Invoke the dispatcher Lambda ad-hoc for the given agent name",
    )
    p.add_argument(
        "--smoke-test",
        metavar="NAME",
        help="Deploy, invoke, and verify a scheduled agent end-to-end",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = _build_parser()
    args = parser.parse_args(argv)

    agents = load_manifest()

    if args.smoke_test:
        if not re.match(r"^[a-z0-9-]+$", args.smoke_test):
            print(f"Invalid agent name '{args.smoke_test}'. Names must match [a-z0-9-]+.")
            return 1
        return _smoke_test(args.smoke_test)

    if args.trigger_lambda:
        if not re.match(r"^[a-z0-9-]+$", args.trigger_lambda):
            print(f"Invalid agent name '{args.trigger_lambda}'. Names must match [a-z0-9-]+.")
            return 1
        return _trigger_lambda(args.trigger_lambda)

    if args.list:
        if not agents:
            print("No agents found in manifest.")
            return 0
        for agent in agents:
            print(f"  {agent['name']:<20}  model={agent.get('model', '?'):<20}  cron={agent.get('cron', '?')}")
            print(f"    {agent.get('description', '')}")
        return 0

    if args.agent:
        if not re.match(r"^[a-z0-9-]+$", args.agent):
            print(f"Invalid agent name '{args.agent}'. Names must match [a-z0-9-]+.")
            return 1
        matching = [a for a in agents if a["name"] == args.agent]
        if not matching:
            print(f"No agent named '{args.agent}'. Use --list to see available agents.")
            return 1
        success = run_agent(matching[0], dry_run=args.dry_run)
        return 0 if success else 1

    if args.due:
        now = datetime.now(timezone.utc)
        due_agents = [a for a in agents if is_agent_due(a, now)]
        if not due_agents:
            print(f"No agents due at {now.strftime('%Y-%m-%d %H:%M UTC')}")
            return 0
        print(f"Running {len(due_agents)} agent(s) due at {now.strftime('%Y-%m-%d %H:%M UTC')}:")
        failures = 0
        for agent in due_agents:
            print(f"  -> {agent['name']}")
            if not run_agent(agent, dry_run=args.dry_run):
                failures += 1
        return failures

    # No mode selected
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
