"""Lambda handler: Scheduled agent dispatcher.

Reads schedule.yaml, determines which agents are due, invokes each agent
via the configured inference provider, and writes findings to S3.

Environment variables
---------------------
GITHUB_PAT             : Local-only GitHub PAT used by the ``github-models`` provider.
S3_LOG_BUCKET          : S3 bucket name for writing agent findings
    (e.g. ``agent-platform-data-lake``).
SCHEDULED_AGENT_MODEL  : Optional model override.

Providers
---------
- ``github-models``: GitHub Models API (local/legacy; not for Lambda).
- ``copilot-sdk`` / ``gemini``: retired per Decision 116 (supersedes
  Decision 49). Agents still configured with either provider in
  schedule.yaml raise ``RetiredProviderError`` and are recorded as failed;
  migration is owned by PLAN-resolve-scheduled-agent-provider / T4.3.

Bedrock dispatch was retired per CD.28 (T1.15 sweep).

Lambda trigger
--------------
Invoked by EventBridge (CloudWatch Events). The handler checks which
agents are due at the current UTC time and runs them sequentially.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# In Lambda the package root is /var/task; locally it's the repo root.
_HANDLER_DIR = Path(__file__).parent
_REPO_ROOT = _HANDLER_DIR.parent.parent.parent  # src/data/handlers -> repo root


def _get_github_pat() -> str:
    """Return the local-only GITHUB_PAT env var; the Lambda path has no PAT since rec-4539 (GitHub Models retired)."""
    return os.environ.get("GITHUB_PAT", "").strip()


def _load_prompt(prompt_path: str) -> str:
    """Read a prompt file, checking repo root then /var/task."""
    candidates = [
        _REPO_ROOT / prompt_path,
        Path("/var/task") / prompt_path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    logger.error("Prompt file not found: %s", prompt_path)
    return ""


def _load_manifest() -> list[dict[str, Any]]:
    """Load the agent schedule manifest from repo root or /var/task."""
    from scripts.run_scheduled_agent import load_manifest

    candidates = [
        _REPO_ROOT / ".github" / "agents" / "schedule.yaml",
        Path("/var/task") / ".github" / "agents" / "schedule.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return load_manifest(candidate)
    logger.error("schedule.yaml not found")
    return []


class RetiredProviderError(RuntimeError):
    """Raised when an agent's schedule.yaml ``provider`` field names a retired provider."""


def _preload_rec_curator_context(prompt_text: str) -> str:
    """Inject recommendation and retro data into the rec-curator prompt.

    Reads open recommendations via the DuckLake closed boundary (Decision 81 cl.7 /
    T2.19 cutover): make_reader().current_state("ops_recommendations", ...).
    On reader failure, degrades gracefully to an empty list with a loud warning.

    Retro entries are still read from S3 (no warehouse table for retro data).

    Files/views injected:
      - DuckLake reader (open recs via closed boundary)
      - logs/.retro-lite-log.jsonl        (S3 or local via s3_log_store)
      - docs/ROADMAP-PLATFORM.yaml        (platform tier items -- Lambda filesystem at /var/task or repo root)
    """
    from scripts.s3_log_store import read_jsonl  # noqa: PLC0415
    from src.common.ducklake_reader_client import make_reader  # noqa: PLC0415

    open_recs: list = []
    try:
        rows = make_reader().current_state("ops_recommendations", row_filter="status = 'open'")
        if rows is not None:
            open_recs = rows
        logger.info("rec-curator context: %d open recs from DuckLake reader", len(open_recs))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "rec-curator context: DuckLake reader unreachable (%s); degrading to empty recs list "
            "(Decision 81 cl.7 -- no fallback backend)",
            exc,
        )

    retro = read_jsonl(".retro-lite-log.jsonl")
    logger.info("rec-curator context: %d retro entries", len(retro))

    def _read_roadmap(filename: str) -> str:
        for candidate in [Path(f"/var/task/docs/{filename}"), _REPO_ROOT / "docs" / filename]:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        logger.warning("%s not found; rec-curator will run without it", filename)
        return f"({filename} not available)"

    roadmap_platform_text = _read_roadmap("ROADMAP-PLATFORM.yaml")

    recs_text = "\n".join(json.dumps(r, ensure_ascii=False) for r in open_recs) or "(empty)"
    retro_text = "\n".join(json.dumps(r, ensure_ascii=False) for r in retro) or "(empty)"

    preamble = (
        "## Data Pre-loaded by Lambda Handler\n\n"
        "The following data has been loaded for you. "
        "Do NOT issue any bash commands to read files -- use this data directly.\n\n"
        "### Open Recommendations (from DuckLake reader via closed boundary)\n"
        "```\n"
        f"{recs_text}\n"
        "```\n\n"
        "### logs/.retro-lite-log.jsonl\n"
        "```\n"
        f"{retro_text}\n"
        "```\n\n"
        "### docs/ROADMAP-PLATFORM.yaml (platform tier items)\n"
        "```\n"
        f"{roadmap_platform_text}\n"
        "```\n\n"
        "---\n\n"
    )

    return preamble + prompt_text


def _invoke_github_models(prompt_text: str, model: str, pat: str) -> tuple[str, bool, str]:
    """Invoke GitHub Models API. Returns (output, error, message)."""
    from scripts.llm.github_models_client import chat_completion

    response = chat_completion(prompt=prompt_text, model=model, api_key=pat)
    if response.get("error"):
        return "", True, response.get("message", "")
    try:
        output = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return "", True, f"Response missing content: {exc}"
    return output, False, ""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point.

    Runs all agents due at the current UTC time and writes their findings
    to S3 using the convention ``agents/{name}/{timestamp}.jsonl``.

    Routes inference by the agent's ``provider`` field:
    - ``github-models``: uses GitHub Models API (local/legacy; the
      absent-field default)
    - ``copilot-sdk`` / ``gemini``: retired per Decision 116 -- raises
      ``RetiredProviderError``, recorded as a failed invocation (no silent
      misroute); migration owned by PLAN-resolve-scheduled-agent-provider.

    Bedrock dispatch was retired per CD.28.

    Args:
        event: EventBridge event payload (unused but required by Lambda).
        context: Lambda context object (unused).

    Returns:
        Summary dict with keys: ``agents_run``, ``agents_failed``,
        ``total_findings``, ``keys_written``.
    """
    if os.environ.get("SCHEDULED_AGENTS_ENABLED", "false").lower() != "true":
        logger.info("Scheduled agents are disabled (SCHEDULED_AGENTS_ENABLED not set to 'true')")
        return {"status": "disabled", "message": "Scheduled agents dispatcher is disabled"}

    from scripts.run_scheduled_agent import is_agent_due, parse_findings
    from scripts.s3_log_store import write_timestamped_findings

    now = datetime.now(timezone.utc)
    logger.info("Dispatcher invoked at %s UTC", now.isoformat())

    agents = _load_manifest()
    model_override = os.environ.get("SCHEDULED_AGENT_MODEL", "")

    # Allow forcing a specific agent via event payload (for testing / manual runs).
    # EventBridge payloads won't include this field; only manual invocations will.
    force_agent: str = event.get("force_agent", "").strip()
    if force_agent:
        due_agents = [a for a in agents if a["name"] == force_agent]
        logger.info("Force-running agent '%s' (bypassing cron check)", force_agent)
    else:
        due_agents = [a for a in agents if is_agent_due(a, now)]
        logger.info("%d agent(s) due at %s", len(due_agents), now.strftime("%H:%M UTC"))

    # Resolve PAT lazily and cache lookup result for github-models agents.
    pat: str = ""
    pat_checked = False

    agents_run = 0
    agents_failed = 0
    total_findings = 0
    keys_written: list[str] = []

    for agent in due_agents:
        name: str = agent["name"]

        if not agent.get("enabled", True):
            logger.info("Agent '%s' is disabled, skipping", name)
            continue

        model: str = model_override or agent.get("model", "gpt-4.1-mini")
        prompt_path: str = agent.get("prompt_path", "")
        provider: str = agent.get("provider", "github-models")

        logger.info(
            "Running agent '%s' with model=%s provider=%s",
            name,
            model,
            provider,
        )

        prompt_text = _load_prompt(prompt_path)
        if not prompt_text:
            logger.error(
                "Skipping agent '%s': prompt not found at %s",
                name,
                prompt_path,
            )
            agents_failed += 1
            continue

        if provider in ("copilot-sdk", "gemini"):
            try:
                raise RetiredProviderError(
                    f"provider '{provider}' retired per Decision 116 (supersedes Decision 49); "
                    "migrate per PLAN-resolve-scheduled-agent-provider / T4.3"
                )
            except RetiredProviderError as exc:
                logger.error("Skipping agent '%s': %s", name, exc)
                agents_failed += 1
                continue

        if not pat_checked:
            pat = _get_github_pat()
            pat_checked = True
        if not pat:
            logger.error("Skipping agent '%s': GitHub PAT not available", name)
            agents_failed += 1
            continue
        output, has_error, err_msg = _invoke_github_models(prompt_text, model, pat)

        if has_error:
            logger.error("Agent '%s' API call failed: %s", name, err_msg)
            agents_failed += 1
            continue

        findings = parse_findings(output)
        for finding in findings:
            finding.setdefault("agent", name)
            finding.setdefault("timestamp", now.isoformat())

        key = write_timestamped_findings(name, findings)
        if key:
            keys_written.append(key)
            total_findings += len(findings)
            agents_run += 1
            logger.info(
                "Agent '%s': %d findings written to %s",
                name,
                len(findings),
                key,
            )
        else:
            logger.error("Agent '%s': failed to write findings to S3", name)
            agents_failed += 1

    summary = {
        "agents_run": agents_run,
        "agents_failed": agents_failed,
        "total_findings": total_findings,
        "keys_written": keys_written,
    }
    logger.info("Dispatcher complete: %s", json.dumps(summary))
    return summary
