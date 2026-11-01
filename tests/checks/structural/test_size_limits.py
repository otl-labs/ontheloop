"""Tests for validate_structural_size_limits() -- the structural-size limit gate
(SGE-01/SGE-02/SGE-04, Decision 166) -- and for escape_violations(), the STATIC half of
the escape-accountability legs (SGE-05) that the same check consumes.

Every test runs against an INJECTED root (tmp_path) except the declared regression guard
and the TestLiveTreeFreezeState class, both of which read the live tree and say so.
"""

from __future__ import annotations

import contextlib
import copy
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.checks import registry
from scripts.checks.structural import _classify
from scripts.checks.structural import size_limits as sl
from scripts.checks.structural.size_limits import escape_violations, validate_structural_size_limits

_REGISTRY_TEMPLATE = """\
schema_version: 1
exclusions:
  suffixes: [".py", ".md"]
  dirs: [pip, lambda-packages, docker, .venv, node_modules, .git, personal_scripts]
generated_pinned: []
classes:
  - slug: generated
    include: []
    governed: false
    unit: n/a
    limit: n/a
    max_line_chars: n/a
  - slug: config
    include: ["config/*.yaml", "config/*.yml"]
    governed: true
    unit: effective-lines
    limit: 500
    max_line_chars: 2000
  - slug: workflow_outputs
    include: ["docs/plans/*.yaml"]
    governed: false
    unit: n/a
    limit: n/a
    max_line_chars: n/a
  - slug: residual
    include: []
    governed: true
    unit: effective-lines
    limit: 500
    max_line_chars: 2000
budgets:
{budgets}
long_line_budgets:
{long_line_budgets}
"""


def _write_registry(tmp_path: Path, budgets: str = "", long_line_budgets: str = "") -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    body = _REGISTRY_TEMPLATE.format(budgets=budgets or "  {}", long_line_budgets=long_line_budgets or "  {}")
    (config_dir / "structural_size_budgets.yaml").write_text(body, encoding="utf-8")


def _clear_cache() -> None:
    _classify._registry_cache.clear()


def _mock_ls_files(tracked_paths: list[str]):
    def _run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "".join(f"{p}\n" for p in tracked_paths)
        return result

    return _run


class TestValidateStructuralSizeLimits:
    def test_over_limit_governed_file_no_roster_entry_fails(self, tmp_path: Path) -> None:
        _write_registry(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "big.yaml").write_text("key: value\n" * 501, encoding="utf-8")

        _clear_cache()
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run", _mock_ls_files(["config/big.yaml"])),
        ):
            failed: list[str] = []
            validate_structural_size_limits(failed)

        assert len(failed) == 1

    def test_roster_entry_at_501_passes_at_502_fails(self, tmp_path: Path) -> None:
        """A registered budget of 501 is a ratchet ceiling, not a permanent free pass: a
        501-line file with a 501 roster entry passes; the SAME roster entry (still 501)
        fails again once the file grows to 502 (ratchet direction)."""
        for file_lines, expect_fail in ((501, False), (502, True)):
            _write_registry(tmp_path, budgets="  config/big.yaml: 501")
            config_dir = tmp_path / "config"
            config_dir.mkdir(exist_ok=True)
            (config_dir / "big.yaml").write_text("key: value\n" * file_lines, encoding="utf-8")

            _clear_cache()
            with (
                patch("scripts.checks._common.ROOT", tmp_path),
                patch("scripts.checks._common.run", _mock_ls_files(["config/big.yaml"])),
            ):
                failed: list[str] = []
                validate_structural_size_limits(failed)

            if expect_fail:
                assert len(failed) == 1, f"file_lines={file_lines} expected FAIL"
            else:
                assert failed == [], f"file_lines={file_lines} expected PASS"

    def test_long_line_fails_without_entry_passes_with_entry(self, tmp_path: Path) -> None:
        long_line = "x" * 2001
        for long_line_budgets, expect_fail in ("", True), ("  config/longline.yaml: 2001", False):
            _write_registry(tmp_path, long_line_budgets=long_line_budgets)
            config_dir = tmp_path / "config"
            config_dir.mkdir(exist_ok=True)
            (config_dir / "longline.yaml").write_text(f"key: value\n{long_line}\n", encoding="utf-8")

            _clear_cache()
            with (
                patch("scripts.checks._common.ROOT", tmp_path),
                patch("scripts.checks._common.run", _mock_ls_files(["config/longline.yaml"])),
            ):
                failed: list[str] = []
                validate_structural_size_limits(failed)

            if expect_fail:
                assert len(failed) == 1
            else:
                assert failed == []

    def test_exempt_class_passes_at_5000_lines(self, tmp_path: Path) -> None:
        _write_registry(tmp_path)
        plans_dir = tmp_path / "docs" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "PLAN-huge.yaml").write_text("key: value\n" * 5000, encoding="utf-8")

        _clear_cache()
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run", _mock_ls_files(["docs/plans/PLAN-huge.yaml"])),
        ):
            failed: list[str] = []
            validate_structural_size_limits(failed)

        assert failed == []

    def test_unmatched_extension_governed_by_residual_default(self, tmp_path: Path) -> None:
        _write_registry(tmp_path)
        (tmp_path / "surface.hcl2").write_text("x = 1\n" * 501, encoding="utf-8")

        _clear_cache()
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run", _mock_ls_files(["surface.hcl2"])),
        ):
            failed: list[str] = []
            validate_structural_size_limits(failed)

        assert len(failed) == 1

    def test_declares_examined_on_measured_files(self, tmp_path: Path) -> None:
        """Decision 170 accounting (check-accounting declaration ratchet, touch-it-fix-it):
        this check must declare examined() over its measured-file population instead of
        returning undeclared."""
        _write_registry(tmp_path)
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "ok.yaml").write_text("key: value\n", encoding="utf-8")

        _clear_cache()
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run", _mock_ls_files(["config/ok.yaml"])),
            registry.outcome_scope("validate_structural_size_limits"),
        ):
            failed: list[str] = []
            validate_structural_size_limits(failed)
            declaration = registry.pop_declaration()

        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.unit == "measured_files"
        assert declaration.count == 1

    def test_failure_message_names_class_and_relief_valves(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        registry_text = (
            "schema_version: 1\n"
            'exclusions:\n  suffixes: [".py", ".md"]\n  dirs: []\n'
            "generated_pinned: []\n"
            "classes:\n"
            "  - slug: config\n"
            '    include: ["config/*.yaml"]\n'
            "    governed: true\n"
            "    unit: effective-lines\n"
            "    limit: 500\n"
            "    max_line_chars: 2000\n"
            '    relief_valve: "compact resolved rows"\n'
            "  - slug: residual\n"
            "    include: []\n"
            "    governed: true\n"
            "    unit: effective-lines\n"
            "    limit: 500\n"
            "    max_line_chars: 2000\n"
            "budgets: {}\n"
            "long_line_budgets: {}\n"
        )
        (config_dir / "structural_size_budgets.yaml").write_text(registry_text, encoding="utf-8")
        (config_dir / "big.yaml").write_text("key: value\n" * 501, encoding="utf-8")

        _clear_cache()
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run", _mock_ls_files(["config/big.yaml"])),
        ):
            failed: list[str] = []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                validate_structural_size_limits(failed)

        output = buf.getvalue()
        assert "config/big.yaml" in output
        assert "class: config" in output
        assert "compact resolved rows" in output


_SYNTHETIC_REGISTRY: dict = {
    "classes": [
        {
            "slug": "roadmaps",
            "include": ["docs/ROADMAP-*.yaml"],
            "governed": True,
            "limit": 500,
            "max_line_chars": 2000,
            "defer_to_incumbent": {
                "docs/ROADMAP-PLATFORM.yaml": {"incumbent": "validate_platform_roadmap", "authorized_by": "dec-166"}
            },
        },
        {"slug": "exempt", "include": [], "governed": False, "limit": "n/a", "max_line_chars": "n/a"},
        {"slug": "residual", "include": [], "governed": True, "limit": 500, "max_line_chars": 2000},
    ],
    "budgets": {"docs/ROADMAP-SEMANTO.yaml": 4707},
    "budget_notes": {
        "docs/ROADMAP-SEMANTO.yaml": {"frozen": True, "rationale": "operator freeze", "retires_when": "the migration"}
    },
}

_SEMANTO = "docs/ROADMAP-SEMANTO.yaml"
_PLATFORM = "docs/ROADMAP-PLATFORM.yaml"


def _rows(reg: dict) -> dict:
    return {row["slug"]: row for row in reg["classes"]}


def _deferrals(reg: dict) -> dict:
    return _rows(reg)["roadmaps"]["defer_to_incumbent"]


def _legs(violations: list[str]) -> set[str]:
    return {v.split(":")[0] for v in violations}


def _probe(tmp_path: Path, mutate=None, *, leg: str = "", authorizes: str = _PLATFORM) -> list[str]:
    """Seed an injected root carrying the paths the E3/E4 existence legs stat plus a
    DECISIONS.md whose dec-166 body authorizes `authorizes`, apply `mutate` to a fresh copy
    of the synthetic registry, and return escape_violations() -- optionally filtered to one
    leg label, which is what keeps a leg-specific assertion from being satisfied by a
    different leg."""
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    for name in ("ROADMAP-PLATFORM.yaml", "ROADMAP-SEMANTO.yaml"):
        (docs / name).write_text("key: value\n", encoding="utf-8")
    (docs / "DECISIONS.md").write_text(
        f"## Decision 166: Structural size (Decided)\n\n**Decision:** Authorizes {authorizes}.\n", encoding="utf-8"
    )
    reg = copy.deepcopy(_SYNTHETIC_REGISTRY)
    if mutate is not None:
        mutate(reg)
    with patch("scripts.checks._common.ROOT", tmp_path):
        out = escape_violations(reg)
    return [v for v in out if v.startswith(leg)] if leg else out


class TestEscapeLegE1ResidualFloor:
    def test_clean_registry_reports_nothing(self, tmp_path: Path) -> None:
        assert _probe(tmp_path) == []

    def test_missing_residual_row_reports_and_never_raises(self, tmp_path: Path) -> None:
        """rec-3020's shape: a removed residual row must produce a LABELLED violation, not a
        silent drop out of the measured set and not an exception inside a presubmit check."""
        v = _probe(tmp_path, lambda r: r.__setitem__("classes", [x for x in r["classes"] if x["slug"] != "residual"]))
        assert "E1" in _legs(v)

    def test_raised_residual_floor_reports_e1(self, tmp_path: Path) -> None:
        assert "E1" in _legs(_probe(tmp_path, lambda r: r["classes"][-1].__setitem__("limit", 2000)))

    def test_raised_residual_max_line_chars_reports_e1(self, tmp_path: Path) -> None:
        assert "E1" in _legs(_probe(tmp_path, lambda r: r["classes"][-1].__setitem__("max_line_chars", 9000)))

    def test_residual_not_last_reports_e1(self, tmp_path: Path) -> None:
        trailing = {"slug": "trailing", "include": [], "governed": True, "limit": 500, "max_line_chars": 2000}
        v = _probe(tmp_path, lambda r: r["classes"].append(trailing), leg="E1")
        assert any("LAST" in x for x in v)

    def test_non_integer_residual_limit_reports_e1_not_typeerror(self, tmp_path: Path) -> None:
        assert "E1" in _legs(_probe(tmp_path, lambda r: r["classes"][-1].__setitem__("limit", "n/a")))

    def test_empty_class_table_reports_e1(self, tmp_path: Path) -> None:
        assert "E1" in _legs(_probe(tmp_path, lambda r: r.__setitem__("classes", [])))

    def test_duplicate_residual_rows_report_e1(self, tmp_path: Path) -> None:
        """Two terminal defaults means the first-match-wins table has two answers for the
        same unmatched path, and only one of them is the one the engine will use."""
        v = _probe(tmp_path, lambda r: r["classes"].append(dict(r["classes"][-1])), leg="E1")
        assert any("exactly one is permitted" in x for x in v)


class TestEscapeLegE2ClassCeilings:
    def test_raised_class_limit_reports_e2(self, tmp_path: Path) -> None:
        assert "E2" in _legs(_probe(tmp_path, lambda r: _rows(r)["roadmaps"].__setitem__("limit", 6000)))

    def test_raised_class_max_line_chars_reports_e2(self, tmp_path: Path) -> None:
        assert "E2" in _legs(_probe(tmp_path, lambda r: _rows(r)["roadmaps"].__setitem__("max_line_chars", 8000)))

    def test_governed_row_with_na_limit_reports_e2_never_typeerror(self, tmp_path: Path) -> None:
        """FAIL LOUDLY, not by exception: the exempt rows carry the string "n/a", so flipping
        one to governed must produce a labelled violation, never a TypeError from comparing a
        string to an integer inside a presubmit run."""
        v = _probe(tmp_path, lambda r: _rows(r)["exempt"].__setitem__("governed", True), leg="E2")
        assert any("non-integer" in x for x in v)

    def test_permitted_maxima_ships_empty(self) -> None:
        """Granting a class a higher ceiling is a reviewed CODE edit that must update this
        pin -- the point of the per-slug mapping is that the grant is visible in review."""
        assert sl._PERMITTED_MAXIMA == {}

    def test_granted_slug_permits_its_ceiling_and_one_over_still_reds(self, tmp_path: Path) -> None:
        with patch.dict(sl._PERMITTED_MAXIMA, {"roadmaps": (6000, 2000)}):
            assert _probe(tmp_path, lambda r: _rows(r)["roadmaps"].__setitem__("limit", 6000)) == []
            assert "E2" in _legs(_probe(tmp_path, lambda r: _rows(r)["roadmaps"].__setitem__("limit", 6001)))

    def test_grant_is_per_slug_not_global(self, tmp_path: Path) -> None:
        """Decision 162 R1 pins a per-MEMBER set: granting roadmaps 6000 must not raise the
        permitted maximum for any other governed row."""
        with patch.dict(sl._PERMITTED_MAXIMA, {"roadmaps": (6000, 2000)}):
            assert "E2" in _legs(_probe(tmp_path, lambda r: _rows(r)["residual"].__setitem__("limit", 6000)))


class TestEscapeLegE3DeferralAccountability:
    def test_glob_key_fails_on_the_glob_rule_by_name(self, tmp_path: Path) -> None:
        """A glob must be rejected ON THE GLOB RULE, not incidentally on non-existence -- a
        glob that happens to match a real file would otherwise slip through."""
        v = _probe(
            tmp_path, lambda r: _deferrals(r).__setitem__("docs/ROADMAP-*.yaml", dict(_deferrals(r)[_PLATFORM])), leg="E3"
        )
        assert any("glob" in x for x in v)

    def test_nonexistent_path_reports_e3(self, tmp_path: Path) -> None:
        entry = {"incumbent": "validate_platform_roadmap", "authorized_by": "dec-166"}
        v = _probe(tmp_path, lambda r: _deferrals(r).__setitem__("docs/ROADMAP-GONE.yaml", entry), leg="E3")
        assert any("does not exist" in x for x in v)

    def test_unsequenced_incumbent_reports_e3(self, tmp_path: Path) -> None:
        v = _probe(tmp_path, lambda r: _deferrals(r)[_PLATFORM].__setitem__("incumbent", "validate_no_such_gate"), leg="E3")
        assert any("not in the validate check sequence" in x for x in v)

    def test_missing_incumbent_and_missing_authorized_by_each_report_e3(self, tmp_path: Path) -> None:
        for field in ("incumbent", "authorized_by"):
            v = _probe(tmp_path, lambda r, f=field: _deferrals(r)[_PLATFORM].pop(f), leg="E3")
            assert any(f"declares no `{field}`" in x for x in v), field

    def test_non_authorizing_decision_reports_e3(self, tmp_path: Path) -> None:
        """Authorization, not existence: a cited Decision whose body never names the exact
        deferred path does not authorize un-governing it."""
        v = _probe(tmp_path, leg="E3", authorizes="some unrelated surface")
        assert any("does not name" in x for x in v)

    def test_list_shaped_defer_to_incumbent_reports_e3(self, tmp_path: Path) -> None:
        """The pre-SGE-05 bare-list shape carries no accountability fields at all and must be
        rejected loudly rather than silently skipped."""
        v = _probe(tmp_path, lambda r: _rows(r)["roadmaps"].__setitem__("defer_to_incumbent", [_PLATFORM]), leg="E3")
        assert any("must be a mapping" in x for x in v)

    def test_non_mapping_deferral_entry_reports_e3(self, tmp_path: Path) -> None:
        v = _probe(tmp_path, lambda r: _deferrals(r).__setitem__(_PLATFORM, "validate_platform_roadmap"), leg="E3")
        assert any("not the {incumbent, authorized_by} mapping" in x for x in v)


class TestEscapeLegE4FrozenNoteIntegrity:
    def test_orphan_note_with_no_budgets_entry_reports_e4(self, tmp_path: Path) -> None:
        v = _probe(tmp_path, lambda r: r["budget_notes"].__setitem__("docs/NOPE.yaml", {"frozen": True}), leg="E4")
        assert any("annotates no" in x for x in v)

    def test_frozen_note_whose_budgets_entry_was_removed_reports_e4(self, tmp_path: Path) -> None:
        assert _probe(tmp_path, lambda r: r["budgets"].pop(_SEMANTO), leg="E4")

    def test_missing_frozen_path_reports_e4_never_filenotfounderror(self, tmp_path: Path) -> None:
        """The planned END STATE, not a hypothetical: retires_when names the migration that
        deletes the guarded file, so E4 must report rather than crash --pre at that moment."""

        def _add_gone(reg: dict) -> None:
            reg["budgets"]["docs/GONE-AFTER-MIGRATION.yaml"] = 100
            reg["budget_notes"]["docs/GONE-AFTER-MIGRATION.yaml"] = dict(reg["budget_notes"][_SEMANTO])

        assert any("no longer exists on disk" in x for x in _probe(tmp_path, _add_gone, leg="E4"))

    def test_frozen_and_deferred_is_mutually_exclusive(self, tmp_path: Path) -> None:
        """Measured bypass: deferring a frozen path to a size-less incumbent drops it from
        the measured set entirely and the engine then reports green."""
        entry = {"incumbent": "validate_platform_roadmap", "authorized_by": "dec-166"}
        v = _probe(tmp_path, lambda r: _deferrals(r).__setitem__(_SEMANTO, entry), leg="E4")
        assert any("mutually" in x for x in v)

    def test_non_mapping_note_and_non_mapping_budget_notes_each_report_e4(self, tmp_path: Path) -> None:
        assert _probe(tmp_path, lambda r: r["budget_notes"].__setitem__(_SEMANTO, "frozen"), leg="E4")
        assert _probe(tmp_path, lambda r: r.__setitem__("budget_notes", "frozen"), leg="E4")

    def test_unfrozen_note_skips_the_existence_and_deferral_legs(self, tmp_path: Path) -> None:
        """Only a FROZEN note carries the existence and mutual-exclusion legs; an ordinary
        annotation on a live entry stays clean."""
        assert _probe(tmp_path, lambda r: r["budget_notes"][_SEMANTO].__setitem__("frozen", False)) == []

    def test_every_message_carries_a_leg_label(self, tmp_path: Path) -> None:
        """Leg labels are a correctness requirement: a verification step must never be
        satisfiable by a different leg than the one its check_id claims to pin."""

        def _break_everything(reg: dict) -> None:
            reg["classes"] = [r for r in reg["classes"] if r["slug"] != "residual"]
            _rows(reg)["roadmaps"]["limit"] = 9000
            _deferrals(reg)["docs/ROADMAP-GONE.yaml"] = {}
            reg["budget_notes"]["docs/NOPE.yaml"] = {"frozen": True}

        violations = _probe(tmp_path, _break_everything)
        assert violations
        assert all(v.split(":")[0] in {"E1", "E2", "E3", "E4"} for v in violations), violations


class TestLiveTreeFreezeState:
    """Asserts the SHIPPED config's own state -- deliberately not injected-root."""

    def test_frozen_entries_are_recorded_at_their_measured_pin(self) -> None:
        reg = _classify.load_registry()
        frozen = {k: v for k, v in (reg.get("budget_notes") or {}).items() if isinstance(v, dict) and v.get("frozen")}
        for path, note in frozen.items():
            assert note["rationale"] and note["retires_when"]
            assert isinstance(reg["budgets"][path], int)

    def test_decision_entry_freeze_is_retired_to_its_class_default(self) -> None:
        reg = _classify.load_registry()
        path = "docs/contracts/decision-entry.yaml"
        assert path not in (reg.get("budgets") or {}) and path not in (reg.get("budget_notes") or {})
        limit = next(row["limit"] for row in reg["classes"] if row.get("slug") == "contracts")
        text = (Path(__file__).resolve().parents[3] / path).read_text(encoding="utf-8")
        assert _classify.effective_lines(text) <= limit

    def test_live_registry_is_escape_clean(self) -> None:
        assert escape_violations(_classify.load_registry()) == []

    def test_secrets_baseline_is_classified_generated(self) -> None:
        """rec-4381: .secrets.baseline (detect-secrets' own output, no do-not-edit banner) was
        added to generated_pinned when its first size growth crossed the residual class's
        500-effective-line default -- pin the membership so a later revert isn't caught only by
        the full-tier gate re-growing past that line."""
        reg = _classify.load_registry()
        assert ".secrets.baseline" in (reg.get("generated_pinned") or [])
        assert _classify.classify_path(".secrets.baseline") == "generated"

    def test_dec114_fails_and_dec147_passes_for_the_platform_deferral(self) -> None:
        """authorized_by means "which Decision authorizes deferring THIS EXACT path"; dec-114
        writes the bare filename and _expand_ancestor_candidates generates no ancestor for a
        2-segment key, so the literal path is the only candidate."""
        reg = copy.deepcopy(_classify.load_registry())
        _deferrals(reg)[_PLATFORM]["authorized_by"] = "dec-114"
        assert [v for v in escape_violations(reg) if v.startswith("E3:")]
        _deferrals(reg)[_PLATFORM]["authorized_by"] = "dec-147"
        assert [v for v in escape_violations(reg) if v.startswith("E3:")] == []


class TestBudgetsEntryCapsOverClassCeiling:
    def test_budgets_entry_caps_its_path_even_under_a_higher_class_ceiling(self, tmp_path: Path) -> None:
        """This -- not E2-strict -- is the mechanism a frozen budgets entry actually depends on:
        a budgets entry is the effective cap regardless of how high its class row goes."""
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        registry_text = (
            "schema_version: 1\n"
            'exclusions:\n  suffixes: [".py", ".md"]\n  dirs: []\n'
            "generated_pinned: []\n"
            "classes:\n"
            "  - slug: roadmaps\n"
            '    include: ["docs/ROADMAP-*.yaml"]\n'
            "    governed: true\n"
            "    unit: effective-lines\n"
            "    limit: 6000\n"
            "    max_line_chars: 2000\n"
            "  - slug: residual\n"
            "    include: []\n"
            "    governed: true\n"
            "    unit: effective-lines\n"
            "    limit: 500\n"
            "    max_line_chars: 2000\n"
            "budgets:\n  docs/ROADMAP-FROZEN.yaml: 400\n"
            "long_line_budgets: {}\n"
        )
        (config_dir / "structural_size_budgets.yaml").write_text(registry_text, encoding="utf-8")
        docs = tmp_path / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "ROADMAP-FROZEN.yaml").write_text("key: value\n" * 401, encoding="utf-8")

        _clear_cache()
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run", _mock_ls_files(["docs/ROADMAP-FROZEN.yaml"])),
        ):
            failed: list[str] = []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                validate_structural_size_limits(failed)

        assert len(failed) == 1
        assert "exceeds limit 400" in buf.getvalue()


class TestRegressionGuardsThatPassOnTheUnmodifiedTree:
    """Declared regression guards: both assertions below already hold on origin/main. They
    are unit-suite guards against this plan's own reshape, not proof of new behaviour."""

    def test_platform_stays_unmeasured_other_roadmap_stays_measured(self) -> None:
        measured = {rel for rel, _slug in _classify.iter_measured_files()}
        assert "docs/ROADMAP-PLATFORM.yaml" not in measured
        assert "docs/ROADMAP-SEMANTO.yaml" in measured

    def test_long_line_companion_fires_on_a_roadmaps_class_file(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        registry_text = (
            "schema_version: 1\n"
            'exclusions:\n  suffixes: [".py", ".md"]\n  dirs: []\n'
            "generated_pinned: []\n"
            "classes:\n"
            "  - slug: roadmaps\n"
            '    include: ["docs/ROADMAP-*.yaml", "docs/ROADMAP-*.yml"]\n'
            "    governed: true\n"
            "    unit: effective-lines\n"
            "    limit: 500\n"
            "    max_line_chars: 2000\n"
            "  - slug: residual\n"
            "    include: []\n"
            "    governed: true\n"
            "    unit: effective-lines\n"
            "    limit: 500\n"
            "    max_line_chars: 2000\n"
            "budgets: {}\nlong_line_budgets: {}\n"
        )
        (config_dir / "structural_size_budgets.yaml").write_text(registry_text, encoding="utf-8")
        docs = tmp_path / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "ROADMAP-JOINED.yaml").write_text("key: value\n" + ("x" * 2001) + "\n", encoding="utf-8")

        _clear_cache()
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.run", _mock_ls_files(["docs/ROADMAP-JOINED.yaml"])),
        ):
            failed: list[str] = []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                validate_structural_size_limits(failed)

        assert len(failed) == 1
        assert "longest line 2001 chars" in buf.getvalue()
