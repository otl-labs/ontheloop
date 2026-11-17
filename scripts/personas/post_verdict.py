"""The post verb: one completed check run per route on the resolved head SHA, from whatever result files exist.

Conclusion mapping (audit Q4): NO_FLAGS, FLAGS_FOUND and PROCEED are success; BLOCK and REVISE are failure; a failure
value, a missing, unparseable or schema-invalid result, a timed-out run or an author off the allowlist is failure.
Never neutral, skipped or action_required. The check name is persona-verdict/<route id>, never a gated persona's name
for a probe route (Decision 264). A POST that still fails after retries is a shell fault (non-zero exit).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema

from scripts.personas import execute, resolve_pr

SUCCESS_VERDICTS = frozenset({"NO_FLAGS", "FLAGS_FOUND", "PROCEED"})
FAILURE_VERDICTS = frozenset({"BLOCK", "REVISE"})
TEXT_LIMIT = 60000
RETRIES = 3


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def judge(root: Path, route_id: str, result: Any, allowlisted: bool) -> tuple[str, str, str]:
    """Return (conclusion, title, summary) for one route."""
    if not allowlisted:
        return "failure", "author not on allowlist", "No persona ran: the pull request author is not an admin or maintainer."
    if not isinstance(result, dict) or result.get("route") != route_id:
        return "failure", "no result", "The run produced no usable result for this route."
    if result.get("failure"):
        return "failure", f"failed: {result['failure']}", f"The route failed before a verdict: {result['failure']}."
    if result.get("timed_out"):
        return "failure", "timed out", "The persona run exceeded its time budget."
    if result.get("exit_code") != 0:
        return "failure", "persona exited non-zero", f"Exit code {result.get('exit_code')}."
    try:
        out = json.loads(str(result.get("stdout") or ""))
    except ValueError:
        return "failure", "unparseable output", "The persona output is not JSON."
    schema = execute.schema_for(root, route_id, "output")
    try:
        if schema is None:
            raise ValueError("no output schema")
        jsonschema.validate(out, schema)
    except (ValueError, jsonschema.ValidationError):
        return "failure", "output failed its schema", "The persona output does not match its output schema."
    if out.get("outcome") != "success":
        failure = str(out.get("failure") or "unknown")
        return "failure", f"failed: {failure}", str(out.get("detail") or failure)[:TEXT_LIMIT]
    verdict = str(out.get("verdict"))
    if verdict in SUCCESS_VERDICTS:
        return "success", f"verdict {verdict}", f"Verdict: {verdict}."
    if verdict in FAILURE_VERDICTS:
        return "failure", f"verdict {verdict}", f"Verdict: {verdict}."
    return "failure", "unknown verdict", "The verdict is not in the conclusion mapping."


def post_check(
    repo: str,
    token: str | None,
    head_sha: str,
    name: str,
    conclusion: str,
    title: str,
    summary: str,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    body = {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": title[:200], "summary": summary[:TEXT_LIMIT]},
    }
    status = 0
    for attempt in range(RETRIES):
        status, _ = resolve_pr.request("POST", f"/repos/{repo}/check-runs", token, body)
        if status == 201:
            return
        if attempt < RETRIES - 1:
            sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"check-run POST for {name} failed with {status}")


def post_all(root: Path, work: Path, repo: str, token: str | None, sleep: Callable[[float], None] = time.sleep) -> list[str]:
    resolution = _read(work / "prepared" / "resolution.json")
    routes_doc = _read(work / "prepared" / "routes.json")
    if not isinstance(resolution, dict) or not isinstance(routes_doc, dict):
        raise RuntimeError("the prepared artifact is missing: no resolution to post against")
    if not resolution.get("resolved"):
        return []
    posted = []
    for route_id in routes_doc.get("routes", []):
        result = _read(work / "results" / f"{route_id}.json")
        conclusion, title, summary = judge(root, route_id, result, bool(resolution.get("allowlisted")))
        name = f"persona-verdict/{route_id}"
        post_check(repo, token, resolution["head_sha"], name, conclusion, title, summary, sleep)
        posted.append(name)
    return posted


def main(argv: list[str]) -> int:
    posted = post_all(execute.ROOT, Path.cwd(), os.environ["GITHUB_REPOSITORY"], os.environ.get("GH_TOKEN"))
    print(f"PERSONA-VERDICT posted={len(posted)}")
    return 0
