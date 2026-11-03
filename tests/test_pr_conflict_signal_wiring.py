"""Wiring tests for scripts/ci/pr_conflict_signal.sh (rec-2735).

The workflow step that delegates to this script declares no `shell:` key, so GitHub runs the
step's own run: body -- now just `bash scripts/ci/pr_conflict_signal.sh` -- as
"bash --noprofile --norc -e -o pipefail {0}". That OUTER invocation inherits errexit, but its one
command spawns a CHILD bash process to run this script, and a child bash does NOT inherit its
parent's shell options (SHELLOPTS is unexported, confirmed empirically: `bash -e parent.sh` where
parent runs `bash child.sh` lets the child continue past both a bare `false` and a failed command
substitution). So THIS script runs WITHOUT inherited errexit in production -- an unhandled gh
failure yields an empty string that silently falls through to "no wake needed" unless every call
site explicitly checks its own exit status.

Every behavioural case below is therefore parameterised over BOTH argvs: PRODUCTION_ARGV (a plain
child `bash <script>`, errexit off -- today's real invocation) and HOSTILE_ARGV (the literal
GitHub run-step invocation, errexit on -- a deliberately hostile superset that is what would catch
a regression if this body were ever re-inlined). Neither is redundant: dropping PRODUCTION_ARGV
would make these tests vacuous with respect to today's actual behaviour, and dropping HOSTILE_ARGV
would leave the re-inlining regression unguarded.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.checks.ci_guards import validate_pr_conflict_signal as validator

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "pr_conflict_signal.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "pr-conflict-signal.yml"

PRODUCTION_ARGV = ("bash",)
HOSTILE_ARGV = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")
ARGVS = {"production": PRODUCTION_ARGV, "hostile": HOSTILE_ARGV}

# A gh shim keyed on call "kind" (list / mergeable:<n> / comments:<n> / comment:<n>), driven
# entirely by env vars: GH_SHIM_CONTROL_FILE (a {kind: response | [response, ...]} JSON map),
# GH_SHIM_LOG_FILE (one "<kind>\t<argv>" line per invocation), GH_SHIM_STATE_DIR (per-kind call
# counters, so a list of responses is consumed one per successive call -- the last entry repeats
# once exhausted). An unrecognised call kind fails loudly (exit 99) rather than silently
# succeeding, so a gap in a test's control map can never masquerade as a passing scenario.
_GH_SHIM = """#!{python}
import json
import os
import sys
from pathlib import Path


def _classify(argv):
    if len(argv) >= 2 and argv[0] == "pr":
        sub = argv[1]
        if sub == "list":
            return "list"
        if sub == "view" and len(argv) > 2:
            number = argv[2]
            if "--json" in argv:
                idx = argv.index("--json")
                fields = argv[idx + 1] if idx + 1 < len(argv) else ""
                if "mergeable" in fields:
                    return f"mergeable:{{number}}"
                if "comments" in fields:
                    return f"comments:{{number}}"
            return f"view:{{number}}"
        if sub == "comment" and len(argv) > 2:
            return f"comment:{{argv[2]}}"
    return "unknown:" + " ".join(argv)


def main() -> int:
    argv = sys.argv[1:]
    control = json.loads(Path(os.environ["GH_SHIM_CONTROL_FILE"]).read_text(encoding="utf-8"))
    kind = _classify(argv)
    with open(os.environ["GH_SHIM_LOG_FILE"], "a", encoding="utf-8") as fh:
        fh.write(kind + "\\t" + " ".join(argv) + "\\n")

    responses = control.get(kind)
    if responses is None:
        print(f"gh shim: unrecognised call kind {{kind!r}} for argv {{argv!r}}", file=sys.stderr)
        return 99

    if isinstance(responses, list):
        state_dir = Path(os.environ["GH_SHIM_STATE_DIR"])
        counter_path = state_dir / (kind.replace(":", "_").replace("/", "_") + ".count")
        idx = int(counter_path.read_text()) if counter_path.exists() else 0
        counter_path.write_text(str(idx + 1))
        response = responses[min(idx, len(responses) - 1)]
    else:
        response = responses

    stdout = response.get("stdout", "")
    stderr = response.get("stderr", "")
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    return int(response.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
"""


class _Harness:
    """A prepared pr_conflict_signal.sh invocation: shimmed gh on PATH, fast retry sleeps."""

    def __init__(self, tmp_path: Path, control: dict) -> None:
        self.tmp_path = tmp_path
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        self.workdir = tmp_path / "work"
        self.workdir.mkdir(exist_ok=True)
        state_dir = tmp_path / "state"
        state_dir.mkdir(exist_ok=True)
        self.log_file = tmp_path / "gh_calls.log"
        self.log_file.write_text("", encoding="utf-8")
        self.step_summary = tmp_path / "step_summary.md"

        gh_path = bin_dir / "gh"
        gh_path.write_text(_GH_SHIM.format(python=sys.executable), encoding="utf-8")
        gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        control_file = tmp_path / "control.json"
        control_file.write_text(json.dumps(control), encoding="utf-8")

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["GH_TOKEN"] = "stub-token"
        env["GH_REPO"] = "example-org/example-repo"
        env["GH_SHIM_CONTROL_FILE"] = str(control_file)
        env["GH_SHIM_LOG_FILE"] = str(self.log_file)
        env["GH_SHIM_STATE_DIR"] = str(state_dir)
        env["GITHUB_STEP_SUMMARY"] = str(self.step_summary)
        env["PR_CONFLICT_SIGNAL_POLL_SLEEP"] = "0"
        env["PR_CONFLICT_SIGNAL_RETRY_SLEEP"] = "0"
        self.env = env

    def run(
        self, argv: tuple[str, ...], script: Path | None = None, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*argv, str(script or SCRIPT)],
            cwd=str(cwd or self.workdir),
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    @property
    def calls(self) -> list[tuple[str, str]]:
        if not self.log_file.exists():
            return []
        pairs = []
        for line in self.log_file.read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                kind, argv_text = line.split("\t", 1)
                pairs.append((kind, argv_text))
        return pairs

    def call_count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)

    def comment_posted(self, number: str) -> bool:
        return self.call_count(f"comment:{number}") >= 1

    @property
    def summary_text(self) -> str:
        return self.step_summary.read_text(encoding="utf-8") if self.step_summary.exists() else ""


class TestArgvFidelity:
    """The harness must reproduce each named invocation's errexit state faithfully -- guards
    against a vacuous suite (mirrors tests/test_subagent_review_wiring.py's own such class)."""

    def test_hostile_argv_is_the_documented_github_run_step_invocation(self) -> None:
        assert HOSTILE_ARGV == ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")

    def test_production_argv_has_no_inherited_errexit(self, tmp_path: Path) -> None:
        probe = tmp_path / "probe.sh"
        probe.write_text("set -uo pipefail\nfalse\necho REACHED\n", encoding="utf-8")
        result = subprocess.run([*PRODUCTION_ARGV, str(probe)], capture_output=True, text=True, check=False)
        assert "REACHED" in result.stdout
        assert result.returncode == 0

    def test_hostile_argv_delivers_errexit(self, tmp_path: Path) -> None:
        probe = tmp_path / "probe.sh"
        probe.write_text("set -uo pipefail\nfalse\necho REACHED\n", encoding="utf-8")
        result = subprocess.run([*HOSTILE_ARGV, str(probe)], capture_output=True, text=True, check=False)
        assert "REACHED" not in result.stdout
        assert result.returncode != 0


class TestSweepContinuation:
    """rec-2735, the wake guarantee: a gh failure on the FIRST PR must still leave the SECOND
    PR polled and woken."""

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_gh_failure_on_first_pr_still_wakes_second(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "101\tagent/branch-101\tabc111\n102\tagent/branch-102\tdef222\n"},
            "mergeable:101": {"exit_code": 1, "stdout": "", "stderr": "gh: simulated API failure"},
            "mergeable:102": {"exit_code": 0, "stdout": "CONFLICTING"},
            "comments:102": {"exit_code": 0, "stdout": ""},
            "comment:102": {"exit_code": 0, "stdout": ""},
        }
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert harness.comment_posted("102"), (
            f"PR 102 was never woken after PR 101's gh call failed (argv={argv_name}). "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert harness.call_count("mergeable:101") == 5
        assert result.returncode != 0


class TestRetryOnFailedCall:
    """The 5-attempt poll loop retries a FAILED CALL, not only an UNKNOWN value -- a call that
    fails twice then succeeds is treated as an ordinary success, not a counted failure."""

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_failed_call_recovers_within_the_poll_bound(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "111\tagent/branch-111\tsha111\n"},
            "mergeable:111": [
                {"exit_code": 1, "stdout": "", "stderr": "transient"},
                {"exit_code": 1, "stdout": "", "stderr": "transient"},
                {"exit_code": 0, "stdout": "CONFLICTING"},
            ],
            "comments:111": {"exit_code": 0, "stdout": ""},
            "comment:111": {"exit_code": 0, "stdout": ""},
        }
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert harness.comment_posted("111")
        assert harness.call_count("mergeable:111") == 3
        assert result.returncode == 0
        # No per-PR FAILURE marker (this is the recovered-within-bound path), but the fixture's
        # only open PR is agent/*-headed, so the transitional prefix `claude/` matched zero this
        # run and the retirement notice fires -- the mandated migration side effect (VP1 fix_if).
        assert "[PR-CONFLICT-SIGNAL] FAILURE" not in harness.summary_text
        assert "transitional prefix retirement notice" in harness.summary_text


class TestUnknownNeverFalseWakes:
    """UNKNOWN-after-poll-bound is a legitimate pending state, never a counted failure and
    never a wake -- the pre-existing invariant this rewrite must preserve exactly."""

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_unknown_after_poll_bound_skips_without_wake_or_failure(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "121\tagent/branch-121\tsha121\n"},
            "mergeable:121": {"exit_code": 0, "stdout": "UNKNOWN"},
        }
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert not harness.comment_posted("121")
        assert harness.call_count("mergeable:121") == 5
        assert result.returncode == 0
        # No FAILURE marker (UNKNOWN-after-poll-bound is never a counted failure), but the only
        # open PR is agent/*-headed, so `claude/` matched zero and the retirement notice fires.
        assert "[PR-CONFLICT-SIGNAL] FAILURE" not in harness.summary_text
        assert "transitional prefix retirement notice" in harness.summary_text


class TestHeadShaIdempotency:
    """The other pre-existing invariant: a marker already present for the current head SHA
    suppresses a repost."""

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_existing_marker_suppresses_repost(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "131\tagent/branch-131\tsha131\n"},
            "mergeable:131": {"exit_code": 0, "stdout": "CONFLICTING"},
            "comments:131": {"exit_code": 0, "stdout": "<!-- conflict-wake:sha131 -->\nalready posted"},
        }
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert not harness.comment_posted("131")
        assert result.returncode == 0


class TestCommentsReadFailurePostsAnyway:
    """Bias inverted (Decision 155 scout flag): a comments-read that fails after retries must
    post the wake ANYWAY -- a duplicate wake is cheap, a missed wake strands a session."""

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_comments_read_failure_still_posts_wake_and_marks_failure(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "141\tagent/branch-141\tsha141\n"},
            "mergeable:141": {"exit_code": 0, "stdout": "CONFLICTING"},
            "comments:141": {"exit_code": 1, "stdout": "", "stderr": "boom"},
            "comment:141": {"exit_code": 0, "stdout": ""},
        }
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert harness.comment_posted("141")
        assert result.returncode != 0
        assert "[PR-CONFLICT-SIGNAL] FAILURE" in result.stderr


class TestMarkerEmission:
    """Any per-PR failure is mirrored to $GITHUB_STEP_SUMMARY with a greppable marker."""

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_failure_mirrored_to_step_summary(self, tmp_path: Path, argv_name: str) -> None:
        control = {"list": {"exit_code": 1, "stdout": "", "stderr": "rate limited"}}
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert "[PR-CONFLICT-SIGNAL] FAILURE" in harness.summary_text
        assert result.returncode != 0

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_clean_sweep_writes_no_failure_marker(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "151\tagent/branch-151\tsha151\n"},
            "mergeable:151": {"exit_code": 0, "stdout": "MERGEABLE"},
        }
        harness = _Harness(tmp_path, control)
        harness.run(ARGVS[argv_name])
        # No per-PR FAILURE marker on a clean sweep, but the only open PR is agent/*-headed, so
        # the transitional prefix `claude/` matched zero this run and the retirement notice fires.
        assert "[PR-CONFLICT-SIGNAL] FAILURE" not in harness.summary_text
        assert "transitional prefix retirement notice" in harness.summary_text


class TestDeclaredPrefixSelection:
    """The declared-prefix selection this plan moves out of gh's --jq and into the script's own
    loop (VP1 -k selector: "declared_prefixes"): the sweep admits exactly the declared prefixes,
    skips every other head, names the unmatched population, and mirrors/notices per the two
    load-bearing observability behaviours acceptance criterion 1 calls out -- a non-empty
    unmatched population mirrored to GITHUB_STEP_SUMMARY, and a transitional prefix matching zero
    open PRs appending the retirement notice."""

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_declared_prefixes_admits_declared_and_skips_others(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {
                "exit_code": 0,
                "stdout": "201\tagent/branch-201\tsha201\n202\tclaude/branch-202\tsha202\n203\tsomeone/branch-203\tsha203\n",
            },
            "mergeable:201": {"exit_code": 0, "stdout": "MERGEABLE"},
            "mergeable:202": {"exit_code": 0, "stdout": "MERGEABLE"},
        }
        harness = _Harness(tmp_path, control)
        harness.run(ARGVS[argv_name])
        # Declared prefixes (agent/, claude/) are polled for mergeability...
        assert harness.call_count("mergeable:201") == 1
        assert harness.call_count("mergeable:202") == 1
        # ...the undeclared head (someone/branch-203) is never polled at all.
        assert harness.call_count("mergeable:203") == 0
        assert not any(kind.startswith("mergeable:203") for kind, _ in harness.calls)

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_declared_prefixes_unmatched_population_mirrored_to_summary(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {
                "exit_code": 0,
                "stdout": "211\tagent/branch-211\tsha211\n212\tsomeone/branch-212\tsha212\n",
            },
            "mergeable:211": {"exit_code": 0, "stdout": "MERGEABLE"},
        }
        harness = _Harness(tmp_path, control)
        harness.run(ARGVS[argv_name])
        assert harness.call_count("mergeable:212") == 0
        # Non-empty unmatched population (someone/branch-212) is named in the run log AND
        # mirrored to GITHUB_STEP_SUMMARY -- a continue-on-error job is otherwise invisible when
        # green, so this mirror is what would have caught the outage this plan fixes.
        assert "someone/branch-212" in harness.summary_text
        assert "unmatched heads" in harness.summary_text

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_declared_prefixes_transitional_zero_match_emits_retirement_notice(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "221\tagent/branch-221\tsha221\n"},
            "mergeable:221": {"exit_code": 0, "stdout": "MERGEABLE"},
        }
        harness = _Harness(tmp_path, control)
        harness.run(ARGVS[argv_name])
        # No open PR carries the transitional `claude/` prefix this run -- a retirement candidate
        # notice must be appended to GITHUB_STEP_SUMMARY, never silently swallowed.
        assert "transitional prefix retirement notice" in harness.summary_text
        assert "claude/" in harness.summary_text

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_declared_prefixes_no_retirement_notice_when_transitional_prefix_matches(
        self, tmp_path: Path, argv_name: str
    ) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "231\tclaude/branch-231\tsha231\n"},
            "mergeable:231": {"exit_code": 0, "stdout": "MERGEABLE"},
        }
        harness = _Harness(tmp_path, control)
        harness.run(ARGVS[argv_name])
        assert "transitional prefix retirement notice" not in harness.summary_text

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_declared_prefixes_admit_a_ccr_harness_branch(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "241\tccr-679d754f-m99ugp\tsha241\n"},
            "mergeable:241": {"exit_code": 0, "stdout": "CONFLICTING"},
            "comments:241": {"exit_code": 0, "stdout": ""},
            "comment:241": {"exit_code": 0, "stdout": ""},
        }
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert harness.call_count("mergeable:241") == 1
        assert harness.comment_posted("241")
        assert "ccr-679d754f-m99ugp" not in harness.summary_text
        assert "ccr-=1" in result.stdout


class TestSignalGreenPredicate:
    def test_signal_green_admits_every_declared_prefix(self) -> None:
        ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
        predicate = ci["jobs"]["signal-green"]["if"]
        ci_tokens = set(re.findall(r"startsWith\(github\.head_ref, '([^']+)'\)", predicate))
        lines = validator._join_continuations(SCRIPT.read_text(encoding="utf-8"))
        declared = set(validator._parse_prefix_array(lines, validator._PREFIX_DECLARATION_RE) or [])
        assert ci_tokens == declared
        assert any("ccr-679d754f-m99ugp".startswith(token) for token in ci_tokens)


class TestExitStatusContract:
    """continue-on-error: true keeps the job green regardless, but the SCRIPT's own exit code
    must be non-zero exactly when at least one gh call failed, and zero otherwise."""

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_no_open_prs_exits_zero(self, tmp_path: Path, argv_name: str) -> None:
        control = {"list": {"exit_code": 0, "stdout": ""}}
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert result.returncode == 0

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_pr_list_failure_is_counted_and_does_not_crash(self, tmp_path: Path, argv_name: str) -> None:
        control = {"list": {"exit_code": 1, "stdout": "", "stderr": "gh: rate limited"}}
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert result.returncode != 0
        assert "1 failure(s)" in result.stderr

    @pytest.mark.parametrize("argv_name", sorted(ARGVS))
    def test_multiple_failures_are_all_counted(self, tmp_path: Path, argv_name: str) -> None:
        control = {
            "list": {"exit_code": 0, "stdout": "161\tagent/branch-161\tsha161\n162\tagent/branch-162\tsha162\n"},
            "mergeable:161": {"exit_code": 1, "stdout": "", "stderr": "x"},
            "mergeable:162": {"exit_code": 1, "stdout": "", "stderr": "x"},
        }
        harness = _Harness(tmp_path, control)
        result = harness.run(ARGVS[argv_name])
        assert result.returncode != 0
        assert "2 failure(s)" in result.stderr


class TestRealWorkflowBodyWiring:
    """Production-fidelity: parse the REAL workflow YAML, extract its actual run: body, and
    EXECUTE that body (never a substring assertion) under the argv the YAML implies (no `shell:`
    key anywhere on the step/job/workflow -> GitHub's hostile default run-step invocation)."""

    def test_real_workflow_body_reaches_delegate_and_completes_sweep(self, tmp_path: Path) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        job = workflow["jobs"]["detect-conflicts"]
        delegating_step = next(step for step in job["steps"] if isinstance(step.get("run"), str))
        run_body = delegating_step["run"]

        assert "shell" not in delegating_step
        assert "shell" not in job
        assert "shell" not in workflow.get("defaults", {}).get("run", {})

        body_file = tmp_path / "run_body.sh"
        body_file.write_text(run_body, encoding="utf-8")

        control = {
            "list": {"exit_code": 0, "stdout": "171\tagent/branch-171\tsha171\n"},
            "mergeable:171": {"exit_code": 0, "stdout": "CONFLICTING"},
            "comments:171": {"exit_code": 0, "stdout": ""},
            "comment:171": {"exit_code": 0, "stdout": ""},
        }
        harness = _Harness(tmp_path, control)
        # cwd=ROOT: the run body's path (scripts/ci/pr_conflict_signal.sh) is repo-root-relative,
        # exactly as it is in the real job (actions/checkout puts the runner's cwd at the repo
        # root before this step executes).
        result = harness.run(HOSTILE_ARGV, script=body_file, cwd=ROOT)

        assert harness.comment_posted("171"), (
            f"the real workflow run: body ({run_body!r}) did not resolve the delegate and "
            f"complete the sweep. stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.returncode == 0
