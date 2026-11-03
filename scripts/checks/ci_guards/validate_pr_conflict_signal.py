"""pr-conflict-signal.yml structural invariants gate (PLAN-pr-conflict-wake-signal; extraction
per Decision 162 / rec-2735; branch-prefix filter realigned to a declared, structurally-asserted
set by PLAN-conflict-wake-prefix-realignment, which retired this module's existence-only literal
prefix check -- the row that let the filter rot silently across a harness branch-prefix rename)."""

from __future__ import annotations

import re

from scripts.checks import _common, registry
from scripts.verify_ci_workflow import _load

_WORKFLOW_PATH = ".github/workflows/pr-conflict-signal.yml"

# The delegation line is fully anchored: `run: bash <repo-root-relative-path>.sh`, nothing else --
# mirrors the composite-action guard's _DELEGATE_LINE_RE anchoring discipline (Decision 162 R1),
# adapted for a workflow step (repo-root-relative cwd via actions/checkout, not
# github.action_path-relative).
_DELEGATE_RUN_RE = re.compile(r"^bash\s+([\w./-]+\.sh)\s*$")

# A literal "gh <subcommand>" call site, anchored to an actual command POSITION (line start, or
# immediately after a command-substitution "$(") -- never a bare `\bgh\b` scan, which would also
# match "gh" inside a comment or inside a quoted prose string (e.g. a _signal_failure message
# describing "gh pr view ... failed"). This guard is filesystem-only (Decision 153 fast-tier
# budget) -- no subprocess, no network, no real shell parsing beyond regex/line-scanning.
_GH_CALL_RE = re.compile(r"(?:^|\$\()\s*gh\s")
# A gh call passed as an ARGUMENT to the shared retry helper (e.g.
# "mergeable=$(_gh_bounded_retry 5 5 \"UNKNOWN\" gh pr view ...)") never matches _GH_CALL_RE --
# "gh" sits after the helper's own leading arguments, not at line-start or immediately after
# "$(" -- so it needs its own, looser word-boundary detector to be recognised as a call site at
# all before the retry-helper exemption below can apply to it.
_GH_WORD_RE = re.compile(r"\bgh\s")
_RETRY_HELPER_TOKEN = "_gh_bounded_retry"
_EXIT_STATUS_CAPTURE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\$\?$")

# The declared branch-prefix universe (docs/contracts/git-ops.yaml
# branching_topology.agent_branch_prefixes carries the published half of the same fact). This
# guard asserts the DECLARATION'S SHAPE only -- never a specific prefix value -- so narrowing or
# widening the declared set reddens no assertion here (constraint: a hardcoded literal would
# recreate the exact rot this plan removes, one string later).
_PREFIX_VAR = "_WAKE_HEAD_PREFIXES"
_TRANSITIONAL_VAR = f"{_PREFIX_VAR}_TRANSITIONAL"
_PREFIX_DECLARATION_RE = re.compile(rf"^{_PREFIX_VAR}=\((.*)\)\s*$")
_TRANSITIONAL_DECLARATION_RE = re.compile(rf"^{_TRANSITIONAL_VAR}=\((.*)\)\s*$")
_PREFIX_TOKEN_RE = re.compile(r"""["']([^"']+)["']""")
_PREFIX_TERMINATORS = ("/", "-")
_PREFIX_CONSUMPTION_TOKEN = "${" + _PREFIX_VAR + "[@]}"

_GH_PR_LIST_RE = re.compile(r"(?:^|\$\()\s*gh\s+pr\s+list\b")
_GH_LIMIT_RE = re.compile(r"--limit\s+\d+")


def _join_continuations(text: str) -> list[str]:
    """Backslash-continued physical lines joined into one stripped logical line each."""
    logical: list[str] = []
    buffer = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        buffer = f"{buffer} {stripped}".strip() if buffer else stripped
        if buffer.endswith("\\"):
            buffer = buffer[:-1].rstrip()
            continue
        logical.append(buffer)
        buffer = ""
    if buffer:
        logical.append(buffer)
    return logical


def _unguarded_gh_call_sites(script_text: str) -> list[str]:
    """gh call sites that are neither routed through the shared retry helper nor immediately
    followed by an explicit `$?` capture -- the rec-2735 shape this guard exists to reject."""
    logical_lines = _join_continuations(script_text)
    unguarded: list[str] = []
    for index, line in enumerate(logical_lines):
        if not line or line.startswith("#"):
            continue
        routed_through_helper = _RETRY_HELPER_TOKEN in line and bool(_GH_WORD_RE.search(line))
        if routed_through_helper:
            continue
        if not _GH_CALL_RE.search(line):
            continue
        following = logical_lines[index + 1] if index + 1 < len(logical_lines) else ""
        if _EXIT_STATUS_CAPTURE_RE.match(following):
            continue
        unguarded.append(line)
    return unguarded


def _parse_prefix_array(logical_lines: list[str], declaration_re: re.Pattern[str]) -> list[str] | None:
    """Quoted tokens of the first logical line matching `declaration_re`, or None if absent --
    the "declaration absent" case a prose-only mention (a comment or log string containing a
    prefix literal but no real array assignment) must still hit, since it never matches the
    anchored `VAR=(...)` shape this regex requires."""
    for line in logical_lines:
        match = declaration_re.match(line)
        if match:
            return _PREFIX_TOKEN_RE.findall(match.group(1))
    return None


def _assert_prefix_declaration(failed: list[str], script_text: str) -> None:
    """The declared-prefix universe and its transitional subset, asserted structurally: present,
    non-empty, every token separator-terminated, no duplicates, transitional a subset of declared,
    and consumed (expanded) somewhere outside its own declaration line -- never a specific value."""
    logical_lines = _join_continuations(script_text)
    declared = _parse_prefix_array(logical_lines, _PREFIX_DECLARATION_RE)
    transitional = _parse_prefix_array(logical_lines, _TRANSITIONAL_DECLARATION_RE)

    if declared is None:
        print(f"  FAIL: no {_PREFIX_VAR}=(...) declaration found")
        failed.append("pr-conflict-signal: branch-prefix declaration absent")
        return
    if not declared:
        print(f"  FAIL: {_PREFIX_VAR} declares an empty prefix set")
        failed.append("pr-conflict-signal: branch-prefix declaration empty")
    elif not all(token.endswith(_PREFIX_TERMINATORS) for token in declared):
        print(f"  FAIL: {_PREFIX_VAR} contains a token not ending in '/' or '-': {declared}")
        failed.append("pr-conflict-signal: branch-prefix declaration malformed (token missing trailing / or -)")
    elif len(declared) != len(set(declared)):
        print(f"  FAIL: {_PREFIX_VAR} contains duplicate tokens: {declared}")
        failed.append("pr-conflict-signal: branch-prefix declaration contains duplicate tokens")
    else:
        print(f"  PASS: {_PREFIX_VAR} declares {len(declared)} well-formed prefix(es)")

    if transitional is None:
        print(f"  FAIL: no {_TRANSITIONAL_VAR}=(...) declaration found")
        failed.append("pr-conflict-signal: transitional branch-prefix declaration absent")
    elif not set(transitional) <= set(declared):
        extra = sorted(set(transitional) - set(declared))
        print(f"  FAIL: {_TRANSITIONAL_VAR} is not a subset of {_PREFIX_VAR}: {extra}")
        failed.append("pr-conflict-signal: transitional branch-prefix set is not a subset of the declared set")
    else:
        print(f"  PASS: {_TRANSITIONAL_VAR} ({len(transitional)}) is a subset of {_PREFIX_VAR}")

    if _PREFIX_CONSUMPTION_TOKEN in script_text:
        print(f"  PASS: {_PREFIX_VAR} is consumed (expanded) in the selection logic")
    else:
        print(f"  FAIL: {_PREFIX_VAR} is declared but never consumed by the selection logic")
        failed.append("pr-conflict-signal: branch-prefix declaration is unconsumed")


def _assert_gh_list_limit(failed: list[str], script_text: str) -> None:
    """The gh pr list call site(s) carry an explicit --limit, so the sweep cannot silently
    truncate at gh's default page size."""
    logical_lines = _join_continuations(script_text)
    list_lines = [line for line in logical_lines if _GH_PR_LIST_RE.search(line)]
    if not list_lines:
        print("  FAIL: no gh pr list call site found")
        failed.append("pr-conflict-signal: gh pr list call site not found")
        return
    if not all(_GH_LIMIT_RE.search(line) for line in list_lines):
        print(f"  FAIL: gh pr list call site missing --limit: {list_lines}")
        failed.append("pr-conflict-signal: gh pr list call site missing --limit")
        return
    print(f"  PASS: gh pr list call site(s) carry an explicit --limit ({len(list_lines)})")


def _resolve_delegate(jobs: dict) -> tuple[str | None, str | None, int | None]:
    """First (job_name, script_rel_path, step_index) whose `run:` is a bare delegation line."""
    for job_name, job in jobs.items():
        steps = job.get("steps") or []
        for index, step in enumerate(steps):
            run_body = step.get("run")
            if not isinstance(run_body, str):
                continue
            match = _DELEGATE_RUN_RE.match(run_body.strip())
            if match:
                return job_name, match.group(1), index
    return None, None, None


@registry.register("validate_pr_conflict_signal", owner="platform")
def validate_pr_conflict_signal(failed: list[str]) -> None:
    """Assert pr-conflict-signal.yml's load-bearing shape (PLAN-pr-conflict-wake-signal).

    This is the push:[main] counterpart to signal-green: it delivers the merge-conflict-transition
    wake that the pull_request-only signal-green job structurally cannot (a push to main fires no
    pull_request event on open PRs). The poll step is a thin delegation to
    scripts/ci/pr_conflict_signal.sh (Decision 162 R1/R3); this guard follows that delegation and
    asserts its semantic invariants against the SCRIPT's own contents -- including the declared
    branch-prefix universe's shape (never a specific value) and its --limit -- plus delegate
    existence, checkout-precedes-delegation, errexit clearing, and per-call-site exit-status
    handling. Each guard failure appends a distinct label to `failed` rather than raising, matching
    the ci_guards module pattern.
    """
    print("\n=== pr-conflict-signal guard gate ===")
    try:
        data = _load(_WORKFLOW_PATH)
    except Exception as exc:
        print(f"  FAIL: could not load {_WORKFLOW_PATH}: {exc}")
        failed.append("pr-conflict-signal: workflow file unreadable")
        return

    on = data.get("on", {})
    push = on.get("push") or {}
    if push.get("branches") != ["main"]:
        print(f"  FAIL: on.push.branches is not [main]: {push.get('branches')!r}")
        failed.append("pr-conflict-signal: push trigger not scoped to [main]")
    else:
        print("  PASS: on.push.branches == [main]")

    if "workflow_dispatch" not in on:
        print("  FAIL: workflow_dispatch trigger missing")
        failed.append("pr-conflict-signal: missing workflow_dispatch trigger")
    else:
        print("  PASS: workflow_dispatch trigger present")

    jobs = data.get("jobs", {})
    if not jobs:
        print("  FAIL: no jobs defined")
        failed.append("pr-conflict-signal: no jobs defined")
        return

    permissions_ok = any((job.get("permissions") or {}).get("pull-requests") == "write" for job in jobs.values())
    if permissions_ok:
        print("  PASS: a job declares permissions.pull-requests: write")
    else:
        print("  FAIL: no job declares permissions.pull-requests: write")
        failed.append("pr-conflict-signal: missing pull-requests: write permission")

    coe_ok = any(step.get("continue-on-error") is True for job in jobs.values() for step in job.get("steps", []))
    if coe_ok:
        print("  PASS: a step declares continue-on-error: true")
    else:
        print("  FAIL: no step declares continue-on-error: true")
        failed.append("pr-conflict-signal: missing continue-on-error: true")

    job_name, script_rel_path, step_index = _resolve_delegate(jobs)
    if script_rel_path is None or job_name is None or step_index is None:
        print("  FAIL: could not resolve a delegate script from any step's run: body")
        failed.append("pr-conflict-signal: could not resolve delegate script")
        return

    script_path = _common.ROOT / script_rel_path
    if not script_path.is_file():
        print(f"  FAIL: delegate script {script_rel_path} does not exist on disk")
        failed.append("pr-conflict-signal: delegate script missing on disk")
        return
    print(f"  PASS: delegate script {script_rel_path} exists")

    steps = jobs[job_name].get("steps") or []
    checkout_before = any(str(step.get("uses", "")).startswith("actions/checkout") for step in steps[:step_index])
    if checkout_before:
        print("  PASS: actions/checkout precedes the delegation step")
    else:
        print("  FAIL: no actions/checkout step precedes the delegation step")
        failed.append("pr-conflict-signal: checkout does not precede delegation")

    script_text = script_path.read_text(encoding="utf-8")

    if "\nset +e\n" in f"\n{script_text}\n":
        print("  PASS: delegate script clears inherited errexit (set +e)")
    else:
        print("  FAIL: delegate script does not clear errexit (no `set +e` line)")
        failed.append("pr-conflict-signal: delegate script does not clear errexit")

    checks = [
        ("mergeable poll", "mergeable" in script_text),
        ("UNKNOWN-skip handling", "UNKNOWN" in script_text),
        ("CONFLICTING-only comment gate", "CONFLICTING" in script_text),
        ("head-SHA dedup marker", "conflict-wake:" in script_text),
    ]
    for label, present in checks:
        if present:
            print(f"  PASS: {label}")
        else:
            print(f"  FAIL: {label} not found in delegate script")
            failed.append(f"pr-conflict-signal: missing {label}")

    _assert_prefix_declaration(failed, script_text)
    _assert_gh_list_limit(failed, script_text)

    unguarded = _unguarded_gh_call_sites(script_text)
    if unguarded:
        print(f"  FAIL: gh call site(s) without explicit exit-status handling: {unguarded}")
        failed.append("pr-conflict-signal: gh call site(s) missing explicit exit-status handling")
    else:
        print("  PASS: every gh call site handles its exit status")

    registry.examined(1, unit="pr_conflict_signal_workflows")
