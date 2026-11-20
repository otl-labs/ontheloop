"""Planning-time decision_drafts report (PLAN-decision-serves-kind-grammar, Decision 243 clause 8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.north_star_models import load_north_star
from scripts.roadmap import plan_decision_drafts as pdd
from scripts.roadmap.plan_obligations import main

REPO = Path(__file__).resolve().parents[1]
_FIXTURE = REPO / "tests/fixtures/decision_drafts/placeholder_conforming.txt"
_ENV_TAIL = "significance:\n  value: numbered_decision\n  justification: r.\nkind:\n  value: direction\n  justification: j.\n"


_OPTIONS = "**Options considered:** a second table -- rejected: it would drift.\n"


def _draft(
    number: str = "{{N}}", env_number: str | None = None, kind: bool = True, extra: str = "", title: str = "Drafted"
) -> str:
    tail = _ENV_TAIL if kind else _ENV_TAIL.split("kind:")[0]
    head = f"## Decision {number}: {title}\n\n```yaml\nnumber: {env_number or number}\n"
    return f"{head}{tail}{extra}```\n\n**Status:** Decided\n\n{_OPTIONS}"


def _plan(tmp_path: Path, text: str, slug: str = "demo") -> Path:
    path = tmp_path / f"PLAN-{slug}.yaml"
    path.write_text(yaml.safe_dump({"slug": slug, "intent": "x", "context": [text]}), encoding="utf-8")
    return path


def _sidecar(tmp_path: Path, blocks: list[dict], slug: str = "demo") -> None:
    d = tmp_path / "docs" / "plans" / "approved"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.yaml").write_text(yaml.safe_dump({"schema_version": 1, "slug": slug, "blocks": blocks}), encoding="utf-8")


def _lines(path: Path, root: Path | None = None) -> list[str]:
    return pdd.report_for_plan(path, root)


class TestPlanText:
    def test_conforming_placeholder_draft_reports_clean(self, tmp_path: Path) -> None:
        assert _lines(_plan(tmp_path, _draft())) == ["decision_drafts: 1 drafted, 0 findings"]

    def test_missing_kind_reports_a_fail_quoting_the_test(self, tmp_path: Path) -> None:
        lines = _lines(_plan(tmp_path, _draft(kind=False)))
        assert lines[0] == "decision_drafts: 1 drafted, 1 findings"
        assert lines[1].startswith("  FAIL {{N}} kind: kind element is missing")
        assert "If the entry's own clauses were undone" in lines[1]

    def test_missing_significance_and_never_numbered_routing_fail(self, tmp_path: Path) -> None:
        no_sig = _draft().replace(_ENV_TAIL.split("kind:")[0], "")
        assert any("significance" in f.field for f in pdd.findings_for_plan(_plan(tmp_path, no_sig)))
        never = _draft().replace("numbered_decision", "operational_fact")
        assert any(
            f.field == "significance" and "Never a numbered Decision" in f.detail.replace("never", "Never")
            for f in pdd.findings_for_plan(_plan(tmp_path, never))
        )

    def test_literal_draft_with_a_later_reversal_conditions_stanza_passes(self, tmp_path: Path) -> None:
        text = _draft() + "\n```yaml reversal-conditions\ncondition: x\n```\n"
        assert _lines(_plan(tmp_path, text)) == ["decision_drafts: 1 drafted, 0 findings"]

    @pytest.mark.parametrize("blank", [False, True])
    def test_both_folded_scalar_shapes_report_the_one_envelope_fail(self, tmp_path: Path, blank: bool) -> None:
        sep = "\n" if blank else " "
        text = f"## Decision {{{{N}}}}: Folded{sep}```yaml number: {{{{N}}}} kind: x ```"
        found = pdd.findings_for_plan(_plan(tmp_path, text))
        assert [(f.field, f.detail) for f in found] == [("envelope", "folded scalar: put the draft in a literal (|) block")]

    def test_quoted_scalars_pass(self, tmp_path: Path) -> None:
        for quote in ('"', "'"):
            text = _draft().replace("justification: j.", f"justification: {quote}j.{quote}")
            assert _lines(_plan(tmp_path, text)) == ["decision_drafts: 1 drafted, 0 findings"]

    def test_an_anchor_heading_with_no_title_is_not_a_draft(self, tmp_path: Path) -> None:
        assert _lines(_plan(tmp_path, "see\n## Decision 229:\nthen text")) == ["decision_drafts: none"]

    def test_a_concrete_number_is_one_warn_never_a_number_fail(self, tmp_path: Path) -> None:
        found = pdd.findings_for_plan(_plan(tmp_path, _draft(number="9999", title="Not landed")))
        assert [(f.severity, f.field, f.detail) for f in found] == [
            ("WARN", "number", "assign at implementation: Decision 217 cl.5")
        ]

    def test_findings_accept_str_and_path_and_carry_title_and_field(self, tmp_path: Path) -> None:
        plan = _plan(tmp_path, _draft(kind=False))
        for arg in (plan, str(plan)):
            found = pdd.findings_for_plan(arg)
            assert found[0].title == "Drafted" and found[0].field == "kind"

    def test_a_plan_with_no_drafted_decision_reports_none(self, tmp_path: Path) -> None:
        assert _lines(_plan(tmp_path, "plain text")) == ["decision_drafts: none"]

    def test_an_existing_entry_quoted_in_the_plan_is_not_a_draft(self, tmp_path: Path) -> None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "DECISIONS.md").write_text("## Decision 5: Already here\n", encoding="utf-8")
        text = "## Decision 5: Already here\n\nquoted anchor text\n"
        assert _lines(_plan(tmp_path, text), root=tmp_path) == ["decision_drafts: none"]

    def test_an_unreadable_plan_is_a_finding_not_a_raise(self, tmp_path: Path) -> None:
        found = pdd.findings_for_plan(tmp_path / "missing.yaml")
        assert found[0].severity == "FAIL" and found[0].title == "" and found[0].field == "path"
        bad = tmp_path / "bad.yaml"
        bad.write_text("a: [", encoding="utf-8")
        assert pdd.findings_for_plan(bad)[0].field == "path"
        scalar = tmp_path / "scalar.yaml"
        scalar.write_text("just text", encoding="utf-8")
        assert pdd.findings_for_plan(scalar) == []


class TestSidecar:
    def test_a_sidecar_block_draft_is_found_and_a_quoted_before_heading_is_not(self, tmp_path: Path) -> None:
        quoted = "## Decision 5: Existing\n\nold\n"
        blocks = [{"id": "b1", "file": "docs/DECISIONS.md", "before": quoted, "after": quoted + "\n" + _draft()}]
        _sidecar(tmp_path, blocks)
        assert _lines(_plan(tmp_path, "x"), root=tmp_path) == ["decision_drafts: 1 drafted, 0 findings"]

    def test_a_sidecar_draft_without_kind_fails(self, tmp_path: Path) -> None:
        _sidecar(
            tmp_path,
            [{"id": "b1", "file": "docs/DECISIONS.md", "before": "anchor\n", "after": "anchor\n" + _draft(kind=False)}],
        )
        assert any(f.field == "kind" for f in pdd.findings_for_plan(_plan(tmp_path, "x"), root=tmp_path))

    def test_other_files_are_ignored(self, tmp_path: Path) -> None:
        _sidecar(tmp_path, [{"id": "b1", "file": "docs/other.md", "before": "a\n", "after": "a\n" + _draft(kind=False)}])
        assert _lines(_plan(tmp_path, "x"), root=tmp_path) == ["decision_drafts: none"]

    def test_an_unparseable_sidecar_is_a_finding(self, tmp_path: Path) -> None:
        d = tmp_path / "docs" / "plans" / "approved"
        d.mkdir(parents=True)
        (d / "demo.yaml").write_text("not: a sidecar\n", encoding="utf-8")
        found = pdd.findings_for_plan(_plan(tmp_path, "x"), root=tmp_path)
        assert found[0].field == "sidecar" and found[0].title == ""


class TestDraftFile:
    def test_the_committed_fixture_reports_clean_through_the_cli(self, capsys) -> None:
        assert main(["--decision-draft", str(_FIXTURE)]) == 0
        assert capsys.readouterr().out.strip() == "decision_drafts: 1 drafted, 0 findings"

    def test_a_draft_missing_kind_prints_a_fail_and_still_exits_zero(self, tmp_path: Path, capsys) -> None:
        draft = tmp_path / "d.txt"
        draft.write_text(_draft(kind=False), encoding="utf-8")
        assert main(["--decision-draft", str(draft)]) == 0
        assert "  FAIL {{N}} kind:" in capsys.readouterr().out

    def test_an_unreadable_draft_is_a_finding(self, tmp_path: Path) -> None:
        assert pdd.findings_for_draft(tmp_path / "nope.txt")[0].field == "path"

    def test_plan_cli_appends_the_section_and_exits_zero(self, tmp_path: Path, capsys) -> None:
        assert main(["--plan", str(_plan(tmp_path, "plain text"))]) == 0
        assert "decision_drafts: none" in capsys.readouterr().out


class TestMissingContracts:
    def test_a_missing_contract_set_still_yields_findings_and_never_raises(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(pdd, "ROOT", tmp_path)
        found = pdd.findings_for_plan(_plan(tmp_path, _draft()), root=tmp_path)
        assert all(isinstance(f, pdd.Finding) for f in found)


def _serves_draft(serves: list[str] | None = None, title: str = "Drafted", related: str = "") -> str:
    extra = f"serves: [{', '.join(serves)}]\n" if serves else ""
    rel = f"\n**Related:** {related}\n" if related else ""
    return _draft(extra=extra, title=title) + rel


def _ns_entry(ns_id: str):
    return next(p for p in load_north_star().principles if p.id == ns_id)


def _infos(lines: list[str]) -> list[str]:
    return [x for x in lines if x.lstrip().startswith("INFO")]


class TestDraftsReportInfo:
    def test_one_id_prints_one_info_line(self, tmp_path: Path) -> None:
        entry = _ns_entry("NS.6")
        lines = pdd.report_for_plan(_plan(tmp_path, _serves_draft(["NS.6"])))
        assert lines[0] == "decision_drafts: 1 drafted, 0 findings"
        (info,) = _infos(lines)
        assert f'principle "{entry.principle}"' in info
        assert f'failure_signal "{" ".join(entry.failure_signal.split())}"' in info
        assert f"carried_by [{', '.join(str(n) for n in entry.carried_by)}]" in info
        assert "links none" in info

    def test_related_line_naming_a_carrier_is_a_link(self, tmp_path: Path) -> None:
        carrier = _ns_entry("NS.6").carried_by[0]
        plan = _plan(tmp_path, _serves_draft(["NS.6"], related=f"Decision {carrier}"))
        (info,) = _infos(pdd.report_for_plan(plan))
        assert f"links {carrier} " in info

    def test_amends_edge_into_carried_by_is_a_link(self, tmp_path: Path) -> None:
        carrier = _ns_entry("NS.6").carried_by[0]
        plan = _plan(tmp_path, _serves_draft(["NS.6"], title=f"Drafted (amends Decision {carrier})"))
        (info,) = _infos(pdd.report_for_plan(plan))
        assert f"links {carrier} " in info

    def test_envelope_supersedes_into_carried_by_is_a_link(self, tmp_path: Path) -> None:
        carrier = _ns_entry("NS.6").carried_by[0]
        draft = _draft(extra=f"serves: [NS.6]\nsupersedes: [{carrier}]\n")
        (info,) = _infos(pdd.report_for_plan(_plan(tmp_path, draft)))
        assert f"links {carrier} " in info

    def test_two_ids_print_two_lines_in_envelope_order(self, tmp_path: Path) -> None:
        ids = sorted(load_north_star().ids())[:2]
        lines = _infos(pdd.report_for_plan(_plan(tmp_path, _serves_draft(list(reversed(ids))))))
        assert [x.split()[3].rstrip(":") for x in lines] == list(reversed(ids))

    def test_unknown_id_prints_fail_and_no_info(self, tmp_path: Path) -> None:
        lines = pdd.report_for_plan(_plan(tmp_path, _serves_draft(["NS.9999"])))
        assert any(x.startswith("  FAIL") and "serves" in x for x in lines)
        assert _infos(lines) == []

    def test_draft_without_serves_prints_none(self, tmp_path: Path) -> None:
        assert _infos(pdd.report_for_plan(_plan(tmp_path, _draft()))) == []

    def test_findings_never_include_info(self, tmp_path: Path) -> None:
        assert all(f.severity != "INFO" for f in pdd.findings_for_plan(_plan(tmp_path, _serves_draft(["NS.6"]))))

    def test_missing_north_star_prints_none_without_raising(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(pdd, "ROOT", tmp_path)
        assert _infos(pdd.report_for_plan(_plan(tmp_path, _serves_draft(["NS.6"])), tmp_path)) == []

    def test_sidecar_drafted_decision_prints_info(self, tmp_path: Path) -> None:
        after = _draft(extra="serves: [NS.6]\n")
        _sidecar(tmp_path, [{"id": "b", "file": "docs/DECISIONS.md", "before": "", "after": after}])
        lines = pdd.report_for_plan(_plan(tmp_path, "x"), tmp_path)
        assert len(_infos(lines)) == 1


def _candidate_root(root: Path, threshold: int) -> Path:
    (root / "docs/contracts").mkdir(parents=True)
    rows = [{"number": 1, "live": True, "currency": "current", "amends": []}]
    rows += [{"number": n, "live": True, "currency": "current", "amends": [1]} for n in (2, 3, 4)]
    (root / "docs/decisions-index.json").write_text(json.dumps({"decisions": rows}), encoding="utf-8")
    (root / "docs/contracts/decision-compaction.yaml").write_text(
        f"consolidation:\n  threshold: {threshold}\n", encoding="utf-8"
    )
    return root


def test_amending_a_candidate_root_prints_nomination(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdd, "ROOT", _candidate_root(tmp_path / "fixture", 4))
    plan = _plan(tmp_path, _draft(title="Drafted (amends Decision 1)"))
    lines = pdd.report_for_plan(plan)
    assert lines[0].startswith("decision_drafts: 1 drafted")
    (nomination,) = [x for x in lines if x.lstrip().startswith("NOMINATION")]
    assert "Decision 1, a consolidation candidate: 4 live amenders" in nomination
    assert _infos(lines) == []
    assert not any("NOMINATION" in f.detail for f in pdd.findings_for_plan(plan))


def test_a_sidecar_drafted_amender_prints_nomination_and_a_non_candidate_prints_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pdd, "ROOT", _candidate_root(tmp_path / "fixture", 4))
    after = _draft(title="Drafted (amends Decision 1)")
    _sidecar(tmp_path, [{"id": "b", "file": "docs/DECISIONS.md", "before": "", "after": after}])
    assert any(x.lstrip().startswith("NOMINATION") for x in pdd.report_for_plan(_plan(tmp_path, "x"), tmp_path))
    monkeypatch.setattr(pdd, "ROOT", _candidate_root(tmp_path / "other", 9))
    assert not any(x.lstrip().startswith("NOMINATION") for x in pdd.report_for_plan(_plan(tmp_path, "x"), tmp_path))
    monkeypatch.setattr(pdd, "ROOT", tmp_path / "absent")
    assert not any(x.lstrip().startswith("NOMINATION") for x in pdd.report_for_plan(_plan(tmp_path, "x"), tmp_path))


def test_drafted_entries_and_blind_text(tmp_path: Path) -> None:
    mech = _draft(title="Mech (amends Decision 12)", kind=False).replace(
        "significance:", "kind:\n  value: mechanism\n  justification: only the means,\n    over two lines.\nsignificance:"
    )
    both = _draft(title="Dir", extra="amends: [5]\nsupersedes: [6]\n") + "\n" + mech
    _sidecar(tmp_path, [{"id": "d", "file": "docs/DECISIONS.md", "before": "", "after": both}])
    path = _plan(tmp_path, "x")
    entries = pdd.drafted_entries(path, tmp_path)
    assert [e["kind"] for e in entries] == ["direction", "mechanism"]
    assert entries[0]["amends"] == [5, 6] and entries[1]["amends"] == [12]
    blind = pdd.blind_text(entries[1]["block"])
    assert "kind:" not in blind and "mechanism" not in blind and "over two lines" not in blind
    assert "significance:" in blind and "**Status:** Decided" in blind and "amends" not in blind.split("```")[2]
    assert pdd.blind_text("no fence here") == "no fence here"
    assert pdd.drafted_entries(tmp_path / "missing.yaml", tmp_path) == []


def test_drafted_entries_of_a_non_mapping_plan(tmp_path: Path) -> None:
    path = tmp_path / "PLAN-list.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    assert pdd.drafted_entries(path, tmp_path) == []


def test_drafted_titles_match_the_scan_count_and_order(tmp_path: Path) -> None:
    _sidecar(tmp_path, [{"id": "d", "file": "docs/DECISIONS.md", "before": "", "after": _draft(title="Side")}])
    path = _plan(tmp_path, _draft(title="Text") + "\n" + _draft(title="Text2"))
    assert pdd.drafted_titles(path, tmp_path) == ["Text", "Text2", "Side"]
    assert len(pdd.drafted_titles(path, tmp_path)) == pdd._scan_plan(path, tmp_path)[0]


def test_drafted_titles_is_total(tmp_path: Path) -> None:
    assert pdd.drafted_titles(tmp_path / "missing.yaml", tmp_path) == []
    scalar = tmp_path / "PLAN-list.yaml"
    scalar.write_text("- a\n", encoding="utf-8")
    assert pdd.drafted_titles(scalar, tmp_path) == []


def test_a_title_drafted_in_text_and_sidecar_appears_twice(tmp_path: Path) -> None:
    _sidecar(tmp_path, [{"id": "d", "file": "docs/DECISIONS.md", "before": "", "after": _draft(title="Same")}])
    path = _plan(tmp_path, _draft(title="Same"))
    assert pdd.drafted_titles(path, tmp_path) == ["Same", "Same"]


_DELEGATES = (
    "delegates:\n  - key: docs/contracts/decision-entry.yaml#size_governance.measured_size_norm.cap_bytes\n"
    "    change_rule: contract_amendment\n    bounds: {min: 4096, max: 5120}\n"
)


def _fields(text: str, tmp_path: Path, slug: str = "demo") -> list[tuple[str, str, str]]:
    return [(f.severity, f.field, f.detail) for f in pdd.findings_for_plan(_plan(tmp_path, text, slug=slug))]


class TestOptionsSizeDelegatesAndPlan:
    """The planning-time report applies the gate's delegates, plan, options and measured-cap rules."""

    def test_a_draft_without_an_options_block_fails(self, tmp_path: Path) -> None:
        found = _fields(_draft().replace(_OPTIONS, ""), tmp_path)
        assert [f[1] for f in found] == ["size"] and "carries no **Options considered:**" in found[0][2]

    def test_a_draft_over_the_measured_cap_fails(self, tmp_path: Path) -> None:
        text = _draft().replace("**Status:** Decided\n", "**Status:** Decided\n\n" + "x" * 5200 + "\n")
        assert any("measured without its envelope" in f[2] for f in _fields(text, tmp_path))

    def test_a_draft_listed_in_the_exemptions_for_its_plan_passes_without_options(self, tmp_path: Path, monkeypatch) -> None:
        listing = tmp_path / "exempt.yaml"
        listing.write_text("exempt_sidecars:\n- {slug: demo, title: Drafted}\n", encoding="utf-8")
        monkeypatch.setattr(pdd, "_EXEMPTIONS", listing.as_posix())
        text = _draft().replace(_OPTIONS, "")
        assert _fields(text, tmp_path) == []
        assert _fields(text, tmp_path, slug="other")

    def test_a_malformed_delegates_fails_and_a_well_formed_one_resolving_at_head_passes(self, tmp_path: Path) -> None:
        assert any(f[1] == "delegates" for f in _fields(_draft(extra="delegates: []\n"), tmp_path))
        assert _fields(_draft(extra=_DELEGATES), tmp_path) == []

    def test_a_key_that_does_not_resolve_is_a_fail_unless_the_sidecar_binds_its_file(self, tmp_path: Path) -> None:
        missing = _DELEGATES.replace("measured_size_norm.cap_bytes", "nope.cap_bytes")
        assert any(f[0] == "FAIL" and "does not resolve" in f[2] for f in _fields(_draft(extra=missing), tmp_path))
        path = _plan(tmp_path, "x", slug="bound")
        _sidecar(
            tmp_path,
            [{"id": "c", "file": "docs/contracts/decision-entry.yaml", "before": "a\n", "after": "b\n"}]
            + [{"id": "d", "file": "docs/DECISIONS.md", "before": "", "after": _draft(extra=missing)}],
            slug="bound",
        )
        found = [(f.severity, f.field) for f in pdd.findings_for_plan(path, tmp_path)]
        assert ("WARN", "delegates") in found and ("FAIL", "delegates") not in found

    def test_a_plan_field_fails_when_malformed_and_warns_when_the_plan_is_unknown(self, tmp_path: Path) -> None:
        assert any(f[:2] == ("FAIL", "plan") for f in _fields(_draft(extra="plan: Not A Slug\n"), tmp_path))
        assert any(f[:2] == ("WARN", "plan") for f in _fields(_draft(extra="plan: no-such-plan\n"), tmp_path))
        assert not any(f[1] == "plan" for f in _fields(_draft(extra="plan: decision-delegation-and-cap-measure\n"), tmp_path))


class TestSizeFindingsDegradeQuietly:
    def test_a_missing_exemptions_file_or_contract_adds_no_exemption_and_no_crash(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(pdd, "_EXEMPTIONS", (tmp_path / "absent.yaml").as_posix())
        assert [f.field for f in pdd.findings_for_plan(_plan(tmp_path, _draft().replace(_OPTIONS, "")))] == ["size"]
        monkeypatch.setattr(pdd, "_ENTRY_CONTRACT", "docs/contracts/does-not-exist.yaml")
        assert pdd._size_findings(_draft(), "demo") == []

    def test_a_contract_that_no_longer_parses_skips_the_size_legs(self, tmp_path: Path, monkeypatch) -> None:
        broken = tmp_path / "broken.yaml"
        broken.write_text("size_governance: [", encoding="utf-8")
        monkeypatch.setattr(pdd, "_ENTRY_CONTRACT", broken.as_posix())
        assert pdd._size_findings(_draft(), "demo") == []

    @pytest.mark.parametrize("text", ["a: [", "other: 1\n"])
    def test_a_key_in_an_unparseable_or_keyless_file_does_not_resolve(self, tmp_path: Path, monkeypatch, text: str) -> None:
        (tmp_path / "k.yaml").write_text(text, encoding="utf-8")
        monkeypatch.setattr(pdd, "ROOT", tmp_path)
        assert pdd._resolves_at_head("k.yaml", "a.b") is False
        assert pdd._resolves_at_head("absent.yaml", "a") is False
