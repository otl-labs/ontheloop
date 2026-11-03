"""Wiring tests for .github/actions/subagent-plan-review/review.sh (rec-2924).

GitHub runs a composite action's `shell: bash` step body as

    bash --noprofile --norc -e -o pipefail {0}

so errexit is INHERITED by the body, and a body-level `set -uo pipefail` does not clear it (`set`
only touches the options it names). scripts/ci/review_verdict.py exits 0/2/1 BY CONTRACT for
PROCEED/REVISE/STARVED, so under inherited -e the `VERDICT=$(... review_verdict.py ...)`
assignment aborted the shell on every non-PROCEED verdict: the same-budget STARVED retry and the
outcome-writing case block were dead code, the caller observed an EMPTY outputs.outcome, missed
its `== 'starved'` anti-masking carve-out (Decision 55), and latched a FALSE RED convergence
record for a run in which terraform apply never executed.

Every case below therefore drives review.sh through the literal GitHub invocation
(GITHUB_COMPOSITE_BASH) rather than a convenient `bash review.sh` -- a child bash does NOT inherit
its parent's shell options, so an easier harness would silently drop the -e that is the entire
root cause and make these tests vacuously pass. TestGitHubStepInvocation proves the harness really
does deliver errexit before any behavioural case relies on it.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
ACTION_DIR = ROOT / ".github" / "actions" / "subagent-plan-review"
REVIEW_SH = ACTION_DIR / "review.sh"
ARCHIVE_SH = ACTION_DIR / "archive_transcripts.sh"
ACTION_YML = ACTION_DIR / "action.yml"
VERDICT_SCRIPT = ROOT / "scripts" / "ci" / "review_verdict.py"

# The exact argv GitHub uses for a composite-action `shell: bash` step body. Load-bearing: the
# inherited -e here is rec-2924's root cause, so the harness must reproduce it literally.
GITHUB_COMPOSITE_BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")

# Representative claude -p --output-format json transcripts, one per verdict class. Fed to the
# REAL review_verdict.py so the python3 shim's exit codes are derived from the live contract
# instead of hardcoded (a hardcoded 0/2/1 would silently rot if that contract ever changed).
_CLASS_TRANSCRIPTS = {
    "PROCEED": {"type": "result", "subtype": "success", "is_error": False, "result": "PROCEED"},
    "REVISE": {"type": "result", "subtype": "success", "is_error": False, "result": "REVISE - unexpected destroy"},
    "STARVED": {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": ""},
}

_PYTHON3_SHIM = """\
#!/usr/bin/env bash
# Stands in for both python3 entrypoints review.sh calls, keyed on the script path in $1.
SCRIPT="${1:-}"
case "$SCRIPT" in
  *terraform_plan_digest.py)
    if [ "${PY3_SHIM_KILL_ON_GUARD:-0}" = "1" ]; then
      kill -KILL "$PPID"
      exit 137
    fi
    if [ "${PY3_SHIM_GUARD_FAIL:-0}" = "1" ]; then
      echo "guard: cannot read plan.json" >&2
      exit 1
    fi
    echo "# stub plan digest"
    exit 0
    ;;
  *review_verdict.py)
    IDX=0
    if [ -f "$VERDICT_COUNTER" ]; then IDX=$(cat "$VERDICT_COUNTER"); fi
    IDX=$((IDX + 1))
    echo "$IDX" > "$VERDICT_COUNTER"
    VERDICT=$(sed -n "${IDX}p" "$VERDICT_SEQUENCE_FILE")
    if [ -z "$VERDICT" ]; then VERDICT="STARVED"; fi
    echo "$VERDICT"
    case "$VERDICT" in
      PROCEED) exit __PROCEED_RC__ ;;
      REVISE)  exit __REVISE_RC__ ;;
      *)       exit __STARVED_RC__ ;;
    esac
    ;;
esac
# Any other invocation (the stats line's `python3 -`) runs on the real interpreter by an absolute
# path baked in here: PATH would re-enter this shim, and env -i strips any variable.
exec "__REAL_PYTHON__" "$@"
"""

# One line per invocation (the prompt review.sh builds is a single line, so the arg echo cannot
# inflate the count). The log path is baked in: review.sh runs claude under env -i.
# claude_p_retry.sh funnels this stdout into the transcript file.
_CLAUDE_SHIM = """\
#!/usr/bin/env bash
echo "invocation $*" >> "__CLAUDE_LOG__"
echo '{"type":"result","subtype":"success","is_error":false,"result":"stub"}'
exit 0
"""

# sudo / npm must never actually run: `command -v claude` finds the shim above, so the install
# branch is unreachable in a healthy run. These record any breach instead of hitting the network.
_NETWORK_SHIM = """\
#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$NETWORK_CALL_LOG"
exit 0
"""


def _write_shim(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture(scope="session")
def verdict_exit_codes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, int]:
    """Derive PROCEED/REVISE/STARVED exit codes by running the REAL review_verdict.py.

    Binds the shim to the live classifier contract: if scripts/ci/review_verdict.py ever changed
    its exit codes, the shim moves with it rather than testing a stale fiction.
    """
    workdir = tmp_path_factory.mktemp("verdict_contract")
    codes: dict[str, int] = {}
    for verdict_class, envelope in _CLASS_TRANSCRIPTS.items():
        transcript = workdir / f"{verdict_class.lower()}.json"
        transcript.write_text(json.dumps(envelope), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(VERDICT_SCRIPT), str(transcript)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.stdout.strip() == verdict_class, f"{verdict_class} transcript misclassified: {result.stdout!r}"
        codes[verdict_class] = result.returncode
    return codes


class _Harness:
    """A prepared review.sh invocation: shimmed PATH, a workdir, and the step's GITHUB_OUTPUT."""

    def __init__(self, tmp_path: Path, env: dict[str, str]) -> None:
        self.tmp_path = tmp_path
        self.env = env
        self.workdir = tmp_path / "work"
        self.github_output = tmp_path / "github_output"
        self.claude_log = tmp_path / "claude_invocations"
        self.network_log = tmp_path / "network_calls"
        self.sequence_file = tmp_path / "verdict_sequence"

    def set_verdicts(self, *verdicts: str) -> None:
        self.sequence_file.write_text("\n".join(verdicts) + "\n", encoding="utf-8")

    def run(self, script: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*GITHUB_COMPOSITE_BASH, str(script or REVIEW_SH)],
            cwd=self.workdir,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    @property
    def outputs(self) -> list[tuple[str, str]]:
        """GITHUB_OUTPUT parsed as ordered key/value pairs (GitHub's own file format)."""
        if not self.github_output.exists():
            return []
        pairs = []
        for line in self.github_output.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                pairs.append((key, value))
        return pairs

    @property
    def outcome(self) -> str:
        """The effective outputs.outcome GitHub would expose (last write wins)."""
        values = [value for key, value in self.outputs if key == "outcome"]
        return values[-1] if values else ""

    def _count_lines(self, path: Path) -> int:
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    @property
    def claude_invocations(self) -> int:
        return self._count_lines(self.claude_log)

    @property
    def network_calls(self) -> int:
        return self._count_lines(self.network_log)

    @property
    def retry_transcript(self) -> Path:
        return self.workdir / "review_retry.json"

    @property
    def step_summary_text(self) -> str:
        path = Path(self.env["GITHUB_STEP_SUMMARY"])
        return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.fixture()
def harness(tmp_path: Path, verdict_exit_codes: dict[str, int]) -> _Harness:
    """review.sh wired to shimmed python3/claude/sudo/npm, with the real claude_p_retry.sh."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "plan.json").write_text("{}", encoding="utf-8")

    python3_shim = (
        _PYTHON3_SHIM.replace("__PROCEED_RC__", str(verdict_exit_codes["PROCEED"]))
        .replace("__REVISE_RC__", str(verdict_exit_codes["REVISE"]))
        .replace("__STARVED_RC__", str(verdict_exit_codes["STARVED"]))
        .replace("__REAL_PYTHON__", sys.executable)
    )
    _write_shim(bin_dir / "python3", python3_shim)
    _write_shim(bin_dir / "claude", _CLAUDE_SHIM.replace("__CLAUDE_LOG__", str(tmp_path / "claude_invocations")))
    _write_shim(bin_dir / "sudo", _NETWORK_SHIM)
    _write_shim(bin_dir / "npm", _NETWORK_SHIM)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["CLAUDE_CODE_OAUTH_TOKEN"] = "stub-token"
    env["CONTEXT_DESCRIPTION"] = "the sandbox auto-apply pipeline"
    env["WORKSPACE_ROOT"] = str(ROOT)
    env["GITHUB_OUTPUT"] = str(tmp_path / "github_output")
    env["GITHUB_STEP_SUMMARY"] = str(tmp_path / "step_summary")
    env["CLAUDE_INVOCATION_LOG"] = str(tmp_path / "claude_invocations")
    env["NETWORK_CALL_LOG"] = str(tmp_path / "network_calls")
    env["VERDICT_COUNTER"] = str(tmp_path / "verdict_counter")
    env["VERDICT_SEQUENCE_FILE"] = str(tmp_path / "verdict_sequence")
    env["CLAUDE_P_RETRY_BACKOFF_BASE"] = "0"

    built = _Harness(tmp_path, env)
    built.set_verdicts("STARVED")
    return built


class TestGitHubStepInvocation:
    """The harness must reproduce GitHub's composite-step shell, errexit included."""

    def test_invocation_is_the_documented_github_argv(self):
        assert GITHUB_COMPOSITE_BASH == ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")

    def test_harness_shell_really_delivers_errexit(self, tmp_path: Path):
        """Guards against a vacuous suite: prove -e aborts a body that only sets -uo pipefail."""
        probe = tmp_path / "probe.sh"
        probe.write_text("set -uo pipefail\nfalse\necho REACHED\n", encoding="utf-8")
        result = subprocess.run([*GITHUB_COMPOSITE_BASH, str(probe)], capture_output=True, text=True, check=False)
        assert "REACHED" not in result.stdout
        assert result.returncode != 0

    def test_set_plus_e_is_what_clears_it(self, tmp_path: Path):
        """The fix's mechanism, under the same shell: `set +e` restores post-failure execution."""
        probe = tmp_path / "probe.sh"
        probe.write_text("set -uo pipefail\nset +e\nfalse\necho REACHED\n", encoding="utf-8")
        result = subprocess.run([*GITHUB_COMPOSITE_BASH, str(probe)], capture_output=True, text=True, check=False)
        assert "REACHED" in result.stdout


class TestVerdictExitCodeContract:
    """review_verdict.py's 0/2/1 contract -- the codes review.sh must survive."""

    def test_proceed_exits_zero(self, verdict_exit_codes):
        assert verdict_exit_codes["PROCEED"] == 0

    def test_revise_and_starved_exit_nonzero(self, verdict_exit_codes):
        """Both non-PROCEED classes are exactly the codes inherited -e turned into a shell abort."""
        assert verdict_exit_codes["REVISE"] != 0
        assert verdict_exit_codes["STARVED"] != 0
        assert verdict_exit_codes["REVISE"] != verdict_exit_codes["STARVED"]


class TestProceed:
    """Classifier exit 0 -> outcome=proceed, script rc 0, no retry."""

    def test_outcome_is_proceed(self, harness):
        harness.set_verdicts("PROCEED")
        harness.run()
        assert harness.outcome == "proceed"

    def test_exit_zero(self, harness):
        harness.set_verdicts("PROCEED")
        assert harness.run().returncode == 0

    def test_retry_did_not_fire(self, harness):
        harness.set_verdicts("PROCEED")
        harness.run()
        assert harness.claude_invocations == 1
        assert not harness.retry_transcript.exists()

    def test_no_network_install_attempted(self, harness):
        harness.set_verdicts("PROCEED")
        harness.run()
        assert harness.network_calls == 0


class TestRevise:
    """Classifier exit 2 -> outcome=revise, non-zero rc, and NO retry (Decision 55)."""

    def test_outcome_is_revise(self, harness):
        harness.set_verdicts("REVISE")
        harness.run()
        assert harness.outcome == "revise"

    def test_exit_nonzero(self, harness):
        harness.set_verdicts("REVISE")
        assert harness.run().returncode != 0

    def test_retry_did_not_fire(self, harness):
        """A substantive rejection is a verdict, never starvation -- it must not be re-run."""
        harness.set_verdicts("REVISE")
        harness.run()
        assert harness.claude_invocations == 1
        assert not harness.retry_transcript.exists()


class TestStarvedThenProceed:
    """The T2.39 c2 same-budget retry must ACTUALLY run and its verdict must win."""

    def test_retry_actually_ran(self, harness):
        harness.set_verdicts("STARVED", "PROCEED")
        harness.run()
        assert harness.claude_invocations == 2

    def test_retry_used_a_separate_transcript(self, harness):
        harness.set_verdicts("STARVED", "PROCEED")
        harness.run()
        assert harness.retry_transcript.exists()

    def test_outcome_reflects_the_retry(self, harness):
        harness.set_verdicts("STARVED", "PROCEED")
        result = harness.run()
        assert harness.outcome == "proceed"
        assert result.returncode == 0

    def test_retry_keeps_the_same_turn_budget(self, harness):
        """T2.39 c2: the retry is same-budget, never a larger one."""
        harness.set_verdicts("STARVED", "PROCEED")
        result = harness.run()
        assert "--max-turns 5" in Path(harness.env["CLAUDE_INVOCATION_LOG"]).read_text(encoding="utf-8")
        assert result.returncode == 0


class TestStarvedTwice:
    """The false-red case: still STARVED after the retry must still publish outcome=starved."""

    def test_outcome_is_starved(self, harness):
        harness.set_verdicts("STARVED", "STARVED")
        harness.run()
        assert harness.outcome == "starved"

    def test_starved_is_present_in_github_output(self, harness):
        """The caller's `== 'starved'` anti-masking carve-out reads this literal line."""
        harness.set_verdicts("STARVED", "STARVED")
        harness.run()
        assert "outcome=starved" in harness.github_output.read_text(encoding="utf-8")

    def test_exit_nonzero_blocks_apply(self, harness):
        harness.set_verdicts("STARVED", "STARVED")
        assert harness.run().returncode != 0

    def test_retry_ran_exactly_once(self, harness):
        harness.set_verdicts("STARVED", "STARVED")
        harness.run()
        assert harness.claude_invocations == 2


class TestTotalOutputChannel:
    """An outcome must be defined on EVERY exit path, including unexpected aborts."""

    def test_signal_killed_midscript_still_publishes_starved(self, harness):
        """python3 dies unexpectedly (SIGKILLs the step shell) -- never an empty outcome."""
        harness.set_verdicts("PROCEED")
        harness.env["PY3_SHIM_KILL_ON_GUARD"] = "1"
        result = harness.run()
        assert result.returncode != 0
        assert "outcome=starved" in harness.github_output.read_text(encoding="utf-8")
        assert harness.outcome == "starved"

    def test_missing_env_abort_still_publishes_starved(self, harness):
        """A regressed action.yml env: block trips `set -u` mid-body -- still fail-closed."""
        harness.set_verdicts("PROCEED")
        del harness.env["CONTEXT_DESCRIPTION"]
        result = harness.run()
        assert result.returncode != 0
        assert harness.outcome == "starved"

    def test_missing_token_publishes_starved(self, harness):
        harness.set_verdicts("PROCEED")
        harness.env["CLAUDE_CODE_OAUTH_TOKEN"] = ""
        result = harness.run()
        assert result.returncode != 0
        assert harness.outcome == "starved"
        assert harness.claude_invocations == 0

    def test_guard_digest_failure_publishes_starved(self, harness):
        """A guard-digest build failure is an infra failure, not a plan rejection."""
        harness.set_verdicts("PROCEED")
        harness.env["PY3_SHIM_GUARD_FAIL"] = "1"
        result = harness.run()
        assert result.returncode != 0
        assert harness.outcome == "starved"
        assert harness.claude_invocations == 0


class TestActionAdapter:
    """action.yml must stay a thin adapter -- the body must not be re-inlined."""

    @pytest.fixture()
    def manifest(self) -> dict:
        return yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))

    def test_review_script_exists(self):
        assert REVIEW_SH.is_file()

    def test_step_delegates_to_review_sh(self, manifest):
        run_body = manifest["runs"]["steps"][0]["run"]
        assert "review.sh" in run_body

    def test_logic_is_not_inlined_in_the_manifest(self, manifest):
        """Re-inlining would restore the inherited-errexit exposure these tests cover."""
        run_body = manifest["runs"]["steps"][0]["run"]
        for token in ("review_verdict.py", "claude_p_retry.sh", "GITHUB_OUTPUT", "MAX_TURNS"):
            assert token not in run_body

    def test_step_exports_the_workspace_root_the_script_reads(self, manifest):
        assert "WORKSPACE_ROOT" in manifest["runs"]["steps"][0]["env"]

    def test_outcome_output_binding_preserved(self, manifest):
        assert manifest["outputs"]["outcome"]["value"] == "${{ steps.review.outputs.outcome }}"

    def test_script_clears_inherited_errexit(self):
        """The one line that makes the whole no-errexit design hold."""
        assert "\nset +e\n" in REVIEW_SH.read_text(encoding="utf-8")

    def test_second_step_delegates_to_archive_transcripts_sh(self, manifest):
        steps = manifest["runs"]["steps"]
        assert len(steps) == 2
        assert "archive_transcripts.sh" in steps[1]["run"]

    def test_second_step_runs_always(self, manifest):
        assert manifest["runs"]["steps"][1]["if"] == "always()"

    def test_second_step_sets_its_own_working_directory(self, manifest):
        """Composite steps do NOT inherit the previous step's working-directory -- a regression
        here silently strands the archival step in the wrong directory (Round-3 polish (b)).
        """
        assert manifest["runs"]["steps"][1]["working-directory"] == "${{ inputs.working-directory }}"


class TestStarvedMarker:
    """Decision 155 marker shape (mirrors the DCG-03 orphan-guard skip marker, Decision 149):
    distinct, greppable, and mirrored to $GITHUB_STEP_SUMMARY on every STARVED exit path, so an
    accumulating STARVED rate is operator-observable rather than buried in a per-run job log.
    """

    def test_missing_token_prints_marker_and_summary(self, harness):
        harness.set_verdicts("PROCEED")
        harness.env["CLAUDE_CODE_OAUTH_TOKEN"] = ""
        result = harness.run()
        assert "[SUBAGENT-REVIEW] STARVED" in result.stderr
        assert "[SUBAGENT-REVIEW] STARVED" in harness.step_summary_text

    def test_guard_digest_failure_prints_marker_and_summary(self, harness):
        harness.set_verdicts("PROCEED")
        harness.env["PY3_SHIM_GUARD_FAIL"] = "1"
        result = harness.run()
        assert "[SUBAGENT-REVIEW] STARVED" in result.stderr
        assert "[SUBAGENT-REVIEW] STARVED" in harness.step_summary_text

    def test_starved_twice_prints_marker_and_summary(self, harness):
        harness.set_verdicts("STARVED", "STARVED")
        result = harness.run()
        assert "[SUBAGENT-REVIEW] STARVED" in result.stderr
        assert "[SUBAGENT-REVIEW] STARVED" in harness.step_summary_text

    def test_marker_never_changes_the_outcome_or_exit_status(self, harness):
        harness.set_verdicts("STARVED", "STARVED")
        result = harness.run()
        assert result.returncode != 0
        assert harness.outcome == "starved"

    def test_marker_absent_on_proceed(self, harness):
        """The marker is STARVED-specific -- a clean PROCEED must not print it."""
        harness.set_verdicts("PROCEED")
        result = harness.run()
        assert "[SUBAGENT-REVIEW] STARVED" not in result.stderr
        assert harness.step_summary_text == ""

    def test_marker_absent_on_revise(self, harness):
        """REVISE is a substantive rejection, not starvation -- no STARVED marker."""
        harness.set_verdicts("REVISE")
        result = harness.run()
        assert "[SUBAGENT-REVIEW] STARVED" not in result.stderr

    def test_no_crash_when_step_summary_unset(self, harness):
        """GITHUB_STEP_SUMMARY absent (e.g. a non-Actions harness) must not abort the script --
        the marker still prints to stderr.
        """
        harness.set_verdicts("STARVED", "STARVED")
        del harness.env["GITHUB_STEP_SUMMARY"]
        result = harness.run()
        assert result.returncode != 0
        assert "[SUBAGENT-REVIEW] STARVED" in result.stderr


_AWS_SUCCESS_SHIM = "#!/usr/bin/env bash\nexit 0\n"
_AWS_FAIL_SHIM = '#!/usr/bin/env bash\necho "aws: simulated failure" >&2\nexit 1\n'


class TestArchivalTotality:
    """archive_transcripts.sh (rec-2923 preventive action) is TOTAL: always exits 0 regardless of
    outcome, and its own failure path prints a distinct marker -- never a bare `|| true`.
    """

    def _run(
        self, tmp_path: Path, *, aws: str, transcripts: tuple[str, ...] = ("review.json",)
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        workdir = tmp_path / "work"
        workdir.mkdir()
        for name in transcripts:
            (workdir / name).write_text("{}", encoding="utf-8")

        env = {"PATH": "/usr/bin:/bin"}
        if aws != "absent":
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            _write_shim(bin_dir / "aws", _AWS_SUCCESS_SHIM if aws == "success" else _AWS_FAIL_SHIM)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        env["RUN_ID"] = "999"
        env["ARCHIVE_BUCKET"] = "test-bucket"
        env["GITHUB_STEP_SUMMARY"] = str(tmp_path / "step_summary")

        result = subprocess.run(
            [*GITHUB_COMPOSITE_BASH, str(ARCHIVE_SH)],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        summary_path = Path(env["GITHUB_STEP_SUMMARY"])
        summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
        return result, summary

    def test_aws_absent_exits_zero_with_marker(self, tmp_path: Path) -> None:
        result, summary = self._run(tmp_path, aws="absent")
        assert result.returncode == 0, result.stderr
        assert "[SUBAGENT-REVIEW] transcript archival FAILED" in result.stderr
        assert "aws CLI not found" in result.stderr
        assert "[SUBAGENT-REVIEW] transcript archival FAILED" in summary

    def test_aws_failing_exits_zero_with_marker(self, tmp_path: Path) -> None:
        result, summary = self._run(tmp_path, aws="fail")
        assert result.returncode == 0, result.stderr
        assert "[SUBAGENT-REVIEW] transcript archival FAILED" in result.stderr
        assert "aws s3 cp failed" in result.stderr
        assert "[SUBAGENT-REVIEW] transcript archival FAILED" in summary

    def test_aws_succeeding_exits_zero_no_failure_marker(self, tmp_path: Path) -> None:
        result, _summary = self._run(tmp_path, aws="success")
        assert result.returncode == 0, result.stderr
        assert "transcript archival FAILED" not in result.stderr

    def test_no_transcripts_present_exits_zero(self, tmp_path: Path) -> None:
        result, _summary = self._run(tmp_path, aws="success", transcripts=())
        assert result.returncode == 0, result.stderr
        assert "nothing to archive" in result.stdout

    def test_partial_failure_both_files_attempted(self, tmp_path: Path) -> None:
        """A failure on one transcript must not abort the loop before the other is attempted."""
        result, _summary = self._run(tmp_path, aws="fail", transcripts=("review.json", "review_retry.json"))
        assert result.returncode == 0, result.stderr
        assert result.stderr.count("aws s3 cp failed") == 2

    def test_script_is_total_by_construction(self) -> None:
        """No bare `|| true` in actual code (the phrase appears in prose comments only) --
        every failure path prints its own distinct marker instead.
        """
        code_lines = [line for line in ARCHIVE_SH.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("#")]
        assert not any("|| true" in line for line in code_lines)
        assert "\nset +e\n" in ARCHIVE_SH.read_text(encoding="utf-8")
