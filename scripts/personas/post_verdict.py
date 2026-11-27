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

from scripts.persona_contracts import findings
from scripts.personas import app_token, execute, findings_record, override, rescout, resolve_pr

SUCCESS_VERDICTS = frozenset({"NO_FLAGS", "FLAGS_FOUND", "PROCEED"})
FAILURE_VERDICTS = frozenset({"BLOCK", "REVISE"})
TEXT_LIMIT = 60000
RETRIES = 3
TITLE_CHARS = 200


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
    failed = isinstance(out, dict) and out.get("outcome") == "failure"
    schema = execute.failure_schema_for(root, route_id) if failed else execute.schema_for(root, route_id, "output")
    try:
        if schema is None:
            raise ValueError("no output schema")
        jsonschema.validate(out, schema)
    except (ValueError, jsonschema.ValidationError):
        return "failure", "output failed its schema", "The persona output does not match its output schema."
    if failed:
        failure = str(out["failure"])
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
) -> dict[str, Any]:
    body: dict[str, Any] = {
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
        status, raw = resolve_pr.request("POST", f"/repos/{repo}/check-runs", token, body)
        if status == 201:
            try:
                created = json.loads(raw)
            except (TypeError, ValueError):
                created = None
            return created if isinstance(created, dict) else {}
        if attempt < RETRIES - 1:
            sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"check-run POST for {name} failed with {status}")


def _escape(value: str) -> str:
    return " ".join(value.replace("<!--", "&lt;!--").split())


READS_HEADER = "Reads"
READS_PREFIX = "  | "
READS_PATHS = 100
READS_PATH_CHARS = 200


def _path_line(path: Any) -> str:
    text = str(path)
    shown = text if text.isprintable() else repr(text)
    return READS_PREFIX + "  " + _escape(shown)[:READS_PATH_CHARS]


def reads_section(result: Any, room: int) -> str | None:
    """The check text's Reads section for a routed result that carries reads or reads_unknown, bounded to room characters."""
    if not isinstance(result, dict) or ("reads" not in result and not result.get("reads_unknown")):
        return None
    lines = [READS_HEADER]
    reads = result.get("reads")
    if isinstance(reads, list):
        lines.append(f"{READS_PREFIX}read_tree calls: {len(reads)}")
        counts: dict[tuple[str, str], int] = {}
        for row in reads:
            if isinstance(row, dict):
                key = (_escape(str(row.get("tree"))), _escape(str(row.get("op"))))
                counts[key] = counts.get(key, 0) + 1
        lines += [f"{READS_PREFIX}{tree} {op}: {n}" for (tree, op), n in sorted(counts.items())]
        if result.get("reads_dropped"):
            lines.append(f"{READS_PREFIX}read records beyond the first 2000 were dropped")
    else:
        lines.append(f"{READS_PREFIX}read records unavailable")
    try:
        out = json.loads(str(result.get("stdout") or ""))
    except ValueError:
        out = None
    if isinstance(out, dict) and out.get("outcome") == "failure" and out.get("failure") == "review_incomplete":
        lines.append(f"{READS_PREFIX}review_incomplete: {_escape(str(out.get('detail') or ''))[:200]}")
    unseen = result.get("not_examined")
    if isinstance(unseen, list):
        lines.append(f"{READS_PREFIX}not_examined: {len(unseen)}")
        lines += [_path_line(path) for path in unseen[:READS_PATHS]]
        if len(unseen) > READS_PATHS:
            lines.append(f"{READS_PREFIX}{len(unseen) - READS_PATHS} more")
    kept: list[str] = []
    used = 0
    for line in lines:
        used += len(line) + 1
        if used > room:
            kept.append(f"{READS_PREFIX}[section cut at the text limit]")
            break
        kept.append(line)
    return "\n".join(kept)


def success_output(root: Path, route_id: str, result: Any) -> dict[str, Any] | None:
    """The parsed output of a route's schema-valid, outcome-success result, else None."""
    if not (isinstance(result, dict) and result.get("route") == route_id):
        return None
    if result.get("failure") or result.get("timed_out") or result.get("exit_code") != 0:
        return None
    try:
        out = json.loads(str(result.get("stdout") or ""))
        schema = execute.schema_for(root, route_id, "output")
        if schema is None:
            return None
        jsonschema.validate(out, schema)
    except (ValueError, jsonschema.ValidationError):
        return None
    return out if isinstance(out, dict) and out.get("outcome") == "success" else None


def overridable_output(root: Path, route_id: str, result: Any) -> tuple[str, list[str], dict[str, Any]] | None:
    """(verdict, blocking ids, parsed output) of a parsed, schema-valid, overridable verdict result, else None."""
    if not (isinstance(result, dict) and result.get("route") == route_id and override.overridable(route_id)):
        return None
    extract = override.extractor_for(route_id)
    out = success_output(root, route_id, result) if extract is not None else None
    if out is None or extract is None:
        return None
    verdict, ids = extract(out)
    if verdict not in override.OVERRIDABLE_VERDICTS or not ids:
        return None
    return verdict, sorted(set(ids)), out


FALLBACK_RAN = ("success", "failure", "cancelled")
FALLBACK_MARK = " (fallback role)"
FALLBACK_NO_RESULT = " (fallback role: no result)"


def record_text(route_id: str, head_sha: str, verdict: str, ids: list[str], out: dict[str, Any]) -> str:
    lines = [override.render_record(route_id, head_sha, verdict, ids, os.environ.get("GITHUB_RUN_ID", "0"))]
    titles = {
        str(f.get("id")): str(f.get("title") or f.get("summary") or "")
        for f in out.get("findings") or []
        if isinstance(f, dict) and isinstance(f.get("id"), str)
    }
    for fid in ids:
        lines.append(f"- {_escape(fid).replace(' ', '_')}: {_escape(titles.get(fid, ''))[:TITLE_CHARS]}")
    return "\n".join(lines)


def outcome_of(root: Path, route_id: str, result: Any) -> tuple[str, str | None, dict[str, Any] | None, dict[str, Any] | None]:
    """(run_outcome, failure_id, restricted result, parsed output) of a fresh route's result, as judge reads it."""
    if not isinstance(result, dict) or result.get("route") != route_id:
        return "host_failure", "no_result", None, None
    if result.get("failure"):
        return "host_failure", _failure_id(result["failure"]), None, None
    if result.get("timed_out"):
        return "host_failure", "timed_out", None, None
    if result.get("exit_code") != 0:
        return "host_failure", "non_zero_exit", None, None
    try:
        out = json.loads(str(result.get("stdout") or ""))
    except ValueError:
        return "host_failure", "unparseable_output", None, None
    failed = isinstance(out, dict) and out.get("outcome") == "failure"
    schema = execute.failure_schema_for(root, route_id) if failed else execute.schema_for(root, route_id, "output")
    try:
        if schema is None:
            raise ValueError("no output schema")
        jsonschema.validate(out, schema)
    except (ValueError, jsonschema.ValidationError):
        return "host_failure", "schema_invalid", None, None
    if failed:
        return "persona_failure", _failure_id(out.get("failure")), None, out
    restricted = findings.restrict_result(route_id, out)
    if restricted is None:
        return "host_failure", "schema_invalid", None, None
    return "verdict", None, restricted, out


def _failure_id(value: Any) -> str:
    return value if isinstance(value, str) and findings_record.SAFE.fullmatch(value) else "unknown"


def _head_count(lines: list[str]) -> int:
    n = 0
    if lines and lines[0].startswith(override.RECORD_PREFIX):
        n = 1
    if len(lines) > n and rescout.INPUT_RE.match(lines[n]):
        n += 1
    return n


def with_findings(text: str | None, line: str) -> str:
    """The text with the findings line after its leading record and input lines."""
    lines = (text or "").split("\n") if text else []
    lines.insert(_head_count(lines), line)
    return "\n".join(lines)


def findings_line(
    root: Path, route_id: str, result: Any, head_sha: str, outcome: tuple[str, str | None, dict[str, Any] | None, Any]
) -> str | None:
    """The findings line of a fresh covered route whose results record carries a run_ref, else None."""
    ref = result.get("run_ref") if isinstance(result, dict) else None
    if route_id not in findings.LISTS or not isinstance(ref, str):
        return None
    run_id, attempt = os.environ.get("GITHUB_RUN_ID", "0"), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    models = result.get("served_models")
    facts = {
        "route": route_id,
        "head_sha": head_sha,
        "run_id": run_id,
        "run_attempt": int(attempt) if attempt.isdigit() else None,
        "run_ref": ref,
        "session_started_at": result.get("session_started_at"),
        "definition_sha256": result.get("definition_sha256"),
        "served_models": [m for m in models if isinstance(m, str)] if isinstance(models, list) else [],
        "run_outcome": outcome[0],
        "failure_id": outcome[1],
    }
    return findings_record.render(facts, outcome[2])


def extra_titles(route_id: str, out: dict[str, Any] | None, shown: list[str], room: int) -> list[str]:
    """'- <id>: <title>' lines for the findings not already in the blocking block, cut to room characters."""
    lines: list[str] = []
    used = 0
    seen = set(shown)
    if out is None:
        return lines
    for spec in findings.LISTS[route_id]:
        for item in findings.items(out, spec.path):
            fid = spec.finding_id(item)
            if fid is None or fid in seen:
                continue
            seen.add(fid)
            title = str(item.get("title") or item.get("summary") or item.get("reason") or "")
            line = f"- {_escape(fid).replace(' ', '_')}: {_escape(title)[:TITLE_CHARS]}"
            used += len(line) + 1
            if used > room:
                return lines
            lines.append(line)
    return lines


def _override_form(
    approved: dict[str, Any], verdict: str, ids: list[str], record: str, source: str | None = None
) -> tuple[str, str, str]:
    who = approved["approver"]
    title = f"{verdict} dispositioned by {who}" if verdict == "FLAGS_FOUND" else f"{verdict} overridden by {who}"
    summary = (
        f"Original verdict: {verdict}. Covered ids: {', '.join(ids)}. Approver: {who}. "
        f"Approved at: {approved['approved_at']}. Override run: {approved['url']}."
    )
    marker = {"run_id": approved["run_id"], "approver": who, "approved_at": approved["approved_at"]}
    kept = rescout.input_line(source if source is not None else record)
    mark = override.OVERRIDE_PREFIX + json.dumps(marker, sort_keys=True) + " -->"
    found = findings_record.line_of(source if source is not None else record)
    text = record.split("\n", 1)[0] + "\n" + (kept + "\n" if kept else "") + (found + "\n" if found else "") + mark
    return title, summary, text


def plan_override(resolution: dict[str, Any], repo: str, read_token: str | None) -> dict[str, Any]:
    """The re-run event: the body of one new success check run of the failing check's name, from the stored record."""
    ov = resolution["override"]
    route_id, head = ov["route"], ov["head_sha"]
    try:
        if not override.overridable(route_id):
            raise override.Refused("the route is not overridable")
        rec, _text = override.origin(repo, read_token, head, route_id, int(ov["check_run_id"]))
        if (
            not rec["blocking_ids"]
            or rec["verdict"] not in override.OVERRIDABLE_VERDICTS
            or override.ids_digest(rec["blocking_ids"]) != ov["ids_digest"]
        ):
            raise override.Refused("the record does not match the approved override")
    except override.Refused as exc:
        raise RuntimeError(f"override re-check failed: {exc}") from exc
    record = override.render_record(route_id, head, rec["verdict"], rec["blocking_ids"], rec["run_id"])
    title, summary, text = _override_form(ov, rec["verdict"], rec["blocking_ids"], record, _text)
    return _body(f"persona-verdict/{route_id}", head, "success", title, summary, text)


def _body(name: str, head_sha: str, conclusion: str, title: str, summary: str, text: str | None) -> dict[str, Any]:
    return {"name": name, "head_sha": head_sha, "conclusion": conclusion, "title": title, "summary": summary, "text": text}


def _carried_body(
    root: Path, resolution: dict[str, Any], entry: dict[str, Any], repo: str, read_token: str | None
) -> dict[str, Any]:
    route_id, head = str(entry["route"]), resolution["head_sha"]
    name = f"persona-verdict/{route_id}"
    if not resolution.get("allowlisted"):
        conclusion, title, summary = judge(root, route_id, None, False)
        return _body(name, head, conclusion, title, summary, None)
    verdict, run_id = str(entry["verdict"]), os.environ.get("GITHUB_RUN_ID", "0")
    ids = sorted({str(i) for i in entry.get("blocking_ids") or []})
    conclusion = "success" if verdict in SUCCESS_VERDICTS else "failure"
    title = f"verdict {verdict} (carried from {str(entry['from_head'])[:7]})"
    summary = f"Verdict: {verdict}. The scout-input fingerprint is unchanged since {entry['from_head']}; no persona ran."
    lines: list[str] = []
    if ids:
        titles = entry.get("titles") or {}
        lines = [override.render_record(route_id, head, verdict, ids, run_id)]
        lines += [f"- {_escape(i).replace(' ', '_')}: {_escape(str(titles.get(i, '')))}" for i in ids]
    line = rescout.render_input(route_id, head, verdict, entry["cites"], entry["fingerprints"], run_id, entry["from_head"])
    text = rescout.with_input("\n".join(lines) if lines else None, line)
    if conclusion == "failure" and ids:
        try:
            approved = override.lookup(repo, read_token, int(resolution["number"]), head, route_id, override.ids_digest(ids))
        except override.Refused:
            approved = None
        if approved:
            conclusion = "success"
            title, summary, text = _override_form(approved, verdict, ids, text)
    return _body(name, head, conclusion, title, summary, text)


def plan_all(root: Path, work: Path, repo: str, read_token: str | None) -> list[dict[str, Any]]:
    """Every check body, built with GH_TOKEN reads only; never touches the App role or key."""
    resolution = _read(work / "prepared" / "resolution.json")
    routes_doc = _read(work / "prepared" / "routes.json")
    if not isinstance(resolution, dict) or not isinstance(routes_doc, dict):
        raise RuntimeError("the prepared artifact is missing: no resolution to post against")
    if not resolution.get("resolved"):
        return []
    if resolution.get("override"):
        return [plan_override(resolution, repo, read_token)]
    bodies = []
    fallback_run = os.environ.get("PERSONA_FALLBACK_RESULT", "") in FALLBACK_RAN
    for route_id in routes_doc.get("routes", []):
        fallback = _read(work / "results-fallback" / f"{route_id}.json")
        result = fallback if fallback is not None else _read(work / "results" / f"{route_id}.json")
        marker = FALLBACK_MARK if fallback is not None else FALLBACK_NO_RESULT if fallback_run else ""
        allowlisted = bool(resolution.get("allowlisted"))
        conclusion, title, summary = judge(root, route_id, result, allowlisted)
        text: str | None = None
        info = overridable_output(root, route_id, result) if allowlisted else None
        if info is not None:
            verdict, ids, out = info
            text = record_text(route_id, resolution["head_sha"], verdict, ids, out)
        fresh = success_output(root, route_id, result) if allowlisted else None
        if fresh is not None:
            line = rescout.fresh_input(
                root, work, route_id, resolution["head_sha"], fresh, os.environ.get("GITHUB_RUN_ID", "0")
            )
            text = rescout.with_input(text, line) if line else text
        if allowlisted and route_id in findings.LISTS:
            outcome = outcome_of(root, route_id, result)
            line = findings_line(root, route_id, result, resolution["head_sha"], outcome)
            if line:
                text = with_findings(text, line)
                shown = list(info[1]) if info is not None else []
                room = TEXT_LIMIT - len(text) - 2
                extra = extra_titles(route_id, outcome[3], shown, room // 2)
                text = "\n".join([text, *extra]) if extra else text
        section = reads_section(result, TEXT_LIMIT - len(text or "") - 2) if allowlisted else None
        if section:
            text = f"{text}\n{section}" if text else section
        if info is not None:
            verdict, ids, out = info
            if conclusion == "failure":
                try:
                    approved = override.lookup(
                        repo,
                        read_token,
                        int(resolution["number"]),
                        resolution["head_sha"],
                        route_id,
                        override.ids_digest(ids),
                    )
                except override.Refused:
                    approved = None
                if approved:
                    conclusion = "success"
                    title, summary, text = _override_form(approved, verdict, ids, text or "")
        bodies.append(_body(f"persona-verdict/{route_id}", resolution["head_sha"], conclusion, title + marker, summary, text))
    for entry in routes_doc.get("carried") or []:
        bodies.append(_carried_body(root, resolution, entry, repo, read_token))
    return bodies


def post_all(
    root: Path, work: Path, repo: str, read_token: str | None, sleep: Callable[[float], None] = time.sleep
) -> list[str]:
    bodies = plan_all(root, work, repo, read_token)
    if not bodies:
        return []
    expected = override.app_id()
    posted: list[str] = []
    with app_token.session(repo) as token:
        for body in bodies:
            created = post_check(
                repo,
                token,
                body["head_sha"],
                body["name"],
                body["conclusion"],
                body["title"],
                body["summary"],
                sleep,
                body["text"],
            )
            if not posted and (created.get("app") or {}).get("id") != expected:
                raise RuntimeError("the created check run is not from the verdict App")
            posted.append(body["name"])
    return posted


def main(argv: list[str]) -> int:
    try:
        posted = post_all(execute.ROOT, Path.cwd(), os.environ["GITHUB_REPOSITORY"], os.environ.get("GH_TOKEN"))
    except app_token.AppTokenError:
        return 1
    except Exception as exc:  # noqa: BLE001 - reported by class only
        print(f"PERSONA-VERDICT FAIL {type(exc).__name__}", flush=True)
        return 1
    print(f"PERSONA-VERDICT posted={len(posted)}")
    return 0
