"""Closure-time artifact obligation for escape-classified recs (Decision 186); Decision 201's
trailer-acceptance verdict precondition (ACCEPTANCE_VERDICTS, AcceptanceVerdictRefused,
assert_acceptance_verdict) shares this module as its single predicate home, beside the closure
obligation it is asserted next to at update_rec's own single enforcement site.

Owner-concern: the escape-classified predicate, the `<kind>:<ref>` artifact grammar and its
FIX-BOUND resolver for the four kinds, the categorised waiver vocabulary, and the single
ClosureArtifactRequired(ValueError). Decomposed into its own module (Decision 128: ops_data_portal.py
had 78 SLOC of headroom, not enough for this body) and called from
scripts.ops_data_portal::update_rec immediately before _ducklake_write -- the single enforcement
site every path closing a writer-allocated rec-NNN funnels through.

RESOLUTION IS FIX-BOUND, NOT EXISTENCE-ONLY (Decision 186 point 4): a `<kind>:<ref>` token
resolves only when (i) its kind-specific fact holds AND (ii) its defining file was added or
modified in the closing write's `fixed_by_sha`. `check:<name>` resolves against the named
registry entry's TIER fact (scripts.checks._schema.Entry.pre is True), NEVER bare membership in
scripts.checks.registry._ALL_ENTRIES -- membership alone is vacuous for the majority escape mode
(tier_misplaced), whose catching check is already registered before any fix lands.

Never remediates (Decision 55/72): every function here either resolves a fact or raises. Nothing
in this module writes, files, retries, or downgrades a status. The single subprocess call is a
read-only local `git diff-tree` -- no network, no reader egress (Decision 88).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess  # noqa: S404 -- one read-only local `git diff-tree` call; see module docstring
from pathlib import Path
from typing import Optional

from scripts.ops_portal._common import ROOT

# Declared strength ordering (contract: docs/contracts/ci-rca-lifecycle.yaml::closure_obligation.
# kind_strength) -- strongest first. Only "shard:" carries the audit's still-red-on-reintroduction
# property (the fail-on-revert differential); the other three prove existence, not discrimination.
KIND_STRENGTH: tuple[str, ...] = ("shard", "pytest", "check", "fixture")
ARTIFACT_KINDS: frozenset[str] = frozenset(KIND_STRENGTH)

# The ratified FOUR-MEMBER seed vocabulary (Decision 186 point 5/8). duplicate_of is DELIBERATELY
# ABSENT -- human ruling: a duplicate asserts an artifact is OWED (cites the class artifact,
# fix-bound like any other), never that one is infeasible. Adding, dropping or renaming a member
# is a numbered-Decision event (point 8) -- hand back, never edit this set at implement time.
WAIVER_CATEGORIES: frozenset[str] = frozenset(
    {
        "stale_no_recurrence",
        "environment_only",
        "no_premerge_gate_by_design",
        "risk_accepted",
    }
)

_ARTIFACT_TOKEN_RE = re.compile(r"^(shard|pytest|check|fixture):(.+)$")
_FIXTURES_ROOT = "tests/fixtures/"
_BOUND_STATUSES = frozenset({"closed", "declined", "superseded"})


class ClosureArtifactRequired(ValueError):
    """Raised when an escape-classified rec would close with no resolvable closure_artifact and
    no well-formed closure_waiver_category/closure_waiver_reason pair. Writes nothing -- the
    caller (scripts.ops_data_portal::update_rec) never reaches _ducklake_write on this path."""


def parse_context_json(raw: object) -> dict:
    """Best-effort parse of a context_v2_json cell (JSON string, dict, or None/malformed) into a
    plain dict. Never raises -- an absent or malformed blob parses to {}, the same permissive
    shape ci_rca_lifecycle's own read-mutate-write helpers use. Shared by update_rec's two
    parses (existing + merged) so a single call site owns this permissive-parse convention."""
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw) if isinstance(raw, str) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def closure_stamps_applicable(raw: object) -> bool:
    """True iff raw parses (via parse_context_json) to a non-empty dict -- the STAMPABILITY
    precondition for a closure_* kwarg, evaluated in the PARSED-DICT domain. This is deliberately
    NOT Decision 186 point 1's escape-class predicate (is_escape_classified): a rec can be
    stampable without being escape-classified, and vice versa is never asserted here. Kept out of
    assert_closure_obligation's call path so it never reads as a second gate.

    The parsed domain is the only one consistent across this predicate's three sanctioned
    consumers -- scripts/ops_data_portal.py (update_rec), scripts/ops_portal/ci_rca_lifecycle.py
    (close_recs_from_trailer) and scripts/ci_rca/inactivity_sweep.py (close_inactive_recs) --
    since the trailer path holds a raw JSON cell while the sweep holds an already-parsed dict, and
    a bare bool() over the raw cell would disagree between them ("{}" is truthy, {} is not).
    """
    return bool(parse_context_json(raw))


def is_escape_classified(ctx: dict) -> bool:
    """True when ctx['escape_class'] OR ctx['detection_gap']['escape_mode'] is SET.

    'undetermined' counts as set for detection_gap.escape_mode (Decision 186 point 1) -- this is
    the probe's own abstention value, not an absence. Never keys on failure_category ==
    'gate_escape', which config/ci_rca_taxonomy.yaml's agent_only_categories means is never
    actually emitted (matches zero rows).
    """
    if not isinstance(ctx, dict):
        return False
    if ctx.get("escape_class"):
        return True
    detection_gap = ctx.get("detection_gap")
    return bool(isinstance(detection_gap, dict) and detection_gap.get("escape_mode"))


def _check_kind_fact(name: str, root: Path) -> tuple[bool, list[str]]:
    """check:<name> -- the named registry entry's TIER fact (Entry.pre is True), never bare
    _ALL_ENTRIES membership. Function-local import of the registry module only (never
    full_sequence(), which imports every check module just to answer a membership question)."""
    from scripts.checks import registry  # noqa: PLC0415

    entry = registry._ALL_ENTRIES.get(name)
    if entry is None or not entry.pre:
        return False, []
    module_parts = entry.module.split(".")
    domain = module_parts[2] if len(module_parts) > 2 else ""
    defining_files = [str(Path(*module_parts)) + ".py"]
    if domain:
        defining_files.append(str(Path("scripts", "checks", domain, "_manifest.py")))
    return True, [f.replace("\\", "/") for f in defining_files]


def _shard_kind_fact(shard_id: str, root: Path) -> tuple[bool, list[str]]:
    """shard:<id> -- config/agent/verification_registry/entries/<id>.yaml exists."""
    rel = f"config/agent/verification_registry/entries/{shard_id}.yaml"
    if not (root / rel).exists():
        return False, []
    return True, [rel]


_PYTEST_DEF_RE_TEMPLATE = r"^\s*(?:async\s+)?def\s+{name}\b"


def _pytest_kind_fact(nodeid: str, root: Path) -> tuple[bool, list[str]]:
    """pytest:<nodeid> -- the test file exists AND contains a matching `def` line for the node
    id's own function name (the last '::'-separated segment; class-scoped node ids are supported
    the same way, since only the final segment is a def name)."""
    file_part = nodeid.split("::", 1)[0]
    func_name = nodeid.rsplit("::", 1)[-1]
    path = root / file_part
    if not file_part or file_part == nodeid or not path.is_file():
        return False, []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False, []
    pattern = re.compile(_PYTEST_DEF_RE_TEMPLATE.format(name=re.escape(func_name)), re.MULTILINE)
    if not pattern.search(text):
        return False, []
    return True, [file_part]


def _fixture_kind_fact(rel_path: str, root: Path) -> tuple[bool, list[str]]:
    """fixture:<path> -- the path exists AND is under tests/fixtures/. Unconstrained, a bare
    fixture:README.md would resolve while proving nothing -- the root constraint is load-bearing."""
    normalized = rel_path.replace("\\", "/")
    if not normalized.startswith(_FIXTURES_ROOT):
        return False, []
    if not (root / normalized).exists():
        return False, []
    return True, [normalized]


_KIND_FACT_RESOLVERS = {
    "check": _check_kind_fact,
    "shard": _shard_kind_fact,
    "pytest": _pytest_kind_fact,
    "fixture": _fixture_kind_fact,
}


def changed_files(sha: Optional[str], root: Path) -> Optional[set[str]]:
    """The set of file paths changed by `sha`'s diff-tree. None if `sha` is falsy or unresolvable
    (non-zero exit), preserving the existing fail-closed posture callers rely on (Decision 55),
    mirroring classify_closed_head's posture. No fetch, no network: an unresolvable sha is the
    caller's problem to fix (e.g. a shallow checkout), never this module's to paper over. The
    single read-only local `git diff-tree` call this module makes; see module docstring."""
    if not sha:
        return None
    result = subprocess.run(  # noqa: S603 -- literal argv, read-only git verb; see module docstring
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


_TEST_PATH_PREFIX = "tests/"
_BOOKKEEPING_PREFIXES = ("docs/plans/", "config/agent/verification_registry/entries/")


def confirmed_repair_track(fix_sha: Optional[str], root: Path) -> Optional[str]:
    """Decision 226 cl.1: the track a fix commit's diff confirms. None when the sha is unresolvable or only
    plan/registry bookkeeping changed; test_defect when every remaining path is under tests/."""
    touched = changed_files(fix_sha, root)
    if not touched:
        return None
    remaining = [f for f in touched if not f.startswith(_BOOKKEEPING_PREFIXES)]
    if not remaining:
        return None
    return "test_defect" if all(f.startswith(_TEST_PATH_PREFIX) for f in remaining) else "product_regression"


def confirmed_track_stamp(source: Optional[str], fix_sha: Optional[str], root: Path) -> dict:
    if source != "ci_rca":
        return {}
    track = confirmed_repair_track(fix_sha, root)
    return {"repair_track_confirmed": track} if track else {}


def _fix_commit_touches(defining_files: list[str], fix_sha: Optional[str], root: Path) -> bool:
    """True iff fix_sha resolves (via changed_files()) and its changed-file list intersects
    defining_files. Delegates the diff-tree call to changed_files() -- an unresolvable/falsy
    fix_sha yields None, which never intersects, preserving the prior fail-closed behaviour."""
    touched = changed_files(fix_sha, root)
    if touched is None:
        return False
    return any(f in touched for f in defining_files)


def resolve_closure_artifact(token: Optional[str], fix_sha: Optional[str], root: Optional[Path] = None) -> bool:
    """Full FIX-BOUND resolution of one `<kind>:<ref>` token. Never raises -- returns False for
    any parse failure, unresolved kind-fact, or unresolvable/non-touching fix commit.

    Never dereferences a rec id: the strict `<kind>:<ref>` grammar (kind in ARTIFACT_KINDS) means
    a bare 'rec-3132'-shaped token simply fails to parse and resolves False -- no promise transfer
    is even expressible here.
    """
    if not token:
        return False
    resolved_root = root if root is not None else ROOT
    match = _ARTIFACT_TOKEN_RE.match(token)
    if not match:
        return False
    kind, ref = match.group(1), match.group(2)
    if not ref:
        return False
    holds, defining_files = _KIND_FACT_RESOLVERS[kind](ref, resolved_root)
    if not holds:
        return False
    return _fix_commit_touches(defining_files, fix_sha, resolved_root)


def artifact_exists(token: Optional[str], root: Optional[Path] = None) -> bool:
    """Kind-fact-only existence check for a `<kind>:<ref>` token -- STATIC, deliberately ignoring
    the fix-commit binding (that leg only matters at write-closure time, never at a later
    read-time re-check). Used by scripts.ci_rca.back_validation to grade whether a prior rec's
    named artifact SURVIVES today, not whether it was originally fix-bound. Never raises."""
    if not token:
        return False
    resolved_root = root if root is not None else ROOT
    match = _ARTIFACT_TOKEN_RE.match(token)
    if not match:
        return False
    kind, ref = match.group(1), match.group(2)
    if not ref:
        return False
    holds, _defining_files = _KIND_FACT_RESOLVERS[kind](ref, resolved_root)
    return holds


def is_valid_waiver(category: Optional[str], reason: Optional[str]) -> bool:
    """True iff category is a MEMBER of WAIVER_CATEGORIES (not merely well-shaped) and reason is
    non-empty. An unknown category is REFUSED here -- membership is the acceptance bar, or
    Decision 186 point 8's ratchet is defeatable by typing a new category into a string."""
    if not isinstance(category, str) or category not in WAIVER_CATEGORIES:
        return False
    return isinstance(reason, str) and bool(reason.strip())


def assert_closure_obligation(
    existing_status: Optional[str],
    new_status: Optional[str],
    existing_ctx: dict,
    merged_ctx: dict,
    root: Optional[Path] = None,
) -> None:
    """Raise ClosureArtifactRequired iff this write closes an escape-classified rec with no
    resolvable closure_artifact and no well-formed waiver. No-op (returns None) otherwise.

    Fires on a transition INTO a bound status ({closed, declined, superseded}) FROM a status not
    already bound ('terminal' defined on the FROM side, Decision 186 point 3) -- this is what
    gates the first hop of an `open -> declined -> superseded` route while leaving
    `declined -> superseded` and every status-preserving write (stamp_fixed_by_sha, bumps,
    corrections) ungated, since the obligation was already discharged at the first hop. `failed`
    is deliberately NOT in the bound set -- it is a live, non-resolved status.

    The predicate is evaluated over existing_ctx UNION merged_ctx: context_v2_json carries no
    contract rule and no monotonicity guard,
    so update_rec(id, {"status": "closed", "context_v2_json": "{}"}) must still be refused --
    reading merged_ctx alone would let the closing write erase its own classification.
    """
    if new_status not in _BOUND_STATUSES or existing_status in _BOUND_STATUSES:
        return
    if not (is_escape_classified(existing_ctx) or is_escape_classified(merged_ctx)):
        return

    resolved_root = root if root is not None else ROOT
    artifact = merged_ctx.get("closure_artifact")
    fix_sha = merged_ctx.get("fixed_by_sha")
    category = merged_ctx.get("closure_waiver_category")
    reason = merged_ctx.get("closure_waiver_reason")

    if resolve_closure_artifact(artifact, fix_sha, root=resolved_root):
        return
    if is_valid_waiver(category, reason):
        return

    raise ClosureArtifactRequired(
        "escape-classified rec cannot close without a landed, FIX-BOUND artifact or a categorised "
        "waiver. Supply --closure-artifact <shard:id|pytest:nodeid|check:name|fixture:path> together "
        "with --closure-fix-sha <sha> (the fix commit that touches the artifact's defining file), or "
        f"--closure-waiver-category <one of {sorted(WAIVER_CATEGORIES)}> plus --closure-waiver-reason "
        '"<proof>". See docs/contracts/ci-rca-lifecycle.yaml::closure_obligation (Decision 186).'
    )


# The ratified FOUR-VALUE trailer-acceptance vocabulary (Decision 201). A separate axis from
# docs/contracts/vp-red-before.yaml's OUTCOME_CLASSES -- this one classifies whether a rec's own
# oracle HOLDS at the closing commit, not whether a VP step correctly failed before a fix existed.
HOLDS = "holds"
FAILS = "fails"
UNMEASURABLE = "unmeasurable"
OUT_OF_GRAMMAR = "out_of_grammar"

ACCEPTANCE_VERDICTS: frozenset[str] = frozenset({HOLDS, FAILS, UNMEASURABLE, OUT_OF_GRAMMAR})


class AcceptanceVerdictRefused(ValueError):
    """Raised when a supplied acceptance_verdict record does not resolve to a holds verdict
    correctly keyed to this rec, its current acceptance text and the closing sha. Writes nothing
    -- the caller (scripts.ops_data_portal::update_rec) never reaches _ducklake_write on this
    path. This predicate alone never makes an ABSENT verdict a refusal (see
    assert_acceptance_verdict); the structural binding that keeps a trailer-named rec with no
    verdict out of update_rec entirely lives one layer up, in
    ci_rca_lifecycle.close_recs_from_trailer's require_acceptance_verdict flag (Decision 163)."""


def assert_acceptance_verdict(
    existing_status: Optional[str],
    new_status: Optional[str],
    rec_id: str,
    acceptance: Optional[str],
    closing_sha: Optional[str],
    record: Optional[dict],
) -> None:
    """Raise AcceptanceVerdictRefused iff this write is a transition INTO a bound status
    ({closed, declined, superseded}) FROM a status not already bound, `record` IS supplied, and
    it is not a holds verdict correctly keyed to `rec_id`, the sha256 of the CURRENT `acceptance`
    text, and `closing_sha`. No-op (returns None) when `record` is None or the write is not that
    first-hop transition -- mirroring assert_closure_obligation's own gating (Decision 186 point
    3), so a status-preserving write or a later declined -> superseded hop is never refused here.

    `closing_sha` is compared against `record["sha"]` as an INDEPENDENT gate input, supplied by
    the caller from its own commit_sha, never read back out of `record` itself -- doing the
    latter would make the sha arm a self-comparison, vacuously true in production while a direct
    call to this predicate (as in the VP suite) still stayed green.
    """
    if record is None:
        return
    if new_status not in _BOUND_STATUSES or existing_status in _BOUND_STATUSES:
        return
    acceptance_sha256 = hashlib.sha256((acceptance or "").encode("utf-8")).hexdigest()
    if (
        record.get("verdict") != HOLDS
        or record.get("rec_id") != rec_id
        or record.get("acceptance_sha256") != acceptance_sha256
        or record.get("sha") != closing_sha
    ):
        raise AcceptanceVerdictRefused(
            f"{rec_id}: acceptance_verdict record does not resolve to a holds verdict keyed to this "
            f"rec, its current acceptance text and closing sha {closing_sha!r} -- record={record!r}. "
            "See docs/contracts/git-ops.yaml::trailer_acceptance_gate (Decision 201)."
        )
