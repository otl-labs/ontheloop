"""Read-only multi-run read of telemetry_persona_findings for a UTC day window, over both lanes (rec-5742; Decision 199 cl.1).

in_session: the existing telemetry read verbs (root sessions, session trees, the masked content reader), project_result over
the persona run's final text. actions: the verdict App's check runs on the heads of the window's persona-verdict workflow
runs, project_result over the findings line (scripts.personas.findings_record). Prints typed rows and gaps as JSON lines,
never text; it writes nothing and imports no writer. Exit 0 on success, 1 on a typed error line.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.persona_contracts import ROOT, load_index
from scripts.persona_contracts.findings import BLOCK_MARKER, LISTS, FindingRow, RunIdentity, project_result
from scripts.personas import findings_record, resolve_pr
from scripts.session import merge_checks

DEFAULT_REPO = "otl-labs/ontheloop"
WORKFLOW = "persona-verdict.yml"
TITLE = re.compile(r"^persona-verdict pull_request ([0-9a-f]{40})$")
PER_PAGE = 100
MAX_PAGES = 50
MAX_FETCH = 4
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CHECK_PREFIX = "persona-verdict/"
Named = Callable[..., list[dict[str, Any]]]
Get = Callable[[str], tuple[int, bytes]]


class ReadError(RuntimeError):
    """A typed read failure; its message is one fixed class word, never a body, token or value."""


def _registry_keys() -> dict[str, str]:
    """persona id -> registry key for the personas the relation covers."""
    return {r.persona: r.registry_key for r in load_index().personas if r.persona in LISTS}


def _days(day_from: str, day_to: str) -> list[str]:
    start, end = date.fromisoformat(day_from), date.fromisoformat(day_to)
    return [(start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1)]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def decode_entry(text: str) -> str:
    """The text of a stored entry: a JSON object's string values joined by newlines, else the text itself."""
    try:
        doc = json.loads(text)
    except ValueError:
        return text
    return "\n".join(_strings(doc)) if isinstance(doc, (dict, list)) else text


def _final_text(content: Any, tree_session: str, rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """(decoded text, event_id) of the last agent row of the child session holding a result block, else the last read."""
    from scripts.telemetry_content import fetch_row

    agent_rows = [r for r in rows if r.get("origin") == "agent" and r.get("purpose") in ("response", "tool_input")]
    agent_rows.sort(key=lambda r: int(r.get("source_ordinal") or 0), reverse=True)
    last: tuple[str | None, str | None] = (None, None)
    for row in agent_rows[:MAX_FETCH]:
        text, _env = fetch_row(content, tree_session, row["producer"], row["event_id"], int(row["parser_version"]))
        last = (decode_entry(text), str(row["event_id"]))
        if BLOCK_MARKER in last[0]:
            return last
    return last


def read_in_session(named: Named, content: Any, day_from: str, day_to: str) -> list[FindingRow]:
    from scripts.telemetry_verify import rows as tree_rows

    keys = set(_registry_keys().values())
    out: list[FindingRow] = []
    for day in _days(day_from, day_to):
        for root in named("telemetry_root_sessions", day_from=day, day_to=day):
            tables = tree_rows.decode_rows(tree_rows.read_tree(named, str(root["session_id"])))
            seen: set[str] = set()
            for agent in tables.get("telemetry_agents", []):
                run = str(agent.get("agent_run_id"))
                if agent.get("agent_type") not in keys or not agent.get("observation_id") or run in seen:
                    continue
                seen.add(run)
                child = str(agent["session_id"])
                transcripts = [t for t in tables.get("telemetry_transcripts", []) if str(t.get("session_id")) == child]
                text, event_id = _final_text(content, child, transcripts)
                identity = RunIdentity(
                    str(agent["tenant_id"]),
                    str(agent["project_id"]),
                    run,
                    str(agent["agent_type"]),
                    agent.get("definition_sha256"),
                    "in_session",
                    run_ref=str(agent["entity_ref"]),
                )
                out += project_result(identity, text=text, source_event_id=event_id)
    return out


def verdict_app_id(root: Path = ROOT) -> int:
    """gates.verdict_app.app_id of origin/main (Decision 271 cl.1), never the working tree."""
    done = subprocess.run(
        ["git", "show", "origin/main:docs/contracts/project-configuration.yaml"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    value = merge_checks.verdict_app_id(done.stdout) if done.returncode == 0 else None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReadError("no_verdict_app")
    return value


def _paged(get: Get, path: str, key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sep = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        status, raw = get(f"{path}{sep}per_page={PER_PAGE}&page={page}")
        if status != 200:
            raise ReadError("rate_limited" if status in (403, 429) else f"http_{status}")
        try:
            chunk = json.loads(raw).get(key)
        except (ValueError, AttributeError):
            raise ReadError("bad_shape") from None
        if not isinstance(chunk, list):
            raise ReadError("bad_shape")
        rows += [r for r in chunk if isinstance(r, dict)]
        if len(chunk) < PER_PAGE:
            return rows
    raise ReadError("listing_not_complete")


def _record_rows(registry: dict[str, str], head: str, record: dict[str, Any]) -> list[FindingRow]:
    route = str(record.get("route"))
    key = registry.get(route)
    if key is None or record.get("head_sha") != head or not isinstance(record.get("run_ref"), str):
        return []
    digest = record.get("definition_sha256")
    models = record.get("served_models")
    identity = RunIdentity(
        None,
        None,
        None,
        key,
        digest if isinstance(digest, str) and HEX64.match(digest) else None,
        "actions",
        run_ref=record["run_ref"],
        served_model=models[-1] if isinstance(models, list) and models and isinstance(models[-1], str) else None,
    )
    failure = record.get("failure_id")
    result = record.get("result")
    return project_result(
        identity,
        result=result if isinstance(result, dict) else None,
        subject_sha=head,
        run_outcome=record.get("run_outcome") if record.get("run_outcome") in findings_record.RUN_OUTCOMES else None,
        failure_id=failure if isinstance(failure, str) else None,
    )


def _best_records(checks: list[dict[str, Any]], head: str) -> dict[str, tuple[int, dict[str, Any] | None, str]]:
    """One entry per run_ref (highest check id) for the head's verdict-App checks; a record-less route keys as gap:<route>."""
    best: dict[str, tuple[int, dict[str, Any] | None, str]] = {}
    for check in checks:
        output = check.get("output") or {}
        record = findings_record.parse(str(output.get("text") or ""))
        route = str(check["name"])[len(CHECK_PREFIX) :]
        if record is not None and (record.get("route") != route or record.get("head_sha") != head):
            record = None
        if record is None or not isinstance(record.get("run_ref"), str):
            reason = "carried" if "carried from" in str(output.get("title") or "") else "no_record"
            best.setdefault(f"gap:{route}", (check["id"], None, reason))
            continue
        ref = str(record["run_ref"])
        if ref not in best or check["id"] > best[ref][0]:
            best[ref] = (check["id"], record, "")
    return best


def _head_result(
    registry: dict[str, str], head: str, checks: list[dict[str, Any]]
) -> tuple[list[FindingRow], list[dict[str, str]]]:
    best = _best_records(checks, head)
    covered = {str(rec.get("route")) for _cid, rec, _why in best.values() if rec is not None}
    rows: list[FindingRow] = []
    gaps: list[dict[str, str]] = []
    for ref, (_cid, record, reason) in sorted(best.items()):
        if record is None:
            if ref[len("gap:") :] not in covered:
                gaps.append({"head_sha": head, "route": ref[len("gap:") :], "reason": reason})
            continue
        built = _record_rows(registry, head, record)
        rows += built
        if not built:
            gaps.append({"head_sha": head, "route": str(record.get("route")), "reason": "no_record"})
    return rows, gaps


def read_actions(
    get: Get, repo: str, app_id: int, day_from: str, day_to: str
) -> tuple[list[FindingRow], list[dict[str, str]]]:
    registry = _registry_keys()
    runs = _paged(get, f"/repos/{repo}/actions/workflows/{WORKFLOW}/runs?created={day_from}..{day_to}", "workflow_runs")
    heads: dict[str, str] = {}
    for run in runs:
        match = TITLE.match(str(run.get("display_title")))
        if match:
            sha, conclusion = match.group(1), str(run.get("conclusion"))
            heads[sha] = conclusion if heads.get(sha, "cancelled") == "cancelled" else heads[sha]
    rows: list[FindingRow] = []
    gaps: list[dict[str, str]] = []
    for head, conclusion in sorted(heads.items()):
        checks = [
            c
            for c in _paged(get, f"/repos/{repo}/commits/{head}/check-runs", "check_runs")
            if (c.get("app") or {}).get("id") == app_id
            and str(c.get("name")).startswith(CHECK_PREFIX)
            and type(c.get("id")) is int
        ]
        if not checks and conclusion == "cancelled":
            gaps.append({"head_sha": head, "route": "", "reason": "cancelled"})
        head_rows, head_gaps = _head_result(registry, head, checks)
        rows += head_rows
        gaps += head_gaps
    return rows, gaps


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="findings_read", description=__doc__.splitlines()[0])
    parser.add_argument("--day-from", required=True)
    parser.add_argument("--day-to", required=True)
    parser.add_argument("--lane", choices=["both", "in_session", "actions"], default="both")
    parser.add_argument("--profile", default=None)
    return parser


def _github_get(path: str) -> tuple[int, bytes]:
    return resolve_pr.request("GET", path, os.environ.get("GH_TOKEN"))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _days(args.day_from, args.day_to)
        rows: list[FindingRow] = []
        gaps: list[dict[str, str]] = []
        if args.lane in ("both", "in_session"):
            from scripts.telemetry_capture import read_route

            rows += read_in_session(
                read_route.make_routed_reader(args.profile).named,
                read_route.content_reader(args.profile),
                args.day_from,
                args.day_to,
            )
        if args.lane in ("both", "actions"):
            actions, gaps = read_actions(
                _github_get, os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO), verdict_app_id(), args.day_from, args.day_to
            )
            rows += actions
    except ReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- the error line names a class, never a message, URL or value
        print(f"error: {type(exc).__name__}", file=sys.stderr)
        return 1
    for row in rows:
        print(json.dumps(dataclasses.asdict(row), default=str))
    for gap in gaps:
        print(json.dumps({"gap": gap}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
