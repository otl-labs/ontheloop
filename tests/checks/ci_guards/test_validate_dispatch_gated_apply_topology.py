"""Tests for validate_dispatch_gated_apply_topology() -- terraform-apply-sandbox.yml's
dispatch-reachable gated-apply topology guard (Decision 183; rec-2918 part (c)).

Pass-path fixture is the REAL committed workflow (never an inline stand-in, so a future edit that
quietly drifts the real file is caught here too). Every negative deep-copies that real parsed
workflow and mutates exactly one invariant-relevant field, then patches this module's own `_load`
binding to return the mutated dict -- so each negative isolates to its own invariant's failure
label and never trips a sibling invariant by accident -- except invariant (xiii), which reads
reconcile.yml and the action file through the same `_load`, so under the single-return-value `_run`
the sandbox dict stands in for both and every existing negative also fails (xiii); that is expected,
not a regression. TestRevisePathInvariants patches `_load` per path instead.
"""

from __future__ import annotations

import copy
from unittest.mock import patch

from scripts.checks import _common, registry
from scripts.checks.ci_guards.validate_dispatch_gated_apply_topology import (
    _ARTIFACT_MARKER,
    validate_dispatch_gated_apply_topology,
)
from scripts.verify_ci_workflow import _load

_MODULE = "scripts.checks.ci_guards.validate_dispatch_gated_apply_topology"
_WORKFLOW_REL_PATH = ".github/workflows/terraform-apply-sandbox.yml"


def _real_workflow() -> dict:
    return _load(str(_common.ROOT / _WORKFLOW_REL_PATH))


def _run(data: dict) -> list[str]:
    with patch(f"{_MODULE}._load", return_value=data):
        failed: list[str] = []
        validate_dispatch_gated_apply_topology(failed)
    return failed


def _pre_fix_shape() -> dict:
    """Deep-copy the real parsed workflow and rebuild the EXACT pre-fix shape gated-apply carried
    before Decision 183: its `if` regains the push conjunct that made a guard-ROUTED
    workflow_dispatch structurally unreachable (rec-2918's dispatch-vs-push deadlock). This is the
    LOAD-BEARING negative -- it must fail invariant (i) and nothing else."""
    data = copy.deepcopy(_real_workflow())
    gated_if = str(data["jobs"]["gated-apply"]["if"])
    data["jobs"]["gated-apply"]["if"] = f"{gated_if} && github.event_name == 'push'"
    return data


class TestPassPath:
    def test_passes_against_the_real_committed_workflow(self) -> None:
        failed: list[str] = []
        validate_dispatch_gated_apply_topology(failed)
        assert failed == []

    def test_registered_in_pre_sequence(self) -> None:
        names = {step.name for step in registry.pre_sequence()}
        assert "validate_dispatch_gated_apply_topology" in names

    def test_examined_count_is_eight_topology_invariants(self) -> None:
        with registry.outcome_scope("validate_dispatch_gated_apply_topology"):
            failed: list[str] = []
            validate_dispatch_gated_apply_topology(failed)
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 13
        assert declaration.unit == "topology_invariants"


class TestPreFixShapeNegative:
    def test_pre_fix_shape_fails_only_invariant_i(self) -> None:
        failed = _run(_pre_fix_shape())
        assert any("(i)" in item for item in failed), failed
        # LOAD-BEARING: the pre-fix shape only reintroduces the push conjunct -- every other
        # invariant must stay green, or this negative is not isolating the defect it claims to.
        assert not any("(ii)" in item for item in failed), failed
        assert not any("(iii)" in item for item in failed), failed


class TestPerInvariantNegatives:
    def test_invariant_i_fails_when_routed_signal_missing(self) -> None:
        data = copy.deepcopy(_real_workflow())
        data["jobs"]["gated-apply"]["if"] = "always() && needs.apply-sandbox.result == 'success'"
        failed = _run(data)
        assert any("(i)" in item for item in failed), failed

    def test_invariant_ii_fails_when_environment_dropped(self) -> None:
        data = copy.deepcopy(_real_workflow())
        del data["jobs"]["gated-apply"]["environment"]
        failed = _run(data)
        assert any("(ii)" in item for item in failed), failed

    def test_invariant_ii_fails_when_terraform_plan_smuggled_in(self) -> None:
        data = copy.deepcopy(_real_workflow())
        data["jobs"]["gated-apply"]["steps"].append({"name": "sneaky replan", "run": "terraform plan -out=plan.bin"})
        failed = _run(data)
        assert any("(ii)" in item for item in failed), failed

    def test_invariant_iii_fails_when_fresh_plan_pending_output_missing(self) -> None:
        data = copy.deepcopy(_real_workflow())
        del data["jobs"]["apply-sandbox"]["outputs"]["fresh_plan_pending"]
        failed = _run(data)
        assert any("(iii)" in item for item in failed), failed

    def test_invariant_iv_fails_when_upload_artifact_step_ungated(self) -> None:
        data = copy.deepcopy(_real_workflow())
        for step in data["jobs"]["apply-sandbox"]["steps"]:
            if str(step.get("uses", "")).startswith("actions/upload-artifact"):
                step["if"] = "success()"
        failed = _run(data)
        assert any("(iv)" in item for item in failed), failed

    def test_invariant_iv_is_not_fooled_by_an_unrelated_earlier_upload(self) -> None:
        """A first-match `next()` over upload-artifact steps would validate this unrelated,
        correctly-gated upload and let the real fresh-plan upload go ungated. Marker-anchored
        selection must still fail."""
        data = copy.deepcopy(_real_workflow())
        steps = data["jobs"]["apply-sandbox"]["steps"]
        decoy = {"name": "Upload some other diagnostic", "uses": "actions/upload-artifact@v4", "if": "always()"}
        steps.insert(0, decoy)
        for step in steps:
            if _ARTIFACT_MARKER in str(step.get("name", "")) and str(step.get("uses", "")).startswith(
                "actions/upload-artifact"
            ):
                step["if"] = "success()"
        failed = _run(data)
        assert any("(iv)" in item for item in failed), failed

    def test_invariant_iv_fails_when_the_marked_upload_step_is_absent(self) -> None:
        data = copy.deepcopy(_real_workflow())
        steps = data["jobs"]["apply-sandbox"]["steps"]
        data["jobs"]["apply-sandbox"]["steps"] = [step for step in steps if _ARTIFACT_MARKER not in str(step.get("name", ""))]
        failed = _run(data)
        assert any("(iv)" in item for item in failed), failed

    def test_invariant_v_fails_when_download_artifact_step_ungated(self) -> None:
        data = copy.deepcopy(_real_workflow())
        for step in data["jobs"]["gated-apply"]["steps"]:
            if str(step.get("uses", "")).startswith("actions/download-artifact"):
                step["if"] = "success()"
        failed = _run(data)
        assert any("(v)" in item for item in failed), failed

    def test_invariant_v_fails_when_fetch_plan_not_mutually_exclusive(self) -> None:
        data = copy.deepcopy(_real_workflow())
        for step in data["jobs"]["gated-apply"]["steps"]:
            if step.get("id") == "fetch_plan":
                step["if"] = "success()"
        failed = _run(data)
        assert any("(v)" in item for item in failed), failed

    def test_invariant_vi_fails_when_artifact_sha_not_consumed(self) -> None:
        data = copy.deepcopy(_real_workflow())
        for step in data["jobs"]["gated-apply"]["steps"]:
            if step.get("id") == "artifact_sha":
                step["run"] = 'echo "value=hardcoded" >> "$GITHUB_OUTPUT"'
        failed = _run(data)
        assert any("(vi)" in item for item in failed), failed

    def test_invariant_vii_fails_when_wake_sha_not_derived_from_ack_input(self) -> None:
        data = copy.deepcopy(_real_workflow())
        for step in data["jobs"]["apply-sandbox"]["steps"]:
            if "wake" in str(step.get("name", "")).lower():
                step["env"]["SHA"] = "${{ github.sha }}"
        failed = _run(data)
        assert any("(vii)" in item for item in failed), failed

    def test_invariant_v_is_not_fooled_by_an_unrelated_earlier_download(self) -> None:
        data = copy.deepcopy(_real_workflow())
        steps = data["jobs"]["gated-apply"]["steps"]
        steps.insert(0, {"name": "Download something else", "uses": "actions/download-artifact@v4", "if": "always()"})
        for step in steps:
            if _ARTIFACT_MARKER in str(step.get("name", "")) and str(step.get("uses", "")).startswith(
                "actions/download-artifact"
            ):
                step["if"] = "success()"
        failed = _run(data)
        assert any("(v)" in item for item in failed), failed

    def test_invariant_viii_fails_when_apply_sandbox_admits_pull_request(self) -> None:
        """gated-apply carries no event conjunct any more, so a widened apply-sandbox `if` is the
        one thing that would make the tf-gated-apply Environment PR-reachable."""
        data = copy.deepcopy(_real_workflow())
        data["jobs"]["apply-sandbox"]["if"] = (
            "${{ github.event_name == 'push' || github.event_name == 'workflow_dispatch' "
            "|| github.event_name == 'pull_request' }}"
        )
        failed = _run(data)
        assert any("(viii)" in item for item in failed), failed
        assert not any("(i)" in item for item in failed), failed

    def test_invariant_viii_fails_when_apply_sandbox_drops_its_event_gate(self) -> None:
        data = copy.deepcopy(_real_workflow())
        data["jobs"]["apply-sandbox"]["if"] = "success()"
        failed = _run(data)
        assert any("(viii)" in item for item in failed), failed


class TestMissingJobs:
    def test_fails_and_declares_vacuous_when_a_required_job_is_absent(self) -> None:
        data = copy.deepcopy(_real_workflow())
        del data["jobs"]["gated-apply"]
        with registry.outcome_scope("validate_dispatch_gated_apply_topology"):
            failed = _run(data)
        declaration = registry.pop_declaration()
        assert any("job missing" in item for item in failed), failed
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 0


class TestUnreadableWorkflow:
    def test_skipped_is_declared_when_workflow_unreadable(self) -> None:
        with registry.outcome_scope("validate_dispatch_gated_apply_topology"):
            with patch(f"{_MODULE}._load", side_effect=OSError("no such file")):
                failed: list[str] = []
                validate_dispatch_gated_apply_topology(failed)
        declaration = registry.pop_declaration()
        assert failed == []
        assert declaration is not None
        assert declaration.kind == "skipped"


_RECONCILE_REL_PATH = ".github/workflows/reconcile.yml"
_ACTION_REL_PATH = ".github/actions/subagent-plan-review/action.yml"
_DISJUNCT = "github.event_name == 'push' && steps.review.outcome == 'success' && steps.review.outputs.outcome == 'revise'"


def _run_by_path(sandbox: dict, reconcile: dict | None = None, action: dict | None = None) -> list[str]:
    by_path = {
        _WORKFLOW_REL_PATH: sandbox,
        _RECONCILE_REL_PATH: reconcile if reconcile is not None else _load(str(_common.ROOT / _RECONCILE_REL_PATH)),
        _ACTION_REL_PATH: action if action is not None else _load(str(_common.ROOT / _ACTION_REL_PATH)),
    }

    def fake_load(path: str) -> dict:
        for rel, data in by_path.items():
            if path.endswith(rel):
                return data
        raise AssertionError(path)

    with patch(f"{_MODULE}._load", side_effect=fake_load):
        failed: list[str] = []
        validate_dispatch_gated_apply_topology(failed)
    return failed


def _steps_of(data: dict, job: str = "apply-sandbox") -> list[dict]:
    return data["jobs"][job]["steps"]


def _sandbox() -> dict:
    return copy.deepcopy(_real_workflow())


def _step(data: dict, step_id: str) -> dict:
    return next(step for step in _steps_of(data) if step.get("id") == step_id)


def _only(failed: list[str], label: str) -> None:
    assert len(failed) == 1 and label in failed[0], failed


class TestRevisePathInvariants:
    def test_real_files_pass_every_new_invariant(self) -> None:
        assert _run_by_path(_sandbox()) == []

    def test_invariant_ix_fails_when_revise_routes_is_not_push_only(self) -> None:
        data = _sandbox()
        _step(data, "review")["with"]["revise-routes"] = "true"
        _only(_run_by_path(data), "(ix)")

    def test_invariant_x_fails_without_the_proceed_conjunct(self) -> None:
        data = _sandbox()
        step = _step(data, "apply")
        step["if"] = step["if"].replace(" && steps.review.outputs.outcome == 'proceed'", "")
        _only(_run_by_path(data), "(x)")

    def test_invariant_xi_fails_when_the_job_output_drops_the_disjunct(self) -> None:
        data = _sandbox()
        data["jobs"]["apply-sandbox"]["outputs"]["routed"] = "${{ steps.guard.outputs.routed }}"
        _only(_run_by_path(data), "(xi)")

    def test_invariant_xi_fails_when_record_write_drops_the_disjunct(self) -> None:
        data = _sandbox()
        _step(data, "record_write")["with"]["routed"] = "${{ steps.guard.outputs.routed }}"
        _only(_run_by_path(data), "(xi)")

    def test_invariant_xi_fails_when_the_wake_env_drops_the_disjunct(self) -> None:
        data = _sandbox()
        wake = next(s for s in _steps_of(data) if "wake" in str(s.get("name", "")).lower())
        wake["env"]["ROUTED"] = "${{ steps.guard.outputs.routed }}"
        _only(_run_by_path(data), "(xi)")

    def test_invariant_xi_fails_when_any_site_drops_the_success_conjunct(self) -> None:
        weak = _DISJUNCT.replace("steps.review.outcome == 'success' && ", "")
        for site in ("output", "record_write", "wake"):
            data = _sandbox()
            if site == "output":
                holder, key = data["jobs"]["apply-sandbox"]["outputs"], "routed"
            elif site == "record_write":
                holder, key = _step(data, "record_write")["with"], "routed"
            else:
                holder = next(s for s in _steps_of(data) if "wake" in str(s.get("name", "")).lower())["env"]
                key = "ROUTED"
            holder[key] = holder[key].replace(_DISJUNCT, weak)
            _only(_run_by_path(data), "(xi)")

    def test_invariant_xii_fails_when_the_emit_step_mentions_the_review(self) -> None:
        data = _sandbox()
        step = _step(data, "upload_fresh_plan_sha256")
        step["if"] += " && steps.review.outputs.outcome == 'revise'"
        _only(_run_by_path(data), "(xii)")

    def test_invariant_xiii_fails_when_reconcile_passes_revise_routes(self) -> None:
        reconcile = copy.deepcopy(_load(str(_common.ROOT / _RECONCILE_REL_PATH)))
        step = next(
            s
            for job in reconcile["jobs"].values()
            for s in job.get("steps", [])
            if s.get("uses") == "./.github/actions/subagent-plan-review"
        )
        step["with"]["revise-routes"] = "true"
        _only(_run_by_path(_sandbox(), reconcile=reconcile), "(xiii)")

    def test_invariant_xiii_fails_when_the_action_default_flips(self) -> None:
        action = copy.deepcopy(_load(str(_common.ROOT / _ACTION_REL_PATH)))
        action["inputs"]["revise-routes"]["default"] = "true"
        _only(_run_by_path(_sandbox(), action=action), "(xiii)")

    def test_invariant_xiii_fails_and_does_not_skip_when_reconcile_is_unreadable(self) -> None:
        real = _sandbox()

        def fake_load(path: str) -> dict:
            if path.endswith(_RECONCILE_REL_PATH):
                raise OSError("gone")
            return real if path.endswith(_WORKFLOW_REL_PATH) else _load(path)

        with registry.outcome_scope("validate_dispatch_gated_apply_topology"):
            with patch(f"{_MODULE}._load", side_effect=fake_load):
                failed: list[str] = []
                validate_dispatch_gated_apply_topology(failed)
            declaration = registry.pop_declaration()
        _only(failed, "(xiii)")
        assert declaration is not None and declaration.kind == "examined"

    def test_pre_globs_cover_every_file_read(self) -> None:
        from fnmatch import fnmatch

        step = next(s for s in registry.pre_sequence() if s.name == "validate_dispatch_gated_apply_topology")
        assert step.pre_globs is not None
        for rel in (_ACTION_REL_PATH, _RECONCILE_REL_PATH, _WORKFLOW_REL_PATH):
            assert any(fnmatch(rel, glob) for glob in step.pre_globs), rel
