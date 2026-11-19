"""Mirror test for validate_delegated_key_changes (Decision 276 clauses 1 and 3), on tmp roots with a fake baseline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.decisions.validate_delegated_key_changes import validate_delegated_key_changes

KEY = "config/knob.yaml#sec.cap"
DECISIONS = "docs/DECISIONS.md"
ARCHIVE = "docs/DECISIONS_ARCHIVE.md"


def _entry(number: int, kind: str = "direction", extra: str = "", body: str = "") -> str:
    return (
        f"## Decision {number}: Entry {number} (Decided)\n\n```yaml\nnumber: {number}\nkind:\n  value: {kind}\n"
        f"  justification: j\n{extra}```\n\n**Status:** Decided\n\n{body}---\n\n"
    )


def _delegator(bounds: str = "{min: 1, max: 9}") -> str:
    return _entry(5, extra=f"delegates:\n  - key: {KEY}\n    change_rule: contract_amendment\n    bounds: {bounds}\n")


def _knob(value: object) -> str:
    return yaml.safe_dump({"sec": {"cap": value}})


class _Run:
    def __init__(self, tmp_path: Path, base: dict[str, str | None], head: dict[str, str]) -> None:
        self.root = tmp_path
        self.base = base
        for rel, text in head.items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(text, encoding="utf-8")

    def go(self, baseline: set[int] | None = None, **kwargs: object) -> tuple[list[str], str, int | None]:
        failed: list[str] = []
        registry.pop_declaration()
        validate_delegated_key_changes(
            failed,
            root=self.root,
            baseline_reader=lambda r: baseline,
            base_text_reader=lambda r, rel: self.base.get(rel),
            **kwargs,  # type: ignore[arg-type]
        )
        declaration = registry.pop_declaration()
        return failed, getattr(declaration, "kind", ""), getattr(declaration, "count", None)


def _corpus(*blocks: str) -> str:
    return "".join(blocks)


def _setup(
    tmp_path: Path,
    base_value: object,
    head_value: object,
    new: str = "",
    head_delegator: str | None = None,
    bounds: str = "{min: 1, max: 9}",
) -> _Run:
    base_decisions = _corpus(_delegator(bounds))
    return _Run(
        tmp_path,
        {DECISIONS: base_decisions, ARCHIVE: "", "config/knob.yaml": _knob(base_value)},
        {
            DECISIONS: _corpus(head_delegator if head_delegator is not None else _delegator(), new),
            ARCHIVE: "",
            "config/knob.yaml": _knob(head_value),
        },
    )


class TestDelegatedKeyOnlyEntryFails:
    """rec-3023's acceptance: a baseline entry delegates a key, the diff changes it inside bounds and adds a mechanism
    entry citing it -> FAIL."""

    MECHANISM = _entry(9, kind="mechanism", body="Raise sec.cap in config/knob.yaml to 4.\n\n")

    def test_the_mechanism_entry_citing_the_changed_key_fails(self, tmp_path: Path, capsys) -> None:
        failed, kind, _ = _setup(tmp_path, 2, 4, self.MECHANISM).go({5})
        assert failed == ["Delegated-key changes"] and kind == "examined"
        assert "Decision 9 re-sets" in capsys.readouterr().out

    def test_the_same_diff_without_the_entry_passes(self, tmp_path: Path) -> None:
        assert _setup(tmp_path, 2, 4).go({5})[0] == []

    def test_an_unchanged_key_passes_even_with_the_entry(self, tmp_path: Path) -> None:
        assert _setup(tmp_path, 2, 2, self.MECHANISM).go({5})[0] == []

    def test_a_mechanism_entry_with_its_own_delegates_is_exempt(self, tmp_path: Path) -> None:
        own = _entry(
            9,
            kind="mechanism",
            extra="delegates:\n  - key: other.yaml#x\n    change_rule: contract_amendment\n    bounds: {text: t}\n",
            body="Sets sec.cap in config/knob.yaml.\n\n",
        )
        assert _setup(tmp_path, 2, 4, own).go({5})[0] == []

    def test_a_mechanism_entry_not_naming_the_file_and_key_is_ignored(self, tmp_path: Path) -> None:
        other = _entry(9, kind="mechanism", body="Unrelated text about caps.\n\n")
        assert _setup(tmp_path, 2, 4, other).go({5})[0] == []

    def test_a_direction_entry_amending_the_delegator_waives_it(self, tmp_path: Path) -> None:
        amender = _entry(8, extra="amends: [5]\n")
        assert _setup(tmp_path, 2, 4, self.MECHANISM + amender).go({5})[0] == []


class TestBounds:
    def test_a_value_outside_min_max_fails_unless_a_direction_entry_amends(self, tmp_path: Path) -> None:
        assert _setup(tmp_path, 2, 12).go({5})[0] == ["Delegated-key changes"]
        assert _setup(tmp_path / "w", 2, 12, _entry(8, extra="amends: [5]\n")).go({5})[0] == []

    def test_one_of_bounds_and_a_non_numeric_value_fail_outside_them(self, tmp_path: Path) -> None:
        one_of = _delegator("{one_of: [a, b]}")
        assert _setup(tmp_path / "a", "a", "c", head_delegator=one_of, bounds="{one_of: [a, b]}").go({5})[0]
        assert _setup(tmp_path / "b", "a", "b", head_delegator=one_of, bounds="{one_of: [a, b]}").go({5})[0] == []
        assert _setup(tmp_path / "c", 2, "four").go({5})[0]

    def test_text_bounds_are_never_evaluated(self, tmp_path: Path) -> None:
        text = _delegator("{text: any}")
        assert _setup(tmp_path, 2, 99, head_delegator=text, bounds="{text: any}").go({5})[0] == []

    def test_an_unresolvable_key_warns(self, tmp_path: Path, capsys) -> None:
        run = _setup(tmp_path, 2, 4)
        (tmp_path / "config/knob.yaml").write_text("sec: {}\n", encoding="utf-8")
        assert run.go({5})[0] == []
        assert "no longer resolves" in capsys.readouterr().out


class TestBaseBoundsGovern:
    """Red before: bounds come from origin/main, so an in-place edit of the delegating entry cannot move them."""

    def test_widening_the_bounds_in_place_plus_an_out_of_bounds_value_fails(self, tmp_path: Path) -> None:
        wide = _delegator("{min: 1, max: 99}")
        failed = _setup(tmp_path, 2, 50, head_delegator=wide).go({5})[0]
        assert failed == ["Delegated-key changes"]

    def test_removing_the_delegates_block_plus_an_out_of_bounds_value_fails(self, tmp_path: Path) -> None:
        assert _setup(tmp_path, 2, 50, head_delegator=_entry(5)).go({5})[0]

    def test_removing_the_entry_from_both_files_fails(self, tmp_path: Path) -> None:
        assert _setup(tmp_path, 2, 2, head_delegator="").go({5})[0]

    def test_either_change_with_a_direction_amender_passes(self, tmp_path: Path) -> None:
        amender = _entry(8, extra="amends: [5]\n")
        assert _setup(tmp_path, 2, 50, amender, head_delegator=_entry(5)).go({5})[0] == []

    def test_an_archive_move_with_delegates_unchanged_passes(self, tmp_path: Path) -> None:
        run = _setup(tmp_path, 2, 2, head_delegator="")
        (tmp_path / ARCHIVE).write_text(_delegator(), encoding="utf-8")
        assert run.go({5})[0] == []

    def test_a_compacted_stub_whose_superseder_lists_it_at_base_is_exempt(self, tmp_path: Path) -> None:
        superseder = _entry(6, extra="supersedes: [5]\n")
        stub = _entry(5, body="**Superseded by: Decision 6**\n\n").replace("**Status:** Decided", "**Status:** Superseded")
        run = _setup(tmp_path, 2, 2, superseder, head_delegator=stub)
        run.base[DECISIONS] = _delegator() + superseder
        assert run.go({5, 6})[0] == []

    @pytest.mark.parametrize("target", ["Decision 99", "Decision 7"])
    def test_a_stub_whose_superseder_does_not_list_it_fails(self, tmp_path: Path, target: str) -> None:
        stub = _entry(5, body=f"**Superseded by: {target}**\n\n")
        run = _setup(tmp_path, 2, 2, head_delegator=stub)
        run.base[DECISIONS] = _delegator() + _entry(7)
        assert run.go({5, 7})[0]


class TestCostGatesAndSentinels:
    def test_no_delegates_substring_in_the_base_files_examines_zero_before_any_parse(self, tmp_path: Path) -> None:
        run = _Run(tmp_path, {DECISIONS: _entry(1), ARCHIVE: ""}, {DECISIONS: _entry(1), ARCHIVE: ""})
        assert run.go({1}) == ([], "examined", 0)

    def test_the_production_root_with_no_decisions_or_yaml_change_examines_zero(self, tmp_path: Path) -> None:
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.get_changed_files", return_value=["a.py"]),
        ):
            failed: list[str] = []
            registry.pop_declaration()
            validate_delegated_key_changes(
                failed, baseline_reader=lambda r: set(), base_text_reader=lambda r, rel: "delegates:"
            )
            declaration = registry.pop_declaration()
        assert failed == [] and declaration is not None and declaration.kind == "examined"

    def test_the_production_root_with_a_yaml_change_runs_the_check(self, tmp_path: Path) -> None:
        run = _setup(tmp_path, 2, 12)
        with (
            patch("scripts.checks._common.ROOT", tmp_path),
            patch("scripts.checks._common.get_changed_files", return_value=["x.yaml"]),
        ):
            failed: list[str] = []
            validate_delegated_key_changes(
                failed, baseline_reader=lambda r: {5}, base_text_reader=lambda r, rel: run.base.get(rel)
            )
        assert failed

    def test_an_unreachable_baseline_skips(self, tmp_path: Path) -> None:
        assert _setup(tmp_path, 2, 4).go(None)[1] == "skipped"

    def test_a_compared_run_declares_the_number_compared(self, tmp_path: Path) -> None:
        assert _setup(tmp_path, 2, 2).go({5}) == ([], "examined", 1)


class TestMalformedBaseInputsNeverRaise:
    def test_non_numeric_base_bounds_fail_instead_of_raising(self, tmp_path: Path) -> None:
        assert _setup(tmp_path, 2, 4, bounds="{min: lo, max: hi}").go({5})[0] == ["Delegated-key changes"]

    def test_a_delegation_key_with_no_dotted_part_is_skipped(self, tmp_path: Path) -> None:
        bad = _delegator().replace(KEY, "config/knob.yaml")
        run = _Run(tmp_path, {DECISIONS: bad, ARCHIVE: ""}, {DECISIONS: bad, ARCHIVE: ""})
        assert run.go({5}) == ([], "examined", 1)

    def test_an_unparseable_base_value_file_reads_as_absent(self, tmp_path: Path) -> None:
        run = _setup(tmp_path, 2, 4)
        run.base["config/knob.yaml"] = "sec: ["
        assert run.go({5})[0] == []
