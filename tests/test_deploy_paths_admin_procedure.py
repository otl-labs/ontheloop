"""Regression guard for docs/contracts/deploy-paths.yaml's admin_out_of_band.procedure.

Decision 178 clause 4: the terraform/bootstrap grant-surface narrowing is already merged in HCL
but must not be applied while a dependent terraform/personal destroy or gated apply is still in
flight (Decision 156 point 2's lockout shape, generalized to any narrowing that races a dependent
operation). This guards the PRE-APPLY ordering step that mitigates that hazard, and that the
pre-existing POST-APPLY simulate step survives alongside it, in the right order.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CONTRACT_PATH = Path("docs/contracts/deploy-paths.yaml")


def _procedure_steps() -> list[str]:
    doc = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    steps = doc["admin_out_of_band"]["procedure"]["steps"]
    assert isinstance(steps, list) and steps, "admin_out_of_band.procedure.steps must be a non-empty list"
    return steps


def test_admin_procedure_has_preapply_grant_surface_step() -> None:
    """A PRE-APPLY step must name the grant-surface ordering precondition, and must precede the
    existing POST-APPLY simulate step -- the list is ordered, so a pre-apply instruction appended
    after the post-apply one reads wrong."""
    steps = _procedure_steps()

    pre_apply_idx = next((i for i, s in enumerate(steps) if s.lstrip().startswith("PRE-APPLY")), None)
    assert pre_apply_idx is not None, "no procedure step begins with the PRE-APPLY token"
    pre_apply_step = steps[pre_apply_idx]
    assert "grant surface" in pre_apply_step.lower(), "PRE-APPLY step must name the grant-surface precondition"

    post_apply_idx = next((i for i, s in enumerate(steps) if s.lstrip().startswith("POST-APPLY")), None)
    assert post_apply_idx is not None, "the existing POST-APPLY simulate step did not survive"
    assert "scripts.ci.iam_simulate" in steps[post_apply_idx], "POST-APPLY step must still name the live-simulate gate"

    assert pre_apply_idx < post_apply_idx, "PRE-APPLY step must precede the POST-APPLY step"


def test_admin_procedure_has_self_access_isolation_step() -> None:
    """A step must name both BootstrapStateProvisioning and IAMFull, forbid bundling them with any
    other change, and sit before the POST-APPLY simulate step (PLAN-platform-iam-bootstrap-adopt)."""
    steps = _procedure_steps()

    isolation_idx = next(
        (i for i, s in enumerate(steps) if "BootstrapStateProvisioning" in s and "IAMFull" in s),
        None,
    )
    assert isolation_idx is not None, "no procedure step names both BootstrapStateProvisioning and IAMFull"
    isolation_step = steps[isolation_idx]
    assert "no other change" in isolation_step, "the self-access isolation step must forbid bundling other changes"

    post_apply_idx = next((i for i, s in enumerate(steps) if s.lstrip().startswith("POST-APPLY")), None)
    assert post_apply_idx is not None, "the existing POST-APPLY simulate step did not survive"
    assert isolation_idx < post_apply_idx, "the self-access isolation step must precede the POST-APPLY step"


def test_split_apply_step_excludes_platform_iam() -> None:
    """PLAN-platform-iam-personal-release (Decision 223): the SPLIT-APPLY step no longer names
    PlatformDev/PlatformAdmin IAM as its trigger -- that IAM now lives in terraform/bootstrap and
    changes through this loop with no split and no -target -- and the PRE-APPLY, self-access
    isolation and POST-APPLY steps survive in order around it."""
    steps = _procedure_steps()

    split_apply_idx = next((i for i, s in enumerate(steps) if s.lstrip().startswith("SPLIT-APPLY")), None)
    assert split_apply_idx is not None, "no procedure step begins with the SPLIT-APPLY token"
    split_apply_step = steps[split_apply_idx]
    assert "terraform/bootstrap" in split_apply_step
    assert "no split" in split_apply_step
    assert "any inline-policy write on PlatformDev" not in split_apply_step

    pre_apply_idx = next((i for i, s in enumerate(steps) if s.lstrip().startswith("PRE-APPLY")), None)
    isolation_idx = next((i for i, s in enumerate(steps) if "BootstrapStateProvisioning" in s and "IAMFull" in s), None)
    post_apply_idx = next((i for i, s in enumerate(steps) if s.lstrip().startswith("POST-APPLY")), None)
    assert None not in (pre_apply_idx, isolation_idx, post_apply_idx), "a surrounding step did not survive"
    assert split_apply_idx < pre_apply_idx < isolation_idx < post_apply_idx, (
        "SPLIT-APPLY, PRE-APPLY, the self-access isolation step and POST-APPLY must stay in that order"
    )


class TestPreMergeLivePlan:
    """rec-4140 / admin_out_of_band.pre_merge_live_plan: the standing rule's contract key, its
    amendment_log entry, and the four skill pointers (planning, implement, code-review,
    plan-critique). No exact rule-length assertion (tests/CLAUDE.md count-coupling rule)."""

    SKILL_PATHS = (
        Path(".claude/skills/planning/SKILL.md"),
        Path(".claude/skills/implement/SKILL.md"),
        Path(".claude/skills/code-review/SKILL.md"),
        Path(".claude/skills/plan-critique/SKILL.md"),
    )

    def _doc(self) -> dict:
        return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_key_exists_with_description(self) -> None:
        key = self._doc()["admin_out_of_band"]["pre_merge_live_plan"]
        assert isinstance(key.get("description"), str) and key["description"].strip()

    def test_rule_clause_order(self) -> None:
        rule = self._doc()["admin_out_of_band"]["pre_merge_live_plan"]["rule"]
        assert isinstance(rule, list) and len(rule) >= 3
        assert rule[0].lstrip().startswith("PRE-MERGE GATE (ADMIN)")
        assert rule[1].lstrip().startswith("ENVIRONMENT CHECK")
        assert rule[2].lstrip().startswith("V3 RESOLUTION")

    def test_environment_check_uses_caller_arn_helper(self) -> None:
        rule = self._doc()["admin_out_of_band"]["pre_merge_live_plan"]["rule"]
        env_check = " ".join(rule[1].split())
        assert "bin/venv-python -m scripts.aws_identity caller-arn --profile agent_platform_admin" in env_check
        assert "get-caller-identity" not in env_check

    def test_rule_has_decision_101_counts_only_entry(self) -> None:
        rule = self._doc()["admin_out_of_band"]["pre_merge_live_plan"]["rule"]
        assert any("Decision 101" in entry for entry in rule), "no rule entry names Decision 101"

    def test_enforcement_names_the_validator(self) -> None:
        key = self._doc()["admin_out_of_band"]["pre_merge_live_plan"]
        assert "validate_admin_root_premerge_gate" in key.get("enforcement", "")

    def test_precedent_present(self) -> None:
        key = self._doc()["admin_out_of_band"]["pre_merge_live_plan"]
        assert isinstance(key.get("precedent"), str) and key["precedent"].strip()

    def test_an_amendment_log_entry_names_the_key(self) -> None:
        """Presence, not position: a later plan (e.g. bootstrap-adopt, personal-release) may
        prepend its own amendment_log entry, which would otherwise displace this one from index
        0 without this plan's own entry ever having gone missing."""
        amendments = self._doc()["amendment_log"]
        assert isinstance(amendments, list) and amendments
        assert any("pre_merge_live_plan" in entry.get("summary", "") for entry in amendments)

    def test_pre_existing_pre_apply_test_unchanged(self) -> None:
        """Confirms this class's addition did not disturb the pre-existing PRE-APPLY test."""
        test_admin_procedure_has_preapply_grant_surface_step()

    def test_each_skill_points_at_the_rule(self) -> None:
        for path in self.SKILL_PATHS:
            text = path.read_text(encoding="utf-8")
            assert "admin_out_of_band.pre_merge_live_plan" in text, f"{path} missing the pointer"


def _steps(block: dict) -> list[dict]:
    steps = block["steps"]
    assert steps and all(step["command"].strip() and step["check"].strip() for step in steps), (
        "every step needs a command and a check"
    )
    return steps


def _index(steps: list[dict], step_id: str) -> int:
    ids = [step["id"] for step in steps]
    assert step_id in ids, (step_id, ids)
    return ids.index(step_id)


def test_production_catalog_provisioning_orders_its_two_sittings() -> None:
    """Decision 230 cl.4: the carve-out's procedure orders each sitting, keeps the two independent, names the operator for the
    stats verbs, and rolls the smoke switch back through release mode before the deploy hold is released."""
    doc = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    block = doc["admin_out_of_band"]["production_catalog_provisioning"]
    assert block["record_file"].endswith("ADMIN-SESSION-RECORD.md")
    assert block["measurement_file"].endswith("MEASUREMENT.md")

    one, two = _steps(block["sitting_1"]), _steps(block["sitting_2"])
    assert "sitting 2" not in str(block["sitting_1"]).lower() and "sitting_2" not in str(block["sitting_1"])
    sitting_2_text = str({k: v for k, v in block["sitting_2"].items() if k != "rollback"}).lower()
    assert "sitting 1" not in sitting_2_text and "sitting_1" not in sitting_2_text

    first = [_index(one, name) for name in ("measurement", "provision_telemetry_catalog", "provision_writer_prod_login")]
    assert first == sorted(first) and _index(one, "provision_reader_login") < _index(one, "ensure_catalog_indexes")
    assert _index(one, "provision_telemetry_catalog") < _index(one, "ensure_catalog_indexes")
    assert _index(one, "bootstrap_apply_and_simulate") < _index(one, "measurement")

    second = [
        _index(two, name)
        for name in (
            "adopt_smoke",
            "maintainer_login",
            "merge_switch_pr",
            "clean_owner_rerun",
            "narrow_smoke_writer",
            "release_hold",
        )
    ]
    assert second == sorted(second) and _index(two, "release_hold") < _index(two, "live_proof_deploy")

    every = one + two + block["sitting_2"]["rollback"]
    for step in every:
        command = step["command"]
        if "catalog_stats" in command or "ensure_catalog_indexes" in command:
            assert step["actor"] == "operator", step["id"]
        if any(verb in command for verb in ("provision_catalog_owner", "provision_telemetry_catalog", "login_steps_ref")):
            assert step["actor"] == "admin_agent", step["id"]
        assert "merge_ops" not in command and "gc_ops" not in command, step["id"]
        assert step["actor"] in {"operator", "admin_agent", "coordinator"}, step["id"]
    assert {s["id"]: s["actor"] for s in one}["measurement"] == "operator"
    assert {s["id"]: s["actor"] for s in one}["ensure_catalog_indexes"] == "operator"

    rollback = block["sitting_2"]["rollback"]
    ids = [step["id"] for step in rollback]
    assert ids[0] == "keep_hold" and ids.index("revert_switch_pr") < ids.index("release_mode") < ids.index("green_deploy_run")
    assert ids.index("green_deploy_run") < ids.index("release_hold_after_rollback")
    assert '"mode":"release"' in rollback[ids.index("release_mode")]["command"]
    for gate in (
        "MAINTENANCE_MERGE",
        "MAINTENANCE_GC",
        "MAINTENANCE_BREAKER",
        "MAINTENANCE_HOT_MERGE",
        "MAINTENANCE_CREATE_TELEMETRY",
    ):
        assert gate in rollback[ids.index("green_deploy_run")]["check"], gate
