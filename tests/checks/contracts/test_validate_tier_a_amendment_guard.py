"""Mirror tests for scripts/checks/contracts/validate_tier_a_amendment_guard.py (rec-3959, Decision 243 cl.6).

Each node runs the guard in a temporary git repository: the real north-star.yaml, a fixture DECISIONS.md, the real
CODEOWNERS and capabilities.yaml, committed as main with origin/main pointing at it, then head edits in the working
tree. Unless a node says otherwise an authorized change also adds the citing Decision to carried_by, a reason with an
approval pointer and an amendment_log row naming the key, so each node fails for one cause.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.checks import _common, _marker_guard, registry
from scripts.checks.contracts import _manifest
from scripts.checks.contracts import validate_tier_a_amendment_guard as guard

_REPO = Path(__file__).resolve().parents[3]
_NS = "docs/contracts/north-star.yaml"
_DEC = "docs/DECISIONS.md"
_POINTER = "approval: ref 2026-11-03"
_ID4 = "  - id: NS.4\n"
_EDIT4 = ("Why: maintaining anything", "Why: probe. Maintaining anything")
_CARRIED4 = ("carried_by: [86, 127, 128, 211]", "carried_by: [86, 127, 128, 211, 244]")
_LOG = 'amendment_log:\n  - date: "2026-11-03"\n    semantic_break: false\n    change_class: prose_improvement\n'


def _git(root: Path, *args: str) -> None:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env={**env, "PATH": "/usr/bin:/bin"})


def _head(root: Path) -> str:
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _put(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _get(root: Path, rel: str = _NS) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _sub(root: Path, old: str, new: str, rel: str = _NS) -> None:
    text = _get(root, rel)
    assert old in text, old
    _put(root, rel, text.replace(old, new, 1))


def _decision(number: int, body: str) -> str:
    return f"## Decision {number}: Title {number} (Decided)\n\n{body}\n\n---\n"


_CORPUS = (
    "# Decisions\n\n" + _decision(243, "Ratifies the north-star contract.") + _decision(86, "Agent-first, NS.4 and NS.5.")
)


def _rebase(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--allow-empty", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/main", _head(root))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.setattr(_common, "ROOT", tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    _put(tmp_path, _NS, _get(_REPO))
    for rel in (".github/CODEOWNERS", "config/agent/executor/capabilities.yaml"):
        _put(tmp_path, rel, _get(_REPO, rel))
    _put(tmp_path, _DEC, _CORPUS)
    _rebase(tmp_path)
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def _run(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[list[str], str]:
    _marker_guard._decision_bodies_cache.clear()
    failed: list[str] = []
    guard.validate_tier_a_amendment_guard(failed)
    return failed, capsys.readouterr().out


def _fails(root: Path, capsys: pytest.CaptureFixture[str], needle: str) -> None:
    failed, out = _run(root, capsys)
    assert failed == [guard.LABEL], out
    assert needle in out, out


def _passes(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    failed, out = _run(root, capsys)
    assert failed == [], out


def _change(
    root: Path,
    *,
    marker: str | None = "244",
    reason: str = _POINTER,
    carried: bool = True,
    log: str | None = "probe NS.4",
    body: str | None = "Probe naming NS.4.",
) -> None:
    """Edit NS.4's detail and wire the authorization around it; each argument disables one piece."""
    _sub(root, *_EDIT4)
    if carried:
        _sub(root, *_CARRIED4)
    if log is not None:
        _sub(root, "amendment_log:\n", _LOG + f'    summary: "{log}"\n')
    if body is not None:
        _sub(root, "---\n", "---\n" + _decision(244, body), _DEC)
    if marker is not None:
        _sub(root, _ID4, f"  - id: NS.4  # tier-a-amendment-approved: dec-{marker} probe {reason}\n")


def test_authorization_shaped_binds_marker_guard_at_check_name_level(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = _marker_guard.check_state_diff
    monkeypatch.setattr(
        _marker_guard, "check_state_diff", lambda spec, base_reader=None: calls.append(spec.token) or real(spec, base_reader)
    )
    _change(repo)
    _passes(repo, capsys)
    assert calls == [guard.TOKEN]


@pytest.mark.parametrize("body", ["Only NS.40.", "The range NS.1-NS.9.", "scripts/checks only.", f"See {_NS}."])
def test_inexact_naming_does_not_authorize(repo: Path, capsys: pytest.CaptureFixture[str], body: str) -> None:
    _change(repo, body=body)
    _fails(repo, capsys, "does not authorize")


def test_unmarked_change_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, marker=None)
    _fails(repo, capsys, "NS.4: unauthorized change")


def test_spent_mention_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, marker="86", body=None)
    _fails(repo, capsys, "no text added")


def test_appended_note_authorizes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, marker="86", body=None)
    _sub(repo, "Agent-first, NS.4 and NS.5.\n", "Agent-first, NS.4 and NS.5.\nNote 2026-11-03: NS.4 detail.\n", _DEC)
    _passes(repo, capsys)


def test_in_place_rewrite_never_authorizes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, marker="86", body=None)
    _sub(repo, "Agent-first, NS.4 and NS.5.", "Agent-first, reworded NS.4 and NS.5.", _DEC)
    _fails(repo, capsys, "no text added")


def _add_ns10(root: Path) -> None:
    block = "  - id: NS.10\n    kind: north_star\n    principle: Probe.\n    carried_by: [244]\n\npartial_order:\n"
    _sub(root, "\npartial_order:\n", "\n" + block)


def test_addition_needs_a_fresh_marker(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _add_ns10(repo)
    _fails(repo, capsys, "NS.10: unauthorized change")
    _sub(repo, "  - id: NS.10\n", f"  - id: NS.10  # tier-a-amendment-approved: dec-244 x {_POINTER}\n")
    _sub(repo, "amendment_log:\n", _LOG + '    summary: "adds NS.10"\n')
    _sub(repo, "---\n", "---\n" + _decision(244, "Adds NS.10."), _DEC)
    _passes(repo, capsys)


def test_retirement_needs_a_fresh_marker(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _sub(repo, "  - id: NS.3\n    kind: north_star\n", "  - id: NS.3\n    status: retired\n    kind: north_star\n")
    _fails(repo, capsys, "NS.3: unauthorized change")


def test_removal_has_no_marker_escape(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = _get(repo)
    start = text.index("  - id: NS.9\n")
    _put(repo, _NS, text[:start].rstrip("\n") + "\n\n" + text[text.index("partial_order:") :])
    _fails(repo, capsys, "NS.9: removed -- removal has no marker escape")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("statement: >-\n    On The Loop is", "statement: >-\n    On The Loop really is"),
        ("    - [NS.6, NS.9]\n", "    - [NS.6, NS.9, NS.2]\n"),
        ("  binds: >-\n    Who the entry binds", "  binds: >-\n    Whom the entry binds"),
    ],
)
def test_mission_partial_order_and_semantics_are_gated(
    repo: Path, capsys: pytest.CaptureFixture[str], old: str, new: str
) -> None:
    _sub(repo, old, new)
    _fails(repo, capsys, ": unauthorized change")


def test_cited_decision_outside_carried_by_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, carried=False)
    _fails(repo, capsys, "which is not in its carried_by")


def _semantics(root: Path, marker: str, note_on: int) -> None:
    _sub(
        root,
        "  binds: >-\n    Who the entry binds",
        f"  binds: >-  # tier-a-amendment-approved: dec-{marker} s {_POINTER}\n    Whom the entry binds",
    )
    _sub(root, "amendment_log:\n", _LOG + '    summary: "semantics"\n')
    _sub(root, "---\n", "---\n" + _decision(245, "Field semantics note."), _DEC) if note_on == 245 else _sub(
        root, "Ratifies the north-star contract.\n", "Ratifies the north-star contract.\nNote 2026-11-03: semantics.\n", _DEC
    )


def test_block_keys_need_a_dated_note_on_the_ratifying_decision(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _semantics(repo, "245", 245)
    _fails(repo, capsys, "not the ratifying Decision")
    _git(repo, "checkout", "-q", "--", ".")
    _semantics(repo, "243", 243)
    _passes(repo, capsys)


def test_ratified_via_is_read_at_the_base(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _semantics(repo, "245", 245)
    _sub(repo, 'ratified_via: "Decision 243', 'ratified_via: "Decision 245')
    _fails(repo, capsys, "not the ratifying Decision")


def test_ratified_via_edit_does_not_red_persisted_markers(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _semantics(repo, "243", 243)
    _rebase(repo)
    _sub(repo, 'ratified_via: "Decision 243', 'ratified_via: "Decision 86')
    _passes(repo, capsys)


def test_second_change_to_a_block_key_takes_two_prs(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _semantics(repo, "243", 243)
    _rebase(repo)
    _sub(repo, "Whom the entry binds", "Who the entry might bind")
    _sub(repo, "amendment_log:\n", _LOG + '    summary: "semantics again"\n')
    _fails(repo, capsys, "takes two PRs")
    _sub(repo, "Who the entry might bind", "Whom the entry binds")
    _sub(repo, f"  # tier-a-amendment-approved: dec-243 s {_POINTER}", "")
    _passes(repo, capsys)
    _rebase(repo)
    _sub(repo, "Whom the entry binds", "Who the entry might bind")
    _sub(repo, "Ratifies the north-star contract.\n", "Ratifies the north-star contract.\nNote 2: semantics.\n", _DEC)
    _sub(repo, "binds: >-\n    Who", f"binds: >-  # tier-a-amendment-approved: dec-243 s {_POINTER}\n    Who")
    _sub(repo, "amendment_log:\n", _LOG + '    summary: "semantics third"\n')
    _passes(repo, capsys)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t + "mission: {}\n",
        lambda t: t.replace("  - id: NS.2\n", "  - id: NS.1\n", 1),
        lambda t: t.replace("kind: north_star\n    principle: Source", "kind: &a north_star\n    principle: Source", 1),
        lambda t: t.replace("kind: north_star\n    principle: Every", "kind: *a\n    principle: Every", 1).replace(
            "class: D", "class: &a D", 1
        ),
        lambda t: t.replace("class: D", "class: !!str D", 1),
        lambda t: t + "bad: [unclosed\n",
        lambda t: "- a\n",
        lambda t: t.replace("  kind: north_star\n  statement", "  kind: north_star\n  1: x\n  statement", 1),
        lambda t: t[: t.index("principles:")] + "principles: x\n\n" + t[t.index("partial_order:") :],
        lambda t: t[: t.index("principles:")] + "principles: [x]\n\n" + t[t.index("partial_order:") :],
        lambda t: t.replace("principles:\n  - id: NS.1", "principles: {id: NS.1}\n  - id: NS.1", 1),
    ],
)
def test_head_shape_defects_fail_closed(repo: Path, capsys: pytest.CaptureFixture[str], mutate: Any) -> None:
    _put(repo, _NS, mutate(_get(repo)))
    _fails(repo, capsys, "PARSE")


def test_unquoted_date_does_not_crash(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo)
    _sub(repo, 'date: "2026-11-03"', "date: 2026-11-03")
    _passes(repo, capsys)


def test_comment_inside_an_entry_is_a_change(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _sub(
        repo,
        "    kind: north_star\n    principle: The repo is for agents.\n",
        "    kind: north_star\n    # note\n    principle: The repo is for agents.\n",
    )
    _fails(repo, capsys, "NS.4: unauthorized change")


def test_comment_on_a_block_scalar_indicator_line_is_a_change(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    old = "    detail: >-\n      Why: maintaining anything"
    _sub(repo, old, "    detail: >-  # note\n      Why: maintaining anything")
    _fails(repo, capsys, "NS.4: unauthorized change")
    _git(repo, "checkout", "-q", "--", ".")
    _change(repo, marker=None)
    _sub(
        repo,
        "    detail: >-\n      Why: probe.",
        f"    detail: >-  # tier-a-amendment-approved: dec-244 probe {_POINTER}\n      Why: probe.",
    )
    _passes(repo, capsys)


def test_comment_before_the_first_principle_is_a_change(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _sub(repo, "principles:\n", "principles:\n  # stray\n")
    _fails(repo, capsys, "NS.1: unauthorized change")


def test_hash_inside_a_block_scalar_is_content(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _sub(
        repo,
        "      Why: maintaining anything",
        f"      # tier-a-amendment-approved: dec-244 x {_POINTER}\n      Why: maintaining anything",
    )
    _fails(repo, capsys, "NS.4: unauthorized change")


def test_edited_log_row_is_not_a_new_row(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, log=None)
    _sub(repo, "PLAN-north-star-tier-a: created", "PLAN-north-star-tier-a NS.4: created")
    _fails(repo, capsys, "NS.4: changed with no new amendment_log row")


def test_marker_without_approval_pointer_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, reason="no pointer")
    _fails(repo, capsys, "carries no `approval:")


def test_change_without_a_log_row_naming_the_key_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, log="probe NS.5")
    _fails(repo, capsys, "NS.4: changed with no new amendment_log row")


def test_marker_on_another_entry_does_not_authorize(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, marker=None)
    _sub(repo, "  - id: NS.5\n", f"  - id: NS.5  # tier-a-amendment-approved: dec-244 x {_POINTER}\n")
    _fails(repo, capsys, "NS.4: unauthorized change")


def test_two_markers_on_one_entry_fail(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo)
    _sub(
        repo,
        "    principle: The repo is for agents.",
        f"    principle: The repo is for agents.  # tier-a-amendment-approved: dec-244 y {_POINTER}",
    )
    _fails(repo, capsys, "more than one marker")


def test_reasonless_marker_is_no_marker(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, marker=None)
    _sub(repo, _ID4, "  - id: NS.4  # tier-a-amendment-approved: dec-244\n")
    _fails(repo, capsys, "NS.4: unauthorized change")
    _git(repo, "checkout", "-q", "--", ".")
    _sub(repo, "  - id: NS.5\n", "  - id: NS.5  # tier-a-amendment-approved: dec-244\n")
    _fails(repo, capsys, "NS.5: unauthorized change")


def test_marker_added_or_reworded_alone_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    marker = f"  - id: NS.5  # tier-a-amendment-approved: dec-86 base {_POINTER}\n"
    _sub(repo, "  - id: NS.5\n", marker)
    _rebase(repo)
    _passes(repo, capsys)
    _sub(repo, "dec-86 base", "dec-86 reworded")
    _fails(repo, capsys, "never added or reworded alone")
    _git(repo, "checkout", "-q", "--", ".")
    _sub(repo, marker, "  - id: NS.5\n")
    _passes(repo, capsys)
    _rebase(repo)
    _sub(repo, "  - id: NS.6\n", f"  - id: NS.6  # tier-a-amendment-approved: dec-86 new {_POINTER}\n")
    _fails(repo, capsys, "NS.6: a marker records a change")


def test_prose_before_the_marker_token_is_text(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _change(repo, marker=None)
    _sub(repo, _ID4, f"  - id: NS.4  # prose tier-a-amendment-approved: dec-244 probe {_POINTER}\n")
    _fails(repo, capsys, "NS.4: unauthorized change")


def test_corpus_read_failure_fails_closed(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = guard._cat_batch
    monkeypatch.setattr(
        guard, "_cat_batch", lambda root, ref, rels: None if guard._CORPUS[0] in rels else real(root, ref, rels)
    )
    _change(repo)
    _fails(repo, capsys, "Decision corpus unreadable at the base")


def test_header_comment_amendment_log_and_reflow_are_free(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _sub(repo, "# Tier A north stars", "# Tier A north stars (edited header)")
    _sub(repo, "amendment_log:\n", _LOG + '    summary: "free"\n')
    _sub(repo, "On The\n      Loop Labs supports", "On The Loop\n      Labs supports")
    _sub(repo, "\n  - id: NS.3\n", "\n\n  - id: NS.3\n")
    text = _get(repo)
    start, end = text.index("semantics:\n"), text.index("\nmission:")
    block = "".join(("  " + ln if ln.strip() else ln) for ln in text[start:end].splitlines(True)[1:])
    _put(repo, _NS, text[:start] + "semantics:\n" + block + text[end:])
    _passes(repo, capsys)
    _sub(repo, "carried_by: [86, 127, 128, 211]", "carried_by: [86,127,128,211]")
    _fails(repo, capsys, "NS.4: unauthorized change")


def test_unchanged_file_runs_only_the_every_run_legs(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(_marker_guard, "check_state_diff", lambda *a, **k: seen.append("diff") or [])
    monkeypatch.setattr(guard, "narrowing_violations", lambda *a: seen.append("narrowing") or [])
    _passes(repo, capsys)
    assert seen == ["narrowing"]


def test_retroactive_scan_flags_an_unauthorized_present_marker(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _sub(repo, "  - id: NS.5\n", f"  - id: NS.5  # tier-a-amendment-approved: dec-1 x {_POINTER}\n")
    _rebase(repo)
    _fails(repo, capsys, "retroactive scan")


def test_push_to_main_rechecks_the_merged_commit(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _sub(repo, *_EDIT4)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "merged")
    _git(repo, "update-ref", "refs/remotes/origin/main", _head(repo))
    _passes(repo, capsys)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    _fails(repo, capsys, "NS.4: unauthorized change")


def test_no_diff_base_skips_loudly_and_records_a_skip(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    _change(repo, marker=None)
    _passes(repo, capsys)
    assert registry._CURRENT_DECLARATION.kind == "skipped"  # type: ignore[union-attr]


def test_no_diff_base_still_runs_the_head_legs(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    _put(repo, _NS, _get(repo) + "bad: [unclosed\n")
    _fails(repo, capsys, "SKIP")


def test_unparseable_base_skips_the_diff_legs_loudly(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _put(repo, _NS, _get(repo) + "bad: [unclosed\n")
    _rebase(repo)
    _put(repo, _NS, _get(repo).replace("bad: [unclosed\n", ""))
    _sub(repo, *_EDIT4)
    _failed, out = _run(repo, capsys)
    assert "SKIP: base north-star.yaml unparseable" in out
    assert registry._CURRENT_DECLARATION.kind == "skipped"  # type: ignore[union-attr]


def test_base_reader_failure_skips_loudly(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(guard, "_cat_batch", lambda *a: None)
    _passes(repo, capsys)
    assert registry._CURRENT_DECLARATION.reason == "git cat-file --batch failed"  # type: ignore[union-attr]


def test_cat_batch_skips_every_body_by_declared_size(repo: Path) -> None:
    got = guard._cat_batch(repo, "origin/main", ["docs", _NS, "nope.txt", ".github/CODEOWNERS"])
    assert (
        got is not None
        and got["docs"] == ""
        and got["nope.txt"] == ""
        and "# " in got[_NS]
        and "@benjamin" in got[".github/CODEOWNERS"]
    )


def test_missing_head_file_reports_every_key_removed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / _NS).unlink()
    _fails(repo, capsys, "NS.1: removed")


@pytest.mark.parametrize(
    ("rel", "needle"),
    [
        (".github/CODEOWNERS", "no CODEOWNERS line owns it"),
        ("config/agent/executor/capabilities.yaml", "no boundary_patterns entry covers it"),
    ],
)
def test_ownership_rows_required(repo: Path, capsys: pytest.CaptureFixture[str], rel: str, needle: str) -> None:
    lines = [ln for ln in _get(repo, rel).splitlines(True) if "validate_tier_a_amendment_guard" not in ln]
    _put(repo, rel, "".join(lines))
    _fails(repo, capsys, needle)


def test_narrowing_protected_with_its_rows_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = "PROTECTED = (" + ", ".join(repr(p) for p in (*guard.PROTECTED, "scripts/checks/extra_guarded.py")) + ")\n"
    _put(repo, guard._SELF_REL, source)
    _put(repo, ".github/CODEOWNERS", _get(repo, ".github/CODEOWNERS") + "/scripts/checks/extra_guarded.py @x\n")
    _sub(
        repo,
        "  - docs/contracts/north-star.yaml\n",
        "  - docs/contracts/north-star.yaml\n  - scripts/checks/extra_guarded\n",
        "config/agent/executor/capabilities.yaml",
    )
    _rebase(repo)
    _sub(repo, "/scripts/checks/extra_guarded.py @x\n", "", ".github/CODEOWNERS")
    _sub(repo, "  - scripts/checks/extra_guarded\n", "", "config/agent/executor/capabilities.yaml")
    _failed, out = _run(repo, capsys)
    assert "dropped from PROTECTED" in out and "CODEOWNERS line removed" in out and "boundary_patterns entry removed" in out


def test_registered_ungated_in_both_tiers() -> None:
    entry = next(e for e in _manifest.ENTRIES if e.name == "validate_tier_a_amendment_guard")
    assert entry.pre is True and not entry.pre_globs
    assert guard.TOKEN == "tier-a-amendment-approved"
    assert entry.name in [s.name for s in registry.pre_sequence()]
    assert entry.name in [s.name for s in registry.full_sequence()]


def test_real_tree_is_clean(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "ROOT", _REPO)
    failed: list[str] = []
    guard.validate_tier_a_amendment_guard(failed)
    assert failed == [], capsys.readouterr().out


def test_cat_batch_failure_modes(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    done = subprocess.CompletedProcess
    monkeypatch.setattr(_common, "run", lambda *a, **k: done(a, 1, b"", b""))
    assert guard._cat_batch(repo, "origin/main", [_NS]) is None
    monkeypatch.setattr(_common, "run", lambda *a, **k: done(a, 0, b"", b""))
    assert guard._cat_batch(repo, "origin/main", [_NS]) is None


def test_unparseable_capabilities_fail_closed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _put(repo, "config/agent/executor/capabilities.yaml", "boundary_patterns: [unclosed\n")
    _fails(repo, capsys, "no boundary_patterns entry covers it")
