"""Tests for validate_composite_action_shell_bodies() -- R1/R2/R3 composite-action shell-body
rules (rec-2923 preventive action, Decision 162)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks.ci_guards.validate_composite_action_shell_bodies import (
    _R1_KNOWN_VIOLATORS,
    _R3_SPEC,
    _delegated_script,
    _effective_lines,
    _inline_shell_steps,
    _iter_manifests,
    _output_producing_step_ids,
    _r2_test_covers,
    _r3_mention_candidates,
    scan_repository,
    validate_composite_action_shell_bodies,
)

_MODULE = "scripts.checks.ci_guards.validate_composite_action_shell_bodies"


def _make_repo(tmp_path: Path) -> None:
    (tmp_path / ".github" / "actions").mkdir(parents=True)
    (tmp_path / "tests").mkdir(parents=True)
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)


def _write_action(tmp_path: Path, name: str, manifest_text: str) -> Path:
    action_dir = tmp_path / ".github" / "actions" / name
    action_dir.mkdir(parents=True)
    (action_dir / "action.yml").write_text(manifest_text, encoding="utf-8")
    return action_dir


class TestEffectiveLines:
    def test_strips_blank_and_comment_lines(self) -> None:
        body = "\n# a comment\n  \necho hi\n   echo bye  \n"
        assert _effective_lines(body) == ["echo hi", "echo bye"]

    def test_empty_body(self) -> None:
        assert _effective_lines("") == []


class TestDelegatedScript:
    """`_delegated_script` -- the R1 thin-adapter classifier."""

    def test_single_line_delegation_is_thin_adapter(self) -> None:
        body = 'bash "${{ github.action_path }}/foo.sh"\n'
        assert _delegated_script(body) == "foo.sh"

    def test_dollar_github_action_path_form_is_thin_adapter(self) -> None:
        body = 'if ! bash "$GITHUB_ACTION_PATH/assert_review_outcome.sh"; then\n  exit 1\nfi\n'
        assert _delegated_script(body) == "assert_review_outcome.sh"

    def test_delegation_plus_extra_logic_is_not_thin(self) -> None:
        body = 'echo "setup"\nbash "${{ github.action_path }}/foo.sh"\n'
        assert _delegated_script(body) is None

    def test_one_line_if_carrying_computation_is_not_thin(self) -> None:
        """LOAD-BEARING (code-review round 1, High #2): a single-line `if ...; then ...; fi` that
        computes and publishes a step output alongside a delegation must NOT classify as a thin
        adapter. The pre-fix `^if\\b.*` scaffolding wildcard admitted it, which let the exact
        rec-2923 shape -- a computed, potentially-undefined step output -- pass R1.
        """
        body = (
            'if OUT=$(curl -s http://example); then echo "routed=$OUT" >> "$GITHUB_OUTPUT"; fi\n'
            'bash "${{ github.action_path }}/foo.sh"\n'
        )
        assert _delegated_script(body) is None

    def test_multiline_if_carrying_computation_is_not_thin(self) -> None:
        body = (
            "if OUT=$(curl -s http://example); then\n"
            '  echo "routed=$OUT" >> "$GITHUB_OUTPUT"\n'
            "fi\n"
            'bash "$GITHUB_ACTION_PATH/foo.sh"\n'
        )
        assert _delegated_script(body) is None

    def test_compound_delegate_line_is_not_thin(self) -> None:
        """The delegate line is FULLY anchored: an unanchored search accepted arbitrary commands
        riding alongside the delegation on one line (code-review round 1, Medium).
        """
        assert _delegated_script('rm -rf /tmp/x; bash "$GITHUB_ACTION_PATH/foo.sh"') is None

    def test_bare_scaffolding_tokens_are_still_admitted(self) -> None:
        """The legitimate wrapper shape this repo actually uses must keep passing."""
        body = 'if ! bash "$GITHUB_ACTION_PATH/assert_review_outcome.sh"; then\n  exit 1\nfi\n'
        assert _delegated_script(body) == "assert_review_outcome.sh"

    def test_no_delegation_returns_none(self) -> None:
        assert _delegated_script('echo "hello"\nexit 0\n') is None

    def test_empty_body_returns_none(self) -> None:
        assert _delegated_script("") is None


class TestR3MarkerGuardBinding:
    """Thin binding assertions -- R3's marker parsing/authorization delegates to the shared
    scripts.checks._marker_guard module. Behavioural extractor/authorization cases live in
    tests/checks/test__marker_guard.py."""

    def test_spec_token_and_direction(self) -> None:
        assert _R3_SPEC.token == "raise-approved"
        assert _R3_SPEC.gated_direction == "up"

    def test_spec_reason_required(self) -> None:
        """R3 is the only registry that requires a non-empty <reason> -- see
        test_marker_with_no_reason_text_does_not_override_growth for the behavioural proof."""
        assert _R3_SPEC.reason_required is True

    def test_mention_candidates_returns_full_key_and_action_rel_dir(self) -> None:
        key = ".github/actions/materialise-tfvars::#0"
        assert _r3_mention_candidates(key) == [key, ".github/actions/materialise-tfvars"]

    def test_r1_never_reaches_shared_authorization_path(self) -> None:
        """R1 has no marker escape at all -- a valid raise-approved marker on a pinned R1
        entry still fails (see TestR1Rule.test_r1_growth_is_not_rescued_by_a_raise_approved_marker);
        this only confirms the module carries no local existence-only shim R1 could reach."""
        import scripts.checks.ci_guards.validate_composite_action_shell_bodies as mod

        assert not hasattr(mod, "_decision_header_exists")


class TestR2TestCovers:
    def test_no_tests_dir_returns_false(self, tmp_path: Path) -> None:
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            assert _r2_test_covers("foo.sh") is False

    def test_script_referenced_without_literal_argv_is_false(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        (tmp_path / "tests" / "test_foo.py").write_text("# runs foo.sh somehow\n", encoding="utf-8")
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            assert _r2_test_covers("foo.sh") is False

    def test_script_referenced_with_literal_argv_is_true(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        (tmp_path / "tests" / "test_foo.py").write_text(
            'ARGV = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")\n# drives foo.sh\n',
            encoding="utf-8",
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            assert _r2_test_covers("foo.sh") is True


class TestScanRepositoryRealTree:
    """Against the LIVE repo tree -- the universe of output-declaring actions is exactly four."""

    def test_known_r1_violators_present(self) -> None:
        scan = scan_repository()
        assert ".github/actions/deterministic-guard::guard" in scan["r1_violations"]
        assert ".github/actions/fetch-saved-plan::resolve" in scan["r1_violations"]

    def test_review_sh_is_r1_conformant(self) -> None:
        scan = scan_repository()
        assert scan["r1_conformant_scripts"].get(".github/actions/subagent-plan-review::review") == "review.sh"

    def test_no_fifth_output_producing_action_exists(self) -> None:
        scan = scan_repository()
        producing_actions = {k.split("::")[0] for k in {**scan["r1_violations"], **scan["r1_conformant_scripts"]}}
        assert producing_actions == {
            ".github/actions/aws-region",
            ".github/actions/deterministic-guard",
            ".github/actions/fetch-saved-plan",
            ".github/actions/subagent-plan-review",
        }


class TestValidateCompositeActionShellBodiesRealTree:
    """Positive fixture: the live repo, with its checked-in baseline, must pass cleanly."""

    def test_passes_against_real_repository_tree(self) -> None:
        failed: list[str] = []
        validate_composite_action_shell_bodies(failed)
        assert failed == []


class TestR1Rule:
    """R1: a NEW output-producing inline body fails; no marker can ever admit it."""

    def test_new_output_producing_inline_body_fails(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_action(
            tmp_path,
            "new-action",
            "name: new-action\n"
            "outputs:\n"
            "  foo:\n"
            "    value: ${{ steps.step1.outputs.foo }}\n"
            "runs:\n"
            "  using: composite\n"
            "  steps:\n"
            "    - id: step1\n"
            "      shell: bash\n"
            '      run: |\n        echo "foo=bar" >> "$GITHUB_OUTPUT"\n',
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("R1" in f and "new-action::step1" in f for f in failed)

    def test_thin_adapter_output_producing_body_is_not_an_r1_violation(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_action(
            tmp_path,
            "adapter-action",
            "name: adapter-action\n"
            "outputs:\n"
            "  foo:\n"
            "    value: ${{ steps.step1.outputs.foo }}\n"
            "runs:\n"
            "  using: composite\n"
            "  steps:\n"
            "    - id: step1\n"
            "      shell: bash\n"
            '      run: bash "${{ github.action_path }}/body.sh"\n',
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert not any("R1" in f for f in failed)

    def _write_known_violator(self, tmp_path: Path, n_lines: int) -> str:
        """Materialise a live output-producing inline body under a KNOWN violator's exact key."""
        known = sorted(_R1_KNOWN_VIOLATORS)[0]
        action_name = known.split("::")[0].rsplit("/", 1)[1]
        step_id = known.split("::")[1]
        body = "\n".join(f'        echo "k{i}=v" >> "$GITHUB_OUTPUT"' for i in range(n_lines))
        _write_action(
            tmp_path,
            action_name,
            "name: x\n"
            "outputs:\n"
            "  routed:\n"
            f"    value: ${{{{ steps.{step_id}.outputs.routed }}}}\n"
            "runs:\n"
            "  using: composite\n"
            "  steps:\n"
            f"    - id: {step_id}\n"
            "      shell: bash\n"
            f"      run: |\n{body}\n",
        )
        return known

    def test_r1_growth_of_a_pinned_violator_fails(self, tmp_path: Path) -> None:
        """LOAD-BEARING (code-review round 2, High). The r1 section is strictly shrinking: a pinned
        known violator that GROWS must fail. Before this check the r1 loop compared membership
        only, so the declared 13/9 line counts were inert decoration that read as an enforced
        ceiling -- defeating plan acceptance criterion 5 and Decision 162 point 1 on the two bodies
        that ARE the rec-2923 defect class.
        """
        _make_repo(tmp_path)
        known = self._write_known_violator(tmp_path, 40)
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            f'r1:\n  "{known}": 13\nr3: {{}}\n', encoding="utf-8"
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("R1" in f and "GREW" in f and known in f for f in failed), failed

    def test_r1_at_or_below_its_pin_passes(self, tmp_path: Path) -> None:
        """Shrinking (or holding) a pinned violator is always allowed -- the ratchet is one-way."""
        _make_repo(tmp_path)
        known = self._write_known_violator(tmp_path, 5)
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            f'r1:\n  "{known}": 13\nr3: {{}}\n', encoding="utf-8"
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert failed == []

    def test_r1_growth_is_not_rescued_by_a_raise_approved_marker(self, tmp_path: Path) -> None:
        """R1 has NO marker escape at all -- unlike r3, no Decision citation can admit growth."""
        _make_repo(tmp_path)
        known = self._write_known_violator(tmp_path, 40)
        (tmp_path / "docs" / "DECISIONS.md").write_text("## Decision 162: Test (Decided)\n", encoding="utf-8")
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            f'r1:\n  "{known}": 13  # raise-approved: dec-162 please let this grow\nr3: {{}}\n',
            encoding="utf-8",
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("R1" in f and "GREW" in f for f in failed), failed

    def test_unrecognized_baseline_r1_entry_fails(self, tmp_path: Path) -> None:
        """A baseline r1 section can never grow via a config edit alone -- the guard cross-checks
        against its own frozen constant, so an extra entry is rejected even with no live violation.
        """
        _make_repo(tmp_path)
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            'r1:\n  ".github/actions/not-a-real-known-violator::step": 5\nr3: {}\n', encoding="utf-8"
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("r1 section" in f for f in failed)


class TestR2Rule:
    """R2: an R1-conformant delegate script needs literal-argv test coverage."""

    _MANIFEST = (
        "name: adapter-action\n"
        "outputs:\n"
        "  foo:\n"
        "    value: ${{ steps.step1.outputs.foo }}\n"
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - id: step1\n"
        "      shell: bash\n"
        '      run: bash "${{ github.action_path }}/body.sh"\n'
    )

    def test_thin_adapter_with_no_literal_argv_test_fails_r2(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        action_dir = _write_action(tmp_path, "adapter-action", self._MANIFEST)
        (action_dir / "body.sh").write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("R2" in f and "body.sh" in f for f in failed)

    def test_thin_adapter_with_literal_argv_test_passes_r2(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        action_dir = _write_action(tmp_path, "adapter-action", self._MANIFEST)
        (action_dir / "body.sh").write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
        (tmp_path / "tests" / "test_body.py").write_text(
            'GITHUB_COMPOSITE_BASH = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")\n'
            "# drives body.sh under GITHUB_COMPOSITE_BASH\n",
            encoding="utf-8",
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert not any("R2" in f for f in failed)


def _write_r3_action(tmp_path: Path, name: str, n_lines: int) -> None:
    body = "\n".join(f"        echo line{i}" for i in range(n_lines))
    _write_action(
        tmp_path,
        name,
        f"name: {name}\nruns:\n  using: composite\n  steps:\n    - id: step1\n      shell: bash\n      run: |\n{body}\n",
    )


class TestR3Rule:
    """R3: an unbaselined body fails; a baselined-and-matching body passes."""

    def test_unbaselined_new_body_fails(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_r3_action(tmp_path, "r3-action", 5)
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("R3" in f and "r3-action::step1" in f and "no" in f for f in failed)

    def test_baselined_matching_body_passes(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_r3_action(tmp_path, "r3-action", 5)
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            'r1: {}\nr3:\n  ".github/actions/r3-action::step1": 5\n', encoding="utf-8"
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert failed == []

    def test_baselined_body_with_headroom_passes(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_r3_action(tmp_path, "r3-action", 5)
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            'r1: {}\nr3:\n  ".github/actions/r3-action::step1": 10\n', encoding="utf-8"
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert failed == []


class TestBaselineRatchet:
    """Prove the ratchet ratchets: growth and a new entry are both rejected, and only the inline
    Decision-citing marker overrides -- a free-form comment does not.
    """

    def test_growth_beyond_baseline_with_no_marker_fails(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_r3_action(tmp_path, "r3-action", 20)
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            'r1: {}\nr3:\n  ".github/actions/r3-action::step1": 10\n', encoding="utf-8"
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("R3" in f and "GREW" in f for f in failed)

    def test_new_entry_with_no_baseline_row_fails(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_r3_action(tmp_path, "brand-new-action", 3)
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text("r1: {}\nr3: {}\n", encoding="utf-8")
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("R3" in f and "brand-new-action::step1" in f for f in failed)

    def test_free_form_comment_does_not_override_growth(self, tmp_path: Path) -> None:
        """A bare, non-marker comment on the entry line must NOT exempt it -- only the exact
        `# raise-approved: dec-NNN <reason>` grammar does.
        """
        _make_repo(tmp_path)
        _write_r3_action(tmp_path, "r3-action", 20)
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            'r1: {}\nr3:\n  ".github/actions/r3-action::step1": 10  # this is fine, trust me\n', encoding="utf-8"
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("R3" in f and "GREW" in f for f in failed)

    def test_marker_with_no_reason_text_does_not_override_growth(self, tmp_path: Path) -> None:
        """R3 alone requires a non-empty <reason> (RegistrySpec.reason_required=True, mirroring
        the incumbent composite-only `_RAISE_APPROVED_RE`'s `\\s+\\S` requirement) -- a bare
        `# raise-approved: dec-NNN` with nothing after the decision id must parse as NO marker
        at all, never silently exempting the entry from its declared ceiling."""
        _make_repo(tmp_path)
        _write_r3_action(tmp_path, "r3-action", 20)
        (tmp_path / "docs" / "DECISIONS.md").write_text(
            "## Decision 162: Test decision (Decided)\n\n**Decision:** Authorizes .github/actions/r3-action.\n",
            encoding="utf-8",
        )
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            'r1: {}\nr3:\n  ".github/actions/r3-action::step1": 10  # raise-approved: dec-162\n', encoding="utf-8"
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("R3" in f and "GREW" in f for f in failed), failed

    def test_valid_raise_approved_marker_overrides_growth(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_r3_action(tmp_path, "r3-action", 20)
        (tmp_path / "docs" / "DECISIONS.md").write_text(
            "## Decision 162: Test decision (Decided)\n\n**Decision:** Authorizes .github/actions/r3-action.\n",
            encoding="utf-8",
        )
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            "r1: {}\nr3:\n"
            '  ".github/actions/r3-action::step1": 10  '
            "# raise-approved: dec-162 deliberately larger for test coverage\n",
            encoding="utf-8",
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert failed == []

    def test_marker_citing_nonexistent_decision_fails(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_r3_action(tmp_path, "r3-action", 20)
        (tmp_path / "docs" / "DECISIONS.md").write_text("## Decision 1: Unrelated (Decided)\n", encoding="utf-8")
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text(
            'r1: {}\nr3:\n  ".github/actions/r3-action::step1": 10  # raise-approved: dec-999 nonexistent\n',
            encoding="utf-8",
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("does not authorize" in f for f in failed)


class TestMalformedManifestTolerance:
    """The parser degrades on malformed shapes rather than raising -- a guard that crashes on an
    unrelated manifest typo takes the whole presubmit tier down with it (rec-2027 pattern).
    """

    def test_no_actions_dir_yields_no_manifests(self, tmp_path: Path) -> None:
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            assert _iter_manifests() == []

    def test_outputs_not_a_dict_yields_no_ids(self) -> None:
        assert _output_producing_step_ids({"outputs": "not-a-dict"}) == set()

    def test_non_dict_output_entry_skipped(self) -> None:
        data = {"outputs": {"a": "not-a-dict", "b": {"value": "${{ steps.step1.outputs.x }}"}}}
        assert _output_producing_step_ids(data) == {"step1"}

    def test_non_composite_runs_yields_no_steps(self) -> None:
        assert _inline_shell_steps({"runs": {"using": "node20"}}) == []
        assert _inline_shell_steps({"runs": "not-a-dict"}) == []

    def test_steps_not_a_list_yields_no_steps(self) -> None:
        assert _inline_shell_steps({"runs": {"using": "composite", "steps": "nope"}}) == []

    def test_non_bash_and_non_dict_steps_skipped(self) -> None:
        data = {
            "runs": {
                "using": "composite",
                "steps": [
                    "not-a-dict",
                    {"uses": "actions/checkout@v7"},
                    {"shell": "pwsh", "run": "echo hi"},
                    {"id": "keep", "shell": "bash", "run": "echo kept"},
                ],
            }
        }
        assert _inline_shell_steps(data) == [("keep", "echo kept")]

    def test_non_string_run_body_skipped(self) -> None:
        data = {"runs": {"using": "composite", "steps": [{"shell": "bash", "run": {"not": "a string"}}]}}
        assert _inline_shell_steps(data) == []

    def test_unparseable_manifest_skipped_without_raising(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_action(tmp_path, "broken", "name: [unterminated\n  description: oops")
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            scan = scan_repository()
        assert scan["r1_violations"] == {}
        assert scan["r3_bodies"] == {}

    def test_non_dict_manifest_root_skipped(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        _write_action(tmp_path, "scalar", "just-a-string\n")
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            scan = scan_repository()
        assert scan["r1_violations"] == {}

    def test_unreadable_test_file_skipped_in_r2_scan(self, tmp_path: Path) -> None:
        """A test file the scanner cannot decode must not abort the R2 walk."""
        _make_repo(tmp_path)
        (tmp_path / "tests" / "test_binary.py").write_bytes(b"\xff\xfe\x00 not utf-8 \xff")
        (tmp_path / "tests" / "test_good.py").write_text(
            'ARGV = ("bash", "--noprofile", "--norc", "-e", "-o", "pipefail")\n# drives foo.sh\n',
            encoding="utf-8",
        )
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            assert _r2_test_covers("foo.sh") is True


class TestKnownViolatorBaselineRegistration:
    """A known R1 violator that is live but ABSENT from the baseline's r1 section fails -- the
    pinning is an explicit registration, not an implicit exemption granted by the frozen constant.
    """

    def test_live_known_violator_missing_from_baseline_fails(self, tmp_path: Path) -> None:
        _make_repo(tmp_path)
        # Reproduce a known violator's key by naming the action after it.
        known = sorted(_R1_KNOWN_VIOLATORS)[0]  # ".github/actions/deterministic-guard::guard"
        action_name = known.split("::")[0].rsplit("/", 1)[1]
        step_id = known.split("::")[1]
        _write_action(
            tmp_path,
            action_name,
            "name: x\n"
            "outputs:\n"
            "  routed:\n"
            f"    value: ${{{{ steps.{step_id}.outputs.routed }}}}\n"
            "runs:\n"
            "  using: composite\n"
            "  steps:\n"
            f"    - id: {step_id}\n"
            "      shell: bash\n"
            '      run: |\n        echo "routed=true" >> "$GITHUB_OUTPUT"\n',
        )
        (tmp_path / "config" / "composite_action_body_baseline.yaml").write_text("r1: {}\nr3: {}\n", encoding="utf-8")
        with patch(f"{_MODULE}._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_composite_action_shell_bodies(failed)
        assert any("missing from" in f and known in f for f in failed)
