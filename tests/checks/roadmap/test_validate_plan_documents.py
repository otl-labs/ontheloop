from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.checks.roadmap import validate_plan_documents as module
from scripts.checks.roadmap.validate_plan_documents import validate_plan_documents

_FIXTURE_NAME = "PLAN-obligation-fixture.yaml"
_SCOUTED = {"gate": "decision-scout", "round": 1, "verdict": "NO_FLAGS"}
_CRITIQUED = (
    _SCOUTED,
    {"gate": "plan-critique", "round": 1, "verdict": "REVISE", "blocking": 1},
    {"gate": "plan-critique", "round": 2, "verdict": "PROCEED"},
)


def _plan() -> dict:
    return {
        "schema_version": 4,
        "handoff_policy": {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"},
        "slug": "obligation-fixture",
        "intent": "Exercise deterministic test-obligation validation.",
        "plan_type": "IMPLEMENTATION",
        "verification_tier": "V2",
        "plan_path": "docs/plans/PLAN-obligation-fixture.yaml",
        "phase": "test",
        "scope": [{"file": "scripts/example.py", "action": "Modify", "purpose": "Change behavior."}],
        "acceptance_criteria": ["The behavior is covered."],
        "verification_plan": [
            {
                "step": 1,
                "phase": "pre-deploy",
                "action": "test",
                "command": "pytest tests/test_example.py::test_behavior",
                "expected": "passes",
                "fix_if": "repair",
            }
        ],
        "execution_steps": ["Implement."],
    }


def _validate(tmp_path: Path, data: dict, capsys, added: set[str] | None = None) -> tuple[list[str], str]:
    (tmp_path / _FIXTURE_NAME).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    failed: list[str] = []
    validate_plan_documents(failed, plans_dir=tmp_path, added_plan_names=added)
    return failed, capsys.readouterr().out


def test_behavior_scope_without_obligation_fails_with_plan_and_source(tmp_path: Path, capsys) -> None:
    failed, output = _validate(tmp_path, _plan(), capsys)
    assert failed == ["Plan document schema validation"]
    assert _FIXTURE_NAME in output
    assert "scripts/example.py" in output


def test_linked_obligation_passes(tmp_path: Path, capsys) -> None:
    data = _plan()
    data["test_obligations"] = [
        {
            "source": "scripts/example.py",
            "behavior": "changes behavior",
            "test_selector": "tests/test_example.py::test_behavior",
            "verification_step": 1,
            "red_green_expectation": "fails before the implementation and passes after it",
        }
    ]
    failed, _ = _validate(tmp_path, data, capsys)
    assert failed == []


def test_per_source_waiver_passes(tmp_path: Path, capsys) -> None:
    data = _plan()
    data["test_obligations"] = [
        {
            "source": "scripts/example.py",
            "behavior": "configuration-only change with no runtime path",
            "test_selector": "tests/test_example.py::test_behavior",
            "verification_step": 1,
            "waiver_reason": "No deterministic runtime path exists for this configuration constant.",
        }
    ]
    failed, _ = _validate(tmp_path, data, capsys)
    assert failed == []


def test_test_and_plan_scope_needs_no_obligation(tmp_path: Path, capsys) -> None:
    data = _plan()
    data["scope"] = [
        {"file": "tests/test_example.py", "action": "Create", "purpose": "Add coverage."},
        {"file": ".claude/skills/planning/SKILL.md", "action": "Modify", "purpose": "Update instructions."},
        {"file": "docs/plans/PLAN-other.yaml", "action": "Modify", "purpose": "Bookkeeping."},
        {"file": "scripts/gone.py", "action": "Delete", "purpose": "Retire the module."},
    ]
    failed, _ = _validate(tmp_path, data, capsys)
    assert failed == []


@pytest.mark.parametrize(
    "source",
    [
        "config/settings.json",
        "config/tool.ini",
        "terraform/main.tf",
        "bin/runner",
        "Dockerfile",
        "Makefile",
        "config/agent/executor/prompts/implement.prompt.md",
        ".github/prompts/scheduled/doc-freshness.md",
    ],
)
def test_behavior_capable_formats_cannot_escape(tmp_path: Path, capsys, source: str) -> None:
    data = _plan()
    data["scope"] = [{"file": source, "action": "Modify", "purpose": "Change behavior."}]
    failed, output = _validate(tmp_path, data, capsys)
    assert failed == ["Plan document schema validation"]
    assert source in output


def test_historical_schema_remains_valid_without_obligation(tmp_path: Path, capsys) -> None:
    data = _plan()
    data["schema_version"] = 3
    failed, _ = _validate(tmp_path, data, capsys)
    assert failed == []


def test_non_implementation_plan_needs_no_obligation(tmp_path: Path, capsys) -> None:
    data = _plan()
    data["plan_type"] = "REPORT-ONLY"
    data["execution_steps"] = []
    data.pop("handoff_policy")
    failed, _ = _validate(tmp_path, data, capsys)
    assert failed == []


def test_newly_added_plan_below_current_schema_version_is_rejected(tmp_path: Path, capsys) -> None:
    data = _plan()
    data["schema_version"] = 3
    failed, output = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
    assert failed == ["Plan document schema validation"]
    assert "must declare schema_version 5" in output


def test_newly_added_plan_at_current_schema_version_is_accepted(tmp_path: Path, capsys) -> None:
    data = _plan()
    data["schema_version"] = 5
    data["test_obligations"] = [
        {
            "source": "scripts/example.py",
            "behavior": "changes behavior",
            "test_selector": "tests/test_example.py::test_behavior",
            "verification_step": 1,
            "red_green_expectation": "fails before the implementation and passes after it",
        }
    ]
    data["gate_rounds"] = list(_CRITIQUED)
    failed, _ = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
    assert failed == []


def _plan_with_linked_obligation() -> dict:
    data = _plan()
    data["schema_version"] = 5
    data["test_obligations"] = [
        {
            "source": "scripts/example.py",
            "behavior": "changes behavior",
            "test_selector": "tests/test_example.py::test_behavior",
            "verification_step": 1,
            "red_green_expectation": "fails before the implementation and passes after it",
        }
    ]
    data["gate_rounds"] = list(_CRITIQUED)
    return data


def test_added_plan_with_oversized_context_warns_without_failing(tmp_path: Path, capsys) -> None:
    data = _plan_with_linked_obligation()
    data["context"] = [f"line {i}" for i in range(45)]
    failed, output = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
    assert failed == []  # WARNING, not FAIL
    assert "WARN:" in output and "context block is 46 rendered lines" in output


def test_existing_plan_with_oversized_context_is_silent(tmp_path: Path, capsys) -> None:
    data = _plan_with_linked_obligation()
    data["context"] = [f"line {i}" for i in range(45)]
    failed, output = _validate(tmp_path, data, capsys, added=set())
    assert failed == []
    assert "context block is" not in output


def test_added_plan_with_exactly_40_line_context_does_not_warn(tmp_path: Path, capsys) -> None:
    data = _plan_with_linked_obligation()
    data["context"] = [f"line {i}" for i in range(39)]  # header + 39 entries = 40 rendered lines
    failed, output = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
    assert failed == []
    assert "context block is" not in output


def test_added_plan_with_41_line_context_warns(tmp_path: Path, capsys) -> None:
    data = _plan_with_linked_obligation()
    data["context"] = [f"line {i}" for i in range(40)]  # header + 40 entries = 41 rendered lines
    failed, output = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
    assert failed == []
    assert "WARN:" in output and "context block is 41 rendered lines" in output


def test_added_plan_names_selects_only_new_plan_files(monkeypatch) -> None:
    monkeypatch.setattr(
        module._common,
        "get_status_aware_diff",
        lambda: [
            ("A", "docs/plans/PLAN-new.yaml"),
            ("??", "docs/plans/PLAN-untracked.yaml"),
            ("M", "docs/plans/PLAN-edited.yaml"),
            ("D", "docs/plans/PLAN-removed.yaml"),
            ("A", "docs/plans/README.md"),
            ("A", "scripts/example.py"),
        ],
    )
    assert module._added_plan_names() == {"PLAN-new.yaml", "PLAN-untracked.yaml"}


def test_canonical_plan_template_declares_the_gated_schema_version() -> None:
    """The template agents copy must not drift below the version the obligation gate binds at.

    This is the drift that made the gate inert: prose said 'schema 4', the template still emitted 3.
    """
    skill = Path(__file__).resolve().parents[3] / ".claude" / "skills" / "planning" / "SKILL.md"
    declared = re.search(r"^schema_version: (\d+)", skill.read_text(encoding="utf-8"), re.MULTILINE)
    assert declared is not None, "planning SKILL.md has no schema_version line in its plan template"
    assert int(declared.group(1)) == module._MIN_NEW_PLAN_SCHEMA_VERSION


def test_empty_directory_passes(tmp_path: Path, capsys) -> None:
    failed: list[str] = []
    validate_plan_documents(failed, plans_dir=tmp_path)
    assert failed == []
    assert "no PLAN-*.yaml" in capsys.readouterr().out


def test_schema_failure_is_reported(tmp_path: Path, capsys) -> None:
    data = _plan()
    data["unexpected"] = True
    failed, output = _validate(tmp_path, data, capsys)
    assert failed == ["Plan document schema validation"]
    assert "unexpected" in output


def test_import_failure_is_reported_and_sys_path_restored(tmp_path: Path, capsys, monkeypatch) -> None:
    (tmp_path / _FIXTURE_NAME).write_text(yaml.safe_dump(_plan(), sort_keys=False), encoding="utf-8")
    root_str = str(Path(__file__).resolve().parents[3])
    monkeypatch.setattr("scripts.checks.roadmap.validate_plan_documents._common.ROOT", Path(root_str))
    monkeypatch.setattr(
        "scripts.checks.roadmap.validate_plan_documents.sys.path", [p for p in __import__("sys").path if p != root_str]
    )
    original_import = __import__

    def fail_import(name: str, *args: object, **kwargs: object):
        if name == "scripts.roadmap.plan_document":
            raise ImportError("simulated")
        return original_import(name, *args, **kwargs)

    failed: list[str] = []
    with patch("builtins.__import__", fail_import):
        validate_plan_documents(failed, plans_dir=tmp_path)
    assert failed == ["Plan document schema validation"]
    assert root_str not in __import__("sys").path
    assert "simulated" in capsys.readouterr().out


def test_declares_examined_count_of_scanned_plans(tmp_path: Path, capsys) -> None:
    """Check-accounting: a completed scan declares examined(n) with the plan-document count."""
    with patch.object(module.registry, "examined") as mock_examined:
        _validate(tmp_path, _plan(), capsys)
    mock_examined.assert_called_once_with(1, unit="plan_documents")


def test_declares_examined_zero_for_an_empty_plans_dir(tmp_path: Path, capsys) -> None:
    """Check-accounting: an empty scan domain declares examined(0) -- the vacuous status."""
    failed: list[str] = []
    with patch.object(module.registry, "examined") as mock_examined:
        validate_plan_documents(failed, plans_dir=tmp_path, added_plan_names=set())
    capsys.readouterr()
    assert failed == []
    mock_examined.assert_called_once_with(0, unit="plan_documents")


class TestSchemaVersionFloorSplit:
    """PLAN-vp-expected-literal-carrier split the single _MIN_NEW_PLAN_SCHEMA_VERSION constant
    into two independently pinned floors: the AUTHORING floor (bumped 4 -> 5) and
    _TEST_OBLIGATION_MIN_SCHEMA_VERSION (stays at 4). Both floors must be asserted
    independently -- the second must survive the first's bump."""

    def test_authoring_floor_is_five(self) -> None:
        assert module._MIN_NEW_PLAN_SCHEMA_VERSION == 5

    def test_test_obligation_floor_stays_independently_pinned_at_four(self) -> None:
        assert module._TEST_OBLIGATION_MIN_SCHEMA_VERSION == 4

    def test_authoring_floor_refuses_a_newly_added_v4_plan(self, tmp_path: Path, capsys) -> None:
        data = _plan()  # schema_version 4, fully covered -- isolates the authoring-floor failure
        data["test_obligations"] = [
            {
                "source": "scripts/example.py",
                "behavior": "changes behavior",
                "test_selector": "tests/test_example.py::test_behavior",
                "verification_step": 1,
                "red_green_expectation": "fails before the implementation and passes after it",
            }
        ]
        failed, output = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
        assert failed == ["Plan document schema validation"]
        assert "must declare schema_version 5" in output

    def test_obligation_gate_still_binds_a_v4_plan_post_bump(self, tmp_path: Path, capsys) -> None:
        """A v4 IMPLEMENTATION plan that is NOT newly-added (so the authoring floor never
        applies) still fails the test-obligation gate when it carries an uncovered
        behavior-changing scope row -- the independently-pinned obligation floor survives the
        authoring floor's move to 5."""
        data = _plan()  # schema_version 4, no test_obligations -- scope row is uncovered
        failed, output = _validate(tmp_path, data, capsys, added=set())
        assert failed == ["Plan document schema validation"]
        assert "lacks a linked test obligation" in output


class TestGateRoundRecord:
    """A newly-added plan carries its critique record (PLAN-critique-severity-convergence)."""

    @pytest.mark.parametrize(
        ("rows", "message"),
        [
            ([_SCOUTED], "must end with a plan-critique PROCEED that ends Step 9"),
            ([_SCOUTED, {"gate": "plan-critique", "round": 1, "verdict": "REVISE", "blocking": 1}], "must end with"),
            (
                [
                    _SCOUTED,
                    {"gate": "plan-critique", "round": 1, "verdict": "REVISE", "blocking": 2},
                    {"gate": "plan-critique", "round": 2, "verdict": "PROCEED", "nits": 3},
                ],
                "or the confirming round after a PROCEED listing nits",
            ),
            ([{"gate": "plan-critique", "round": 1, "verdict": "PROCEED"}], "has no decision-scout row"),
            (
                [
                    _SCOUTED,
                    {"gate": "plan-critique", "round": 1, "verdict": "REVISE", "blocking": 1, "escalated": True},
                    {"gate": "plan-critique", "round": 2, "verdict": "PROCEED"},
                ],
                "escalated with no escalation_choice",
            ),
        ],
    )
    def test_gaps_fail_on_the_plan_pr(self, tmp_path: Path, capsys, monkeypatch, rows: list[dict], message: str) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        data = _plan_with_linked_obligation()
        data["gate_rounds"] = rows
        failed, output = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
        assert failed == ["Plan document schema validation"]
        assert f"FAIL: {_FIXTURE_NAME}: gate_rounds" in output
        assert message in output

    @pytest.mark.parametrize("event", [None, "push", "workflow_dispatch"])
    def test_gaps_only_warn_off_the_plan_pr(self, tmp_path: Path, capsys, monkeypatch, event: str | None) -> None:
        monkeypatch.setenv("CI", "true")
        if event is None:
            monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
        else:
            monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        data = _plan_with_linked_obligation()
        data["gate_rounds"] = []
        failed, output = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
        assert failed == []
        assert (
            "WARN: PLAN-obligation-fixture.yaml: gate_rounds must end with a plan-critique PROCEED that ends Step 9" in output
        )

    def test_answered_escalation_and_confirmed_proceed_pass_on_the_plan_pr(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        data = _plan_with_linked_obligation()
        data["gate_rounds"] = [
            _SCOUTED,
            {
                "gate": "plan-critique",
                "round": 1,
                "verdict": "REVISE",
                "blocking": 1,
                "escalated": True,
                "escalation_choice": "one more round",
            },
            {"gate": "plan-critique", "round": 2, "verdict": "PROCEED", "nits": 2},
            {"gate": "plan-critique", "round": 3, "verdict": "PROCEED", "nits": 1},
        ]
        failed, output = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
        assert failed == []
        assert "gate_rounds" not in output

    def test_an_accepted_deferral_ends_the_record_on_the_plan_pr(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        data = _plan_with_linked_obligation()
        data["gate_rounds"] = [
            _SCOUTED,
            {"gate": "plan-critique", "round": 1, "verdict": "REVISE", "blocking": 1},
            {
                "gate": "plan-critique",
                "round": 2,
                "verdict": "REVISE",
                "blocking": 1,
                "escalated": True,
                "escalation_choice": "accept-with-deferral",
            },
        ]
        failed, output = _validate(tmp_path, data, capsys, added={_FIXTURE_NAME})
        assert failed == []
        assert "gate_rounds" not in output

    def test_existing_plans_are_not_held_to_the_record(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        data = _plan_with_linked_obligation()
        data["gate_rounds"] = []
        failed, _ = _validate(tmp_path, data, capsys, added=set())
        assert failed == []


class TestWordingCheckRecord:
    """A plan whose approved-text sidecar the diff adds or changes needs a fresh PASS wording-check row."""

    _DIGEST = "d" * 64
    _BASE = "e" * 40

    @classmethod
    def _wording(cls, round_: int = 1, verdict: str = "PASS", selection: str = "complete", digest: str | None = None) -> dict:
        return {
            "gate": "wording-check",
            "round": round_,
            "verdict": verdict,
            "digest": digest or cls._DIGEST,
            "base": cls._BASE,
            "selection": selection,
        }

    def _run(self, tmp_path: Path, capsys, monkeypatch, rows: list[dict], *, event: str, digest: str | None = "UNSET"):
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        monkeypatch.setattr(module._common, "status_diff_base", lambda root=None: "basesha")
        found = self._DIGEST if digest == "UNSET" else digest
        monkeypatch.setattr(module, "_changed_sidecar_digest", lambda slug, base: found)
        data = _plan_with_linked_obligation()
        data["schema_version"] = 5
        data["handoff_policy"] = {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"}
        data["gate_rounds"] = [*_CRITIQUED, *rows]
        return _validate(tmp_path, data, capsys, added=set())

    @pytest.mark.parametrize(
        "rows",
        [
            [],
            [
                {
                    "gate": "wording-check",
                    "round": 1,
                    "verdict": "FAIL",
                    "digest": "d" * 64,
                    "base": "e" * 40,
                    "selection": "complete",
                }
            ],
            [
                {
                    "gate": "wording-check",
                    "round": 1,
                    "verdict": "PASS",
                    "digest": "d" * 64,
                    "base": "e" * 40,
                    "selection": "degraded",
                }
            ],
            [
                {
                    "gate": "wording-check",
                    "round": 1,
                    "verdict": "PASS",
                    "digest": "f" * 64,
                    "base": "e" * 40,
                    "selection": "complete",
                }
            ],
        ],
        ids=["no-row", "last-row-fail", "degraded", "digest-mismatch"],
    )
    def test_gaps_fail_on_the_plan_pr(self, tmp_path: Path, capsys, monkeypatch, rows: list[dict]) -> None:
        failed, output = self._run(tmp_path, capsys, monkeypatch, rows, event="pull_request")
        assert failed == ["Plan document schema validation"]
        assert "wording-check rows with a PASS, selection complete" in output

    def test_gaps_only_warn_off_the_plan_pr(self, tmp_path: Path, capsys, monkeypatch) -> None:
        failed, output = self._run(tmp_path, capsys, monkeypatch, [], event="push")
        assert failed == []
        assert (
            "WARN: PLAN-obligation-fixture.yaml: the diff adds or changes docs/plans/approved/obligation-fixture.yaml"
            in output
        )

    def test_a_fresh_pass_row_satisfies_the_plan_pr(self, tmp_path: Path, capsys, monkeypatch) -> None:
        rows = [self._wording(1, "FAIL"), self._wording(2)]
        failed, output = self._run(tmp_path, capsys, monkeypatch, rows, event="pull_request")
        assert failed == []
        assert "wording-check" not in output

    def test_no_requirement_without_a_changed_sidecar(self, tmp_path: Path, capsys, monkeypatch) -> None:
        failed, output = self._run(tmp_path, capsys, monkeypatch, [], event="pull_request", digest=None)
        assert failed == []
        assert "wording-check" not in output

    def test_an_older_schema_needs_no_row(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setattr(module._common, "status_diff_base", lambda root=None: "basesha")
        monkeypatch.setattr(module, "_changed_sidecar_digest", lambda slug, base: self._DIGEST)
        data = _plan_with_linked_obligation()
        data["schema_version"] = 4
        del data["gate_rounds"]
        failed, _ = _validate(tmp_path, data, capsys, added=set())
        assert failed == []

    def test_an_unresolved_merge_base_warns_once_and_requires_nothing(self, tmp_path: Path, capsys, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
        monkeypatch.setattr(module._common, "status_diff_base", lambda root=None: None)
        data = _plan_with_linked_obligation()
        data["schema_version"] = 5
        data["handoff_policy"] = {"full_validation_required_before_commit": True, "timeout_disposition": "blocked"}
        data["gate_rounds"] = list(_CRITIQUED)
        failed, output = _validate(tmp_path, data, capsys, added=set())
        assert failed == []
        assert output.count("no merge base resolves") == 1

    def test_the_sidecar_digest_reads_added_and_changed_states_only(self, monkeypatch) -> None:
        from scripts.roadmap import approved_text

        monkeypatch.setattr(approved_text, "load_sidecar", lambda slug, root: object())
        monkeypatch.setattr(approved_text, "digest", lambda sidecar: "the-digest")
        states = {"added": "the-digest", "changed": "the-digest", "unchanged": None, "absent": None, "deleted": None}
        for state, expected in states.items():
            monkeypatch.setattr(approved_text, "sidecar_state", lambda slug, root, base, s=state: s)
            assert module._changed_sidecar_digest("x", "base") == expected
