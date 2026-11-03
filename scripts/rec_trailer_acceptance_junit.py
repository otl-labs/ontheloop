"""Trailer-acceptance junit verdict SOURCE (Decision 201, slice B).

Sibling of scripts/rec_trailer_acceptance.py (Decision 128 decompose-by-default: a new concern
gets a new module, not growth of an unmeasured one). Pure and filesystem-only -- reads no
warehouse, executes no command, touches no network.

Two independent concerns:

  GRAMMAR_ARMS  -- a DECLARED-cell classification of an acceptance command's shape onto exactly
                   one of three ROUTEs: junit, refuse (out_of_grammar), or static (slice A's
                   probe path, unchanged). Computed from the command TEXT ALONE -- no report
                   read, no disk resolution, no execution -- so the routing partition cannot
                   fail open. classify_grammar() is the entry point; rec_trailer_acceptance.py's
                   census stage calls it for every acceptance is_pytest_command() admits.

  JUNIT_ARMS    -- the total outcome vocabulary a report lookup resolves to, and
                   verdict_for_junit() maps it onto slice A's ACCEPTANCE_VERDICTS. Only `passed`
                   yields `holds`; every other arm collapses into `fails` or `unmeasurable` --
                   positive evidence admits, everything else refuses (Decision 190). node_key()
                   inverts a known node id into the report's own dotted (classname + "." + name)
                   identity and matches by PREFIX AT `.`/`[` BOUNDARIES, never by reconstructing a
                   path from classname (that is scripts/ci_rca/fingerprint.py's
                   _nodeid_from_testcase, deliberately not reused here -- see rec-4003).

MUST NOT import scripts.ops_portal.closure_gate.closure_stamps_applicable (constraint 11,
inherited from slice A's own docstring note).
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, NamedTuple, Optional

from scripts.ops_portal.closure_gate import FAILS, HOLDS, UNMEASURABLE

SOURCE_JUNIT = "junit"

# --- JUNIT_ARMS: the total outcome vocabulary a report lookup resolves to -------------------

PASSED = "passed"
FAILED = "failed"
ERRORED = "errored"
SKIPPED = "skipped"
ABSENT = "absent"
REPORT_UNAVAILABLE = "report_unavailable"

JUNIT_ARMS: frozenset[str] = frozenset({PASSED, FAILED, ERRORED, SKIPPED, ABSENT, REPORT_UNAVAILABLE})

# TOTAL over JUNIT_ARMS -- only `passed` admits; everything else refuses (Decision 190: an
# unreadable or absent measurement resolves to the non-benign verdict, never a silent pass).
_JUNIT_ARM_TO_VERDICT: dict[str, str] = {
    PASSED: HOLDS,
    FAILED: FAILS,
    ERRORED: FAILS,
    SKIPPED: UNMEASURABLE,
    ABSENT: UNMEASURABLE,
    REPORT_UNAVAILABLE: UNMEASURABLE,
}

# Priority order when a single acceptance names several node ids (conjunction) or a class-level
# target expands to several report entries: the worst outcome wins, so "all must pass" holds.
_ARM_PRIORITY: tuple[str, ...] = (FAILED, ERRORED, SKIPPED, ABSENT, REPORT_UNAVAILABLE, PASSED)


def verdict_for_junit(arm: str) -> str:
    """TOTAL: raises KeyError for any arm outside JUNIT_ARMS -- never resolves silently."""
    return _JUNIT_ARM_TO_VERDICT[arm]


# --- GRAMMAR_ARMS: a declared-cell classification of an acceptance command's shape ----------

ROUTE_JUNIT = "junit"
ROUTE_STATIC = "static"
ROUTE_REFUSE = "refuse"
ROUTES: frozenset[str] = frozenset({ROUTE_JUNIT, ROUTE_STATIC, ROUTE_REFUSE})

GRAMMAR_SINGLE_NODE_CHAIN = "single_node_chain"
GRAMMAR_UNPARSEABLE = "unparseable"
GRAMMAR_MIXED_CONJUNCTION = "mixed_conjunction"
GRAMMAR_MIXED_OTHER_OPERATOR = "mixed_other_operator"
GRAMMAR_NEGATED_SEGMENT = "negated_segment"
GRAMMAR_NO_NODE_ID = "no_node_id"
GRAMMAR_SELECTOR_FLAG = "selector_flag"

# TOTAL over every arm classify_grammar() can return -- (route, reason). The residual default
# (no ::node_id found in an otherwise-ordinary single segment, e.g. a bare `pytest <path>`) is
# GRAMMAR_NO_NODE_ID, routed to STATIC: slice A's existing behaviour, no regression (Decision 191
# spirit: the destination is this declared cell, never a silent absence). GRAMMAR_UNPARSEABLE is
# the one fail-closed arm: a command whose segments cannot be enumerated must never reach an
# executor, so it refuses rather than falling to the probe.
GRAMMAR_ARMS: dict[str, tuple[str, str]] = {
    GRAMMAR_SINGLE_NODE_CHAIN: (
        ROUTE_JUNIT,
        "a pure single-segment command naming one or more pytest ::node_id targets",
    ),
    GRAMMAR_UNPARSEABLE: (
        ROUTE_REFUSE,
        (
            "the command could not be tokenized (unbalanced quote or trailing backslash); a "
            "command whose segments cannot be enumerated must never reach an executor"
        ),
    ),
    GRAMMAR_MIXED_CONJUNCTION: (
        ROUTE_STATIC,
        (
            "a multi-segment &&-only chain stays on slice A's probe path; junit cannot attribute "
            "a chain's outcome to one segment"
        ),
    ),
    GRAMMAR_MIXED_OTHER_OPERATOR: (
        ROUTE_STATIC,
        "a chain joined by || / | / ; stays on slice A's probe path, unchanged",
    ),
    GRAMMAR_NEGATED_SEGMENT: (
        ROUTE_STATIC,
        "a negated segment stays on slice A's probe path; junit has no negation semantics",
    ),
    GRAMMAR_SELECTOR_FLAG: (
        ROUTE_STATIC,
        "a -k/-m/--deselect selector's selection cannot be keyed against the junit report",
    ),
    GRAMMAR_NO_NODE_ID: (
        ROUTE_STATIC,
        ("no pytest ::node_id target found in the segment; the residual default, stays on slice A's probe path unchanged"),
    ),
}

_CONTROL_OPERATORS: frozenset[str] = frozenset({"&&", "||", "|", ";"})
# Any single-dash cluster reaching a k or m: argparse expands combined short flags (`-vk foo`) and
# accepts an attached value (`-mslow`). Over-matching a value-taking cluster only refuses junit.
_SHORT_SELECTOR_RE = re.compile(r"^-[A-Za-z]*[km]")
_PYTEST_MENTION_RE = re.compile(r"\bpytest\b")
_NODE_ID_RE = re.compile(r"^\S+\.py::\S+$")


class GrammarResult(NamedTuple):
    arm: str
    route: str
    reason: str
    node_ids: tuple[str, ...] = ()


def _tokenize(cmd: str) -> Optional[list[str]]:
    try:
        lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return None


def _split_segments(tokens: list[str]) -> tuple[list[list[str]], list[str]]:
    segments: list[list[str]] = []
    operators: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token in _CONTROL_OPERATORS:
            segments.append(current)
            operators.append(token)
            current = []
        else:
            current.append(token)
    segments.append(current)
    return segments, operators


def _is_selector_flag(token: str) -> bool:
    return bool(_SHORT_SELECTOR_RE.match(token)) or token.startswith("--deselect")


def _is_pytest_invocation(token: str) -> bool:
    return token.rsplit("/", 1)[-1] == "pytest"


def _pytest_arguments(segment: list[str]) -> list[str]:
    # Tokens before the invocation belong to the interpreter (the `-m` in `bin/venv-python -m
    # pytest` is Python's module flag, not a marker selector). The scan is anchored only when the
    # first token mentioning pytest is the invocation itself, leading the segment or following
    # `-m`; anything else (`pytest-3`, `uv run --with pytest pytest`) scans the whole segment.
    for index, token in enumerate(segment):
        if _PYTEST_MENTION_RE.search(token):
            if _is_pytest_invocation(token) and (index == 0 or segment[index - 1] == "-m"):
                return segment[index + 1 :]
            break
    return segment


def _classify_arm(cmd: str) -> tuple[str, tuple[str, ...]]:
    tokens = _tokenize(cmd)
    if tokens is None:
        return GRAMMAR_UNPARSEABLE, ()

    segments, operators = _split_segments(tokens)
    if len(segments) > 1:
        if all(op == "&&" for op in operators):
            return GRAMMAR_MIXED_CONJUNCTION, ()
        return GRAMMAR_MIXED_OTHER_OPERATOR, ()

    segment = segments[0]
    if segment and segment[0] == "!":
        return GRAMMAR_NEGATED_SEGMENT, ()
    if any(_is_selector_flag(token) for token in _pytest_arguments(segment)):
        return GRAMMAR_SELECTOR_FLAG, ()

    node_ids = tuple(token for token in segment if _NODE_ID_RE.match(token))
    if not node_ids:
        return GRAMMAR_NO_NODE_ID, ()
    return GRAMMAR_SINGLE_NODE_CHAIN, node_ids


def classify_grammar(cmd: str) -> GrammarResult:
    """TOTAL: every acceptance shape lands in a named GRAMMAR_ARMS cell carrying its own reason
    and declared route -- never falls through to None or an implicit default."""
    arm, node_ids = _classify_arm(cmd)
    route, reason = GRAMMAR_ARMS[arm]
    return GrammarResult(arm=arm, route=route, reason=reason, node_ids=node_ids)


# --- Report indexing and node-id inversion --------------------------------------------------


def node_key(node_id: str) -> str:
    """Invert a pytest node id (`tests/x.py::TestKlass::test_y`) into the DOTTED IDENTITY a
    junit report's own classname carries (`tests.x.TestKlass`, no `file=` attribute emitted
    under this repo's xunit2 default). Never reconstructs a path FROM classname -- that
    direction is scripts/ci_rca/fingerprint.py's _nodeid_from_testcase, whose classname branch
    mangles a class-based node id (rec-4003)."""
    path, _, rest = node_id.partition("::")
    module = path[:-3] if path.endswith(".py") else path
    module = module.replace("/", ".")
    if not rest:
        return module
    return f"{module}.{rest.replace('::', '.')}"


def _matches(identity: str, key: str) -> bool:
    """PREFIX AT `.`/`[` BOUNDARIES: a class-level key matches every nested method
    (`key.method`); a parametrized key with no bracket suffix matches every report entry whose
    name is the target or starts with `target[` -- pytest's own selection semantics. A boundary
    is required so `tests.x.TestKlass` never falsely matches `tests.x.TestKlassExtra...`."""
    return identity == key or identity.startswith((key + ".", key + "["))


def index_report(path: Path) -> Optional[dict[str, str]]:
    """Index every testcase in the junit report at `path` by its dotted identity
    (`classname + "." + name`, or bare `name` when classname is empty) onto one of
    {passed, failed, errored, skipped}. Returns None -- never raises, never an empty-but-present
    index -- when the report is absent or malformed, so an absent and an empty document are
    never indistinguishable to the caller."""
    if not path.is_file():
        return None
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return None

    index: dict[str, str] = {}
    for testcase in tree.getroot().iter("testcase"):
        classname = testcase.get("classname") or ""
        name = testcase.get("name") or ""
        identity = f"{classname}.{name}" if classname else name
        if testcase.find("failure") is not None:
            outcome = FAILED
        elif testcase.find("error") is not None:
            outcome = ERRORED
        elif testcase.find("skipped") is not None:
            outcome = SKIPPED
        else:
            outcome = PASSED
        index[identity] = outcome
    return index


def outcome_for(index: Optional[dict[str, str]], node_id: str) -> str:
    """Resolve a single node id against `index` (as produced by index_report). `index=None`
    (report absent/unreadable) resolves unconditionally to report_unavailable -- never to a
    pass. A target matching no entry resolves to absent. A target matching several entries
    (a class-level or bracket-free parametrized selector) resolves to the WORST outcome among
    them, so "all must pass" holds."""
    if index is None:
        return REPORT_UNAVAILABLE
    key = node_key(node_id)
    matches = [outcome for identity, outcome in index.items() if _matches(identity, key)]
    if not matches:
        return ABSENT
    for arm in _ARM_PRIORITY:
        if arm in matches:
            return arm
    return PASSED  # unreachable: _ARM_PRIORITY covers every value index_report can emit


def _combine(arms: list[str]) -> str:
    for arm in _ARM_PRIORITY:
        if arm in arms:
            return arm
    return REPORT_UNAVAILABLE  # unreachable when arms is non-empty; never called otherwise


def junit_verdict(census_doc: dict[str, Any], junit_path: Path, sha: str) -> dict[str, Any]:
    """Resolve a verdict for every census entry routed to junit (route == "junit"), re-deriving
    each entry's node ids from its own acceptance text via classify_grammar -- never trusting a
    stored, possibly-stale node-id list. Returns {sha, report_available, source: "junit",
    records: [{rec_id, sha, acceptance_sha256, verdict, source, arm}]} in slice A's record
    shape. An absent or unreadable report resolves EVERY routed entry to unmeasurable, never a
    pass (Decision 190)."""
    index = index_report(junit_path)
    records: list[dict[str, Any]] = []
    for entry in census_doc.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("route") != ROUTE_JUNIT:
            continue
        grammar = classify_grammar(str(entry.get("acceptance") or ""))
        arms = [outcome_for(index, node_id) for node_id in grammar.node_ids]
        arm = _combine(arms) if arms else REPORT_UNAVAILABLE
        records.append(
            {
                "rec_id": entry["rec_id"],
                "sha": sha,
                "acceptance_sha256": entry["acceptance_sha256"],
                "verdict": verdict_for_junit(arm),
                "source": SOURCE_JUNIT,
                "arm": arm,
            }
        )
    return {
        "sha": sha,
        "report_available": index is not None,
        "source": SOURCE_JUNIT,
        "records": records,
    }


# --- Thin CLI adapter --------------------------------------------------------------------
#
# Invoked DIRECTLY as `python -m scripts.rec_trailer_acceptance_junit` (never via
# scripts.rec_trailer_acceptance's own subparser) so that validate_sandbox_runner_test_deps's
# M2 marker -- which arms on any job whose step bodies invoke scripts.backlog_health.probe or
# any module importing it -- never arms on this job: this module imports neither probe nor
# census, only stdlib. The routing that DOES import probe stays in the census stage, where M2
# arming is correct.

_CENSUS_ARTIFACT = "trailer_census.json"
_VERDICT_ARTIFACT = "junit_verdict.json"


def _current_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        timeout=10,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.rec_trailer_acceptance_junit")
    parser.add_argument("--artifact-dir", type=Path, default=Path(tempfile.gettempdir()) / "trailer_acceptance")
    parser.add_argument("--junit-report", type=Path, default=None)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    census_path = args.artifact_dir / _CENSUS_ARTIFACT
    if not census_path.is_file():
        # FAIL LOUD, never write an artifact: trailer-closure's own download of this artifact
        # is continue-on-error, so a missing artifact here (trailer-census itself failed) reaches
        # rejoin's declared refusal branch rather than a falsely-successful empty verdict.
        print(
            f"[rec_trailer_acceptance_junit] FAIL: expected census artifact not found: {census_path} "
            "(did trailer-census run?)",
            file=sys.stderr,
        )
        return 1

    census_doc = json.loads(census_path.read_text(encoding="utf-8"))
    junit_report = args.junit_report if args.junit_report is not None else args.artifact_dir / "pytest-junit.xml"
    result = junit_verdict(census_doc, junit_report, _current_sha())
    print(
        f"[rec_trailer_acceptance_junit] sha={result['sha']} report_available={result['report_available']} "
        f"verdicts={[(r['rec_id'], r['verdict']) for r in result['records']]}"
    )
    out_path = args.artifact_dir / _VERDICT_ARTIFACT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
