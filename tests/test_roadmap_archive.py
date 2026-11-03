"""Mirror test for scripts/roadmap/roadmap_archive.py (Decision 227, PLAN-roadmap-lifecycle-archival).

Covers the stub, integrity, hydration and check-support helpers, the load() hook in
scripts/platform_roadmap_state.py, the validate_platform_roadmap wiring of the archive legs, the live
archive, and the tier-item-lifecycle contract's lifecycle_archival / archival_exceptions keys. Every
synthetic case writes tmp_path fixtures; nothing here writes the live roadmap.
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks.roadmap import validate_platform_roadmap as vpr
from scripts.contract_rules import parse_yaml
from scripts.platform_roadmap_state import load
from scripts.roadmap.roadmap_archive import (
    ARCHIVE_REL_PATH,
    RESERVED_STUB_FIELDS,
    STUB_FIELDS,
    ArchiveIntegrityError,
    archive_path_for,
    archive_texts,
    criterion_ii_archive_issues,
    derive_stub,
    edited_records_with_bare_strings,
    has_stubs,
    hydrate,
    hydrate_base,
    hydrate_from_disk,
    integrity_issues,
    read_archive,
)

_ROADMAP_REL = "docs/ROADMAP-PLATFORM.yaml"
_DOC_META = {"id": "ROADMAP-TEST", "version": 1, "status": "draft", "filed_via": "pending_log_decision_lambda"}
_MET = [{"id": "c1", "text": "structured criterion", "status": "met", "met_by": "a" * 40}]


def _record(item_id: str, status: str = "complete", **extra: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": item_id,
        "tier": "T9",
        "name": f"Item {item_id}",
        "intent": f"RESERVED: absorbed elsewhere ({item_id})." if status == "reserved" else f"Intent of {item_id}.",
        "depends_on": [],
        "files_in_scope": [],
        "exit_criteria": copy.deepcopy(_MET),
        "effort": "S",
        "status": status,
    }
    if status == "complete":
        record["completed_at"] = "2026-01-01"
    record.update(extra)
    return record


def _unarchived(**t91: Any) -> dict[str, Any]:
    live = {"id": "T9.0", "tier": "T9", "name": "Live item", "depends_on": ["T9.1"], "status": "not_started"}
    return {"document": dict(_DOC_META), "tier_items": [_record("T9.1", **t91), live, _record("T9.2", status="reserved")]}


def _archived(**t91: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    document = _unarchived(**t91)
    records = [item for item in document["tier_items"] if item["status"] in ("complete", "reserved")]
    document["tier_items"] = [derive_stub(item) if item in records else item for item in document["tier_items"]]
    return document, {"archive_of": _ROADMAP_REL, "tier_items": records}


def _write(directory: Path, document: Any, archive: Any = None, pad: int = 0) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ROADMAP-PLATFORM.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    if archive is not None:
        text = yaml.safe_dump(archive, sort_keys=False) + "# pad\n" * pad
        archive_path_for(path).write_text(text, encoding="utf-8")
    return path


def _issues(document: Any, archive: Any) -> list[str]:
    with pytest.raises(ArchiveIntegrityError) as excinfo:
        hydrate(document, archive)
    assert str(excinfo.value).startswith("archive integrity: ")
    assert excinfo.value.issues == integrity_issues(document, archive)
    return excinfo.value.issues


class TestDeriveStub:
    def test_a_complete_record_keeps_the_complete_stub_fields(self) -> None:
        assert derive_stub(_record("T9.1")) == {
            "id": "T9.1",
            "tier": "T9",
            "name": "Item T9.1",
            "depends_on": [],
            "status": "complete",
            "completed_at": "2026-01-01",
            "archived": True,
        }

    def test_a_reserved_record_keeps_intent_in_place_of_completed_at(self) -> None:
        stub = derive_stub(_record("T9.2", status="reserved"))

        assert set(stub) == set(RESERVED_STUB_FIELDS) | {"archived"}
        assert stub["intent"].startswith("RESERVED:")
        assert "completed_at" not in stub and "completed_at" in STUB_FIELDS

    def test_absent_optional_fields_are_omitted(self) -> None:
        record = _record("T9.1")
        del record["depends_on"], record["completed_at"]

        assert derive_stub(record) == {"id": "T9.1", "tier": "T9", "name": "Item T9.1", "status": "complete", "archived": True}


class TestHydrate:
    def test_a_document_without_stubs_or_archive_is_returned_unchanged(self) -> None:
        document = _unarchived()

        assert hydrate(document, None) is document

    def test_each_stub_is_replaced_in_place_by_its_record(self) -> None:
        document, archive = _archived()
        hydrated = hydrate(document, archive)

        assert hydrated["tier_items"] == _unarchived()["tier_items"]
        assert hydrated["document"] == document["document"]
        assert document["tier_items"][0]["archived"] is True

    def test_an_empty_archive_beside_a_stub_less_document_returns_it_unchanged(self) -> None:
        document = _unarchived()

        assert hydrate(document, {"archive_of": _ROADMAP_REL, "tier_items": []}) is document

    def test_a_non_mapping_document_passes_through(self) -> None:
        assert hydrate(["not", "a", "mapping"], {"archive_of": _ROADMAP_REL, "tier_items": []}) == ["not", "a", "mapping"]

    def test_a_stub_that_differs_from_its_record_fails_the_load(self, tmp_path: Path) -> None:
        document, archive = _archived()
        document["tier_items"][0]["name"] = "Renamed in the stub only"
        path = _write(tmp_path, document, archive)

        with pytest.raises(ArchiveIntegrityError) as excinfo:
            load(path)

        assert str(excinfo.value).startswith("archive integrity: ")
        assert excinfo.value.issues == ["stub 'T9.1' differs from derive_stub of its archived record"]

    @pytest.mark.parametrize(
        ("mutate", "needle"),
        [
            pytest.param(lambda d, a: a.clear() or a.update({"x": 1}), "exactly the keys", id="a-keys"),
            pytest.param(lambda d, a: a.update({"archive_of": ""}), "archive_of must be", id="a-archive-of"),
            pytest.param(lambda d, a: a.update({"tier_items": {"T9.1": 1}}), "string ids", id="a-not-a-list"),
            pytest.param(lambda d, a: a["tier_items"][0].update({"id": 7}), "string ids", id="a-non-str-id"),
            pytest.param(lambda d, a: a["tier_items"].append(copy.deepcopy(a["tier_items"][0])), "'T9.1' appears", id="b-dup"),
            pytest.param(lambda d, a: a["tier_items"][0].update({"status": "in_progress"}), "not complete or", id="b-status"),
            pytest.param(lambda d, a: a["tier_items"][0].update({"archived": True}), "carries an archived key", id="b-flag"),
            pytest.param(lambda d, a: d["tier_items"][1].update({"archived": False}), "'T9.0' carries archived", id="c"),
            pytest.param(lambda d, a: d["tier_items"].append(dict(d["tier_items"][0])), "stub 'T9.1' appears", id="d-dup"),
            pytest.param(lambda d, a: a["tier_items"].pop(0), "stub 'T9.1' has no record", id="d-no-record"),
            pytest.param(lambda d, a: d["tier_items"][2].update({"tier": "T8"}), "stub 'T9.2' differs", id="d-differs"),
            pytest.param(lambda d, a: d["tier_items"].pop(0), "record 'T9.1' has no stub", id="e"),
        ],
    )
    def test_each_integrity_rule_names_its_breach(self, mutate: Any, needle: str) -> None:
        document, archive = _archived()
        mutate(document, archive)

        assert any(needle in issue for issue in _issues(document, archive)), _issues(document, archive)

    def test_a_non_mapping_archive_is_a_shape_issue(self) -> None:
        document, _ = _archived()

        assert any("exactly the keys" in issue for issue in _issues(document, ["records"]))

    def test_stubs_without_an_archive_are_one_issue(self) -> None:
        document, _ = _archived()

        assert _issues(document, None) == [f"2 archived stub(s) but {ARCHIVE_REL_PATH} is absent"]


class TestHydrateBase:
    def test_has_stubs_reads_only_an_archived_key_on_a_mapping_item(self) -> None:
        assert has_stubs(_archived()[0]) is True
        assert has_stubs(_unarchived()) is False
        assert has_stubs({"tier_items": "not a list"}) is False
        assert has_stubs(["not a mapping"]) is False

    def test_stubs_are_replaced_by_id_without_a_stub_equality_check(self) -> None:
        document, archive = _archived()
        del document["tier_items"][0]["tier"]

        hydrated = hydrate_base(document, yaml.safe_dump(archive))

        assert hydrated["tier_items"] == _unarchived()["tier_items"]

    def test_a_stub_with_no_base_record_is_kept(self) -> None:
        document, archive = _archived()
        archive["tier_items"].pop(0)

        hydrated = hydrate_base(document, yaml.safe_dump(archive))

        assert hydrated["tier_items"][0] == document["tier_items"][0]
        assert hydrated["tier_items"][2] == _unarchived()["tier_items"][2]

    @pytest.mark.parametrize("text", [None, "tier_items: [unclosed\n", "- a list\n", "tier_items: 3\n"])
    def test_an_unusable_base_archive_means_no_records(self, text: str | None) -> None:
        document, _ = _archived()

        assert hydrate_base(document, text)["tier_items"] == document["tier_items"]

    def test_a_stub_less_document_passes_through(self) -> None:
        document = _unarchived()

        assert hydrate_base(document, "tier_items: []\n") is document


class TestArchivePaths:
    def test_the_archive_is_the_stem_archive_sibling(self, tmp_path: Path) -> None:
        assert archive_path_for(tmp_path / "ROADMAP-PLATFORM.yaml") == tmp_path / "ROADMAP-PLATFORM-ARCHIVE.yaml"
        assert ARCHIVE_REL_PATH == "docs/" + archive_path_for(Path(_ROADMAP_REL)).name

    def test_an_absent_archive_reads_as_none_and_no_texts(self, tmp_path: Path) -> None:
        path = _write(tmp_path, _unarchived())

        assert read_archive(path) is None
        assert archive_texts(path) == []

    def test_a_present_archive_is_parsed_and_its_text_returned(self, tmp_path: Path) -> None:
        document, archive = _archived()
        path = _write(tmp_path, document, archive)

        assert read_archive(path) == archive
        assert archive_texts(path) == [archive_path_for(path).read_text(encoding="utf-8")]

    def test_hydrate_from_disk_reads_the_sibling(self, tmp_path: Path) -> None:
        document, archive = _archived()
        path = _write(tmp_path, document, archive)

        assert hydrate_from_disk(document, path)["tier_items"] == _unarchived()["tier_items"]


_BARE = ["a bare-string criterion"]


def _base_reader(base: dict[str, Any] | None, base_archive: dict[str, Any] | None = None) -> Any:
    texts = {_ROADMAP_REL: base, ARCHIVE_REL_PATH: base_archive}
    return lambda rel: None if texts.get(rel) is None else yaml.safe_dump(texts[rel])


class TestCriterionIiArchive:
    def test_an_edited_archived_record_with_a_bare_string_criterion_fails(self, tmp_path: Path) -> None:
        document, archive = _archived(exit_criteria=["an edited bare-string criterion"])
        path = _write(tmp_path, document, archive)

        issues = criterion_ii_archive_issues(path, _base_reader(_unarchived(exit_criteria=_BARE)))

        assert issues == [
            (
                "  FAIL: archived tier_item 'T9.1' is edited relative to origin/main but still has bare-string criteria "
                "-- convert to ExitCriterion objects"
            )
        ]

    def test_a_pure_move_passes(self, tmp_path: Path) -> None:
        document, archive = _archived(exit_criteria=_BARE)
        path = _write(tmp_path, document, archive)

        assert criterion_ii_archive_issues(path, _base_reader(_unarchived(exit_criteria=_BARE))) == []

    def test_an_absent_archive_gives_nothing_and_reads_no_base(self, tmp_path: Path) -> None:
        def _never(rel: str) -> str | None:
            raise AssertionError(rel)

        assert criterion_ii_archive_issues(_write(tmp_path, _unarchived(exit_criteria=_BARE)), _never) == []

    def test_an_unreachable_base_skips(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        document, archive = _archived(exit_criteria=["an edited bare-string criterion"])

        assert criterion_ii_archive_issues(_write(tmp_path, document, archive), _base_reader(None)) == []
        assert "SKIP: origin/main roadmap unreachable -- criterion (ii) archive leg not evaluated" in capsys.readouterr().out

    def test_a_stub_bearing_base_is_hydrated_leniently(self, tmp_path: Path) -> None:
        document, archive = _archived(exit_criteria=_BARE)
        base, base_archive = copy.deepcopy((document, archive))
        del base["tier_items"][0]["tier"]
        path = _write(tmp_path, document, archive)

        assert criterion_ii_archive_issues(path, _base_reader(base, base_archive)) == []

    def test_an_id_absent_from_the_base_counts_as_edited(self) -> None:
        archive = {"archive_of": _ROADMAP_REL, "tier_items": [_record("T9.1", exit_criteria=_BARE), _record("T9.3")]}

        assert edited_records_with_bare_strings(archive, {}) == ["T9.1"]
        assert edited_records_with_bare_strings(None, {}) == []


class TestLoadHydrates:
    def test_an_archived_fixture_loads_identically(self, tmp_path: Path) -> None:
        document, archive = _archived()
        plain = _write(tmp_path / "plain", _unarchived())
        archived = _write(tmp_path / "archived", document, archive)

        assert load(archived).model_dump() == load(plain).model_dump()
        assert [item.id for item in load(archived).tier_items] == ["T9.1", "T9.0", "T9.2"]


def _check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: Any, archive: Any, base: Any = None, pad: int = 0
) -> list[str]:
    monkeypatch.setattr(_common, "ROOT", tmp_path)
    _write(tmp_path / "docs", document, archive, pad=pad)
    monkeypatch.setattr(vpr.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="", stderr=""))
    monkeypatch.setattr(vpr, "default_base_reader", _base_reader(base))
    failed: list[str] = []
    vpr.validate_platform_roadmap(failed)
    registry.pop_declaration()
    return failed


class TestValidatePlatformRoadmapWiring:
    def test_a_pure_move_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        document, archive = _archived(exit_criteria=_BARE)

        assert _check(tmp_path, monkeypatch, document, archive, base=_unarchived(exit_criteria=_BARE)) == []

    def test_a_broken_archive_fails_with_the_integrity_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        document, archive = _archived()
        archive["tier_items"].pop()

        assert _check(tmp_path, monkeypatch, document, archive) == ["Platform roadmap schema validation"]
        assert "FAIL: Unexpected error: archive integrity: stub 'T9.2' has no record" in capsys.readouterr().out

    def test_an_over_ceiling_archive_fails_naming_the_archive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        document, archive = _archived()

        assert _check(tmp_path, monkeypatch, document, archive, pad=vpr._ROADMAP_MAX_LINES) == [
            "Platform roadmap criteria integrity"
        ]
        out = capsys.readouterr().out
        assert f"FAIL: {ARCHIVE_REL_PATH} is " in out and f"the {vpr._ROADMAP_MAX_LINES}-line ceiling" in out
        assert f"FAIL: {_ROADMAP_REL} is " not in out

    def test_an_edited_bare_string_archived_record_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        document, archive = _archived(exit_criteria=["an edited bare-string criterion"])

        failed = _check(tmp_path, monkeypatch, document, archive, base=_unarchived(exit_criteria=_BARE))

        assert failed == ["Platform roadmap criteria integrity"]
        assert "archived tier_item 'T9.1' is edited relative to origin/main" in capsys.readouterr().out


def _live() -> tuple[dict[str, Any], dict[str, Any]]:
    live = parse_yaml((_common.ROOT / _ROADMAP_REL).read_text(encoding="utf-8"))
    archive = parse_yaml((_common.ROOT / ARCHIVE_REL_PATH).read_text(encoding="utf-8"))
    return live, archive


def _lifecycle() -> tuple[str, dict[str, Any]]:
    text = (_common.ROOT / "docs" / "contracts" / "tier-item-lifecycle.yaml").read_text(encoding="utf-8")
    return text, parse_yaml(text)


class TestLiveArchive:
    def test_the_live_tree_loads_every_item_in_place(self) -> None:
        live, _ = _live()

        assert [item.id for item in load(_common.ROOT / _ROADMAP_REL).tier_items] == [i["id"] for i in live["tier_items"]]

    def test_every_stub_has_a_record_and_no_record_is_an_exception(self) -> None:
        live, archive = _live()
        exceptions = set(_lifecycle()[1]["archival_exceptions"])

        assert integrity_issues(live, archive) == []
        assert archive["archive_of"] == _ROADMAP_REL
        assert not exceptions & {record["id"] for record in archive["tier_items"]}


class TestLifecycleArchivalContract:
    def test_lifecycle_archival_names_the_rule_and_its_un_archive_clause(self) -> None:
        clause = _lifecycle()[1]["lifecycle_archival"].lower()

        for needle in ("derive_stub", "archived: true", "archival_exceptions", "operator-disposed", "un-archiving"):
            assert needle in clause, needle

    def test_every_exception_is_a_live_unarchived_retired_item_with_an_existing_pin(self) -> None:
        live, _ = _live()
        items = {item["id"]: item for item in live["tier_items"]}

        for item_id, row in _lifecycle()[1]["archival_exceptions"].items():
            assert item_id in items, item_id
            assert "archived" not in items[item_id], item_id
            assert items[item_id]["status"] in ("complete", "reserved"), item_id
            assert (_common.ROOT / row["pinned_by"].split("::")[0]).is_file(), row["pinned_by"]

    @pytest.mark.parametrize(
        ("opening", "closing"),
        [
            ("Decision 181 residual (PLAN-roadmap-lifecycle-archival, rec-4458 owns it)", "build the blocking check."),
            ("Decision 181 coverage (PLAN-roadmap-lifecycle-archival)", "residual, rec-4458 owns it."),
        ],
    )
    def test_each_governance_line_sits_on_one_raw_line(self, opening: str, closing: str) -> None:
        lines = [line for line in _lifecycle()[0].splitlines() if opening in line]

        assert len(lines) == 1 and lines[0].rstrip().endswith(closing), lines
