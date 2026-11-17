"""The human override of a failing verdict check (T4.32 c6): record, digest, origin, approval, lookup and the render verb.

Imports stdlib, scripts.* and (through merge_checks) yaml only, never jsonschema: the render job installs pyyaml alone.
Names no persona id; persona classes come from merge_checks and each persona add plan registers its own blocking-id
extractor. Every API error refuses (fail closed). Uses only GH_TOKEN, GITHUB_API_URL and GITHUB_REPOSITORY.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
from collections.abc import Callable
from typing import Any

from scripts.personas import resolve_pr
from scripts.session import merge_checks

RECORD_PREFIX = "<!-- persona-verdict-record v1 "
RECORD_RE = re.compile(r"^<!-- persona-verdict-record v1 (\{.*\}) -->$")
OVERRIDE_PREFIX = "<!-- persona-verdict-override v1 "
VERDICT_WORKFLOW = ".github/workflows/persona-verdict.yml"
OVERRIDE_WORKFLOW = ".github/workflows/persona-verdict-override.yml"
OVERRIDE_FILE = "persona-verdict-override.yml"
ENVIRONMENT = "persona-verdict-override"
APPROVE_JOB = "approve"
PROBE_PREFIX = "verdict_probe."
OVERRIDABLE_VERDICTS = frozenset({"BLOCK", "REVISE", "FLAGS_FOUND"})
RUN_PAGES = 2
TITLE_LIMIT = 200
ROUTE_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)?")
SHA_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"[0-9a-f]{64}")
POSINT_RE = re.compile(r"[1-9][0-9]*")
TITLE_RE = re.compile(r"persona-verdict-override (\S+) (\S+) (\S+) (\S+) (\S+)")
FINDING_RE = re.compile(r"^- (\S+): (.*)$")

Extractor = Callable[[Any], "tuple[str, list[str]]"]


class Refused(Exception):
    """A refusal with a one-line reason."""


def render_record(route: str, head_sha: str, verdict: str, blocking_ids: list[str], run_id: str) -> str:
    body = {
        "route": route,
        "head_sha": head_sha,
        "verdict": verdict,
        "blocking_ids": sorted(set(blocking_ids)),
        "run_id": str(run_id),
    }
    return f"{RECORD_PREFIX}{json.dumps(body, sort_keys=True)} -->"


def parse_record(text: str | None) -> dict[str, Any] | None:
    """Only the first line of the text is read; it must be exactly one valid, fully typed record line."""
    if not isinstance(text, str):
        return None
    match = RECORD_RE.match(text.split("\n", 1)[0])
    if not match:
        return None
    try:
        rec = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(rec, dict) or set(rec) != {"route", "head_sha", "verdict", "blocking_ids", "run_id"}:
        return None
    ids = rec["blocking_ids"]
    ok = (
        all(isinstance(rec[k], str) for k in ("route", "head_sha", "verdict", "run_id"))
        and isinstance(ids, list)
        and all(isinstance(i, str) and i for i in ids)
        and ids == sorted(set(ids))
    )
    return rec if ok else None


def ids_digest(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def _probe_extract(out: Any) -> tuple[str, list[str]]:
    verdict = str(out.get("verdict", "")) if isinstance(out, dict) else ""
    findings = out.get("findings") if isinstance(out, dict) else None
    rows = [f for f in findings if isinstance(f, dict) and isinstance(f.get("id"), str)] if isinstance(findings, list) else []
    if verdict == "FLAGS_FOUND":
        return verdict, [f["id"] for f in rows]
    return verdict, [f["id"] for f in rows if f.get("blocking") is True]


EXTRACTORS: dict[str, Extractor] = {PROBE_PREFIX: _probe_extract}


def register_extractor(prefix: str, fn: Extractor) -> None:
    EXTRACTORS[prefix] = fn


def extractor_for(route: str) -> Extractor | None:
    for prefix, fn in EXTRACTORS.items():
        if route == prefix or route.startswith(prefix if prefix.endswith(".") else prefix + "."):
            return fn
    return None


def overridable(route: str) -> bool:
    if route.startswith(PROBE_PREFIX):
        return True
    return merge_checks.PERSONA_CLASSES.get(route) == "plan"


def finding_titles(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.split("\n")[1:]:
        match = FINDING_RE.match(line)
        if match:
            out[match.group(1)] = match.group(2)
    return out


def _get(path: str, token: str | None) -> Any:
    try:
        return resolve_pr.get_json(path, token)
    except Exception as exc:  # noqa: BLE001 - every API error refuses
        raise Refused(f"API error: {str(exc)[:120]}") from exc


def origin(repo: str, token: str | None, head_sha: str, route: str, check_run_id: int) -> tuple[dict[str, Any], str]:
    """Return (record, output text) for a check run that is a genuine verdict-run check, else raise Refused."""
    run = _get(f"/repos/{repo}/check-runs/{check_run_id}", token)
    if not isinstance(run, dict):
        raise Refused("the check run is unreadable")
    if (run.get("app") or {}).get("slug") != merge_checks.APP:
        raise Refused("the check run is not from github-actions")
    if run.get("name") != f"persona-verdict/{route}" or run.get("head_sha") != head_sha:
        raise Refused("the check run name or head does not match")
    text = str((run.get("output") or {}).get("text") or "")
    rec = parse_record(text)
    if rec is None or rec["route"] != route or rec["head_sha"] != head_sha:
        raise Refused("the check run holds no matching verdict record")
    prod = _get(f"/repos/{repo}/actions/runs/{rec['run_id']}", token)
    suite = (run.get("check_suite") or {}).get("id")
    if not isinstance(prod, dict) or suite is None or prod.get("check_suite_id") != suite:
        raise Refused("the check run was not created by the record's producing run")
    if not (
        prod.get("path") == VERDICT_WORKFLOW
        and prod.get("event") == "workflow_run"
        and prod.get("head_branch") == "main"
        and prod.get("display_title") == f"persona-verdict pull_request {head_sha}"
    ):
        raise Refused("the record's producing run is not a verdict run for this head")
    listed = _get(f"/repos/{repo}/commits/{head_sha}/check-runs?check_name=persona-verdict%2F{route}&per_page=100", token)
    rows = listed.get("check_runs") if isinstance(listed, dict) else None
    ids = [r.get("id") for r in rows or [] if isinstance(r, dict) and isinstance(r.get("id"), int)]
    if not ids or max(ids) != check_run_id:
        raise Refused("the check run is not the newest persona-verdict check of that name on the head")
    return rec, text


def parse_title(title: Any) -> dict[str, Any] | None:
    match = TITLE_RE.fullmatch(str(title or ""))
    if not match:
        return None
    pr, head, route, cid, digest = match.groups()
    if not (
        POSINT_RE.fullmatch(pr)
        and SHA_RE.fullmatch(head)
        and ROUTE_RE.fullmatch(route)
        and POSINT_RE.fullmatch(cid)
        and DIGEST_RE.fullmatch(digest)
    ):
        return None
    return {"pr": int(pr), "head_sha": head, "route": route, "check_run_id": int(cid), "ids_digest": digest}


def _approver(entries: Any) -> str | None:
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("state") != "approved":
            continue
        names = [e.get("name") for e in entry.get("environments") or [] if isinstance(e, dict)]
        if ENVIRONMENT in names:
            user = entry.get("user")
            login = user.get("login") if isinstance(user, dict) else None
            return str(login) if login else None
    return None


def approval(repo: str, token: str | None, run_id: int) -> dict[str, Any] | None:
    """The proven approval of one override run, or None when the run does not prove one. API errors raise Refused."""
    run = _get(f"/repos/{repo}/actions/runs/{run_id}", token)
    if not isinstance(run, dict):
        return None
    if not (
        run.get("path") == OVERRIDE_WORKFLOW
        and run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == "main"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
    ):
        return None
    fields = parse_title(run.get("display_title"))
    if fields is None:
        return None
    jobs = _get(f"/repos/{repo}/actions/runs/{run_id}/jobs", token)
    rows = jobs.get("jobs") if isinstance(jobs, dict) else None
    done = [j for j in rows or [] if isinstance(j, dict) and j.get("name") == APPROVE_JOB and j.get("conclusion") == "success"]
    if not done:
        return None
    approver = _approver(_get(f"/repos/{repo}/actions/runs/{run_id}/approvals", token))
    if approver is None:
        return None
    return {
        "run_id": run_id,
        "approver": approver,
        "approved_at": str(done[0].get("completed_at") or ""),
        "url": str(run.get("html_url") or f"https://github.com/{repo}/actions/runs/{run_id}"),
        **fields,
    }


def lookup(repo: str, token: str | None, pr: int, head_sha: str, route: str, digest: str) -> dict[str, Any] | None:
    for page in range(1, RUN_PAGES + 1):
        body = _get(
            f"/repos/{repo}/actions/workflows/{OVERRIDE_FILE}/runs?event=workflow_dispatch&branch=main&status=success"
            f"&per_page=100&page={page}",
            token,
        )
        runs = body.get("workflow_runs") if isinstance(body, dict) else None
        if not isinstance(runs, list):
            raise Refused("the override run list is unreadable")
        for run in runs:
            fields = parse_title(run.get("display_title")) if isinstance(run, dict) else None
            if (
                fields
                and fields["pr"] == pr
                and fields["head_sha"] == head_sha
                and fields["route"] == route
                and fields["ids_digest"] == digest
                and isinstance(run.get("id"), int)
            ):
                proven = approval(repo, token, run["id"])
                if proven:
                    return proven
        if len(runs) < 100:
            break
    return None


def _clean(title: str) -> str:
    return html.escape(title.replace("`", "").replace("\r", " ").replace("\n", " "))[:TITLE_LIMIT]


def render(env: dict[str, str]) -> list[str]:
    repo, token = env["GITHUB_REPOSITORY"], env.get("GH_TOKEN")
    fields = parse_title(
        " ".join(
            ["persona-verdict-override"]
            + [env.get(k, "") for k in ("INPUT_PR", "INPUT_HEAD_SHA", "INPUT_ROUTE", "INPUT_CHECK_RUN_ID", "INPUT_IDS_DIGEST")]
        )
    )
    if fields is None:
        raise Refused("the inputs are not well formed")
    pull = _get(f"/repos/{repo}/pulls/{fields['pr']}", token)
    head = pull.get("head") if isinstance(pull, dict) else None
    if not (
        isinstance(head, dict)
        and pull.get("state") == "open"
        and head.get("sha") == fields["head_sha"]
        and (head.get("repo") or {}).get("full_name") == repo
    ):
        raise Refused("the pull request is closed, foreign or its head moved")
    route = fields["route"]
    if not overridable(route):
        raise Refused("the route is not overridable")
    rec, text = origin(repo, token, fields["head_sha"], route, fields["check_run_id"])
    if rec["verdict"] not in OVERRIDABLE_VERDICTS or not rec["blocking_ids"]:
        raise Refused("the record holds no overridable verdict")
    if ids_digest(rec["blocking_ids"]) != fields["ids_digest"]:
        raise Refused("ids_digest does not match the record")
    titles = finding_titles(text)
    lines = [
        f"# Override request: PR {fields['pr']}, head {fields['head_sha']}, route {route}",
        f"Verdict: {rec['verdict']}",
        "",
    ]
    for fid in rec["blocking_ids"]:
        lines += ["```", f"{_clean(fid)}: {_clean(titles.get(fid, ''))}", "```"]
    return lines


def main(argv: list[str]) -> int:
    try:
        lines = render(dict(os.environ))
    except Refused as exc:
        print(f"PERSONA-OVERRIDE refused: {exc}")
        return 1
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    print("PERSONA-OVERRIDE render ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
