"""CI fast-tier budget-block warehouse ingester (rec-3288 / D2-2b).

PR #968 put a machine-readable `budget` block into the selection manifest that ci.yml's
`pr-validate` job already uploads as the `selection-manifest` artifact on every run
(`if: always()`). Nothing read it. The `pr-validate` job is deliberately credential-free (it also
runs on fork PRs), so scripts/checks/_budget_recs.py's CI branch skips the portal write and only
mirrors the diagnostic to `$GITHUB_STEP_SUMMARY`; and ci-rca.yml's own manifest fetch is gated on
`head_branch == default_branch`. The residual that leaves: a fast-tier breach REDS pr-validate
(scripts/validate.py exits 1 immediately after recording the outcome, so the breaching run cannot
merge green), but the credential-free job cannot file the warehouse rec -- so the CI breach
population is invisible to the warehouse, and a breach on a PR head that is later fixed and merged
leaves no warehouse trace at all.

This module closes that gap from the convergence-health cron, which already carries the
credentials both legs need (`agent-platform-github-ci-branch` -> ducklake reader + writer) and
already runs the established alarm-only, one-rec-per-episode sensor pattern
(`tf_convergence_stale`, `ducklake_code_drift`, `prod_code_drift`).

EPISODE GRAIN and DEDUPE. One rec per `(branch, dominant_phase)` -- the exact key
`scripts.checks._budget_recs._find_open_budget_breach_rec` matches on, via the exact context
markers (`"Branch: {branch}."` / `"Dominant phase: {phase}."`) the local filing path writes. That
function is IMPORTED, never re-implemented: if the two ever disagreed, a locally-filed rec and an
ingested one would coexist for the same episode. Every ingested rec therefore carries
`source="budget_breach"` regardless of which breach-class outcome produced it (the outcome set is
recorded in the title and context instead) -- a second source value would be invisible to that
matcher and would double-file.

A RESOLVED REC IS NEVER RESURRECTED. The trigger here is an IMMUTABLE artifact inside its 14-day
retention window, not a live condition, so closing the rec does not remove the trigger: an
open-recs-only dedupe re-files a human-closed rec on the very next hourly tick, and keeps doing so
for up to 14 days. This module therefore consults recs in ANY status, taking the status-aware shape
scripts/ci_rca/dedup.py already established ("a CLOSED head is never bumped" -- a closed-head match
routes to drop, never to a bump or a reopen). MECHANISM, chosen as the simplest one the reachable
reader verbs support: `open_recs` cannot serve this at all (it filters `status = 'open'`
server-side), so `recs_by_title_prefix` -- any status, bound to `Fast-tier budget%on {branch}` --
narrows to that branch's budget recs, and `rec_by_id` then pulls each RESOLVED candidate's full row
so the SAME two (branch, dominant_phase) markers can be matched against its context. An episode
matching a resolved rec is DROPPED: no file, no update, no reopen (Decision 103 forbids the silent
reactivation regardless).

NO-OP UPDATES ARE SKIPPED. The update leg fires only when the computed title or context actually
differs from what the open rec already stores. Without that guard an unchanged episode writes one
identical SCD2 history row per hourly tick -- ~336 over an artifact's retention, times every
concurrently-open episode.

FILE/UPDATE ONLY, NEVER CLOSE. Unlike the drift sensors this one has no close leg: `budget_breach`
recs are also filed by the LOCAL validate path, and an episode ageing out of the artifact-retention
window (14 days) is not evidence its rec is resolved. Auto-closing on absence would silently close
locally-filed recs.

FAIL LOUDLY, NO OUTBOX (Decision 84 I-4). A portal write that cannot complete raises at the call
site -- nothing is buffered or staged for replay, and the next hourly tick re-derives the whole
population from the artifact window. A dead GitHub query likewise raises rather than reporting an
empty (and therefore reassuring) population -- Decision 55 anti-masking, the same posture
code_drift._assert_full_history takes against a shallow clone.

Every external dependency -- the GitHub API caller, the artifact fetcher, both rec lists (open and
resolved), the ops portal -- is injected, so the whole module is unit-testable with no network. Part of the
scripts.convergence_health package -- see scripts/convergence_health/__init__.py for the full
public surface.
"""

from __future__ import annotations

import io
import json
import os
import urllib.request
import zipfile
from typing import Any, Callable, Optional, cast

from scripts import repo_identity
from scripts.checks._budget_recs import _find_open_budget_breach_rec
from scripts.convergence_health.approvals import _make_github_caller
from scripts.rec_episode import find_recs

ARTIFACT_NAME = "selection-manifest"
MANIFEST_MEMBER = "selection-manifest.json"

# The budget outcomes worth a rec. "within_budget" is the denominator row build_budget_record
# writes on every reached assertion, and "forced_waived" is the Decision 153 forced-full-suite
# waiver -- neither is an abnormal outcome.
# "bypass" is deliberately absent: scripts/validate.py hard-rejects --ignore-budget when CI=true, so
# no CI-uploaded manifest can carry it, and this path would file it under the wrong source
# ("budget_breach"), hiding it from every budget_bypass consumer.
INGESTED_OUTCOMES: tuple[str, ...] = ("breach", "forced_ceiling_breach")

# Resolved (done) rec statuses. SoT: src.common.ducklake_scd2_schema.STATUS_TRANSITIONS
# ["ops_recommendations"]["resolved"] -- mirrored rather than imported to keep this module's import
# graph free of the schema layer; a test pins the two in step.
RESOLVED_REC_STATUSES: frozenset[str] = frozenset({"closed", "declined", "superseded"})

# The title prefix every budget rec shares -- this ingester's and the local validate path's
# (scripts/checks/_budget_recs._file_budget_breach_rec) alike. Bound into the
# `recs_by_title_prefix` LIKE pattern with the branch as the suffix, so the any-status sweep stays
# bounded to one branch's recs.
_BUDGET_TITLE_PREFIX = "Fast-tier budget"

# One page of the artifacts listing. The endpoint returns newest-first across every workflow, and
# pr-validate uploads one selection-manifest per PR run, so a single page normally spans the hourly
# cron interval. It is NOT self-healing above that: the next tick issues the identical newest-first
# query, so an artifact pushed past the page boundary is never seen again unless run volume drops.
# collect_budget_episodes therefore reads `total_count` off the response and warns loudly when the
# window is lossy, rather than reporting a silently truncated population.
DEFAULT_ARTIFACT_PAGE = 100

_DOWNLOAD_TIMEOUT_S = 30

_NO_TOKEN_MESSAGE = (
    "scripts.convergence_health.budget_ingest: cannot download a selection-manifest artifact with "
    "no GH_TOKEN/GITHUB_TOKEN in the environment. Refusing to degrade to an empty archive."
)

_DEAD_QUERY_MESSAGE = (
    "scripts.convergence_health.budget_ingest: the GitHub artifacts listing returned no payload "
    "(absent GH_TOKEN/GITHUB_TOKEN, or a failed query). Refusing to report an empty budget "
    "population from a query that never ran -- a dead sensor and a breach-free window must not "
    "look the same (Decision 55 anti-masking)."
)

_ALL_UNREADABLE_MESSAGE = (
    "scripts.convergence_health.budget_ingest: all {count} downloadable selection-manifest "
    "artifact(s) in the window failed to read (see the per-artifact diagnostics above). That is a "
    "broken download path, not a breach-free window -- refusing to report an empty budget "
    "population from it (Decision 55 anti-masking). A single unreadable artifact is tolerated and "
    "only counted."
)

_LOSSY_WINDOW_MESSAGE = (
    "[convergence_health] budget_ingest: WARNING -- the artifacts listing reports {total} "
    "{name} artifact(s) but only the newest {per_page} are swept (one page, no pagination). "
    "Breaches past that boundary are NOT ingested and will NOT be re-derived: the next tick issues "
    "the identical newest-first query. Raise per_page or add pagination."
)


class _AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Drop the GitHub `Authorization` header when the artifact endpoint redirects off-host.

    `/actions/artifacts/{id}/zip` answers 302 with a pre-signed blob-storage URL. curl strips
    credentials across a host change; urllib does not (it copies every header but Content-*), and
    the storage backend rejects the request outright when an unrecognised Authorization header
    rides along. Without this the download works under every mock and fails only in production.
    """

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def _api_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _make_artifact_fetcher(token: str) -> Callable[[str], bytes]:
    """Return a callable(archive_download_url) -> zip bytes, bound to `token`.

    Separate from approvals._make_github_caller (which parses JSON and degrades to None on an
    absent token) because an artifact download is binary and must never degrade silently: a
    tokenless call raises.
    """
    opener = urllib.request.build_opener(_AuthStrippingRedirectHandler)

    def _fetch(url: str) -> bytes:
        if not token:
            raise RuntimeError(_NO_TOKEN_MESSAGE)
        request = urllib.request.Request(url, headers=_api_headers(token))
        with opener.open(request, timeout=_DOWNLOAD_TIMEOUT_S) as response:  # noqa: S310
            return bytes(response.read())

    return _fetch


def extract_budget_block(archive: bytes) -> Optional[dict[str, Any]]:
    """Return the selection manifest's `budget` block from one artifact archive, or None.

    None means "this artifact carries no budget block" -- a pre-#968 run, or a manifest whose
    `budget` key is not a mapping. A corrupt archive or malformed JSON RAISES instead: the caller
    counts it as unreadable and says so, rather than folding a broken artifact into the
    indistinguishable "no breach here" bucket.
    """
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        if MANIFEST_MEMBER not in bundle.namelist():
            return None
        payload = json.loads(bundle.read(MANIFEST_MEMBER).decode("utf-8"))
    if not isinstance(payload, dict):
        return None
    budget = payload.get("budget")
    return budget if isinstance(budget, dict) else None


def _episode_from(budget: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    """Normalise one budget block plus its artifact envelope into an episode record.

    The block's own GITHUB_* identity wins; the artifact's `workflow_run` is the fallback for the
    "unknown" degradations build_budget_record writes when an env var was absent. `dominant_phase`
    degrades to "unknown" exactly as _file_budget_breach_rec's `dedup_phase` does, so both filing
    paths key on the same string.
    """
    run = artifact.get("workflow_run") or {}
    branch = str(budget.get("branch") or "").strip()
    if not branch or branch == "unknown":
        branch = str(run.get("head_branch") or "unknown")
    run_id = str(budget.get("run_id") or "").strip()
    if not run_id or run_id == "unknown":
        run_id = str(run.get("id") or "unknown")
    return {
        "branch": branch,
        "dominant_phase": str(budget.get("dominant_phase") or "unknown"),
        "outcome": str(budget.get("outcome") or "unknown"),
        "elapsed_s": float(budget.get("elapsed_s") or 0.0),
        "limit_s": float(budget.get("limit_s") or 0.0),
        "run_id": run_id,
        "repository": str(budget.get("repository") or "").strip() or "unknown",
        "head_sha": str(run.get("head_sha") or "unknown"),
        "artifact_id": artifact.get("id"),
        "created_at": str(artifact.get("created_at") or ""),
    }


def collect_budget_episodes(
    gh_caller: Optional[Callable[[str], Any]] = None,
    artifact_fetcher: Optional[Callable[[str], bytes]] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    per_page: int = DEFAULT_ARTIFACT_PAGE,
) -> dict[str, Any]:
    """Enumerate recent `selection-manifest` artifacts and extract their budget blocks.

    Returns counters plus the episode list: `scanned` (artifacts of that name seen), `total_count`
    (what the endpoint says exists -- larger than `scanned` means the window is lossy and a loud
    warning was printed), `expired` (retention lapsed -- never downloaded), `without_budget`
    (pre-#968 manifests), `unreadable` (corrupt archive or malformed JSON, reported loudly and
    skipped) and `episodes`.
    """
    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    caller = gh_caller or _make_github_caller(token)
    fetcher = artifact_fetcher or _make_artifact_fetcher(token)

    owner, repo = repo_identity.resolve(owner, repo)
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/artifacts?name={ARTIFACT_NAME}&per_page={per_page}"
    data = caller(url)
    if not data:
        raise RuntimeError(_DEAD_QUERY_MESSAGE)

    scanned = expired = without_budget = unreadable = 0
    episodes: list[dict[str, Any]] = []
    for artifact in data.get("artifacts") or []:
        if artifact.get("name") != ARTIFACT_NAME:
            continue
        scanned += 1
        if artifact.get("expired"):
            expired += 1
            continue
        try:
            budget = extract_budget_block(fetcher(str(artifact.get("archive_download_url") or "")))
            episode = _episode_from(budget, artifact) if budget is not None else None
        except Exception as exc:  # noqa: BLE001
            unreadable += 1
            print(f"[convergence_health] budget_ingest: unreadable {ARTIFACT_NAME} artifact {artifact.get('id')}: {exc}")
            continue
        if episode is None:
            without_budget += 1
            continue
        episodes.append(episode)

    downloadable = scanned - expired
    if downloadable and unreadable == downloadable:
        raise RuntimeError(_ALL_UNREADABLE_MESSAGE.format(count=downloadable))

    reported = data.get("total_count")
    total_count = reported if isinstance(reported, int) and not isinstance(reported, bool) else scanned
    if total_count > per_page:
        print(_LOSSY_WINDOW_MESSAGE.format(total=total_count, name=ARTIFACT_NAME, per_page=per_page))

    return {
        "scanned": scanned,
        "total_count": total_count,
        "expired": expired,
        "without_budget": without_budget,
        "unreadable": unreadable,
        "episodes": episodes,
    }


def _group_episodes(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse breach-class episodes onto one group per (branch, dominant_phase).

    That pair IS the rec grain -- the same key _find_open_budget_breach_rec dedupes on -- so a
    branch that breached five times in the window yields one rec, not five.

    `worst` is the whole WORST episode, not just its elapsed_s: a group can mix outcomes whose
    limits differ (`breach` at 300s with `forced_ceiling_breach` at 1500s), and pairing the group
    max elapsed with some other episode's limit_s reports a ratio against a limit the worst run
    never had -- exactly what scripts/validate.py's _BUDGET_LIMIT_BY_OUTCOME exists to prevent.
    Ties keep the first-seen episode (the listing is newest-first).
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for episode in episodes:
        if episode["outcome"] not in INGESTED_OUTCOMES:
            continue
        key = (episode["branch"], episode["dominant_phase"])
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "branch": key[0],
                "dominant_phase": key[1],
                "runs": 1,
                "outcomes": [episode["outcome"]],
                "worst": episode,
            }
            continue
        group["runs"] += 1
        if episode["outcome"] not in group["outcomes"]:
            group["outcomes"].append(episode["outcome"])
        if episode["elapsed_s"] > group["worst"]["elapsed_s"]:
            group["worst"] = episode
    return list(groups.values())


def _outcome_label(group: dict[str, Any]) -> str:
    return "+".join(sorted(group["outcomes"]))


def _build_ingest_context(group: dict[str, Any]) -> str:
    """Context for one ingested episode.

    Carries the two dedupe markers VERBATIM ("Branch: {branch}." / "Dominant phase: {phase}.") --
    they are the matcher's whole contract, not decoration.
    """
    worst = group["worst"]
    elapsed_min = worst["elapsed_s"] / 60
    limit_min = worst["limit_s"] / 60
    return (
        f"CI fast-tier budget outcome(s) [{_outcome_label(group)}] observed in {group['runs']} "
        f"pr-validate selection-manifest artifact(s) in the scanned window: worst elapsed "
        f"{elapsed_min:.1f} min (limit {limit_min:.1f} min). "
        f"Branch: {group['branch']}. Dominant phase: {group['dominant_phase']}. "
        f"Worst run: https://github.com/{worst['repository']}/actions/runs/{worst['run_id']} "
        f"(head {worst['head_sha'][:12]}). "
        "The pr-validate job is credential-free, so the local filing path "
        "(scripts/checks/_budget_recs.py) deliberately skips the portal write in CI and only "
        "mirrors the diagnostic to the step summary. The breach itself reds pr-validate, so the "
        "run never merges green -- but nothing recorded it in the warehouse, and a breach on a PR "
        "head that is later fixed and merged leaves no warehouse trace at all; this "
        "convergence-health sensor is the warehouse-ingestion leg for that population. Deduped on "
        "(branch, dominant_phase) against the same markers the local path writes, so a repeat "
        "breach updates this rec rather than filing a second one -- and once this rec is resolved "
        "the episode is dropped, never re-filed. Investigate which check caused the overrun and "
        "move it to the full tier or optimise it."
    )


def _build_ingest_rec_fields(group: dict[str, Any]) -> dict[str, Any]:
    elapsed_min = group["worst"]["elapsed_s"] / 60
    return {
        "title": f"Fast-tier budget {_outcome_label(group)} ({elapsed_min:.1f} min) on {group['branch']}",
        "file": "scripts/validate.py",
        "status": "open",
        "source": "budget_breach",
        "effort": "S",
        "priority": "Medium",
        "context": _build_ingest_context(group),
        "acceptance": "bin/venv-python -m scripts.validate --pre",
        "risk": "low",
        "automatable": False,
    }


def _fetch_resolved_budget_recs(branch: str, profile: Optional[str] = None) -> list[dict[str, Any]]:
    """Fetch this branch's RESOLVED budget_breach recs from the DuckLake reader (live, never JSONL).

    Two named verbs, because no single verb returns non-open recs WITH their context:
    `recs_by_title_prefix` (id/title/status/source, any status) narrows to this branch's budget
    recs, then `rec_by_id` pulls the full row -- context included -- for each resolved candidate.
    Precedent: scripts/ops_portal/maintenance_ops.purge_postmortems_for does the same any-status
    prefix sweep plus exact client-side re-filter.

    The LIKE pattern is a bound param, never interpolated SQL, and an over-broad match (a `_` in a
    branch name is a LIKE wildcard) is harmless -- the caller re-filters on the exact markers.
    """
    from src.common.ducklake_reader_client import DuckLakeReader, make_reader  # noqa: PLC0415

    # make_reader() is annotated -> Reader (the Protocol, which deliberately does not declare
    # named()) but only ever constructs a DuckLakeReader; cast narrows the type here rather than
    # widening the Protocol by omission -- the same call-site cast scripts/agent_sdk/mcp_server.py
    # applies for describe().
    reader = cast(DuckLakeReader, make_reader(profile=profile))
    rows = reader.named("recs_by_title_prefix", title_prefix=f"{_BUDGET_TITLE_PREFIX}%on {branch}") or []
    resolved: list[dict[str, Any]] = []
    for row in rows:
        if row.get("source") != "budget_breach" or row.get("status") not in RESOLVED_REC_STATUSES:
            continue
        resolved.extend(reader.named("rec_by_id", id=row.get("id")) or [])
    return resolved


def _find_resolved_budget_breach_rec(recs: list[dict[str, Any]], branch: str, dominant_phase: str) -> Optional[dict[str, Any]]:
    """Return the first RESOLVED budget_breach rec matching (branch, dominant_phase), or None.

    Deliberately mirrors _find_open_budget_breach_rec's marker contract -- the same two context
    substrings, the opposite status half -- so the open and resolved halves of the dedupe can never
    disagree about what one episode is.
    """
    branch_marker = f"Branch: {branch}."
    phase_marker = f"Dominant phase: {dominant_phase}."
    for rec in recs:
        if rec.get("source") != "budget_breach" or rec.get("status") not in RESOLVED_REC_STATUSES:
            continue
        context = rec.get("context") or ""
        if branch_marker in context and phase_marker in context:
            return rec
    return None


def _decide_action(
    existing: Optional[dict[str, Any]], resolved: Optional[dict[str, Any]], fields: dict[str, Any]
) -> tuple[str, Optional[str]]:
    """Return (decision, rec_id) for one episode: update | unchanged | drop | file.

    `unchanged` is the no-op guard: an identical title AND context means the hourly tick has
    nothing to say, and writing it anyway costs one duplicate SCD2 history row per tick.
    """
    if existing is not None:
        same = existing.get("title") == fields["title"] and existing.get("context") == fields["context"]
        return ("unchanged" if same else "update"), existing["id"]
    if resolved is not None:
        return "drop", resolved["id"]
    return "file", None


def _portal_file(
    fields: dict[str, Any],
    portal_caller: Optional[Callable[[str, dict[str, Any]], Any]],
    profile: Optional[str],
) -> Any:
    if portal_caller is not None:
        return portal_caller("file", fields)
    from scripts.ops_data_portal import file_rec  # noqa: PLC0415

    return file_rec(fields, profile=profile)


def _portal_update(
    rec_id: str,
    updates: dict[str, Any],
    portal_caller: Optional[Callable[[str, dict[str, Any]], Any]],
    profile: Optional[str],
) -> None:
    if portal_caller is not None:
        portal_caller("update", {"id": rec_id, **updates})
        return
    from scripts.ops_data_portal import update_rec  # noqa: PLC0415

    update_rec(rec_id, updates, profile=profile)


_DRY_RUN_LABELS = {"file": "would_file", "update": "would_update", "unchanged": "would_skip", "drop": "would_drop"}


def _resolved_match_for(
    group: dict[str, Any],
    resolved_recs: Optional[list[dict[str, Any]]],
    cache: dict[str, list[dict[str, Any]]],
    profile: Optional[str],
) -> Optional[dict[str, Any]]:
    """Resolved-rec half of the dedupe for one group, fetched once per branch and memoised."""
    branch = group["branch"]
    if resolved_recs is None:
        if branch not in cache:
            cache[branch] = _fetch_resolved_budget_recs(branch, profile=profile)
        candidates = cache[branch]
    else:
        candidates = resolved_recs
    return _find_resolved_budget_breach_rec(candidates, branch, group["dominant_phase"])


def ingest_budget_breaches(
    gh_caller: Optional[Callable[[str], Any]] = None,
    artifact_fetcher: Optional[Callable[[str], bytes]] = None,
    portal_caller: Optional[Callable[[str, dict[str, Any]], Any]] = None,
    open_recs: Optional[list[dict[str, Any]]] = None,
    resolved_recs: Optional[list[dict[str, Any]]] = None,
    owner: Optional[str] = None,
    repo: Optional[str] = None,
    per_page: int = DEFAULT_ARTIFACT_PAGE,
    profile: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """File or update exactly one deduped budget_breach rec per (branch, dominant_phase) episode.

    Args:
        gh_caller:        Injected callable(url) -> parsed JSON, mirroring approvals.py. When None,
                          built from GH_TOKEN/GITHUB_TOKEN.
        artifact_fetcher: Injected callable(archive_download_url) -> zip bytes. When None, built
                          from the same token.
        portal_caller:    Injected callable(action, fields) for testability, mirroring escalate().
                          When None, uses scripts.ops_data_portal.file_rec / update_rec directly.
        open_recs:        Pre-fetched open budget_breach rec list (for testing). When None,
                          fetched live via scripts.rec_episode.find_recs's source-scoped
                          structural read (current_state on ops_recommendations, row_filter=
                          "source = 'budget_breach'") -- never a bulk fetch of every open rec,
                          never the JSONL read cache. Fetched ONLY when there is at least one
                          episode to file against.
        resolved_recs:    Pre-fetched resolved (closed/declined/superseded) budget_breach recs (for
                          testing). When None, fetched live per branch via _fetch_resolved_budget_recs,
                          and ONLY for an episode with no open match -- see the module docstring's
                          resurrection section.
        owner/repo:       Repository whose artifacts are swept.
        per_page:         Artifacts listing page size.
        profile:          AWS profile for the reader / portal.
        dry_run:          Report what would be filed or updated and touch neither the portal nor
                          anything else. What operators and tests use; the workflow does not.

    Returns:
        {"scanned", "total_count", "expired", "without_budget", "unreadable", "episodes", "groups",
         "dry_run", "actions": [{"action", "rec_id", "branch", "dominant_phase", "runs"}]}
        where action is one of file | update | unchanged | drop (or the matching would_file |
        would_update | would_skip | would_drop under dry_run).
    """
    scan = collect_budget_episodes(
        gh_caller=gh_caller, artifact_fetcher=artifact_fetcher, owner=owner, repo=repo, per_page=per_page
    )
    groups = sorted(_group_episodes(scan["episodes"]), key=lambda g: (g["branch"], g["dominant_phase"]))

    actions: list[dict[str, Any]] = []
    if groups and open_recs is None:
        open_recs = find_recs("budget_breach", profile=profile)
    resolved_cache: dict[str, list[dict[str, Any]]] = {}

    for group in groups:
        fields = _build_ingest_rec_fields(group)
        existing = _find_open_budget_breach_rec(open_recs or [], group["branch"], group["dominant_phase"])
        resolved = None if existing is not None else _resolved_match_for(group, resolved_recs, resolved_cache, profile)
        decision, rec_id = _decide_action(existing, resolved, fields)
        if dry_run:
            action = _DRY_RUN_LABELS[decision]
            print(f"[convergence_health] budget_ingest DRY-RUN {action}: {json.dumps(fields, sort_keys=True)}")
        elif decision == "file":
            rec_id = _portal_file(fields, portal_caller, profile)
            action = "file"
        elif decision == "update":
            _portal_update(str(rec_id), {"title": fields["title"], "context": fields["context"]}, portal_caller, profile)
            action = "update"
        else:
            action = decision
        actions.append(
            {
                "action": action,
                "rec_id": rec_id,
                "branch": group["branch"],
                "dominant_phase": group["dominant_phase"],
                "runs": group["runs"],
            }
        )

    return {
        "scanned": scan["scanned"],
        "total_count": scan["total_count"],
        "expired": scan["expired"],
        "without_budget": scan["without_budget"],
        "unreadable": scan["unreadable"],
        "episodes": len(scan["episodes"]),
        "groups": len(groups),
        "dry_run": dry_run,
        "actions": actions,
    }
