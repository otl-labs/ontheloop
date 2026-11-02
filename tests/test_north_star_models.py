"""Tests for scripts/north_star_models.py (Decision 243): the Tier A shape of docs/contracts/north-star.yaml."""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.north_star_models import DEFAULT_PATH, NorthStarDocument, load_north_star, parse_north_star

_REPO = Path(__file__).resolve().parents[1]
_LABELS = {
    "NS.1": ("both", "bound"),
    "NS.2": ("repository", None),
    "NS.3": ("repository", None),
    "NS.4": ("both", "may_override"),
    "NS.5": ("both", "may_override"),
    "NS.6": ("both", "bound"),
    "NS.7": ("repository", None),
    "NS.8": ("both", "set_scale"),
    "NS.9": ("both", "bound"),
}


def _raw() -> dict[str, Any]:
    return yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))


class TestRealContract:
    def test_default_path_is_the_contract(self) -> None:
        assert DEFAULT_PATH == _REPO / "docs" / "contracts" / "north-star.yaml"

    def test_ids_in_order_all_active(self) -> None:
        doc = load_north_star()
        assert [p.id for p in doc.principles] == [f"NS.{n}" for n in range(1, 10)]
        assert doc.active_ids() == doc.ids()

    def test_binds_and_adopters_labels(self) -> None:
        doc = load_north_star()
        assert (doc.mission.binds, doc.mission.adopters) == ("repository", None)
        assert {p.id: (p.binds, p.adopters) for p in doc.principles} == _LABELS

    def test_partial_order_groups(self) -> None:
        assert load_north_star().partial_order.groups == [["NS.6", "NS.9"], ["NS.1", "NS.3"]]


def _set(path: tuple[Any, ...], value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(d: dict[str, Any]) -> None:
        target = d
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _drop(path: tuple[Any, ...]) -> Callable[[dict[str, Any]], None]:
    def mutate(d: dict[str, Any]) -> None:
        target = d
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


def _duplicate_id(d: dict[str, Any]) -> None:
    d["principles"][1]["id"] = d["principles"][0]["id"]


_P0 = ("principles", 0)
_PS = ("principles", 1)  # NS.2, binds repository
_DEFECTS = {
    "adopters missing for both": _drop((*_P0, "adopters")),
    "adopters missing for release": lambda d: (d["principles"][0].update(binds="release"), d["principles"][0].pop("adopters")),
    "adopters present for repository": _set((*_PS, "adopters"), "bound"),
    "id NS.0": _set((*_P0, "id"), "NS.0"),
    "id NS-1": _set((*_P0, "id"), "NS-1"),
    "id NS.01": _set((*_P0, "id"), "NS.01"),
    "duplicate id": _duplicate_id,
    "detail without Why": _set((*_P0, "detail"), "Because."),
    "empty failure_signal": _set((*_P0, "failure_signal"), "  "),
    "unknown binds": _set((*_P0, "binds"), "world"),
    "unknown adopters": _set((*_P0, "adopters"), "free"),
    "extra key on entry": _set((*_P0, "extra"), 1),
    "extra key on mission": _set(("mission", "extra"), 1),
    "extra key on document": _set(("extra",), 1),
    "kind not north_star": _set((*_P0, "kind"), "decision"),
    "mission kind not north_star": _set(("mission", "kind"), "decision"),
    "empty carried_by": _set((*_P0, "carried_by"), []),
    "non-positive carried_by": _set((*_P0, "carried_by"), [0]),
    "empty partial_order rule": _set(("partial_order", "rule"), " "),
}


class TestEntryRules:
    @pytest.mark.parametrize("name", list(_DEFECTS))
    def test_defect_rejected(self, name: str) -> None:
        raw = copy.deepcopy(_raw())
        _DEFECTS[name](raw)
        with pytest.raises(ValueError):
            parse_north_star(yaml.safe_dump(raw))

    def test_retired_entry_is_known_but_inactive(self) -> None:
        raw = _raw()
        raw["principles"][3]["status"] = "retired"
        doc = parse_north_star(yaml.safe_dump(raw))
        assert "NS.4" in doc.ids()
        assert "NS.4" not in doc.active_ids()

    def test_missing_file_is_value_error_naming_path(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="absent.yaml"):
            load_north_star(tmp_path / "absent.yaml")

    def test_invalid_yaml_is_value_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("a: [unclosed\n", encoding="utf-8")
        with pytest.raises(ValueError, match="bad.yaml"):
            load_north_star(bad)

    def test_document_type(self) -> None:
        assert isinstance(load_north_star(), NorthStarDocument)


class TestNoFirstPartyImports:
    def test_no_first_party_import(self) -> None:
        tree = ast.parse((_REPO / "scripts" / "north_star_models.py").read_text(encoding="utf-8"))
        roots = {
            (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
            for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [None])
        }
        assert not roots & {"scripts", "src", "tests"}
