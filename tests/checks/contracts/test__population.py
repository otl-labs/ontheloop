"""Direct module-boundary coverage for scripts/checks/contracts/_population.py: declared-shape
validation, evaluator resolution, Class-A/B shape detection, extension/depth conformance,
subject uniqueness, base resolution, and census counting."""

from __future__ import annotations

from pathlib import Path

import pytest

import scripts.checks.hygiene.validate_placement  # noqa: F401  -- registers "validate_placement" regardless of run order
from scripts.checks.contracts import _population
from scripts.contracts_schema import EvaluatorSpec

_ROOT = Path(__file__).resolve().parents[3]


class TestClassifyFile:
    def test_ritual_classes(self) -> None:
        for cls in ("A", "B", "C"):
            assert _population.classify_file({"contract": {"class": cls}}) == "ritual"

    def test_class_d(self) -> None:
        assert _population.classify_file({"contract": {"class": "D"}}) == "class_d"

    def test_unshaped_variants(self) -> None:
        assert _population.classify_file({"contract": {"class": "Q"}}) == "unshaped"
        assert _population.classify_file({"contract": {}}) == "unshaped"
        assert _population.classify_file({"no_contract_key": 1}) == "unshaped"
        assert _population.classify_file(["not", "a", "mapping"]) == "unshaped"
        assert _population.classify_file(None) == "unshaped"


class TestDetectSmuggledShape:
    def test_literal_fields_and_verbs(self) -> None:
        assert _population.detect_smuggled_shape({"contract": {}, "fields": {}}) is not None
        assert _population.detect_smuggled_shape({"contract": {}, "verbs": {}}) is not None

    def test_field_shaped_and_verb_shaped_renamed_keys(self) -> None:
        field_shaped = {
            "contract": {},
            "columns": {"c1": {"type": "string", "nullable": False}},
        }
        assert _population.detect_smuggled_shape(field_shaped) is not None

        verb_shaped = {
            "contract": {},
            "operations": {"op1": {"payload_schema_ref": "x", "response_codes": {200: "ok"}}},
        }
        assert _population.detect_smuggled_shape(verb_shaped) is not None

    def test_clean_heterogeneous_body_passes(self) -> None:
        body = {
            "contract": {},
            "prose_section": {"a": "just text", "b": "more text"},
            "list_section": [1, 2, 3],
            "scalar_section": "value",
        }
        assert _population.detect_smuggled_shape(body) is None

    def test_non_dict_values_and_empty_mappings_are_ignored(self) -> None:
        body = {"contract": {}, "empty": {}, "scalar": 1, "listy": [{"type": "string"}]}
        assert _population.detect_smuggled_shape(body) is None

    def test_partial_descriptor_overlap_is_not_smuggled(self) -> None:
        # Only 1 descriptor key on the single leaf -- below the >=2 threshold.
        body = {"contract": {}, "maybe": {"leaf": {"type": "string"}}}
        assert _population.detect_smuggled_shape(body) is None

    def test_mixed_leaf_shapes_do_not_trip_the_all_leaves_rule(self) -> None:
        # One leaf is field-shaped, the other is not -- a genuine mechanism table can have
        # heterogeneous entries, so the rule only fires when EVERY leaf is class-shaped.
        body = {
            "contract": {},
            "mixed": {
                "leaf1": {"type": "string", "nullable": False},
                "leaf2": {"prose": "just a description"},
            },
        }
        assert _population.detect_smuggled_shape(body) is None


class TestExtensionDepthAndScan:
    def test_is_governed_extension_depth(self) -> None:
        assert _population.is_governed_extension_depth("a.yaml")
        assert _population.is_governed_extension_depth("a.yml")
        assert not _population.is_governed_extension_depth("a.YAML")
        assert not _population.is_governed_extension_depth("nested/a.yaml")
        assert not _population.is_governed_extension_depth("a.md")

    def test_scan_tree(self, tmp_path) -> None:
        (tmp_path / "a.yaml").write_text("x: 1\n", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.yaml").write_text("x: 1\n", encoding="utf-8")
        found = {p.relative_to(tmp_path).as_posix() for p in _population.scan_tree(tmp_path)}
        assert found == {"a.yaml", "sub/b.yaml"}


class TestModuleContainsLiteral:
    """Path-shaped literal acceptance + the MATCHER TRAP whitespace-rejection regression
    (migration-step-3-grandfathering)."""

    def test_path_shaped_literal_ending_in_basename_resolves(self, tmp_path) -> None:
        mod = tmp_path / "checker.py"
        mod.write_text('_CONTRACT_REL_PATH = "docs/contracts/decision-entry.yaml"\n', encoding="utf-8")
        assert _population.module_contains_literal(mod, "decision-entry.yaml")

    def test_prose_literal_with_whitespace_is_rejected(self, tmp_path) -> None:
        # The MATCHER TRAP: a long prose annotation string that merely ENDS with the contract's
        # basename must never certify as a reading -- mirrors the live
        # scripts/checks/iam_tf/_write_companions.py instance that motivated this guard.
        mod = tmp_path / "checker.py"
        mod.write_text(
            '_NOTE = "this file writes companion policies referenced by docs/contracts/iam-simulate-fixture.yaml"\n',
            encoding="utf-8",
        )
        assert not _population.module_contains_literal(mod, "iam-simulate-fixture.yaml")

    def test_path_shaped_literal_with_no_slash_falls_back_to_exact_match_only(self, tmp_path) -> None:
        mod = tmp_path / "checker.py"
        mod.write_text('X = "not-the-basename"\n', encoding="utf-8")
        assert not _population.module_contains_literal(mod, "basename.yaml")

    def test_bare_basename_still_resolves_via_exact_match(self, tmp_path) -> None:
        mod = tmp_path / "checker.py"
        mod.write_text('X = "target.yaml"\n', encoding="utf-8")
        assert _population.module_contains_literal(mod, "target.yaml")


class TestResolveEvaluatorModuleBoundary:
    def test_module_contains_literal_excludes_docstrings_and_syntax_errors(self, tmp_path) -> None:
        good = tmp_path / "good.py"
        good.write_text('"""mentions target.yaml only here."""\nX = "target.yaml"\n', encoding="utf-8")
        assert _population.module_contains_literal(good, "target.yaml")

        docstring_only = tmp_path / "doc_only.py"
        docstring_only.write_text('"""mentions target.yaml only here."""\nX = 1\n', encoding="utf-8")
        assert not _population.module_contains_literal(docstring_only, "target.yaml")

        broken = tmp_path / "broken.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        assert not _population.module_contains_literal(broken, "target.yaml")

        missing = tmp_path / "missing.py"
        assert not _population.module_contains_literal(missing, "target.yaml")

    def test_one_hop_scripts_imports_handles_both_import_forms(self, tmp_path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "helper.py").write_text("X = 1\n", encoding="utf-8")
        primary = scripts_dir / "primary.py"
        primary.write_text(
            "from scripts import helper\nimport scripts.helper\nfrom . import sibling\nimport os\n",
            encoding="utf-8",
        )
        hops = _population.one_hop_scripts_imports(primary, tmp_path)
        assert scripts_dir / "helper.py" in hops

    def test_one_hop_scripts_imports_returns_empty_for_unparseable_module(self, tmp_path) -> None:
        broken = tmp_path / "broken.py"
        broken.write_text("def f(:\n", encoding="utf-8")
        assert _population.one_hop_scripts_imports(broken, tmp_path) == []

    def test_one_hop_scripts_imports_falls_back_to_the_leaf_module_itself(self, tmp_path) -> None:
        # `from scripts.contracts_schema import ContractClass` -- ContractClass is a NAME, not a
        # submodule, so candidate=scripts/contracts_schema/ContractClass.py misses and the
        # fallback (the leaf module scripts/contracts_schema.py itself) must be used instead.
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "leaf.py").write_text("ContractClass = 1\n", encoding="utf-8")
        primary = scripts_dir / "primary.py"
        primary.write_text("from scripts.leaf import ContractClass\n", encoding="utf-8")
        hops = _population.one_hop_scripts_imports(primary, tmp_path)
        assert scripts_dir / "leaf.py" in hops

    def test_one_hop_scripts_imports_dedupes_repeated_bare_import(self, tmp_path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "helper.py").write_text("X = 1\n", encoding="utf-8")
        primary = scripts_dir / "primary.py"
        primary.write_text("import scripts.helper\nimport scripts.helper\n", encoding="utf-8")
        hops = _population.one_hop_scripts_imports(primary, tmp_path)
        assert hops == [scripts_dir / "helper.py"]

    def test_find_check_module_returns_none_when_checks_dir_absent(self, tmp_path) -> None:
        assert _population._find_check_module("anything", tmp_path) is None

    def test_find_check_module_skips_unparseable_file_and_returns_none_on_no_match(self, tmp_path) -> None:
        checks_dir = tmp_path / "scripts" / "checks"
        checks_dir.mkdir(parents=True)
        (checks_dir / "broken.py").write_text("def f(:\n", encoding="utf-8")
        (checks_dir / "clean.py").write_text(
            "from scripts.checks import registry\n\n\n"
            "@registry.register('some_other_check')\n"
            "def some_other_check(failed):\n    pass\n",
            encoding="utf-8",
        )
        assert _population._find_check_module("nonexistent_check_name", tmp_path) is None

    def test_call_registers_name_false_for_non_register_call(self) -> None:
        import ast

        tree = ast.parse("@something_else('x')\ndef f():\n    pass\n")
        deco_call = tree.body[0].decorator_list[0]
        assert _population._call_registers_name(deco_call, "x") is False

    def test_agent_surface_read_error_returns_false(self, tmp_path, monkeypatch) -> None:
        target = tmp_path / "x.md"
        target.write_text("data", encoding="utf-8")

        def _raise(*_a, **_k):
            raise OSError("boom")

        monkeypatch.setattr(type(target), "read_text", _raise)
        assert not _population._agent_surface_resolves("x.md", "anything", tmp_path)

    def test_default_sequenced_and_registered_checks_used_when_not_injected(self) -> None:
        resolves, detail = _population.resolve_evaluator(
            "file-router.yaml", EvaluatorSpec(check="validate_placement"), root=_ROOT
        )
        assert resolves, detail

    def test_check_module_unresolvable_fails(self) -> None:
        resolves, detail = _population.resolve_evaluator(
            "anything.yaml",
            EvaluatorSpec(check="totally_fake_name_no_real_module_xyz"),
            root=_ROOT,
            registered_checks={"totally_fake_name_no_real_module_xyz": object()},
            sequenced_names={"totally_fake_name_no_real_module_xyz"},
        )
        assert not resolves
        assert "could not resolve a source module" in detail

    def test_unknown_check_name_fails(self) -> None:
        resolves, detail = _population.resolve_evaluator(
            "anything.yaml",
            EvaluatorSpec(check="unregistered_name"),
            root=_ROOT,
            registered_checks={},
            sequenced_names=set(),
        )
        assert not resolves
        assert "not a registered check" in detail

    def test_registered_but_unsequenced_check_fails(self) -> None:
        resolves, detail = _population.resolve_evaluator(
            "anything.yaml",
            EvaluatorSpec(check="registered_only"),
            root=_ROOT,
            registered_checks={"registered_only": object()},
            sequenced_names=set(),
        )
        assert not resolves
        assert "not sequenced" in detail

    def test_one_hop_import_resolution_via_public_api(self, tmp_path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "helper.py").write_text('_TARGET = "some-contract.yaml"\n', encoding="utf-8")
        (scripts_dir / "primary.py").write_text("from scripts import helper  # noqa: F401\n", encoding="utf-8")
        resolves, detail = _population.resolve_evaluator(
            "some-contract.yaml",
            EvaluatorSpec(check="synthetic_check"),
            root=tmp_path,
            registered_checks={"synthetic_check": object()},
            sequenced_names={"synthetic_check"},
            check_module_resolver=lambda _n: scripts_dir / "primary.py",
        )
        assert resolves, detail

    def test_check_kind_no_match_anywhere_fails(self, tmp_path) -> None:
        primary = tmp_path / "primary.py"
        primary.write_text("VALUE = 1\n", encoding="utf-8")
        resolves, detail = _population.resolve_evaluator(
            "unmentioned.yaml",
            EvaluatorSpec(check="synthetic_check"),
            root=tmp_path,
            registered_checks={"synthetic_check": object()},
            sequenced_names={"synthetic_check"},
            check_module_resolver=lambda _n: primary,
        )
        assert not resolves
        assert "neither" in detail

    def test_agent_surface_pass_and_fail(self, tmp_path) -> None:
        present = tmp_path / "present.md"
        present.write_text("mentions target.yaml here\n", encoding="utf-8")
        resolves, _detail = _population.resolve_evaluator(
            "target.yaml", EvaluatorSpec(agent_surface="present.md"), root=tmp_path
        )
        assert resolves

        resolves2, detail2 = _population.resolve_evaluator(
            "target.yaml", EvaluatorSpec(agent_surface="missing.md"), root=tmp_path
        )
        assert not resolves2
        assert "does not contain" in detail2


class TestSubjectUniquenessDirect:
    def test_empty_inputs(self) -> None:
        assert _population.check_subject_uniqueness({}, {}) == []

    def test_ritual_ids_seed_namespace_without_self_collision(self) -> None:
        errors = _population.check_subject_uniqueness({}, {"a.yaml": "shared-id", "b.yaml": "shared-id"})
        assert errors == []

    def test_two_class_d_files_sharing_a_subject_fail(self) -> None:
        errors = _population.check_subject_uniqueness({"one.yaml": "shared-subject", "two.yaml": "shared-subject"}, {})
        assert len(errors) == 1
        assert "duplicates" in errors[0]

    def test_subject_colliding_with_ritual_id_fails(self) -> None:
        errors = _population.check_subject_uniqueness({"d.yaml": "ritual-id"}, {"r.yaml": "ritual-id"})
        assert len(errors) == 1
        assert "collides" in errors[0]

    def test_self_collision_never_flagged(self) -> None:
        errors = _population.check_subject_uniqueness({"solo.yaml": "solo-subject"}, {"solo.yaml": "solo-subject"})
        assert errors == []


class TestTopicEqualsSubjectDirect:
    def test_no_matching_route_is_not_a_failure(self) -> None:
        assert _population.check_topic_equals_subject("docs/contracts/x.yaml", "x-subject", []) is None
        routes = [{"topic": "unrelated", "targets": ["docs/contracts/other.yaml"]}]
        assert _population.check_topic_equals_subject("docs/contracts/x.yaml", "x-subject", routes) is None

    def test_malformed_route_rows_are_skipped(self) -> None:
        routes = [{"topic": None, "targets": ["docs/contracts/x.yaml"]}, {"topic": "t", "targets": "not-a-list"}]
        assert _population.check_topic_equals_subject("docs/contracts/x.yaml", "x-subject", routes) is None

    def test_matching_route_with_matching_topic_passes(self) -> None:
        routes = [{"topic": "x-subject", "targets": ["docs/contracts/x.yaml"]}]
        assert _population.check_topic_equals_subject("docs/contracts/x.yaml", "x-subject", routes) is None

    def test_matching_route_with_mismatched_topic_fails(self) -> None:
        routes = [{"topic": "other-topic", "targets": ["docs/contracts/x.yaml"]}]
        err = _population.check_topic_equals_subject("docs/contracts/x.yaml", "x-subject", routes)
        assert err is not None
        assert "differs from" in err


class TestResolveBaseContentDirect:
    def test_path_leg_wins_first(self) -> None:
        result = _population.resolve_base_content(
            "docs/contracts/a.yaml",
            None,
            baseline_paths={"docs/contracts/a.yaml"},
            rename_map={},
            id_index={},
            base_reader=lambda rel: "PATH_TEXT" if rel == "docs/contracts/a.yaml" else None,
        )
        assert result == "PATH_TEXT"

    def test_rename_leg_used_when_path_misses(self) -> None:
        result = _population.resolve_base_content(
            "docs/contracts/new.yaml",
            None,
            baseline_paths=set(),
            rename_map={"docs/contracts/new.yaml": "docs/contracts/old.yaml"},
            id_index={},
            base_reader=lambda rel: "OLD_TEXT" if rel == "docs/contracts/old.yaml" else None,
        )
        assert result == "OLD_TEXT"

    def test_id_leg_used_when_path_and_rename_miss(self) -> None:
        result = _population.resolve_base_content(
            "docs/contracts/new.yaml",
            {"contract": {"id": "shared-id"}},
            baseline_paths=set(),
            rename_map={},
            id_index={"shared-id": "ID_TEXT"},
            base_reader=lambda rel: None,
        )
        assert result == "ID_TEXT"

    def test_all_three_miss_returns_none(self) -> None:
        result = _population.resolve_base_content(
            "docs/contracts/new.yaml",
            {"contract": {"id": "unmatched-id"}},
            baseline_paths=set(),
            rename_map={},
            id_index={},
            base_reader=lambda rel: None,
        )
        assert result is None

    def test_no_head_data_skips_id_leg_safely(self) -> None:
        result = _population.resolve_base_content(
            "docs/contracts/new.yaml",
            None,
            baseline_paths=set(),
            rename_map={},
            id_index={"some-id": "TEXT"},
            base_reader=lambda rel: None,
        )
        assert result is None


class TestAmendmentLogAndKindChangeDirect:
    def test_canonical_dump_equal_passes(self) -> None:
        base = {"contract": {"id": "x"}, "amendment_log": []}
        head = {"contract": {"id": "x"}, "amendment_log": [{"date": "2026-01-01"}]}
        assert _population.check_amendment_log_for_class_d(base, head) is None

    def test_changed_with_new_entry_passes(self) -> None:
        base = {"contract": {"id": "x", "note": "old"}, "amendment_log": []}
        head = {"contract": {"id": "x", "note": "new"}, "amendment_log": [{"date": "2026-01-01"}]}
        assert _population.check_amendment_log_for_class_d(base, head) is None

    def test_changed_with_no_new_entry_fails(self) -> None:
        base = {"contract": {"id": "x", "note": "old"}, "amendment_log": []}
        head = {"contract": {"id": "x", "note": "new"}, "amendment_log": []}
        assert _population.check_amendment_log_for_class_d(base, head) is not None

    def test_conformance_loss_both_directions(self) -> None:
        assert _population.check_conformance_loss("ritual", "unshaped") is not None
        assert _population.check_conformance_loss("class_d", "unshaped") is not None
        assert _population.check_conformance_loss("unshaped", "class_d") is None
        assert _population.check_conformance_loss("class_d", "class_d") is None


class TestCensus:
    """The ratchet spec/pin/direction tests migrated to tests/checks/contracts/test__ratchet.py
    alongside the scripts/checks/contracts/_ratchet.py source move (Decision 128 decompose /
    Decision 131 mirror convention). Census itself stays here -- it did not move."""

    def test_census_render_and_identity(self) -> None:
        census = _population.Census(scanned=3, ritual=1, declared=1, grandfathered=1, skipped=0)
        assert census.bucket_identity_holds()
        assert "scanned=3" in census.render()

        broken = _population.Census(scanned=3, ritual=1, declared=1, grandfathered=0, skipped=0)
        assert not broken.bucket_identity_holds()


@pytest.mark.parametrize("kind", ["check", "agent_surface"])
def test_evaluator_spec_accepts_each_kind_alone(kind: str) -> None:
    EvaluatorSpec.model_validate({kind: "value"})
