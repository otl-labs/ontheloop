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

from scripts.personas import execute, override, resolve_pr

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
    text: str | None = None,
) -> None:
    body = {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": title[:200], "summary": summary[:TEXT_LIMIT]},
    }
    if text is not None:
        body["output"]["text"] = text[:TEXT_LIMIT]
    status = 0
    for attempt in range(RETRIES):
        status, _ = resolve_pr.request("POST", f"/repos/{repo}/check-runs", token, body)
        if status == 201:
            return
        if attempt < RETRIES - 1:
            sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"check-run POST for {name} failed with {status}")


def _escape(value: str) -> str:
    return " ".join(value.replace("<!--", "&lt;!--").split())


def overridable_output(root: Path, route_id: str, result: Any) -> tuple[str, list[str], dict[str, Any]] | None:
    """(verdict, blocking ids, parsed output) of a parsed, schema-valid, overridable verdict result, else None."""
    if not (isinstance(result, dict) and result.get("route") == route_id and override.overridable(route_id)):
        return None
    extract = override.extractor_for(route_id)
    if extract is None or result.get("failure") or result.get("timed_out") or result.get("exit_code") != 0:
        return None
    try:
        out = json.loads(str(result.get("stdout") or ""))
        schema = execute.schema_for(root, route_id, "output")
        if schema is None:
            return None
        jsonschema.validate(out, schema)
    except (ValueError, jsonschema.ValidationError):
        return None
    if not isinstance(out, dict) or out.get("outcome") != "success":
        return None
    verdict, ids = extract(out)
    if verdict not in override.OVERRIDABLE_VERDICTS or not ids:
        return None
    return verdict, sorted(set(ids)), out


def record_text(route_id: str, head_sha: str, verdict: str, ids: list[str], out: dict[str, Any]) -> str:
    lines = [override.render_record(route_id, head_sha, verdict, ids, os.environ.get("GITHUB_RUN_ID", "0"))]
    titles = {
        str(f.get("id")): str(f.get("title") or "")
        for f in out.get("findings") or []
        if isinstance(f, dict) and isinstance(f.get("id"), str)
    }
    for fid in ids:
        lines.append(f"- {_escape(fid).replace(' ', '_')}: {_escape(titles.get(fid, ''))}")
    return "\n".join(lines)


def _override_form(approved: dict[str, Any], verdict: str, ids: list[str], record: str) -> tuple[str, str, str]:
    who = approved["approver"]
    title = f"{verdict} dispositioned by {who}" if verdict == "FLAGS_FOUND" else f"{verdict} overridden by {who}"
    summary = (
        f"Original verdict: {verdict}. Covered ids: {', '.join(ids)}. Approver: {who}. "
        f"Approved at: {approved['approved_at']}. Override run: {approved['url']}."
    )
    marker = {"run_id": approved["run_id"], "approver": who, "approved_at": approved["approved_at"]}
    text = record.split("\n", 1)[0] + "\n" + override.OVERRIDE_PREFIX + json.dumps(marker, sort_keys=True) + " -->"
    return title, summary, text


def post_override(
    resolution: dict[str, Any], repo: str, token: str | None, sleep: Callable[[float], None] = time.sleep
) -> str:
    """The re-run event: one new success check run of the failing check's name, from the stored record."""
    ov = resolution["override"]
    route_id, head = ov["route"], ov["head_sha"]
    try:
        if not override.overridable(route_id):
            raise override.Refused("the route is not overridable")
        rec, _text = override.origin(repo, token, head, route_id, int(ov["check_run_id"]))
        if rec["verdict"] not in override.OVERRIDABLE_VERDICTS or override.ids_digest(rec["blocking_ids"]) != ov["ids_digest"]:
            raise override.Refused("the record does not match the approved override")
    except override.Refused as exc:
        raise RuntimeError(f"override re-check failed: {exc}") from exc
    record = override.render_record(route_id, head, rec["verdict"], rec["blocking_ids"], rec["run_id"])
    title, summary, text = _override_form(ov, rec["verdict"], rec["blocking_ids"], record)
    name = f"persona-verdict/{route_id}"
    post_check(repo, token, head, name, "success", title, summary, sleep, text)
    return name


def post_all(root: Path, work: Path, repo: str, token: str | None, sleep: Callable[[float], None] = time.sleep) -> list[str]:
    resolution = _read(work / "prepared" / "resolution.json")
    routes_doc = _read(work / "prepared" / "routes.json")
    if not isinstance(resolution, dict) or not isinstance(routes_doc, dict):
        raise RuntimeError("the prepared artifact is missing: no resolution to post against")
    if not resolution.get("resolved"):
        return []
    if resolution.get("override"):
        return [post_override(resolution, repo, token, sleep)]
    posted = []
    for route_id in routes_doc.get("routes", []):
        result = _read(work / "results" / f"{route_id}.json")
        allowlisted = bool(resolution.get("allowlisted"))
        conclusion, title, summary = judge(root, route_id, result, allowlisted)
        text: str | None = None
        info = overridable_output(root, route_id, result) if allowlisted else None
        if info is not None:
            verdict, ids, out = info
            text = record_text(route_id, resolution["head_sha"], verdict, ids, out)
            if conclusion == "failure":
                try:
                    approved = override.lookup(
                        repo, token, int(resolution["number"]), resolution["head_sha"], route_id, override.ids_digest(ids)
                    )
                except override.Refused:
                    approved = None
                if approved:
                    conclusion = "success"
                    title, summary, text = _override_form(approved, verdict, ids, text)
        name = f"persona-verdict/{route_id}"
        post_check(repo, token, resolution["head_sha"], name, conclusion, title, summary, sleep, text)
        posted.append(name)
    return posted


def main(argv: list[str]) -> int:
    posted = post_all(execute.ROOT, Path.cwd(), os.environ["GITHUB_REPOSITORY"], os.environ.get("GH_TOKEN"))
    print(f"PERSONA-VERDICT posted={len(posted)}")
    return 0
