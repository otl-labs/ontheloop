"""Tests for validate_tier_floor() -- T3.17 (VF-04) deterministic V-tier floor."""

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks import registry
from scripts.checks.roadmap.validate_tier_floor import validate_tier_floor


class TestValidateTierFloor:
    """T3.17 (VF-04): deterministic V-tier floor over schema_version-2 plan scope."""

    FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "plan_documents"

    def _copy_as_plan(self, src_name: str, tmp_path: Path, data: dict | None = None) -> None:
        import yaml

        if data is None:
            with (self.FIXTURES / src_name).open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        target = tmp_path / f"PLAN-{data['slug']}.yaml"
        target.write_text(yaml.safe_dump(data), encoding="utf-8")

    def test_empty_plans_dir_passes(self, tmp_path: Path, capsys) -> None:
        failed: list[str] = []
        validate_tier_floor(failed, plans_dir=tmp_path)
        assert failed == []
        assert "no PLAN-*.yaml files to validate" in capsys.readouterr().out

    def test_lambda_code_file_in_scope_below_v2_fails(self, tmp_path: Path, capsys) -> None:
        self._copy_as_plan("tier_floor_violation_v2.yaml", tmp_path)
        failed: list[str] = []
        validate_tier_floor(failed, plans_dir=tmp_path)
        assert "Deterministic V-tier floor validation" in failed
        assert "below floor V3" in capsys.readouterr().out

    def test_tier_waiver_rescues_lambda_code_violation(self, tmp_path: Path) -> None:
        import yaml

        with (self.FIXTURES / "tier_floor_violation_v2.yaml").open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        data["tier_waiver"] = "conscious V2: handler change is comment-only"
        self._copy_as_plan("tier_floor_violation_v2.yaml", tmp_path, data=data)
        failed: list[str] = []
        validate_tier_floor(failed, plans_dir=tmp_path)
        assert failed == []

    def test_v1_plan_below_floor_skipped_grandfathered(self, tmp_path: Path) -> None:
        import yaml

        with (self.FIXTURES / "tier_floor_violation_v2.yaml").open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        data["schema_version"] = 1
        data["slug"] = "zz-v1-below-floor-demo"
        data["plan_path"] = "docs/plans/PLAN-zz-v1-below-floor-demo.yaml"
        target = tmp_path / "PLAN-zz-v1-below-floor-demo.yaml"
        target.write_text(yaml.safe_dump(data), encoding="utf-8")
        failed: list[str] = []
        validate_tier_floor(failed, plans_dir=tmp_path)
        assert failed == []

    def test_tf_in_scope_forces_v3(self, tmp_path: Path) -> None:
        import yaml

        with (self.FIXTURES / "valid_v2.yaml").open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        data["scope"] = [{"file": "terraform/personal/foo.tf", "action": "Modify", "purpose": "tf change"}]
        target = tmp_path / f"PLAN-{data['slug']}.yaml"
        target.write_text(yaml.safe_dump(data), encoding="utf-8")
        failed: list[str] = []
        validate_tier_floor(failed, plans_dir=tmp_path)
        assert "Deterministic V-tier floor validation" in failed

    def test_python_only_scope_floors_to_v2_and_passes_at_v2(self, tmp_path: Path) -> None:
        self._copy_as_plan("valid_v2.yaml", tmp_path)
        failed: list[str] = []
        validate_tier_floor(failed, plans_dir=tmp_path)
        assert failed == []

    def test_docs_only_scope_floors_to_v1(self, tmp_path: Path) -> None:
        import yaml

        with (self.FIXTURES / "valid_v2.yaml").open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        data["scope"] = [{"file": "docs/PROJECT_CONTEXT.md", "action": "Modify", "purpose": "docs change"}]
        data["verification_tier"] = "V1"
        target = tmp_path / f"PLAN-{data['slug']}.yaml"
        target.write_text(yaml.safe_dump(data), encoding="utf-8")
        failed: list[str] = []
        validate_tier_floor(failed, plans_dir=tmp_path)
        assert failed == []

    def test_alias_matches_registered_check(self) -> None:
        assert validate_tier_floor is registry.resolve("validate_tier_floor")

    def test_lambda_manifest_load_failure_treated_as_no_code_files(self, tmp_path: Path) -> None:
        from scripts.checks.roadmap import validate_tier_floor as _tier_floor_module

        with patch.object(_tier_floor_module.lambda_manifest, "load_all", side_effect=RuntimeError("boom")):
            self._copy_as_plan("valid_v2.yaml", tmp_path)
            failed: list[str] = []
            validate_tier_floor(failed, plans_dir=tmp_path)
        assert failed == []

    def test_stub_manifest_skipped(self, tmp_path: Path) -> None:
        from scripts.checks.roadmap import validate_tier_floor as _tier_floor_module
        from scripts.lambda_manifest import LambdaManifest

        stub_manifest = LambdaManifest(
            artifact="stub.zip",
            handlers=["src/lambdas/ducklake_catalog_dr/handler.py"],
            status="stub",
        )
        with patch.object(_tier_floor_module.lambda_manifest, "load_all", return_value={"stub": stub_manifest}):
            self._copy_as_plan("tier_floor_violation_v2.yaml", tmp_path)
            failed: list[str] = []
            validate_tier_floor(failed, plans_dir=tmp_path)
        # The stub manifest's handler is skipped, so the fixture's scope file (which
        # matches only that stub handler) is not treated as Lambda code -- floors to V2.
        assert failed == []

    def test_excluded_handler_path_skipped(self, tmp_path: Path) -> None:
        from scripts.checks.roadmap import validate_tier_floor as _tier_floor_module
        from scripts.lambda_manifest import LambdaManifest

        excluded_manifest = LambdaManifest(
            artifact="excluded.zip",
            handlers=["src/lambdas/ducklake_catalog_dr/handler.py"],
            excludes=["src/lambdas/ducklake_catalog_dr/handler.py"],
            status="active",
        )
        with patch.object(_tier_floor_module.lambda_manifest, "load_all", return_value={"excluded": excluded_manifest}):
            self._copy_as_plan("tier_floor_violation_v2.yaml", tmp_path)
            failed: list[str] = []
            validate_tier_floor(failed, plans_dir=tmp_path)
        # The only manifest's handler is excludes-listed, so no code files are derived
        # and the fixture's Lambda scope file no longer forces a V3 floor.
        assert failed == []


_CHECK = "validate_tier_floor"
_UNIT = "v2 plans"
_REPO_ROOT = Path(__file__).parents[3]
_FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "plan_documents"


def _write_plan(plans_dir: Path, slug: str, *, schema_version: int = 2, violating: bool = False) -> None:
    import yaml

    source = "tier_floor_violation_v2.yaml" if violating else "valid_v2.yaml"
    with (_FIXTURES / source).open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    data["slug"] = slug
    data["schema_version"] = schema_version
    (plans_dir / f"PLAN-{slug}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _declared(plans_dir: Path | None) -> tuple[list[str], registry._Declaration | None, int]:
    from scripts.checks.roadmap import validate_tier_floor as _tier_floor_module

    registry.pop_declaration()
    failed: list[str] = []
    with patch.object(_tier_floor_module, "_compute_floor", wraps=_tier_floor_module._compute_floor) as spy:
        validate_tier_floor(failed, plans_dir=plans_dir)
    return failed, registry.pop_declaration(), spy.call_count


class TestTierFloorLoader:
    def test_plans_parse_through_libyaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.checks.roadmap import validate_tier_floor as _tier_floor_module
        from tests.fixtures import yaml_parse_spy

        _write_plan(tmp_path, "plan-a")
        _write_plan(tmp_path, "plan-b", violating=True)
        monkeypatch.setattr(_tier_floor_module.lambda_manifest, "load_all", lambda *a, **k: {})
        log = yaml_parse_spy.install(monkeypatch)
        failed: list[str] = []
        validate_tier_floor(failed, plans_dir=tmp_path)
        frame = "checks/roadmap/validate_tier_floor.py:validate_tier_floor"
        assert [e for e in log.pure_entries if frame in e.stack] == []
        assert sum(1 for e in log.entries if e.kind == "libyaml" and frame in e.stack) == 2


class TestTierFloorAccountingDeclaration:
    """The check declares how many schema_version-2 plans it computed a floor for, so a run records enforced
    with a count that tracks the judged plans -- not a constant, not the plan-file total, and not the violation
    count."""

    def test_real_tree_declares_every_judged_plan(self) -> None:
        import yaml

        expected = 0
        for path in sorted((_REPO_ROOT / "docs" / "plans").glob("PLAN-*.yaml")):
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
            if isinstance(data, dict) and data.get("schema_version") == 2:
                expected += 1

        failed, declaration, probes = _declared(None)
        outcome = registry.build_outcome(_CHECK, "check", declaration, bool(failed))

        assert expected > 0
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", expected, _UNIT)
        assert probes == expected
        assert outcome.status == ("failed" if failed else "enforced")

    @pytest.mark.parametrize("n", [1, 3])
    def test_count_tracks_v2_plans_and_excludes_other_documents(self, tmp_path: Path, n: int) -> None:
        for i in range(n):
            _write_plan(tmp_path, f"v2-plan-{i}")
        _write_plan(tmp_path, "v1-plan", schema_version=1, violating=True)
        _write_plan(tmp_path, "v3-plan", schema_version=3, violating=True)
        (tmp_path / "PLAN-not-a-mapping.yaml").write_text("- just\n- a list\n", encoding="utf-8")
        (tmp_path / "PLAN-empty.yaml").write_text("", encoding="utf-8")
        (tmp_path / "notes.yaml").write_text("schema_version: 2\n", encoding="utf-8")

        failed, declaration, probes = _declared(tmp_path)
        outcome = registry.build_outcome(_CHECK, "check", declaration, bool(failed))

        assert failed == []
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", n, _UNIT)
        assert probes == n
        assert outcome.status == "enforced"

    def test_violating_plans_are_counted_and_record_failed(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "a-bad", violating=True)
        _write_plan(tmp_path, "b-clean")
        _write_plan(tmp_path, "c-bad", violating=True)

        failed, declaration, probes = _declared(tmp_path)
        outcome = registry.build_outcome(_CHECK, "check", declaration, bool(failed))

        assert failed == ["Deterministic V-tier floor validation"]
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", 3, _UNIT)
        assert probes == 3
        assert outcome.status == "failed"

    def test_no_v2_plans_declares_vacuous_domain(self, tmp_path: Path) -> None:
        _write_plan(tmp_path, "v1-only", schema_version=1)

        failed, declaration, probes = _declared(tmp_path)
        outcome = registry.build_outcome(_CHECK, "check", declaration, bool(failed))

        assert failed == []
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", 0, _UNIT)
        assert probes == 0
        assert outcome.status == "vacuous"

    def test_empty_plans_dir_declares_vacuous_domain(self, tmp_path: Path) -> None:
        failed, declaration, probes = _declared(tmp_path)
        outcome = registry.build_outcome(_CHECK, "check", declaration, bool(failed))

        assert failed == []
        assert declaration is not None
        assert (declaration.kind, declaration.count, declaration.unit) == ("examined", 0, _UNIT)
        assert probes == 0
        assert outcome.status == "vacuous"
