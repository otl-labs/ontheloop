"""The scout-input fingerprint carry (T4.32 c4 legs 1-2, rec-5653): carry a verdict on an equal fingerprint, else re-run.

Names no persona id: the covered set is merge_checks.FINGERPRINT_PERSONAS. The input line is a separate line beside
override.py's persona-verdict-record v1 line, which is unchanged. Every API error, unreadable record or fingerprint
error routes the persona (Decision 271 cl.3); nothing is carried on doubt. Imports plan_fingerprint (pydantic), so only
the prepare and post jobs load this module, never wake or override.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.personas import override, resolve_pr, route
from scripts.roadmap import approved_words, plan_fingerprint
from scripts.session import merge_checks

INPUT_PREFIX = "<!-- persona-verdict-input v1 "
INPUT_RE = re.compile(r"^<!-- persona-verdict-input v1 (\{.*\}) -->$")
INPUT_KEYS = frozenset({"route", "head_sha", "verdict", "cites", "fingerprints", "run_id", "carried_from"})
SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")
SHA_RE = re.compile(r"[0-9a-f]{40}")
CITE_RE = re.compile(r"dec:[1-9][0-9]*")
FP_RE = re.compile(r"v[0-9]+:[0-9a-f]{64}")
VERDICT_RE = re.compile(r"[A-Z][A-Z_]*")
SCAN_COMMITS = 20
COMMIT_PAGES = 3


class Unreadable(Exception):
    """An input the fingerprint cannot be computed from, or a prior record that cannot be read."""


@dataclass
class Decision:
    routes: list[str]
    carried: list[dict[str, Any]] = field(default_factory=list)
    rescout: dict[str, str] = field(default_factory=dict)
    inputs: dict[str, dict[str, str | None]] | None = None


def render_input(
    route_id: str,
    head_sha: str,
    verdict: str,
    cites: list[str],
    fingerprints: dict[str, str],
    run_id: str,
    carried_from: str | None,
) -> str:
    body = {
        "route": route_id,
        "head_sha": head_sha,
        "verdict": verdict,
        "cites": sorted(set(cites)),
        "fingerprints": dict(sorted(fingerprints.items())),
        "run_id": str(run_id),
        "carried_from": carried_from,
    }
    return f"{INPUT_PREFIX}{json.dumps(body, sort_keys=True, separators=(',', ':'))} -->"


def _typed(rec: dict[str, Any]) -> bool:
    cites, fps, back = rec["cites"], rec["fingerprints"], rec["carried_from"]
    return (
        all(isinstance(rec[k], str) for k in ("route", "head_sha", "verdict", "run_id"))
        and bool(SHA_RE.fullmatch(rec["head_sha"]))
        and bool(VERDICT_RE.fullmatch(rec["verdict"]))
        and isinstance(cites, list)
        and all(isinstance(c, str) and CITE_RE.fullmatch(c) for c in cites)
        and cites == sorted(set(cites))
        and isinstance(fps, dict)
        and bool(fps)
        and all(isinstance(k, str) and isinstance(v, str) and FP_RE.fullmatch(v) for k, v in fps.items())
        and (back is None or (isinstance(back, str) and bool(SHA_RE.fullmatch(back))))
    )


def parse_input(text: str | None) -> dict[str, Any] | None:
    """The one input record in a check's output text (any line), else None."""
    if not isinstance(text, str):
        return None
    found = [m for m in (INPUT_RE.match(line) for line in text.split("\n")) if m]
    if len(found) != 1:
        return None
    try:
        rec = json.loads(found[0].group(1))
    except ValueError:
        return None
    if not isinstance(rec, dict) or set(rec) != INPUT_KEYS or not _typed(rec):
        return None
    return rec


def input_line(text: str | None) -> str | None:
    """The input line of a text, verbatim, when the text holds exactly one valid one."""
    if parse_input(text) is None:
        return None
    return next(line for line in str(text).split("\n") if INPUT_RE.match(line))


def with_input(text: str | None, line: str) -> str:
    """The text with the input line second (first when the text holds no v1 record)."""
    if text is None:
        return line
    lines = text.split("\n")
    at = 1 if lines[0].startswith(override.RECORD_PREFIX) else 0
    lines.insert(at, line)
    return "\n".join(lines)


def _fetch(repo: str, token: str | None, path: str, ref: str) -> str | None:
    status, raw = resolve_pr.request(
        "GET", f"/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}", token, accept="application/vnd.github.raw+json"
    )
    if status == 404:
        return None
    if status != 200:
        raise Unreadable(f"{path} returned {status}")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Unreadable(f"{path} is not utf-8") from exc


def _checked(text: str, what: str) -> str:
    if len(text.encode("utf-8")) > route.PLAN_SIZE_CAP:
        raise Unreadable(f"{what} is over the size cap")
    try:
        aliased = route._has_alias(text)
    except Exception as exc:  # noqa: BLE001 - a yaml error
        raise Unreadable(f"{what} does not parse") from exc
    if aliased:
        raise Unreadable(f"{what} holds an alias or anchor")
    return text


def head_inputs(repo: str, token: str | None, head_sha: str, plan_paths: list[str]) -> dict[str, dict[str, str | None]]:
    out: dict[str, dict[str, str | None]] = {}
    for path in plan_paths:
        text = _fetch(repo, token, path, head_sha)
        if text is None:
            raise Unreadable(f"{path} is missing at the head")
        plan = route.parse_plan(text)
        if plan is None:
            raise Unreadable(f"{path} does not parse as a plan")
        slug = plan.get("slug")
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise Unreadable(f"{path} has no usable slug")
        side = _fetch(repo, token, approved_words.sidecar_rel(slug), head_sha)
        out[path] = {"plan": text, "sidecar": None if side is None else _checked(side, "the sidecar")}
    return out


def fingerprints(inputs: dict[str, dict[str, str | None]], main_root: Path, cites: list[str]) -> dict[str, str]:
    try:
        tree = plan_fingerprint.load_tree(main_root)
        out: dict[str, str] = {}
        for path, item in inputs.items():
            plan = route.parse_plan(item["plan"])
            if plan is None:
                raise Unreadable(f"{path} does not parse as a plan")
            side = item.get("sidecar")
            sidecar = approved_words.parse_sidecar(side) if side is not None else None
            out[path] = plan_fingerprint.fingerprint(plan_fingerprint.projection(plan, tree, sidecar, cites))
        return out
    except Unreadable:
        raise
    except Exception as exc:  # noqa: BLE001 - FingerprintError, PlanEdgesError, ApprovedTextError, yaml errors, io
        raise Unreadable(f"fingerprint failed: {type(exc).__name__}") from exc


Prior = tuple[dict[str, Any], dict[str, Any] | None, dict[str, str], int]


def _commits(repo: str, token: str | None, number: int) -> list[str]:
    shas: list[str] = []
    for page in range(1, COMMIT_PAGES + 1):
        rows = resolve_pr.get_json(f"/repos/{repo}/pulls/{number}/commits?per_page=100&page={page}", token)
        if not isinstance(rows, list):
            raise Unreadable("the commit list is unreadable")
        shas.extend(str(r.get("sha")) for r in rows if isinstance(r, dict))
        if len(rows) < 100:
            break
    return shas[::-1]


def prior(repo: str, token: str | None, number: int, route_id: str, head_sha: str) -> Prior | str:
    """(input record, v1 record or None, finding titles, check run id) of the last verdict-App check, or a reason."""
    try:
        app = override.app_id()
        shas = _commits(repo, token, number)
        if not shas or shas[0] != head_sha:
            return "unreadable"
        for sha in shas[:SCAN_COMMITS]:
            query = f"check_name={urllib.parse.quote(f'persona-verdict/{route_id}', safe='')}&per_page=100"
            listed = resolve_pr.get_json(f"/repos/{repo}/commits/{sha}/check-runs?{query}", token)
            rows = listed.get("check_runs") if isinstance(listed, dict) else None
            mine = [
                r
                for r in rows or []
                if isinstance(r, dict)
                and r.get("name") == f"persona-verdict/{route_id}"
                and isinstance(r.get("id"), int)
                and (r.get("app") or {}).get("id") == app
            ]
            if mine:
                return _read_prior(max(mine, key=lambda r: r["id"]), route_id, sha)
    except Exception:  # noqa: BLE001 - every API or contract error routes the persona
        return "unreadable"
    return "missing"


def _read_prior(run: dict[str, Any], route_id: str, sha: str) -> Prior | str:
    text = str((run.get("output") or {}).get("text") or "")
    rec = parse_input(text)
    if rec is None:
        return "unreadable"
    if rec["route"] != route_id or rec["head_sha"] != sha:
        return "untrusted"
    v1 = override.parse_record(text)
    if v1 is not None and (v1["verdict"], v1["route"], v1["head_sha"]) != (rec["verdict"], rec["route"], rec["head_sha"]):
        return "unreadable"
    return rec, v1, override.finding_titles(text), int(run["id"])


def decide(
    repo: str,
    token: str | None,
    resolution: dict[str, Any],
    routes: list[str],
    plan_paths: list[str],
    unknown: bool,
    main_root: Path,
) -> Decision:
    covered = [r for r in routes if r in merge_checks.FINGERPRINT_PERSONAS]
    if not covered:
        return Decision(list(routes))
    out = Decision(list(routes))
    early = (
        "not_allowlisted"
        if not resolution.get("allowlisted")
        else "unknown_diff"
        if unknown
        else "no_plans"
        if not plan_paths
        else None
    )
    if early:
        out.rescout = dict.fromkeys(covered, early)
        return out
    head = str(resolution["head_sha"])
    try:
        out.inputs = head_inputs(repo, token, head, plan_paths)
    except Exception:  # noqa: BLE001 - any fetch or parse fault routes every covered persona
        out.rescout = dict.fromkeys(covered, "unreadable")
        return out
    for route_id in covered:
        found = prior(repo, token, int(resolution["number"]), route_id, head)
        if isinstance(found, str):
            out.rescout[route_id] = found
            continue
        rec, v1, titles, run_id = found
        try:
            same = fingerprints(out.inputs, main_root, rec["cites"]) == rec["fingerprints"]
        except Unreadable:
            out.rescout[route_id] = "unreadable"
            continue
        if not same:
            out.rescout[route_id] = "moved"
            continue
        out.routes.remove(route_id)
        out.carried.append(
            {
                "route": route_id,
                "verdict": rec["verdict"],
                "blocking_ids": list(v1["blocking_ids"]) if v1 else [],
                "titles": titles,
                "cites": rec["cites"],
                "fingerprints": rec["fingerprints"],
                "from_head": rec["head_sha"],
                "from_check_run_id": run_id,
            }
        )
    return out


def rescout_required(routes_doc: dict[str, Any]) -> bool:
    """True when the routes document's rescout map is non-empty (read by the plan_critic add plan)."""
    return bool(routes_doc.get("rescout"))


def fresh_input(root: Path, work: Path, route_id: str, head_sha: str, out: dict[str, Any], run_id: str) -> str | None:
    """The input line of a fresh covered check from the prepared head inputs and the cite-merged tokens, else None."""
    if route_id not in merge_checks.FINGERPRINT_PERSONAS:
        return None
    try:
        inputs = json.loads((work / "prepared" / route.PLAN_INPUTS).read_text(encoding="utf-8"))
        verdict = str(out["verdict"])
        cites = sorted({f"dec:{c['decision']}" for c in out["cite"]})
        if not (isinstance(inputs, dict) and inputs and VERDICT_RE.fullmatch(verdict)):
            return None
        return render_input(route_id, head_sha, verdict, cites, fingerprints(inputs, root, cites), run_id, None)
    except (OSError, ValueError, KeyError, TypeError, Unreadable):
        return None
