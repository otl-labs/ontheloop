"""Mirror tests for scripts/checks/contracts/validate_glossary.py (Decision 240), in a temporary git repository."""

from __future__ import annotations

import copy
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from scripts.checks import registry
from scripts.checks.contracts import validate_glossary as vg

_REPO = Path(__file__).resolve().parents[3]
_OLD = "The platform runs old."
_PLAN_HEADER = "## Decision NNN: The platform account moves (amends Decision 106) (Decided)"
_PLAN_NOTE = "> **Amended by Decision NNN (2026-MM-DD):** the platform moved; Decision NNN holds it."
_PLAN_UPDATE = "> **Update (DATE):** the product ships."
_PLAN_ROW = "created YYYY-MM-DD for the platform"
_PLAN_BRACED = "Decision {{N}} ({{DATE}}): the platform holds."
_HEADER = "## Decision 240: One name per thing"
_BASE_FILES = {
    "docs/contracts/other.yaml": "key: value\n",
    "docs/plans/PLAN-old.yaml": (
        f"slug: old\nnote: >-\n  {_OLD}\napproved: |-\n"
        + "".join(f"  {line}\n" for line in (_PLAN_HEADER, _PLAN_NOTE, _PLAN_UPDATE, _PLAN_ROW, _PLAN_BRACED))
    ),
    "CLAUDE.md": "# root\n",
    ".claude/skills/x/SKILL.md": "# skill\n",
    ".claude/agents/scheduled/x.md": "# agent\n",
    "docs/audit-prompts/AUDIT-x.md": "# audit\n",
    "tests/helper.py": "x = 1\n",
}
_ENTRY = f"{_HEADER}\n\n**Decision:** names.\n"
_NOTE_DATE = "2026-11-02"


def _git(root: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    done = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, env={**env, "PATH": "/usr/bin:/bin"}
    )
    return done.stdout.strip()


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _older(root: Path, *, noted: bool = True) -> str:
    note = yaml.safe_load((root / "docs/contracts/glossary.yaml").read_text(encoding="utf-8"))["read_as"]["note"]
    body = "## Decision 27: Older (Decided)\n\n**Decision:** older.\n\n"
    return "\n" + body + (note.strip().replace("DATE", _NOTE_DATE) + "\n\n" if noted else "") + "---\n"


def _build(root: Path, *, entry_on_base: bool) -> None:
    _git(root, "init", "-q", "-b", "main")
    for rel in ("docs/contracts/glossary.yaml", "docs/contracts/instruction-architecture.yaml", "docs/CLAUDE.md"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_REPO / rel, root / rel)
    glossary = root / "docs/contracts/glossary.yaml"
    glossary.write_text(
        re.sub(r"(?m)^  noted_decisions: \[.*\]$", "  noted_decisions: [27]", glossary.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    for rel, text in _BASE_FILES.items():
        _write(root, rel, text)
    _write(root, "docs/DECISIONS.md", "# Decisions\n\n" + (_ENTRY + "\n---\n" if entry_on_base else "") + _older(root))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "HEAD"))
    _git(root, "checkout", "-q", "-b", "feature")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    _build(tmp_path, entry_on_base=True)
    return tmp_path


def _run(root: Path) -> list[str]:
    failed: list[str] = []
    vg.validate_glossary(failed, root)
    return failed


def _append(root: Path, rel: str, line: str) -> None:
    path = root / rel
    path.write_text(path.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")


def _contract(root: Path) -> dict[str, Any]:
    return yaml.safe_load((root / "docs/contracts/glossary.yaml").read_text(encoding="utf-8"))


def _save(root: Path, data: dict[str, Any]) -> None:
    (root / "docs/contracts/glossary.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


_PASSING = [
    ("docs/contracts/other.yaml", "see agent-platform-data-lake"),
    ("docs/contracts/other.yaml", "see agent-platform"),
    ("docs/contracts/other.yaml", "the PLATFORM axis"),
    ("docs/contracts/other.yaml", "platform: value"),
    ("docs/contracts/other.yaml", "see `platform` here"),
    ("docs/contracts/other.yaml", 'see "product" here'),
    ("docs/contracts/other.yaml", "an adopter product ships"),
    ("docs/contracts/other.yaml", "owner='platform'"),
    ("docs/contracts/other.yaml", "owner: platform"),
    ("docs/contracts/other.yaml", "owner=platform"),
    ("docs/DECISIONS.md", _OLD),
    ("docs/DECISIONS.md", _PLAN_HEADER.replace("NNN", "241")),
    ("docs/DECISIONS.md", _PLAN_NOTE.replace("NNN", "241").replace("2026-MM-DD", "2026-11-02")),
    ("docs/DECISIONS.md", _PLAN_UPDATE.replace("DATE", "2026-11-02")),
    ("docs/DECISIONS.md", _PLAN_ROW.replace("YYYY-MM-DD", "2026-11-02")),
    ("docs/DECISIONS.md", _PLAN_BRACED.replace("{{N}}", "241").replace("{{DATE}}", "2026-11-02")),
    ("tests/helper.py", "platform = 'unqualified word in a test'"),
]
_FAILING = [
    ("docs/contracts/other.yaml", "The platforms differ."),
    ("docs/contracts/other.yaml", "Product's scope"),
    ("docs/contracts/other.yaml", 'a "quoted span of far more than six words here" platform'),
    ("docs/contracts/other.yaml", "a platform-only repository"),
    ("docs/contracts/other.yaml", "the product-boundary"),
    ("docs/plans/PLAN-old.yaml", f"  {_OLD}"),
    ("CLAUDE.md", "The platform runs nightly."),
    (".claude/skills/x/SKILL.md", "The platform runs nightly."),
    (".claude/agents/scheduled/x.md", "The product runs nightly."),
    ("docs/audit-prompts/AUDIT-x.md", "The platform runs nightly."),
    ("docs/DECISIONS.md", "Platform: the runner stalls."),
    ("docs/DECISIONS.md", _PLAN_HEADER.replace("NNN", "241").replace("106", "107")),
    ("docs/DECISIONS.md", _PLAN_HEADER.replace("NNN", "241") + " Later."),
    ("docs/DECISIONS.md", _PLAN_HEADER.replace("NNN", "two hundred")),
    ("docs/DECISIONS.md", _PLAN_NOTE.replace("NNN", "241", 1).replace("NNN", "242").replace("MM-DD", "11-02")),
    ("docs/DECISIONS.md", _PLAN_NOTE.replace("NNN", "241").replace("2026-MM-DD", "2025-11-02")),
    ("docs/DECISIONS.md", _PLAN_UPDATE.replace("DATE", "soon")),
    ("docs/DECISIONS.md", _PLAN_HEADER.replace("NNN", "\u0662\u0664\u0661")),
    ("docs/DECISIONS.md", _PLAN_ROW.replace("YYYY-MM-DD", "26-11-02")),
    ("docs/DECISIONS.md", _PLAN_BRACED.replace("{{N}}", "241").replace("DATE", "2026-11-02")),
]


@pytest.mark.parametrize(("rel", "line"), _PASSING)
def test_passes_exempt_added_line(repo: Path, capsys: pytest.CaptureFixture[str], rel: str, line: str) -> None:
    _append(repo, rel, line)
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == [] and "PASS" in out, out


@pytest.mark.parametrize(("rel", "line"), _FAILING)
def test_flags_unqualified_use_by_path(repo: Path, capsys: pytest.CaptureFixture[str], rel: str, line: str) -> None:
    _append(repo, rel, line)
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == ["Glossary"] and f"FAIL: {rel}:" in out, out


def test_flags_unqualified_word_in_added_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _append(repo, "docs/contracts/other.yaml", "The platform runs nightly.")
    failed = _run(repo)
    out = capsys.readouterr().out
    say = _contract(repo)["relative_words"]["platform"]["say_instead"]
    assert failed == ["Glossary"]
    assert f'docs/contracts/other.yaml:2: unqualified "platform" -- say instead: {say}' in out


def test_ignores_text_already_on_the_base(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _append(repo, "docs/audit-prompts/AUDIT-x.md", "the platform on the base")
    _git(repo, "commit", "-aqm", "older text")
    _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "HEAD"))
    _append(repo, "docs/audit-prompts/AUDIT-x.md", "an unrelated clean line")
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == [] and "PASS" in out


def test_moved_line_is_not_a_use(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _append(repo, "docs/audit-prompts/AUDIT-x.md", "moved: the platform line")
    _git(repo, "commit", "-aqm", "older text")
    _git(repo, "update-ref", "refs/remotes/origin/main", _git(repo, "rev-parse", "HEAD"))
    (repo / "docs/audit-prompts/AUDIT-x.md").write_text("moved: the platform line\n# audit\n", encoding="utf-8")
    failed = _run(repo)
    assert failed == []


def test_untracked_governed_file_is_linted(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, "docs/contracts/new.yaml", "text: the platform runs\n")
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == ["Glossary"] and "docs/contracts/new.yaml:1" in out


def test_defining_entry_exempt_when_its_header_is_added(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    _build(tmp_path, entry_on_base=False)
    _append(tmp_path, "docs/DECISIONS.md", _ENTRY + "The platform is named here.\n---")
    failed = _run(tmp_path)
    out = capsys.readouterr().out
    assert failed == [] and "PASS" in out, out


def test_line_added_inside_a_defining_entry_already_on_the_base_is_linted(repo: Path, capsys) -> None:
    path = repo / "docs/DECISIONS.md"
    path.write_text(path.read_text(encoding="utf-8").replace("names.\n", "names.\nA later addition: the platform stalls.\n"))
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == ["Glossary"] and "docs/DECISIONS.md:6" in out, out


def test_decision_held_only_in_the_archive_has_no_defining_range(repo: Path, capsys) -> None:
    (repo / "docs/DECISIONS.md").unlink()
    _write(repo, "docs/DECISIONS_ARCHIVE.md", _ENTRY + _older(repo))
    failed = _run(repo)
    assert failed == [] and "PASS" in capsys.readouterr().out


def test_no_base_skips_lint_only(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    _append(repo, "docs/audit-prompts/AUDIT-x.md", "The platform runs nightly.")
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == [] and "SKIP" in out
    data = _contract(repo)
    del data["relative_words"]["product"]
    _save(repo, data)
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == ["Glossary"] and "SKIP" in out


def _extra_key(d: dict[str, Any]) -> None:
    d["extra"] = 1


def _wrong_subject(d: dict[str, Any]) -> None:
    d["contract"]["subject"] = "other"


def _wrong_check(d: dict[str, Any]) -> None:
    d["contract"]["evaluator"]["check"] = "other"


def _missing_decision(d: dict[str, Any]) -> None:
    d["contract"]["ratified_via"] = "Decision 9999 / PLAN-x"


def _fifteenth_term(d: dict[str, Any]) -> None:
    d["terms"]["extra_term"] = {"written": "extra", "reserved_meaning": "m", "do_not_use_for": "n"}


def _duplicate_written(d: dict[str, Any]) -> None:
    d["terms"]["core"]["written"] = d["terms"]["release"]["written"].upper()


def _no_say_instead(d: dict[str, Any]) -> None:
    del d["relative_words"]["product"]["say_instead"]


def _no_identifiers(d: dict[str, Any]) -> None:
    d["relative_words"]["identifiers"] = ""


def _row_without_reads_as(d: dict[str, Any]) -> None:
    del d["read_as"]["rows"][0]["reads_as"]


def _duplicate_phrase(d: dict[str, Any]) -> None:
    d["read_as"]["rows"][1]["phrase"] = d["read_as"]["rows"][0]["phrase"]


def _no_rows(d: dict[str, Any]) -> None:
    d["read_as"]["rows"] = []
    d["read_as"]["rule"] = ""


def _no_do_not_use_for(d: dict[str, Any]) -> None:
    del d["terms"]["core"]["do_not_use_for"]


def _lost_identifier(d: dict[str, Any]) -> None:
    d["relative_words"]["identifiers"] = d["relative_words"]["identifiers"].replace("platform-security-*", "x")


_DEFECTS: dict[str, Callable[[dict[str, Any]], None]] = {
    "extra_key": _extra_key,
    "wrong_subject": _wrong_subject,
    "wrong_check": _wrong_check,
    "missing_decision": _missing_decision,
    "fifteenth_term": _fifteenth_term,
    "duplicate_written": _duplicate_written,
    "no_say_instead": _no_say_instead,
    "no_identifiers": _no_identifiers,
    "row_without_reads_as": _row_without_reads_as,
    "duplicate_phrase": _duplicate_phrase,
    "no_rows": _no_rows,
    "no_do_not_use_for": _no_do_not_use_for,
    "lost_identifier": _lost_identifier,
}


@pytest.mark.parametrize("defect", _DEFECTS)
def test_rejects_shape_defect(repo: Path, capsys: pytest.CaptureFixture[str], defect: str) -> None:
    data = _contract(repo)
    _DEFECTS[defect](data)
    _save(repo, data)
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == ["Glossary"] and "FAIL" in out, out


def test_rejects_term_missing_do_not_use_for(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = _contract(repo)
    _no_do_not_use_for(data)
    _save(repo, data)
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == ["Glossary"] and "term core: do_not_use_for is missing or empty" in out


def test_rejects_missing_ambient_pointer(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "docs/CLAUDE.md").write_text("# no pointer\n", encoding="utf-8")
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == ["Glossary"] and "ambient pointer" in out


def _drop_entry(g: dict[str, Any]) -> dict[str, Any]:
    return {}


def _other_terms(g: dict[str, Any]) -> dict[str, Any]:
    return {**g, "terms": {"only": {}}}


def _other_say_instead(g: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(g)
    out["relative_words"]["platform"] = "different"
    return out


@pytest.mark.parametrize("mutate", [_drop_entry, _other_terms, _other_say_instead])
def test_guidance_mismatch_fails(repo: Path, capsys, monkeypatch: pytest.MonkeyPatch, mutate: Callable[..., Any]) -> None:
    real = vg.get_rec_write_guidance

    def patched(**kwargs: Any) -> dict[str, Any]:
        entry = real(**kwargs)["glossary"]
        shaped = mutate(entry)
        return {"glossary": shaped} if shaped else {}

    monkeypatch.setattr(vg, "get_rec_write_guidance", patched)
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == ["Glossary"] and "guidance" in out, out


def test_unloadable_contract_fails_and_skips(repo: Path, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "docs/contracts/glossary.yaml").write_text("terms: [unclosed\n", encoding="utf-8")
    skipped: list[str] = []
    monkeypatch.setattr(registry, "skipped", skipped.append)
    failed = _run(repo)
    out = capsys.readouterr().out
    assert failed == ["Glossary"] and "FAIL" in out and len(skipped) == 1


def test_entry_resolves_and_is_sequenced() -> None:
    assert registry.resolve("validate_glossary") is vg.validate_glossary
    assert "validate_glossary" in {s.name for s in registry.pre_sequence() if s.kind == "check"}
    assert "validate_glossary" in {s.name for s in registry.full_sequence() if s.kind == "check"}


def test_older_decisions_carry_reads_through_note() -> None:
    from scripts.checks.contracts import _glossary_stock

    assert _glossary_stock.unnoted(_REPO, _contract(_REPO)) == []


def test_live_surfaces_use_glossary_names() -> None:
    from scripts.checks.contracts import _glossary_stock

    plan = vg._plan_lines_at(_REPO, "HEAD")
    hits, _ = _glossary_stock.live_hits(_REPO, _contract(_REPO), vg._pathspecs(_REPO), lambda text: vg._in_plan(text, plan))
    assert hits == []


def _roster(d: dict[str, Any], value: Any) -> None:
    d["read_as"]["noted_decisions"] = value


def _note(d: dict[str, Any], value: str) -> None:
    d["read_as"]["note"] = value


_ROSTER_FINDING = "read_as.noted_decisions must be a non-empty list of distinct integers below 240"
_STOCK_DEFECTS: dict[str, tuple[Callable[[dict[str, Any]], None], str]] = {
    "empty_roster": (lambda d: _roster(d, []), _ROSTER_FINDING),
    "non_integer_roster": (lambda d: _roster(d, [27, "75"]), _ROSTER_FINDING),
    "duplicate_roster": (lambda d: _roster(d, [27, 27]), _ROSTER_FINDING),
    "roster_at_or_above_240": (lambda d: _roster(d, [27, 240]), _ROSTER_FINDING),
    "note_without_prefix": (
        lambda d: _note(d, "Update (DATE): x"),
        "read_as.note must start with > **Update ( and carry the literal DATE",
    ),
    "note_without_date": (
        lambda d: _note(d, "> **Update (2026-11-02):** x"),
        "read_as.note must start with > **Update ( and carry the literal DATE",
    ),
    "records_live_file": (
        lambda d: d["read_as"]["records"].append("docs/PROJECT_CONTEXT.md"),
        "read_as.records entry docs/PROJECT_CONTEXT.md lies outside the record classes",
    ),
    "records_covering_contracts": (
        lambda d: d["read_as"]["records"].append("docs/contracts/"),
        "read_as.records entry docs/contracts/ lies outside the record classes",
    ),
}


def _commit_base(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "more base")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "HEAD"))


class TestStockLegs:
    def test_pass_line_reports_both_legs(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        failed = _run(repo)
        out = capsys.readouterr().out
        assert failed == [] and "1 noted Decisions carry the note" in out and "live files" in out

    @pytest.mark.parametrize("with_base", [True, False])
    def test_missing_note_fails(self, repo: Path, capsys: pytest.CaptureFixture[str], with_base: bool) -> None:
        (repo / "docs/DECISIONS.md").write_text("# Decisions\n\n" + _ENTRY + "\n---\n" + _older(repo, noted=False))
        _commit_base(repo)
        if not with_base:
            _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
        failed = _run(repo)
        out = capsys.readouterr().out
        assert failed == ["Glossary"] and "FAIL: Decision 27 lacks the read_as.note" in out, out

    def test_roster_decision_in_neither_file_fails(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (repo / "docs/DECISIONS.md").write_text("# Decisions\n\n" + _ENTRY + "\n---\n")
        failed = _run(repo)
        assert failed == ["Glossary"] and "neither docs/DECISIONS.md" in capsys.readouterr().out

    @pytest.mark.parametrize("with_base", [True, False])
    def test_live_hit_on_the_base_fails(self, repo: Path, capsys: pytest.CaptureFixture[str], with_base: bool) -> None:
        _append(repo, "docs/contracts/other.yaml", "The platform runs nightly.")
        _commit_base(repo)
        if not with_base:
            _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
        failed = _run(repo)
        out = capsys.readouterr().out
        assert failed == ["Glossary"] and "FAIL: docs/contracts/other.yaml:2: unqualified" in out, out

    def test_live_line_a_plan_carries_is_exempt(self, repo: Path) -> None:
        _append(repo, "docs/contracts/other.yaml", _OLD)
        _commit_base(repo)
        assert _run(repo) == []

    def test_live_amendment_log_row_is_exempt(self, repo: Path) -> None:
        _write(repo, "docs/contracts/other.yaml", "amendment_log:\n  - date: x\n    summary: the platform moved\nkey: v\n")
        _commit_base(repo)
        assert _run(repo) == []

    def test_a_record_may_keep_its_words(self, repo: Path) -> None:
        _append(repo, "docs/audit-prompts/AUDIT-x.md", "The platform runs nightly.")
        _commit_base(repo)
        assert _run(repo) == []

    def test_added_line_lint_runs_beside_stock_findings(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (repo / "docs/DECISIONS.md").write_text("# Decisions\n\n" + _ENTRY + "\n---\n" + _older(repo, noted=False))
        _commit_base(repo)
        _append(repo, "docs/audit-prompts/AUDIT-x.md", "The platform runs nightly.")
        failed = _run(repo)
        out = capsys.readouterr().out
        assert failed == ["Glossary"]
        assert "FAIL: Decision 27 lacks the read_as.note" in out
        assert "FAIL: docs/audit-prompts/AUDIT-x.md:2: unqualified" in out

    def test_a_line_both_stock_and_added_leg_find_reports_once(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _append(repo, "docs/contracts/other.yaml", "The platform runs nightly.")
        _run(repo)
        assert capsys.readouterr().out.count("docs/contracts/other.yaml:2: unqualified") == 1

    @pytest.mark.parametrize("defect", _STOCK_DEFECTS)
    def test_rejects_stock_shape_defect(
        self, repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, defect: str
    ) -> None:
        calls: list[str] = []
        for name in ("unnoted", "live_hits", "_lint"):
            monkeypatch.setattr(vg, name, lambda *a, _n=name, **k: calls.append(_n) or ([], 0))
        mutate, finding = _STOCK_DEFECTS[defect]
        data = _contract(repo)
        mutate(data)
        _save(repo, data)
        failed = _run(repo)
        out = capsys.readouterr().out
        assert failed == ["Glossary"] and f"FAIL: {finding}" in out, out
        assert calls == []
