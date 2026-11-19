"""Tests for scripts/checks/decisions/append_only_census.py (rec-5266's acceptance file).

TestReplay builds throwaway git histories with ANCHOR and SELF_TEST_FIXTURE monkeypatched into them. Every history
carries one acknowledged, annotated fixture rewrite (Decision 2) so the self-test holds. TestWiring pins the workflow,
taxonomy and ledger wiring.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.checks.decisions import append_only_census as census_mod

_ROOT = Path(__file__).resolve().parents[3]

_D1 = """## Decision 1: First (Decided)

**Status:** Decided
**Date:** 2026-01-01

**Decision:**
Ruling one.

---
"""

_D2 = """## Decision 2: Second (Decided)

**Status:** Decided
**Date:** 2026-01-01

**Decision:**
Ruling two, original.

---
"""

_STUB = """## Decision 2: Second (Decided)

**Status:** Superseded
**Date:** 2026-11-19

**Decision:** Superseded by Decision 9.

**Superseded by: Decision 9**

---
"""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


class _Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        _git(root, "init", "-q", "-b", "main")
        _git(root, "config", "user.email", "t@example.com")
        _git(root, "config", "user.name", "T")
        _git(root, "config", "commit.gpgsign", "false")

    def commit(self, live: str, archive: str = "", subject: str = "change", ledger: str | None = None) -> str:
        docs = self.root / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "DECISIONS.md").write_text(live, encoding="utf-8")
        (docs / "DECISIONS_ARCHIVE.md").write_text(archive, encoding="utf-8")
        if ledger is not None:
            (self.root / "config" / "agent").mkdir(parents=True, exist_ok=True)
            (self.root / "config" / "agent" / "decision_census.yaml").write_text(ledger, encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", subject)
        return _git(self.root, "rev-parse", "HEAD")


def _annotation(merge: str) -> str:
    return f"> **Update (2026-11-19, rec-1):** Merge {merge[:8]} rewrote this entry in place; the shortened text stands.\n"


def _ledger(*rows: tuple[str, int, str]) -> str:
    items = [{"merge": m, "decision": d, "digest": g} for m, d, g in rows]
    return yaml.safe_dump({"acknowledged": items})


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """anchor -> fixture rewrite of Decision 2 (#7) -> annotation on Decision 2; returns the repo and shas."""
    repo = _Repo(tmp_path / "repo")
    anchor = repo.commit(_D1 + "\n" + _D2, subject="anchor")
    rewrite = repo.commit(_D1 + "\n" + _D2.replace("original", "REWRITTEN"), subject="rewrite two (#7)")
    monkeypatch.setattr(census_mod, "ANCHOR", anchor)
    monkeypatch.setattr(census_mod, "SELF_TEST_FIXTURE", (rewrite, (2,)))
    monkeypatch.setattr(census_mod._common, "ROOT", repo.root)
    annotated = _D2.replace("original", "REWRITTEN") + _annotation(rewrite)
    return {"repo": repo, "anchor": anchor, "rewrite": rewrite, "annotated": _D1 + "\n" + annotated}


def _digest(history: dict) -> str:
    return census_mod.census(history["repo"].root, "HEAD").findings[0].digest


def _acknowledge(history: dict, digest: str | None = None) -> str:
    """Land the annotation and the ledger row; returns the head sha."""
    digest = digest or _digest(history)
    return history["repo"].commit(history["annotated"], ledger=_ledger((history["rewrite"], 2, digest)), subject="annotate")


class TestReplay:
    def test_acknowledged_annotated_fixture_gives_open_zero_and_exit_zero(self, history: dict, capsys) -> None:
        _acknowledge(history)
        assert census_mod.main([]) == 0
        out = capsys.readouterr().out
        assert "acknowledged=1 open=0 self_test=ok" in out

    def test_append_only_history_gives_no_findings_beyond_the_fixture(self, history: dict) -> None:
        _acknowledge(history)
        history["repo"].commit(history["annotated"] + "\n> **Update (2026-11-20):** later note.\n", subject="append")
        result = census_mod.census(history["repo"].root)
        assert [f.merge for f in result.findings] == [history["rewrite"]]
        assert result.open == [] and result.defects == []

    def test_rewrite_of_a_line_added_by_an_earlier_merge_is_flagged_at_the_rewriting_merge(self, history: dict) -> None:
        repo = history["repo"]
        _acknowledge(history)
        added = repo.commit(history["annotated"].replace("Ruling one.", "Ruling one.\nLine added later."), subject="add line")
        rewriting = repo.commit(
            history["annotated"].replace("Ruling one.", "Ruling one.\nLine REWRITTEN later."), subject="rewrite it (#42)"
        )
        result = census_mod.census(repo.root)
        flagged = list(result.open)
        assert [(f.merge, f.decision, f.pr) for f in flagged] == [(rewriting, 1, 42)]
        assert flagged[0].digest and flagged[0].line == "Line added later."
        assert added != rewriting
        # A comparison of the anchor with head alone does not see it: the line did not exist at the anchor.
        from scripts.checks.decisions import _baseline
        from scripts.checks.decisions.append_only_rule import compare

        base = _baseline.decision_bodies_at(repo.root, history["anchor"])
        cur = _baseline.decision_bodies_at(repo.root, "HEAD")
        assert not [f for f in compare(base["docs/DECISIONS.md"], {}, cur["docs/DECISIONS.md"], {}) if f[0] == 1]
        assert census_mod.main([]) == 1

    def test_row_without_its_annotation_exits_one(self, history: dict) -> None:
        digest = _digest(history)
        history["repo"].commit(
            _D1 + "\n" + _D2.replace("original", "REWRITTEN"), ledger=_ledger((history["rewrite"], 2, digest))
        )
        result = census_mod.census(history["repo"].root)
        assert any("annotation never landed" in d for d in result.defects)
        assert census_mod.main([]) == 1

    def test_row_with_a_wrong_digest_is_stale_with_the_new_digest(self, history: dict) -> None:
        real = _digest(history)
        _acknowledge(history, digest="0" * 16)
        result = census_mod.census(history["repo"].root)
        assert any(f"census now computes {real}" in d for d in result.defects)
        assert len(result.open) == 1
        assert census_mod.main([]) == 1

    def test_row_naming_an_unflagged_merge_exits_one(self, history: dict) -> None:
        digest = _digest(history)
        history["repo"].commit(
            history["annotated"],
            ledger=_ledger((history["rewrite"], 2, digest), (history["anchor"], 1, "1" * 16)),
        )
        result = census_mod.census(history["repo"].root)
        assert any("matches no finding" in d for d in result.defects)
        assert census_mod.main([]) == 1

    def test_anchor_that_is_not_an_ancestor_exits_two(self, history: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = history["repo"]
        _acknowledge(history)
        _git(repo.root, "checkout", "-q", "-b", "side", history["anchor"])
        side = repo.commit(_D1 + "\n" + _D2 + "\nside\n", subject="side")
        _git(repo.root, "checkout", "-q", "main")
        monkeypatch.setattr(census_mod, "ANCHOR", side)
        assert census_mod.main([]) == 2

    def test_unresolvable_anchor_exits_two(self, history: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        _acknowledge(history)
        monkeypatch.setattr(census_mod, "ANCHOR", "f" * 40)
        assert census_mod.main([]) == 2

    def test_parent_read_that_fails_exits_two(self, history: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        _acknowledge(history)
        monkeypatch.setattr(census_mod._baseline, "decision_bodies_at", lambda root, rev: None)
        assert census_mod.main([]) == 2

    def test_annotation_found_only_in_the_archive_still_acknowledges(self, history: dict) -> None:
        digest = _digest(history)
        moved = _D2.replace("original", "REWRITTEN").replace("(Decided)", "(Superseded, archived)").replace(
            "**Status:** Decided", "**Status:** Superseded -- archived"
        ) + _annotation(history["rewrite"])
        history["repo"].commit(
            _D1 + "\n", archive=moved, ledger=_ledger((history["rewrite"], 2, digest)), subject="archive move"
        )
        assert census_mod.main([]) == 0

    def test_acknowledged_decision_later_compacted_to_a_stub_still_passes(self, history: dict) -> None:
        _acknowledge(history)
        history["repo"].commit(_D1 + "\n" + _STUB, subject="compact")
        assert census_mod.main([]) == 0

    def test_compaction_whose_body_never_carried_the_annotation_exits_one(self, history: dict) -> None:
        digest = _digest(history)
        history["repo"].commit(_D1 + "\n" + _STUB, ledger=_ledger((history["rewrite"], 2, digest)), subject="compact")
        assert census_mod.main([]) == 1

    def test_self_test_exits_two_when_the_fixture_is_not_flagged(self, history: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        _acknowledge(history)
        monkeypatch.setattr(census_mod, "SELF_TEST_FIXTURE", (history["anchor"], (2,)))
        assert census_mod.main([]) == 2

    def test_summary_file_receives_the_census_line(self, history: dict, tmp_path: Path) -> None:
        _acknowledge(history)
        summary = tmp_path / "summary.md"
        assert census_mod.main(["--summary", str(summary)]) == 0
        assert "CENSUS merges=" in summary.read_text(encoding="utf-8")

    def test_head_option_replays_an_earlier_commit(self, history: dict) -> None:
        _acknowledge(history)
        assert census_mod.main(["--head", history["rewrite"]]) == 1  # ledger not yet landed there


class TestWiring:
    def _workflow(self) -> dict:
        return yaml.safe_load((_ROOT / ".github/workflows/decision-census.yml").read_text(encoding="utf-8"))

    def test_triggers_and_paths(self) -> None:
        wf = self._workflow()
        triggers = wf.get("on", wf.get(True))
        assert set(triggers) == {"push", "schedule", "workflow_dispatch"}
        paths = set(triggers["push"]["paths"])
        required = {
            "docs/DECISIONS.md",
            "docs/DECISIONS_ARCHIVE.md",
            "scripts/decisions_md.py",
            "scripts/preflight/decision_conditions.py",
            "scripts/checks/decisions/append_only_rule.py",
            "scripts/checks/decisions/_baseline.py",
            "scripts/checks/_common.py",
            "scripts/checks/registry.py",
            "scripts/checks/decisions/append_only_census.py",
            "scripts/checks/decisions/append_only_census_recs.py",
            "config/agent/decision_census.yaml",
            "requirements.txt",
            ".github/workflows/decision-census.yml",
        }
        assert required <= paths

    def test_cron_is_a_daily_shape_the_liveness_sensor_accepts(self) -> None:
        from scripts.convergence_health.sensor_liveness import cron_period_seconds

        wf = self._workflow()
        cron = (wf.get("on") or wf.get(True))["schedule"][0]["cron"]
        assert cron.endswith("*/24 * * *")
        assert cron_period_seconds(cron) == 86400

    def test_only_file_recs_holds_a_credential_and_nothing_gates(self) -> None:
        wf = self._workflow()
        assert wf["name"] == "decision-census"
        assert "pull_request" not in (wf.get("on") or wf.get(True))
        assert wf["permissions"] == {"contents": "read"}
        job = wf["jobs"]["census"]
        assert "permissions" not in job
        assert not any("configure-aws-credentials" in s.get("uses", "") for s in job["steps"])
        for name, other in wf["jobs"].items():
            if name != "file-recs":
                assert "permissions" not in other, name
        filer = wf["jobs"]["file-recs"]
        assert filer["permissions"] == {"id-token": "write", "contents": "read"}
        assert filer["if"] == "github.ref == 'refs/heads/main'"
        assert filer["needs"] == "census"
        text = (_ROOT / ".github/workflows/decision-census.yml").read_text(encoding="utf-8")
        assert text.replace("no id-token", "").count("id-token") == 1
        steps = job["steps"]
        checkout = next(s for s in steps if s["uses"].startswith("actions/checkout@"))
        assert checkout["with"]["fetch-depth"] == 0 and checkout["with"]["persist-credentials"] is False
        runs = [s["run"] for s in steps if "run" in s]
        assert any("scripts.checks.decisions.append_only_census" in r and "--summary" in r for r in runs)
        for jb in wf["jobs"].values():
            for step in jb["steps"]:
                if "uses" in step and not step["uses"].startswith("./"):
                    assert len(step["uses"].split("@")[1].split(" ")[0]) == 40, f"unpinned action {step['uses']}"

    def test_taxonomy_rows(self) -> None:
        tax = yaml.safe_load((_ROOT / "config/ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
        row = tax["workflows"]["decision-census"]
        assert row["tier"] == "not_a_gate" and row["ci_rca"] == "excluded"
        assert "validate_decision_append_only" in tax["function_to_category"]

    def test_ledger_holds_both_1568_rows(self) -> None:
        ledger = yaml.safe_load((_ROOT / "config/agent/decision_census.yaml").read_text(encoding="utf-8"))
        rows = {(r["merge"][:8], r["decision"]) for r in ledger["acknowledged"]}
        assert {("660d5b54", 65), ("660d5b54", 210)} <= rows


class TestEdges:
    def test_unresolvable_head_exits_two(self, history: dict) -> None:
        assert census_mod.main(["--head", "no-such-rev"]) == 2

    def test_malformed_ledger_exits_two(self, history: dict) -> None:
        history["repo"].commit(history["annotated"], ledger="acknowledged: [{merge: x}]\n")
        assert census_mod.main([]) == 2

    def test_absent_ledger_is_empty_and_leaves_the_finding_open(self, history: dict) -> None:
        result = census_mod.census(history["repo"].root)
        assert len(result.open) == 1 and result.defects == []

    def test_git_failure_inside_the_replay_exits_two(self, history: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        real = census_mod._common.run

        def failing(cmd, **kw):
            if cmd[:2] == ["git", "rev-parse"] and cmd[-1].endswith("^"):
                return subprocess.CompletedProcess(cmd, 128, "", "boom")
            return real(cmd, **kw)

        monkeypatch.setattr(census_mod._common, "run", failing)
        assert census_mod.main([]) == 2

    def test_one_decision_with_two_kinds_is_one_finding_listing_both(self, history: dict) -> None:
        stanza = "```yaml reversal-conditions\ndecision: 1\nreview_by: 2027-01-01\n```\n"
        base = _D1.replace("---\n", stanza + "\n---\n")
        history["repo"].commit(base + "\n" + history["annotated"].split("\n\n", 1)[1][0:0] + _D2, subject="add stanza")
        broken = base.replace("Ruling one.", "REWRITTEN").replace(stanza, "")
        history["repo"].commit(broken + "\n" + _D2, subject="two kinds (#9)")
        found = [f for f in census_mod.census(history["repo"].root).findings if f.pr == 9]
        assert len(found) == 1 and found[0].kinds == ["embedding", "stanza"]


class TestResult:
    def test_result_groups_open_findings_by_merge_and_lists_the_replay(self, history: dict, tmp_path: Path) -> None:
        out = tmp_path / "r.json"
        assert census_mod.main(["--result", str(out)]) == 1
        data = json.loads(out.read_text(encoding="utf-8"))
        assert set(data) == {"schema", "head", "merges", "replayed", "acknowledged", "open", "ledger_defects"}
        assert data["schema"] == 1 and data["acknowledged"] == 0 and data["ledger_defects"] == []
        assert data["head"] == history["rewrite"] and data["replayed"] == [history["rewrite"]]
        assert [(e["merge"], e["pr"]) for e in data["open"]] == [(history["rewrite"], 7)]
        assert [d["decision"] for d in data["open"][0]["decisions"]] == [2]
        assert set(data["open"][0]["decisions"][0]) == {"decision", "digest", "kinds", "line"}

    def test_acknowledged_merge_is_replayed_but_not_open(self, history: dict, tmp_path: Path) -> None:
        _acknowledge(history)
        out = tmp_path / "r.json"
        assert census_mod.main(["--result", str(out)]) == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["open"] == [] and data["acknowledged"] == 1 and history["rewrite"] in data["replayed"]

    def test_ledger_defect_is_listed(self, history: dict, tmp_path: Path) -> None:
        history["repo"].commit(history["annotated"], ledger=_ledger((history["rewrite"], 2, "0" * 16)))
        out = tmp_path / "r.json"
        assert census_mod.main(["--result", str(out)]) == 1
        assert any("stale digest" in d for d in json.loads(out.read_text(encoding="utf-8"))["ledger_defects"])

    def test_a_later_merge_gets_its_own_entry_after_the_fixture_merge(self, history: dict, tmp_path: Path) -> None:
        repo = history["repo"]
        both = _D1.replace("Ruling one.", "REWRITTEN ONE") + "\n" + _D2.replace("original", "REWRITTEN")
        merge = repo.commit(both, subject="both (#8)")
        out = tmp_path / "r.json"
        census_mod.main(["--result", str(out)])
        entry = next(e for e in json.loads(out.read_text(encoding="utf-8"))["open"] if e["merge"] == merge)
        assert sorted(d["decision"] for d in entry["decisions"]) == [1]
        assert len(entry["decisions"]) == 1

    def test_no_file_on_exit_two(self, history: dict, tmp_path: Path) -> None:
        out = tmp_path / "r.json"
        assert census_mod.main(["--head", "no-such-rev", "--result", str(out)]) == 2
        assert not out.exists()

    def test_unwritable_result_path_exits_two(self, history: dict, tmp_path: Path, capsys) -> None:
        assert census_mod.main(["--result", str(tmp_path / "no-such-dir" / "r.json")]) == 2
        assert "cannot write result" in capsys.readouterr().out
