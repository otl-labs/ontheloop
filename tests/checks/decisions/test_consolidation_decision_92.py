"""The Decision 92 chain consolidation pilot (rec-5373): stubs, delegated keys, re-homed mechanism, clause accounting.

The consolidator is found by its envelope's supersedes list, so the number it was given at merge never matters.
ACCOUNTING is the prototype of rec-5265's manifest: every clause, update note and reversal condition of the four
victims maps to a restated clause, a delegated or held key, a moved convergence_write_mechanism subkey, a reversal
stanza id, or a stated retirement.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks.decisions import entry_measure as measure
from scripts.checks.decisions.envelope_rules import dotted_value, split_delegated_key
from scripts.decisions_md import extract_superseded_by, is_compacted_stub, iter_decision_sections

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]
VICTIMS = (92, 94, 154, 190)
BASE_SHA = "d029373bacdea00ad2cae63165c3ffd82045e35b"  # pragma: allowlist secret
CWM_SUBKEYS = ("pre_apply_failure", "reviewed_bytes", "anti_masking", "health_signals", "record_schema")
ET = "docs/contracts/environment-taxonomy.yaml"
TPCD = "docs/contracts/terraform-personal-cd.yaml"
_STUB_MARKERS = ("**Status:**", "**Date:**", "**Decision:**", "**Superseded by:")
_SENTINELS = (
    (92, "waves-2-5", "Waves 2-5 and Wave X"),
    (92, "CD.35", "Supersession of CD.35"),
    (92, "ratification-path", "canonical ratification path"),
    (94, "correction", "Correct Decision 92 point 3."),
    (94, "vp10", "Secondary correction (VP10)"),
)


def _r(
    cl: tuple[int, ...] = (),
    key: tuple[str, ...] = (),
    moved: tuple[str, ...] = (),
    stanza: tuple[str, ...] = (),
    held: tuple[str, ...] = (),
    retired: str = "",
    token: str = "",
) -> dict[str, Any]:
    return {"cl": cl, "key": key, "moved": moved, "stanza": stanza, "held": held, "retired": retired, "token": token}


_HIST = "history in git and ops_decisions SCD2; the consolidator's own text replaces it"
_GLOSS = "glossary read_as rows govern"
_RED = f"{TPCD}#convergence_anchor"
_SGA = f"{ET}#axis_a.sandbox_gated_apply"

ACCOUNTING: dict[str, dict[str, Any]] = {
    "92 pt1": _r(cl=(1,)),
    "92 pt2": _r(cl=(2,), key=(_RED,)),
    "92 pt3": _r(cl=(1, 3), key=(_SGA,)),
    "92 pt4": _r(cl=(3,)),
    "92 pt5": _r(cl=(3,), key=(f"{ET}#guard_classification",)),
    "92 pt6": _r(cl=(5,)),
    "92 waves-2-5": _r(cl=(1, 2, 3)),
    "92 CD.35": _r(retired="historical: CD.35 is filed_via ops_decisions:dec-092"),
    "92 problem": _r(retired=_HIST),
    "92 rationale": _r(retired=_HIST),
    "92 ratification-path": _r(held=("Decision 84", "Decision 105")),
    "92 note 154": _r(cl=(2, 4)),
    "92 note 190": _r(cl=(2,)),
    "92 note 238": _r(cl=(6,), held=(f"{TPCD}#speculative_plan_and_apply_the_saved_plan",), token="speculative"),
    "92 note 257": _r(cl=(3,)),
    "92 note 144": _r(cl=(3,)),
    "94 problem": _r(retired=_HIST),
    "94 correction": _r(cl=(1,)),
    "94 vp10": _r(cl=(5,), moved=("anti_masking",), token="credentials step"),
    "154 problem": _r(retired=_HIST),
    "154 intent": _r(retired=_HIST),
    "154 significance": _r(retired=_HIST),
    "154 rationale": _r(retired=_HIST),
    "154 pt1": _r(cl=(4,), moved=("reviewed_bytes",), token="fetch_reviewed"),
    "154 pt2": _r(moved=("reviewed_bytes",), token=".dispatch-tooling"),
    "154 pt3": _r(moved=("reviewed_bytes",), token="DUCKLAKE_DEPS"),
    "154 pt4": _r(cl=(2,), moved=("pre_apply_failure",), token="allow-list"),
    "154 pt5": _r(moved=("pre_apply_failure",), stanza=("marker-hid-mutation",), token="fetch_plan"),
    "154 pt6": _r(cl=(5,), moved=("anti_masking",), token="stale_green_backlog"),
    "154 pt7": _r(moved=("health_signals",), token="infra_error"),
    "154 update 2026-10-17": _r(
        moved=("reviewed_bytes",), held=(f"{ET}#conformance.ducklake_detail",), token="DUCKLAKE_ZIP_MISMATCH"
    ),
    "154 update 2026-11-02": _r(retired=_GLOSS),
    "154 update 2026-11-16": _r(cl=(1, 2), held=(_SGA,), token="gated"),
    "154 reversal a": _r(stanza=("marker-hid-mutation",)),
    "154 reversal b": _r(stanza=("ci-rca-independence-lapses",)),
    "154 reversal c": _r(stanza=("markers-unactioned",)),
    "154 note 190": _r(cl=(2,), moved=("health_signals",), token="red_cause"),
    "190 problem": _r(retired=_HIST),
    "190 significance": _r(retired=_HIST),
    "190 rationale": _r(retired=_HIST),
    "190 pt1": _r(cl=(2,), key=(f"{ET}#convergence_drift_route_exhaustiveness",)),
    "190 pt2": _r(key=(f"{ET}#convergence_marker_vocabulary",)),
    "190 pt3": _r(key=(f"{ET}#convergence_marker_vocabulary",)),
    "190 pt4": _r(moved=("health_signals",), token="red_cause"),
    "190 pt5": _r(moved=("health_signals",), token="tf_drift"),
    "190 pt6": _r(moved=("health_signals",), token="escalate"),
    "190 pt7": _r(moved=("record_schema",), token="plan_sha"),
    "190 update 2026-11-02": _r(retired=_GLOSS),
    "190 reversal a": _r(stanza=("marker-hid-mutation",)),
    "190 reversal b": _r(stanza=("markers-unactioned",)),
    "190 reversal c": _r(stanza=("drift-semantics-move",)),
}

EXPECTED_CLAUSES: frozenset[str] = frozenset(
    {
        "154 intent",
        "154 note 190",
        "154 problem",
        "154 pt1",
        "154 pt2",
        "154 pt3",
        "154 pt4",
        "154 pt5",
        "154 pt6",
        "154 pt7",
        "154 rationale",
        "154 reversal a",
        "154 reversal b",
        "154 reversal c",
        "154 significance",
        "154 update 2026-10-17",
        "154 update 2026-11-02",
        "154 update 2026-11-16",
        "190 problem",
        "190 pt1",
        "190 pt2",
        "190 pt3",
        "190 pt4",
        "190 pt5",
        "190 pt6",
        "190 pt7",
        "190 rationale",
        "190 reversal a",
        "190 reversal b",
        "190 reversal c",
        "190 significance",
        "190 update 2026-11-02",
        "92 CD.35",
        "92 note 144",
        "92 note 154",
        "92 note 190",
        "92 note 238",
        "92 note 257",
        "92 problem",
        "92 pt1",
        "92 pt2",
        "92 pt3",
        "92 pt4",
        "92 pt5",
        "92 pt6",
        "92 ratification-path",
        "92 rationale",
        "92 waves-2-5",
        "94 correction",
        "94 problem",
        "94 vp10",
    }
)


def _sections(text: str) -> dict[int, str]:
    return {int(m.group(1)): block for m, block in iter_decision_sections(text)}


def derive_clauses(text: str) -> set[str]:
    """The clause ids of the four victims in `text`: numbered points, update and amendment notes, reversal items,
    the named prose sections and the sentinel paragraphs."""
    sections = _sections(text)
    found: set[str] = set()
    for number in VICTIMS:
        block = sections[number]
        for line in block.split("\n"):
            point = re.match(r"^(\d+)\. \*\*", line)
            if point:
                found.add(f"{number} pt{point.group(1)}")
            note = re.match(r"^\s*> \*\*(?:Amended by|Widened -> realized by) Decision (\d+)", line)
            if note:
                found.add(f"{number} note {note.group(1)}")
            update = re.match(r"^> \*\*Update \((\d{4}-\d{2}-\d{2})", line)
            if update:
                found.add(f"{number} update {update.group(1)}")
        for section in ("Problem", "Intent", "Significance", "Rationale"):
            if f"**{section}:**" in block:
                found.add(f"{number} {section.lower()}")
        reversal = re.search(r"\*\*Reversal conditions:\*\*(.*?)(?:\n\n|\Z)", block, re.DOTALL)
        if reversal:
            found |= {f"{number} reversal {m}" for m in re.findall(r"\(([abc])\)", reversal.group(1))}
    for number, key, marker in _SENTINELS:
        assert marker in sections[number], (number, marker)
        found.add(f"{number} {key}")
    return found


def _decisions() -> str:
    return (_REPO / "docs/DECISIONS.md").read_text(encoding="utf-8")


def _consolidator() -> tuple[int, str]:
    for number, block in _sections(_decisions()).items():
        envelope = measure.envelope_span(block)
        if envelope is None:
            continue
        parsed = yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", block, re.DOTALL).group(1))
        if sorted(int(x) for x in parsed.get("supersedes") or []) == list(VICTIMS):
            return number, block
    raise AssertionError("no Decision supersedes exactly [92, 94, 154, 190]")


def _envelope(block: str) -> dict[str, Any]:
    return yaml.safe_load(re.search(r"```yaml\n(.*?)\n```", block, re.DOTALL).group(1))


def _stanza_ids(block: str) -> set[str]:
    fence = re.search(r"```yaml reversal-conditions\n(.*?)\n```", block, re.DOTALL)
    return {c["id"] for c in yaml.safe_load(fence.group(1))["conditions"]}


def _clause_numbers(block: str) -> set[int]:
    return {int(m.group(1)) for m in re.finditer(r"^(\d+)\. \*\*", block, re.MULTILINE)}


def _load(rel: str) -> Any:
    return yaml.safe_load((_REPO / rel).read_text(encoding="utf-8"))


def _resolve_key(key: str) -> Any:
    split = split_delegated_key(key)
    assert split is not None, key
    found, value = dotted_value(_load(split[0]), split[1])
    assert found, f"{key} does not resolve"
    return value


def test_victims_are_compacted_stubs() -> None:
    number, _ = _consolidator()
    sections = _sections(_decisions())
    for victim in VICTIMS:
        block = sections[victim]
        assert is_compacted_stub(block), victim
        assert extract_superseded_by(block) == f"dec-{number}", victim
        body = [ln for ln in block.split("\n")[1:] if ln.strip() and ln.strip() != "---"]
        assert len(body) == len(_STUB_MARKERS), (victim, body)
        assert all(ln.startswith(m) for ln, m in zip(body, _STUB_MARKERS)), (victim, body)


def test_consolidator_supersedes_and_fits_the_measured_cap() -> None:
    _, block = _consolidator()
    assert sorted(int(x) for x in _envelope(block)["supersedes"]) == list(VICTIMS)
    cap = _load("docs/contracts/decision-entry.yaml")["size_governance"]["measured_size_norm"]["cap_bytes"]
    size = measure.measured_bytes(block, (measure.METADATA_ENVELOPE, measure.REVERSAL_CONDITIONS, measure.OPTIONS_CONSIDERED))
    assert size <= cap, (size, cap)


def test_every_delegated_key_resolves() -> None:
    _, block = _consolidator()
    delegates = _envelope(block)["delegates"]
    assert delegates
    for entry in delegates:
        _resolve_key(entry["key"])


def test_convergence_write_mechanism_holds_its_subkeys_and_the_unlatch_paths() -> None:
    tpcd = _load(TPCD)
    assert all(tpcd["convergence_write_mechanism"].get(k) for k in CWM_SUBKEYS)
    unlatch = tpcd["convergence_anchor"]["dispatch_ack_unlatch"]
    for token in ("acknowledge-and-retry", "Reconcile", "stale_gated_approval", "drift guard"):
        assert token in unlatch, token
    assert "ONLY" not in unlatch


def test_no_waiver_remains_for_144_over_92() -> None:
    waivers = _load("config/decision_supersession_waivers.yaml")
    rows = waivers if isinstance(waivers, list) else next(v for v in waivers.values() if isinstance(v, list))
    assert not [r for r in rows if r.get("superseder") == 144 and r.get("victim") == 92]


def test_red_cause_note_pins_derive_red_cause() -> None:
    assert "derive_red_cause" in (_REPO / ET).read_text(encoding="utf-8")


def test_accounting_equals_the_victim_clauses() -> None:
    assert set(ACCOUNTING) == EXPECTED_CLAUSES
    reachable = (
        subprocess.run(
            ["git", "cat-file", "-e", f"{BASE_SHA}^{{commit}}"], cwd=_REPO, capture_output=True, check=False
        ).returncode
        == 0
    )
    if reachable:
        shown = subprocess.run(
            ["git", "show", f"{BASE_SHA}:docs/DECISIONS.md"], cwd=_REPO, capture_output=True, check=True
        ).stdout.decode("utf-8")
        assert derive_clauses(shown) == EXPECTED_CLAUSES


def test_every_accounting_row_resolves() -> None:
    number, block = _consolidator()
    clauses, stanza_ids = _clause_numbers(block), _stanza_ids(block)
    cwm = _load(TPCD)["convergence_write_mechanism"]
    headers = {int(m.group(1)) for m, _ in iter_decision_sections(_decisions())}
    for clause, row in ACCOUNTING.items():
        targets = [row["cl"], row["key"], row["moved"], row["stanza"], row["held"], row["retired"]]
        assert any(targets), clause
        assert set(row["cl"]) <= clauses, clause
        assert set(row["stanza"]) <= stanza_ids, clause
        text = ""
        for key in row["key"]:
            _resolve_key(key)
        for sub in row["moved"]:
            assert sub in cwm, (clause, sub)
            text += str(cwm[sub])
        for held in row["held"]:
            if held.startswith("Decision "):
                assert int(held.split()[1]) in headers, (clause, held)
            else:
                text += str(_resolve_key(held))
        if row["moved"] or any(not h.startswith("Decision ") for h in row["held"]):
            assert row["token"] and row["token"] in text, (clause, row["token"])
        if row["retired"]:
            assert len(row["retired"]) > 10, clause
    assert number not in VICTIMS
