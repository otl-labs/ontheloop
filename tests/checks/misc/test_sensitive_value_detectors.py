"""Ratchet and loosening gate for the sensitive-value detector library (rec-4481 plan 1)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.checks import _common, _marker_guard, registry
from scripts.checks.misc import sensitive_value_corpus as corpus
from scripts.checks.misc import sensitive_value_detectors as det
from src.sensitive_values import CLASSES, DetectorClass

ROOT = Path(__file__).resolve().parents[3]
KEY = "read_masked.ACCOUNT_ID.account_id_bare@0123abcd"
OTHER = "read_masked.ACCOUNT_ID.account_id_arn_quoted@4567abcd"
CODEOWNERS = "\n".join(f"/{spec} @owner" for spec in det.PROTECTED)
BOUNDARY = [
    "src/sensitive_values/",
    "src/turn_capture/scrub.py",
    "config/sensitive_value_detectors.yaml",
    "scripts/checks/misc/sensitive_value_",
    "tests/sensitive_values/",
    "tests/checks/misc/test_sensitive_value_",
    "src/lambdas/ducklake_telemetry_content_reader/",
    "src/common/ducklake_content_read_verbs.py",
    "docs/contracts/ducklake_telemetry_content_reader.yaml",
    "tests/lambdas/ducklake_telemetry_content_reader/",
]
CONTENT_READER_PATHS = BOUNDARY[-4:]


def _registered() -> dict[str, int]:
    return yaml.safe_load((ROOT / det.REGISTRY_REL).read_text(encoding="utf-8"))


def test_ratchet_passes_on_committed_tree() -> None:
    failed: list[str] = []
    det.validate_detector_ratchet(failed)
    assert failed == []
    det.validate_detector_registry_edits(failed)
    assert failed == []


def test_registry_equals_live_keys() -> None:
    assert det.registry_violations(_registered(), det.live_keys()) == []


def test_live_key_missing_extra_nonzero_and_value_off_fail() -> None:
    registered, measured = _registered(), det.live_keys()
    gone = dict(registered)
    gone.pop(next(iter(gone)))
    assert det.registry_violations(gone, measured)
    assert det.registry_violations({**registered, KEY: 1}, measured)
    assert det.registry_violations({**registered, KEY: 0}, measured) == []
    off = dict(registered)
    off[next(iter(off))] = 0
    assert det.registry_violations(off, measured)


def test_loosened_pattern_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    row = CLASSES["ACCOUNT_ID"]
    monkeypatch.setitem(
        CLASSES, "ACCOUNT_ID", DetectorClass(row.id, row.family, r"(?<![0-9A-Za-z])\d{13}(?![0-9A-Za-z])", row.kind)
    )
    assert det.registry_violations(_registered(), det.live_keys())


def _swap(monkeypatch: pytest.MonkeyPatch, edit) -> None:
    original = corpus.positives()
    monkeypatch.setattr(corpus, "positives", lambda: edit(list(original)))


def test_case_swap_text_edit_and_planted_narrowing_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    registered = _registered()
    uncaught = corpus.Case("swap_added", "ACCOUNT_ID", "nothing sensitive here", "secret-value")
    _swap(monkeypatch, lambda cases: cases[1:] + [uncaught])
    assert det.registry_violations(registered, det.live_keys())

    first = corpus.positives()[0]
    _swap(monkeypatch, lambda cases: [first._replace(text=first.text + " edited")] + cases[1:])
    assert det.registry_violations(registered, det.live_keys())

    _swap(monkeypatch, lambda cases: [first._replace(planted_value=first.planted_value[:-1])] + cases[1:])
    assert det.registry_violations(registered, det.live_keys())


def test_removed_by_earlier_class_but_not_attributed_to_own_is_zero() -> None:
    akid = corpus.positives()[0].planted_value
    case = corpus.Case("misattributed", "ACCOUNT_ID", f"x {akid} y", akid)
    assert det.measure("read_masked", case) == 0


def test_negatives_hit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    assert det.negative_violations() == []
    bad = corpus.Case("neg_bad", None, "password=hunter2xyz", None)
    monkeypatch.setattr(corpus, "negatives", lambda: (bad,))
    assert det.negative_violations()


def test_protected_path_without_owner_or_boundary_fails() -> None:
    assert det.ownership_violations(det.PROTECTED, CODEOWNERS, BOUNDARY) == []
    assert det.ownership_violations(det.PROTECTED, "", BOUNDARY)
    assert det.ownership_violations(det.PROTECTED, CODEOWNERS, [])
    head = (ROOT / det.CODEOWNERS_REL).read_text(encoding="utf-8")
    caps = yaml.safe_load((ROOT / det.CAPABILITIES_REL).read_text(encoding="utf-8"))["boundary_patterns"]
    assert det.ownership_violations(det.PROTECTED, head, caps) == []


def test_content_reader_paths_are_protected() -> None:
    assert all(path in det.PROTECTED for path in CONTENT_READER_PATHS)
    head = (ROOT / det.CODEOWNERS_REL).read_text(encoding="utf-8")
    caps = yaml.safe_load((ROOT / det.CAPABILITIES_REL).read_text(encoding="utf-8"))["boundary_patterns"]
    assert det.ownership_violations(det.PROTECTED, head, caps) == []
    owned = "\n".join(line for line in head.splitlines() if "ducklake_telemetry_content_reader" not in line)
    assert any("ducklake_telemetry_content_reader" in v for v in det.ownership_violations(det.PROTECTED, owned, caps))


def test_narrowing_against_base() -> None:
    base_src = f"PROTECTED = {det.PROTECTED + ('src/extra/',)!r}\n"
    base_codeowners = CODEOWNERS + "\n/src/extra/ @owner"
    base_boundary = BOUNDARY + ["src/extra/"]
    args = (det.PROTECTED, CODEOWNERS + "\n/src/extra/ @owner", base_boundary + [])
    assert det.narrowing_violations("", "", [], det.PROTECTED, "", []) == []
    clean = det.narrowing_violations(f"PROTECTED = {det.PROTECTED!r}\n", CODEOWNERS, BOUNDARY, *args)
    assert clean == []
    assert any(
        "dropped" in v
        for v in det.narrowing_violations(
            base_src, base_codeowners, base_boundary, det.PROTECTED, base_codeowners, base_boundary
        )
    )
    full = det.PROTECTED + ("src/extra/",)
    assert any(
        "CODEOWNERS" in v
        for v in det.narrowing_violations(base_src, base_codeowners, base_boundary, full, CODEOWNERS, base_boundary)
    )
    assert any(
        "boundary" in v
        for v in det.narrowing_violations(base_src, base_codeowners, base_boundary, full, base_codeowners, BOUNDARY)
    )


def _declared() -> registry._Declaration:
    return registry._CURRENT_DECLARATION


def test_ratchet_skips_leg_e_when_origin_unreachable_or_batch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "origin_main_reachable", lambda _root: False)
    failed: list[str] = []
    det.validate_detector_ratchet(failed)
    assert failed == [] and "skipped" in _declared().unit
    monkeypatch.setattr(_common, "origin_main_reachable", lambda _root: True)
    monkeypatch.setattr(det, "_batched_base_reader", lambda _root, _paths: None)
    det.validate_detector_ratchet(failed)
    assert failed == [] and "skipped" in _declared().unit and _declared().kind == "examined"


def test_edits_check_skips_when_base_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    failed: list[str] = []
    monkeypatch.setattr(_common, "origin_main_reachable", lambda _root: False)
    det.validate_detector_registry_edits(failed)
    assert failed == [] and _declared().kind == "skipped"
    monkeypatch.setattr(_common, "origin_main_reachable", lambda _root: True)
    monkeypatch.setattr(det, "_batched_base_reader", lambda _root, _paths: None)
    det.validate_detector_registry_edits(failed)
    assert failed == [] and _declared().kind == "skipped"


def _edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: str, head: str | None, bodies: dict[int, str]) -> list[str]:
    monkeypatch.setattr(_common, "ROOT", tmp_path)
    monkeypatch.setattr(_marker_guard, "load_decision_bodies", lambda: bodies)
    target = tmp_path / det.REGISTRY_REL
    target.parent.mkdir(parents=True)
    if head is not None:
        target.write_text(head, encoding="utf-8")
    spec = det.registry_spec()
    return _marker_guard.check_state_diff(spec, base_reader=lambda _rel: base) + _marker_guard.check_present_markers(spec)


MARK = "  # detector-loosening-approved: dec-900 retired for a reason"


def test_unmarked_drop_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _edits(tmp_path, monkeypatch, f"{KEY}: 1\n", f"{KEY}: 0\n", {900: KEY})


def test_marker_naming_exact_key_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _edits(tmp_path, monkeypatch, f"{KEY}: 1\n", f"{KEY}: 0{MARK}\n", {900: f"approves {KEY}"}) == []


@pytest.mark.parametrize("body", ["read_masked.ACCOUNT_ID", "read_masked/ACCOUNT_ID", "read_masked", "ACCOUNT_ID"])
def test_marker_naming_only_profile_or_class_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    assert _edits(tmp_path, monkeypatch, f"{KEY}: 1\n", f"{KEY}: 0{MARK}\n", {900: body})


def test_deleting_value_one_fails_tombstone_deletion_new_and_raise_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _edits(tmp_path, monkeypatch, f"{KEY}: 1\n", "", {})
    other = tmp_path.parent / (tmp_path.name + "_b")
    other.mkdir()
    assert _edits(other, monkeypatch, f"{KEY}: 0\n", "", {}) == []
    third = tmp_path.parent / (tmp_path.name + "_c")
    third.mkdir()
    assert _edits(third, monkeypatch, f"{KEY}: 0\n", f"{KEY}: 1\n{OTHER}: 1\n", {}) == []


def test_checks_are_registered() -> None:
    checks = registry.all_checks()
    for name in ("validate_detector_ratchet", "validate_detector_registry_edits"):
        assert name in checks
        entry = registry._ALL_ENTRIES[name]
        assert entry.pre is True and entry.full_segment == "full_after_lint" and not entry.pre_globs


def test_marker_token_declared() -> None:
    grammar = yaml.safe_load((ROOT / "docs/contracts/marker-grammar.yaml").read_text(encoding="utf-8"))
    token = grammar["tokens"]["detector-loosening-approved"]
    assert token["direction"] == "down" and token["reason_required"] is True
    assert token["used_by"] == ["validate_detector_registry_edits"]
    assert "check_state_diff" in _marker_guard.__all__


def test_checks_have_taxonomy_rows() -> None:
    taxonomy = yaml.safe_load((ROOT / "config/ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))["function_to_category"]
    assert taxonomy["validate_detector_ratchet"] == "code_regression"
    assert taxonomy["validate_detector_registry_edits"] == "code_regression"


def test_base_without_protected_constrains_nothing() -> None:
    assert det.base_protected("X = 1\n") is None
    assert det.narrowing_violations("X = 1\n", "", [], det.PROTECTED, "", []) == []


def test_non_mapping_boundary_and_registry_inputs() -> None:
    assert det._boundary("- a\n- b\n") == []
    assert det._load_registry("- a\n") is None
    assert det._load_registry("k: notint\n") is None


def test_ratchet_fails_on_unreadable_or_malformed_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "origin_main_reachable", lambda _root: False)
    failed: list[str] = []
    det.validate_detector_ratchet(failed, root=tmp_path)
    assert failed == ["Sensitive-value detector ratchet"]
    (tmp_path / "config").mkdir()
    (tmp_path / det.REGISTRY_REL).write_text("- not a mapping\n", encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / det.CODEOWNERS_REL).write_text("", encoding="utf-8")
    (tmp_path / "config/agent/executor").mkdir(parents=True)
    (tmp_path / det.CAPABILITIES_REL).write_text("boundary_patterns: []\n", encoding="utf-8")
    failed.clear()
    det.validate_detector_ratchet(failed, root=tmp_path)
    assert failed == ["Sensitive-value detector ratchet"]


def test_edits_check_fails_on_unmarked_drop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "ROOT", tmp_path)
    monkeypatch.setattr(_marker_guard, "load_decision_bodies", dict)
    monkeypatch.setattr(_common, "origin_main_reachable", lambda _root: True)
    monkeypatch.setattr(det, "_batched_base_reader", lambda _root, _paths: lambda _rel: f"{KEY}: 1\n")
    (tmp_path / "config").mkdir()
    (tmp_path / det.REGISTRY_REL).write_text(f"{KEY}: 0\n", encoding="utf-8")
    failed: list[str] = []
    det.validate_detector_registry_edits(failed)
    assert failed == ["Detector registry loosening gate"]
