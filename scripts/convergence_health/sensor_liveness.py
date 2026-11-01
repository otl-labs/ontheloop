"""Scheduled-loop liveness backstop (audit finding LSA-02, PLAN-sensor-liveness).

The fleet's other sensors all reason about the CONTENT of a run (drift, budget, convergence);
none notice a schedule that stops firing at all -- GitHub's 60-day inactivity auto-disable, a
deleted `schedule:` block, a silent delivery drop. This module is the fifth, alarm-only leg on the
convergence-health cron that closes that gap.

PEER SET IS DERIVED, NEVER DECLARED: `derive_scheduled_peers()` walks every
`.github/workflows/*.yml` at run time and reads its `on:.schedule` block -- no `sensors:` register
anywhere (a second register over the same population is the drift-by-design NS6 forbids).
`peers_from_workflow_docs` normalises PyYAML's bare `on:` key (parsed as Python `True`) and RAISES
on an empty result by DEFAULT -- the production chain `main_sensor_liveness -> detect_stale_loops
-> derive_scheduled_peers` passes no override, so fail-loud is inherited, never opt-in.

THRESHOLD IS DERIVED FROM EACH CRON: `cron_period_seconds` is a bounded 5-field shape matcher (no
croniter) covering the four live shapes; anything else raises. `staleness_threshold_seconds`
clamps 2x the nominal period into [6h, 45d] -- the floor tolerates best-effort scheduled delivery,
the ceiling is the largest threshold whose first bucket [T, 2T) still fits the 90-day Actions
run-retention window, so a `run:{id}` anchor never flips to `intro:{sha}` mid-bucket.

EPISODE GRAIN: the open matcher keys on (loop, leg) -- cadence (schedule stopped firing) and
success (fires on cadence, never succeeds; PLAN-monitor-liveness-sweep) resolve independently -- so
a recovery-then-redeath while a rec is open UPDATES it rather than minting a new episode. The
resolved matcher sweeps on (loop) alone (one warehouse read serves both legs, Decision 88) then
discriminates client-side into leg, then two dwell legs: SAME anchor mutes on the MAX resolved
bucket (`current <= max+1`); CROSS anchor costs one `rec_by_id` for the newest resolved rec's
closure timestamp and mutes while `now - closed_at < threshold`. Titles are START-ANCHORED
(`Loop: {k}. Episode: {a}. Bucket: {n}. ...`) because `recs_by_title_prefix` binds the WHOLE LIKE
pattern with no implicit wildcard (Decision 142). A leg marker is appended AFTER that prefix, never
before it; a marker-less title (every rec filed before the success leg existed, e.g. live
rec-3860) is CADENCE -- see sensor_liveness_episodes.py.

NO-OP UPDATES ARE SKIPPED (bucket-quantised title/context, so an unchanged episode writes no
duplicate SCD2 row). FILE/UPDATE ONLY, NEVER CLOSE -- restored cadence does not auto-resolve a
filed rec. FLEET COLLAPSE: `FLEET_COLLAPSE_MIN_STALE` simultaneously-stale peers collapse to one
`__fleet__` episode (per-loop episodes suppressed, never closed) whose bucket derives from its OWN
anchor age -- never `min(bucket over stale peers)`, non-monotonic and able to silently mute an
acknowledged fleet outage. FAIL LOUDLY, NO OUTBOX (Decision 84 I-4): every external dependency is
injected, and a dead query raises rather than reporting an empty, reassuring population.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, cast

import yaml

from scripts import repo_identity
from scripts.convergence_health.approvals import _make_github_caller
from scripts.convergence_health.code_drift import _assert_full_history
from scripts.convergence_health.record import _parse_utc
from scripts.convergence_health.sensor_liveness_episodes import (
    RESOLVED_REC_STATUSES as RESOLVED_REC_STATUSES,  # re-export: tests reach it via sl.RESOLVED_REC_STATUSES
)
from scripts.convergence_health.sensor_liveness_episodes import (
    _cross_anchor_newest,
    _episode_context,
    _episode_title,
    _filter_resolved_rows,
    _is_open_loop_row,
    _parse_episode_title,
    _same_anchor_best_row,
)
from scripts.rec_episode import find_recs

_WORKFLOWS_DIR = ".github/workflows"
_REPO_ROOT = Path(__file__).parent.parent.parent

# Short aliases so injected-dependency signatures stay on one line (repeated 5-40x each below).
_Info = dict[str, Any]
_Rows = list[_Info]
_GhCaller = Callable[[str], Any]
_GitRunner = Callable[[list[str]], str]
_PortalCaller = Callable[[str, _Info], Any]

_MIN_THRESHOLD_SECONDS = 6 * 3600
_MAX_THRESHOLD_SECONDS = 45 * 86400
_THRESHOLD_MULTIPLIER = 2

FLEET_COLLAPSE_MIN_STALE = 3
_FLEET_LOOP_KEY = "__fleet__"

_DRY_RUN_LABELS = {"file": "would_file", "update": "would_update", "unchanged": "would_skip", "drop": "would_drop"}

_EMPTY_PEER_SET_MESSAGE = (
    "scripts.convergence_health.sensor_liveness: derived ZERO schedule-bearing peers from "
    f"{_WORKFLOWS_DIR}/*.yml -- refusing an all-clear from what may be a broken parse (a missing "
    "directory, a swallowed YAML load failure, or the PyYAML `on:`-as-True key going unnormalised). "
    "Decision 55: a dead sensor and a truly schedule-free fleet must not look the same."
)
_DEAD_QUERY_MESSAGE = (
    "scripts.convergence_health.sensor_liveness: the GitHub workflow-runs query for {loop!r} returned "
    "no payload (absent GH_TOKEN/GITHUB_TOKEN, or a failed query). Refusing to report an empty run "
    "population from a query that never ran -- a dead sensor and a live peer must not look the same."
)


# --- Peer derivation: never declared, fail-loud by default on an empty result. ---


def peers_from_workflow_docs(docs: _Info, require_non_empty: bool = True) -> dict[str, list[str]]:
    """Pure seam: {filename: parsed_yaml_dict} -> {filename: [cron_expr, ...]}. Normalises
    PyYAML's `on`-as-True key via `data.get("on", data.get(True, {}))`. Raises when
    `require_non_empty` (the default, and the only value the production chain passes) and the
    result is empty."""
    peers: dict[str, list[str]] = {}
    for filename, data in docs.items():
        if not isinstance(data, dict):
            continue
        on_block = data.get("on", data.get(True, {}))
        schedule = on_block.get("schedule") if isinstance(on_block, dict) else None
        if not isinstance(schedule, list):
            continue
        crons = [entry["cron"] for entry in schedule if isinstance(entry, dict) and entry.get("cron")]
        if crons:
            peers[filename] = crons
    if require_non_empty and not peers:
        raise RuntimeError(_EMPTY_PEER_SET_MESSAGE)
    return peers


def derive_scheduled_peers(require_non_empty: bool = True) -> dict[str, list[str]]:
    """Walk `.github/workflows/*.yml|*.yaml` from the checkout and derive the live peer set."""
    workflows_dir = _REPO_ROOT / _WORKFLOWS_DIR
    docs: _Info = {}
    if workflows_dir.is_dir():
        for path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
            docs[path.name] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return peers_from_workflow_docs(docs, require_non_empty=require_non_empty)


# --- Threshold oracle: a bounded 5-field cron shape matcher, no croniter dependency. ---


def cron_period_seconds(expr: str) -> int:
    """Nominal period (seconds) of a 5-field cron covering the four live shapes -- hourly,
    every-N-hours, weekly-by-dow, monthly-by-dom. Raises on anything else, including a literal dom
    AND dow together (cron's union semantics -- never guessed at)."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"scripts.convergence_health.sensor_liveness: unclassifiable cron shape {expr!r}")
    minute, hour, dom, month, dow = fields

    def _lit(field: str) -> bool:
        return field.isdigit()

    def _star(field: str) -> bool:
        return field == "*"

    def _step(field: str) -> Optional[int]:
        return int(field[2:]) if field.startswith("*/") and field[2:].isdigit() else None

    hour_step = _step(hour)
    if _lit(minute) and hour_step is not None and _star(dom) and _star(month) and _star(dow):
        return hour_step * 3600
    if _lit(minute) and _star(hour) and _star(dom) and _star(month) and _star(dow):
        return 3600
    minute_step = _step(minute)
    if minute_step is not None and _star(hour) and _star(dom) and _star(month) and _star(dow):
        return minute_step * 60
    if _lit(minute) and _lit(hour) and _star(dom) and _star(month) and _lit(dow):
        return 7 * 86400
    if _lit(minute) and _lit(hour) and _lit(dom) and _star(month) and _star(dow):
        return 31 * 86400
    raise ValueError(f"scripts.convergence_health.sensor_liveness: unclassifiable cron shape {expr!r}")


def staleness_threshold_seconds(expr: str) -> int:
    """clamp(2 x nominal period, 6h, 45d) -- see module docstring for the bounds' rationale."""
    period = cron_period_seconds(expr)
    return min(max(_THRESHOLD_MULTIPLIER * period, _MIN_THRESHOLD_SECONDS), _MAX_THRESHOLD_SECONDS)


def _peer_threshold_seconds(crons: list[str]) -> int:
    """A peer with >1 schedule entry is judged against its tightest (soonest-alarming) threshold."""
    return min(staleness_threshold_seconds(c) for c in crons)


def fleet_threshold_seconds(peers: dict[str, list[str]]) -> int:
    """The minimum per-peer threshold over the DERIVED (not stale) set -- a pure function of the
    workflow tree, constant within a fleet episode by construction."""
    return min(_peer_threshold_seconds(crons) for crons in peers.values())


def _bucket_for(age_seconds: float, threshold_seconds: int) -> int:
    """0 while fresh; else floor(log2(age/threshold)) + 1 -- the log2 re-alarm schedule."""
    if age_seconds < threshold_seconds:
        return 0
    return math.floor(math.log2(age_seconds / threshold_seconds)) + 1


# --- Live GitHub queries: per-peer run + one fleet-wide workflow-state listing. ---


def _default_git_runner(cmd: list[str]) -> str:
    import subprocess  # noqa: PLC0415

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout.strip()


def _query_last_run(loop: str, caller: _GhCaller, api_base: str, query_suffix: str = "") -> Optional[_Info]:
    """`?event=schedule{query_suffix}&per_page=1` -- filtered so workflow_dispatch cannot
    masquerade as cron liveness. `query_suffix` (e.g. `&status=success`) lets a sibling leg reuse
    this query. Raises on a dead query (Decision 55) regardless of leg."""
    data = caller(f"{api_base}/actions/workflows/{loop}/runs?event=schedule{query_suffix}&per_page=1")
    if not data:
        raise RuntimeError(_DEAD_QUERY_MESSAGE.format(loop=loop))
    runs = data.get("workflow_runs") or []
    return runs[0] if runs else None


def _query_workflow_states(caller: _GhCaller, api_base: str) -> dict[str, str]:
    """ONE bounded workflows listing per tick, indexed by filename (Decision 100). Degrades to {}
    on a dead query -- `state` only ever refines remediation text and must never suppress the
    age-derived alarm."""
    data = caller(f"{api_base}/actions/workflows?per_page=100")
    if not data:
        return {}
    states: dict[str, str] = {}
    for wf in data.get("workflows") or []:
        name = str(wf.get("path") or "").rsplit("/", 1)[-1]
        if name:
            states[name] = str(wf.get("state") or "")
    return states


def _first_commit(loop: str, runner: _GitRunner) -> tuple[str, int]:
    """The workflow file's own first-commit (sha, unix ts) -- the `intro:{sha}` fallback anchor
    when GitHub reports no `event=schedule` run at all. `_assert_full_history` guards a shallow
    clone first (mirrors code_drift.py)."""
    _assert_full_history(runner)
    rel_path = f"{_WORKFLOWS_DIR}/{loop}"
    output = runner(["git", "log", "--format=%H %ct", "--follow", "--", rel_path]).strip()
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(
            f"scripts.convergence_health.sensor_liveness: no commit history for {rel_path} -- cannot "
            "derive an intro-anchor age (Decision 55)."
        )
    sha, ts = lines[-1].split()
    return sha, int(ts)


def _as_utc(value: Any) -> datetime:
    """Normalise a warehouse timestamp (real rows carry `datetime`; fixtures may carry a string)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return _parse_utc(str(value or ""))


def _assess_peer(
    loop: str,
    crons: list[str],
    caller: _GhCaller,
    runner: _GitRunner,
    now: datetime,
    api_base: str,
    query_suffix: str = "",
) -> _Info:
    """One peer's threshold, anchor, age and bucket -- shared by detect_stale_loops and
    --liveness-probe so the two never compute staleness differently. `query_suffix` lets
    sensor_liveness_success.py reuse this arithmetic; the cadence default ("") is unchanged."""
    threshold = _peer_threshold_seconds(crons)
    run = _query_last_run(loop, caller, api_base, query_suffix=query_suffix)
    if run is not None:
        anchor_ts = _parse_utc(str(run.get("created_at") or ""))
        anchor = f"run:{run.get('id')}"
    else:
        sha, first_ts = _first_commit(loop, runner)
        anchor_ts = datetime.fromtimestamp(first_ts, tz=timezone.utc)
        anchor = f"intro:{sha}"
    age = (now - anchor_ts).total_seconds()
    return {
        "loop": loop,
        "anchor": anchor,
        "anchor_ts": anchor_ts,
        "bucket": _bucket_for(age, threshold),
        "age_seconds": age,
        "threshold_seconds": threshold,
    }


# --- Episode title/context lives in sensor_liveness_episodes.py (re-exported above). Acceptance
# and rec-fields assembly stay here, leg-aware. ---


def _probe_acceptance(loop: str, leg: str = "cadence") -> str:
    """A dedicated function, not an inline f-string (validate_acceptance_literals' static scanner
    would bash-syntax-check and fail it) -- mirrors escalate.py's dynamic acceptance strings. The
    default leg ("cadence") is BARE -- no selector, so every already-filed cadence rec's stored
    acceptance is unchanged; only a non-cadence leg appends one."""
    target = "" if loop == _FLEET_LOOP_KEY else f" {loop}"
    leg_arg = "" if leg == "cadence" else f" {leg}"
    return f"bin/venv-python -m scripts.convergence_health --liveness-probe{target}{leg_arg}"


def _build_rec_fields(info: _Info) -> _Info:
    leg = info.get("leg", "cadence")
    return {
        "title": _episode_title(info["loop"], info["anchor"], info["bucket"], leg=leg),
        "file": ".github/workflows/convergence-health.yml",
        "status": "open",
        "source": "loop_liveness_stale",
        "priority": "High",
        "effort": "S",
        "risk": "medium",
        "verification_tier": "V2",
        "context": _episode_context(info),
        "acceptance": _probe_acceptance(info["loop"], leg=leg),
    }


# --- Open/resolved matchers: open keys on (loop, leg); resolved sweeps on (loop) alone (one
# warehouse read serves both legs, memoised per tick), then discriminates client-side into leg,
# then the same-anchor and cross-anchor post-closure dwell legs. ---


def _find_open_loop_rec(open_recs: _Rows, loop: str, leg: str = "cadence") -> Optional[_Info]:
    for rec in open_recs:
        if _is_open_loop_row(rec, loop, leg):
            return rec
    return None


def _fetch_resolved_loop_recs(loop: str, profile: Optional[str] = None) -> _Rows:
    """One `recs_by_title_prefix` sweep (trailing `%` mandatory, the verb binds the WHOLE pattern),
    unfiltered by leg -- both legs share this one raw pool; the caller re-filters per leg."""
    from src.common.ducklake_reader_client import DuckLakeReader, make_reader  # noqa: PLC0415

    reader = cast(DuckLakeReader, make_reader(profile=profile))
    rows = reader.named("recs_by_title_prefix", title_prefix=f"Loop: {loop}.%") or []
    return _filter_resolved_rows(rows, loop)


def _resolved_rows_for_loop(
    loop: str, resolved_recs: Optional[_Rows], profile: Optional[str], leg: str, cache: dict[str, _Rows]
) -> _Rows:
    """`resolved_recs` (testing) is re-filtered per loop and leg; when None, sweeps live -- but only
    ONCE per loop per tick (`cache`), so a peer stale on both legs pays one warehouse read, not two
    (Decision 88 invariant ii)."""
    if resolved_recs is not None:
        return _filter_resolved_rows(resolved_recs, loop, leg)
    if loop not in cache:
        cache[loop] = _fetch_resolved_loop_recs(loop, profile=profile)
    return _filter_resolved_rows(cache[loop], loop, leg)


def _fetch_rec_by_id(rec_id: Any, profile: Optional[str] = None) -> _Info:
    from src.common.ducklake_reader_client import DuckLakeReader, make_reader  # noqa: PLC0415

    reader = cast(DuckLakeReader, make_reader(profile=profile))
    rows = reader.named("rec_by_id", id=rec_id) or []
    return rows[0] if rows else {}


def _charge(budget: dict[str, int], key: str, loop: str, label: str) -> None:
    """Increment a structural CALL-COUNT cap and raise past it -- unreachable under normal
    operation. Widened to `2 * (len(peers) + 1)` so a peer stale on BOTH legs (up to two sweeps,
    two rec_by_id fetches) cannot trip a false structural-violation raise."""
    budget[key] += 1
    if budget[key] > budget[f"{key}_max"]:
        raise RuntimeError(
            f"scripts.convergence_health.sensor_liveness: {label} ({budget[f'{key}_max']}) exceeded "
            f"while resolving {loop!r} -- structural invariant violation, never accumulated history."
        )


# --- Portal file/update + the file|update|unchanged|drop decision (mirrors budget_ingest). ---


def _decide_action(existing: Optional[_Info], resolved: Optional[_Info], fields: _Info) -> tuple[str, Optional[Any]]:
    if existing is not None:
        same = existing.get("title") == fields["title"] and existing.get("context") == fields["context"]
        return ("unchanged" if same else "update"), existing["id"]
    if resolved is not None:
        return "drop", resolved["id"]
    return "file", None


def _portal_file(fields: _Info, portal_caller: Optional[_PortalCaller], profile: Optional[str]) -> Any:
    if portal_caller is not None:
        return portal_caller("file", fields)
    from scripts.ops_data_portal import file_rec  # noqa: PLC0415

    return file_rec(fields, profile=profile)


def _portal_update(rec_id: str, updates: _Info, portal_caller: Optional[_PortalCaller], profile: Optional[str]) -> None:
    if portal_caller is not None:
        portal_caller("update", {"id": rec_id, **updates})
        return
    from scripts.ops_data_portal import update_rec  # noqa: PLC0415

    update_rec(rec_id, updates, profile=profile)


def _reconcile_episode(info: _Info, open_recs: _Rows, resolved_recs: Optional[_Rows], ctx: _Info, result: _Info) -> None:
    """`ctx` bundles this tick's call-invariant state (budget/now/portal_caller/profile/dry_run) --
    one dict instead of five trailing params, mutated (via `budget`) across every episode a tick reconciles."""
    budget = ctx["budget"]
    now, portal_caller, profile, dry_run = ctx["now"], ctx["portal_caller"], ctx["profile"], ctx["dry_run"]
    resolved_cache = ctx["resolved_cache"]
    loop = info["loop"]
    leg = info.get("leg", "cadence")
    fields = _build_rec_fields(info)
    existing = _find_open_loop_rec(open_recs, loop, leg)
    resolved: Optional[_Info] = None
    if existing is None:
        _charge(budget, "sweeps", loop, "MAX_TITLE_SWEEPS")
        resolved_rows = _resolved_rows_for_loop(loop, resolved_recs, profile, leg, resolved_cache)
        same_anchor = _same_anchor_best_row(resolved_rows, info["anchor"])
        if same_anchor is not None:
            max_bucket = _parse_episode_title(str(same_anchor["title"]))[2]  # type: ignore[index]
            if info["bucket"] <= max_bucket + 1:
                resolved = same_anchor
        if resolved is None:
            cross = _cross_anchor_newest(resolved_rows, info["anchor"])
            if cross is not None:
                # A live sweep row never carries last_updated_timestamp (recs_by_title_prefix
                # projects id/title/status/source only) so production always pays this fetch; a
                # pre-assembled test row that already carries it skips the extra call.
                if "last_updated_timestamp" in cross:
                    full = cross
                else:
                    _charge(budget, "ids", loop, "MAX_REC_BY_ID")
                    full = _fetch_rec_by_id(cross["id"], profile=profile)
                closed_at = _as_utc(full.get("last_updated_timestamp"))
                if (now - closed_at).total_seconds() < info["threshold_seconds"]:
                    resolved = full

    decision, rec_id = _decide_action(existing, resolved, fields)
    if dry_run:
        action = _DRY_RUN_LABELS[decision]
        print(f"[convergence_health] sensor_liveness DRY-RUN {action}: {json.dumps(fields, sort_keys=True)}")
    elif decision == "file":
        rec_id = _portal_file(fields, portal_caller, profile)
        action = "file"
    elif decision == "update":
        _portal_update(str(rec_id), {"title": fields["title"], "context": fields["context"]}, portal_caller, profile)
        action = "update"
    else:
        action = decision
    result[{"file": "filed", "update": "updated", "unchanged": "unchanged", "drop": "dropped"}[decision]].append(
        {"loop": loop, "rec_id": rec_id, "action": action}
    )


# --- Top-level detection + the --liveness-probe acceptance oracle. ---


def _reconcile_leg(
    leg: str,
    stale: dict[str, _Info],
    peers: dict[str, list[str]],
    open_recs: _Rows,
    resolved_recs: Optional[_Rows],
    ctx: _Info,
    result: _Info,
) -> None:
    """Fleet-collapse-or-per-loop reconciliation for ONE leg. Counts DISTINCT PEERS stale on THIS
    leg alone, never (peer, leg) pairs -- else two peers stale on both legs (distinct 2, pairs 4)
    would spuriously collapse. The fleet episode is itself leg-keyed, so the two legs never
    collide on the open matcher and each carries its own remediation."""
    if not stale:
        return
    now = ctx["now"]
    if len(stale) >= FLEET_COLLAPSE_MIN_STALE:
        since_ts = max(info["anchor_ts"] for info in stale.values())
        threshold = fleet_threshold_seconds(peers)
        age = (now - since_ts).total_seconds()
        fleet_info = {
            "loop": _FLEET_LOOP_KEY,
            "leg": leg,
            "anchor": f"since:{since_ts.isoformat().replace('+00:00', 'Z')}",
            "bucket": _bucket_for(age, threshold),
            "age_seconds": age,
            "threshold_seconds": threshold,
            "state": None,
            "stale_peers": sorted(stale),
        }
        _reconcile_episode(fleet_info, open_recs, resolved_recs, ctx, result)
        result["fleet_collapse"] = True
        return

    for loop in sorted(stale):
        _reconcile_episode(stale[loop], open_recs, resolved_recs, ctx, result)


def detect_stale_loops(
    gh_caller: Optional[_GhCaller] = None,
    git_runner: Optional[_GitRunner] = None,
    portal_caller: Optional[_PortalCaller] = None,
    open_recs: Optional[_Rows] = None,
    resolved_recs: Optional[_Rows] = None,
    peers: Optional[dict[str, list[str]]] = None,
    now: Optional[datetime] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    profile: Optional[str] = None,
    dry_run: bool = False,
) -> _Info:
    """File/update exactly one deduped rec per stale loop PER LEG (or one fleet rec per leg on a
    correlated outage). Lazy and bounded (Decision 88): a healthy tick issues zero warehouse reads.
    `peers=None` inherits derive_scheduled_peers()'s fail-loud empty-set guard. Every other
    dependency is injected, so the module stays network-free to test. Two legs: CADENCE (stopped
    firing?) and SUCCESS (fires but never succeeds?) -- assess_success_leg is imported here, not at
    module scope, since that module imports `_assess_peer` from here (avoids a circular import)."""
    if now is None:
        now = datetime.now(timezone.utc)
    if peers is None:
        peers = derive_scheduled_peers()

    from scripts.convergence_health.sensor_liveness_success import assess_success_leg  # noqa: PLC0415

    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    caller = gh_caller or _make_github_caller(token)
    runner = git_runner or _default_git_runner
    owner, repo = repo_identity.resolve(owner, repo)
    api_base = f"https://api.github.com/repos/{owner}/{repo}"
    states = _query_workflow_states(caller, api_base)

    cadence: dict[str, _Info] = {}
    success: dict[str, _Info] = {}
    for loop, crons in sorted(peers.items()):
        c_info = _assess_peer(loop, crons, caller, runner, now, api_base)
        c_info["state"] = states.get(loop)
        c_info["leg"] = "cadence"
        cadence[loop] = c_info

        s_info = assess_success_leg(loop, crons, caller, runner, now, api_base)
        s_info["state"] = states.get(loop)
        s_info["leg"] = "success"
        success[loop] = s_info

    cadence_stale = {loop: info for loop, info in cadence.items() if info["bucket"] > 0}
    success_stale = {loop: info for loop, info in success.items() if info["bucket"] > 0}
    result: _Info = {
        "peers": len(peers),
        "stale": sorted(cadence_stale),
        "stale_success": sorted(success_stale),
        "filed": [],
        "updated": [],
        "unchanged": [],
        "dropped": [],
        "fleet_collapse": False,
    }
    if not cadence_stale and not success_stale:
        return result

    if open_recs is None:
        open_recs = find_recs("loop_liveness_stale", profile=profile)
    budget_max = 2 * (len(peers) + 1)
    budget = {"sweeps": 0, "sweeps_max": budget_max, "ids": 0, "ids_max": budget_max}
    ctx: _Info = {
        "budget": budget,
        "now": now,
        "portal_caller": portal_caller,
        "profile": profile,
        "dry_run": dry_run,
        "resolved_cache": {},
    }

    _reconcile_leg("cadence", cadence_stale, peers, open_recs, resolved_recs, ctx, result)
    _reconcile_leg("success", success_stale, peers, open_recs, resolved_recs, ctx, result)

    return result


def stale_peers_for_probe(
    workflow: Optional[str] = None,
    leg: str = "cadence",
    gh_caller: Optional[_GhCaller] = None,
    git_runner: Optional[_GitRunner] = None,
    peers: Optional[dict[str, list[str]]] = None,
    now: Optional[datetime] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
) -> list[str]:
    """Names of derived peers (or just `workflow`) past their threshold on `leg` ("cadence", the
    unchanged default, or "success"). Backs `--liveness-probe`'s acceptance oracle -- raises on an
    unknown `workflow` or a dead query rather than a reassuring empty list. The bare (cadence) form
    must keep classifying cadence alone, never "stale on either leg", or a success-only outage
    would regress a cadence rec's stored acceptance."""
    resolved_peers = peers if peers is not None else derive_scheduled_peers()
    if workflow is not None:
        if workflow not in resolved_peers:
            raise ValueError(f"{workflow!r} is not a derived scheduled peer")
        targets = {workflow: resolved_peers[workflow]}
    else:
        targets = resolved_peers

    if now is None:
        now = datetime.now(timezone.utc)
    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    caller = gh_caller or _make_github_caller(token)
    runner = git_runner or _default_git_runner
    owner, repo = repo_identity.resolve(owner, repo)
    api_base = f"https://api.github.com/repos/{owner}/{repo}"

    if leg == "cadence":

        def _assess(loop: str, crons: list[str]) -> _Info:
            return _assess_peer(loop, crons, caller, runner, now, api_base)
    else:
        from scripts.convergence_health.sensor_liveness_success import assess_success_leg  # noqa: PLC0415

        def _assess(loop: str, crons: list[str]) -> _Info:
            return assess_success_leg(loop, crons, caller, runner, now, api_base)

    return [loop for loop, crons in sorted(targets.items()) if _assess(loop, crons)["bucket"] > 0]
