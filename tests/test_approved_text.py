"""Tests for scripts/roadmap/approved_text.py (the approved-text binding). Fixture repos only."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.roadmap import approved_text as at
from tests.fixtures import merge_step_repos as fx

SLUG = "demo"
BASE_TEXT = "alpha beta gamma delta\nepsilon zeta eta theta\niota kappa\n"
BLOCKS = [
    {"id": "b1", "file": "doc.md", "before": "beta gamma", "after": "BETA\nGAMMA on {{DATE}}"},
    {"id": "b2", "file": "doc.md", "before": "zeta eta", "after": "ZETA eta{{N}}"},
]
HEAD_TEXT = "alpha BETA GAMMA on 2026-11-02\ndelta epsilon ZETA eta7\ntheta iota kappa\n"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fx.clear_ci_env(monkeypatch)
    work = fx.make_repo(tmp_path)
    fx.write(work, "doc.md", BASE_TEXT)
    fx.write_sidecar(work, SLUG, BLOCKS)
    fx.git(work, "add", "-A")
    fx.git(work, "commit", "-m", "base")
    fx.push_main(work)
    fx.branch(work)
    return work


def _check(repo: Path, head_text: str) -> list[str]:
    fx.write(repo, "doc.md", head_text)
    sidecar = at.load_sidecar_at("main", SLUG, repo)
    assert sidecar is not None
    return at.verify(sidecar, repo, "main")


def test_exact_copy_verifies(repo: Path) -> None:
    assert _check(repo, HEAD_TEXT) == []


def test_rewrapped_copy_verifies(repo: Path) -> None:
    assert _check(repo, " ".join(HEAD_TEXT.split()) + "\n") == []


def test_extra_word_anywhere_fails(repo: Path) -> None:
    messages = _check(repo, HEAD_TEXT + "stray\n")
    assert len(messages) == 1 and "doc.md" in messages[0] and "first difference" in messages[0]


def test_extra_word_far_from_blocks_fails(repo: Path) -> None:
    assert _check(repo, HEAD_TEXT.replace("iota", "iota extra"))


def test_changed_word_fails(repo: Path) -> None:
    assert _check(repo, HEAD_TEXT.replace("delta", "DELTA"))


def test_dropped_word_fails(repo: Path) -> None:
    assert _check(repo, HEAD_TEXT.replace(" kappa", ""))


def test_unapproved_sentence_removal_fails(repo: Path) -> None:
    assert _check(repo, "alpha BETA GAMMA on 2026-11-02\ndelta epsilon ZETA eta7\n")


def test_date_placeholder_matches_dates_only(repo: Path) -> None:
    assert _check(repo, HEAD_TEXT.replace("2026-11-02", "soon"))
    assert _check(repo, HEAD_TEXT.replace("2026-11-02", "{{DATE}}"))


def test_number_placeholder(repo: Path) -> None:
    assert _check(repo, HEAD_TEXT.replace("eta7", "eta{{N}}"))
    assert _check(repo, HEAD_TEXT.replace("eta7", "etax"))


def test_missing_bound_file_fails(repo: Path) -> None:
    sidecar = at.load_sidecar_at("main", SLUG, repo)
    (repo / "doc.md").unlink()
    assert at.verify(sidecar, repo, "main")  # type: ignore[arg-type]


def test_unparseable_bound_yaml_fails(repo: Path) -> None:
    fx.commit_file(repo, "conf.yaml", "a: 1\n", "conf")
    fx.push_main(repo)
    block = {"id": "c1", "file": "conf.yaml", "before": "a: 1", "after": "a: [1"}
    sidecar = at.parse_sidecar(fx.sidecar_text(SLUG, [block]))
    fx.write(repo, "conf.yaml", "a: [1\n")
    messages = at.verify(sidecar, repo, "origin/main")
    assert any("does not parse as YAML" in m for m in messages)


def test_apply_before_count_errors_name_the_block() -> None:
    base = at.words("a b c b")
    with pytest.raises(at.ApprovedTextError, match="block twice"):
        at.apply(base, [at.Block(id="twice", file="f", before="b", after="x")])
    with pytest.raises(at.ApprovedTextError, match="block never"):
        at.apply(base, [at.Block(id="never", file="f", before="zzz", after="x")])


def test_overlapping_blocks_raise() -> None:
    blocks = [
        at.Block(id="o1", file="f", before="a b", after="x"),
        at.Block(id="o2", file="f", before="b c", after="y"),
    ]
    with pytest.raises(at.ApprovedTextError, match="o1 and o2 overlap"):
        at.apply(at.words("a b c d"), blocks)


def test_new_file_with_empty_before(repo: Path) -> None:
    block = at.Block(id="n1", file="new.md", before="", after="fresh text")
    assert at.apply([], [block]) == ["fresh", "text"]
    with pytest.raises(at.ApprovedTextError, match="n1"):
        at.apply(["old"], [block])
    with pytest.raises(at.ApprovedTextError, match="n1"):
        at.apply([], [block, at.Block(id="n2", file="new.md", before="x", after="y")])
    sidecar = at.parse_sidecar(fx.sidecar_text(SLUG, [{"id": "n1", "file": "new.md", "before": "", "after": "fresh text"}]))
    fx.write(repo, "new.md", "fresh  text\n")
    assert at.verify(sidecar, repo, "main") == []


def test_verify_reports_apply_error(repo: Path) -> None:
    block = {"id": "bad", "file": "doc.md", "before": "nowhere", "after": "x"}
    sidecar = at.parse_sidecar(fx.sidecar_text(SLUG, [block]))
    messages = at.verify(sidecar, repo, "main")
    assert len(messages) == 1 and "block bad" in messages[0]


def test_parse_sidecar_rejects_bad_documents() -> None:
    for bad in (
        "schema_version: 2\nslug: s\nblocks: []\n",
        "schema_version: 1\nslug: s\nblocks: []\n",
        "schema_version: 1\nslug: s\nextra: 1\nblocks: [{id: a, file: f, before: b, after: c}]\n",
        "schema_version: 1\nslug: s\nblocks: [{id: a, file: f, before: b, after: c}, {id: a, file: f, before: d, after: e}]\n",
        "schema_version: [1\n",
    ):
        with pytest.raises(at.ApprovedTextError):
            at.parse_sidecar(bad)


def test_load_sidecar_reads_base_and_tree(repo: Path) -> None:
    assert at.load_sidecar_at("main", SLUG, repo) is not None
    assert at.load_sidecar_at("main", "nope", repo) is None
    assert at.load_sidecar(SLUG, repo).slug == SLUG
    with pytest.raises(at.ApprovedTextError):
        at.load_sidecar("nope", repo)


def test_sidecar_state_values(repo: Path) -> None:
    rel = at.sidecar_rel(SLUG)
    assert at.sidecar_state(SLUG, repo, "main") == "unchanged"
    fx.write(repo, rel, fx.sidecar_text(SLUG, BLOCKS[:1]))
    assert at.sidecar_state(SLUG, repo, "main") == "changed"
    (repo / rel).unlink()
    assert at.sidecar_state(SLUG, repo, "main") == "deleted"
    fx.write(repo, at.sidecar_rel("fresh"), fx.sidecar_text("fresh", BLOCKS))
    assert at.sidecar_state("fresh", repo, "main") == "added"
    assert at.sidecar_state("ghost", repo, "main") == "absent"


def test_digest_stable_under_rewrapping_and_sensitive_to_words() -> None:
    one = at.parse_sidecar(fx.sidecar_text("s", [{"id": "a", "file": "f", "before": "x y", "after": "p q"}]))
    wrapped = at.parse_sidecar(fx.sidecar_text("s", [{"id": "a", "file": "f", "before": "x\ny", "after": "p   q"}]))
    changed = at.parse_sidecar(fx.sidecar_text("s", [{"id": "a", "file": "f", "before": "x y", "after": "p r"}]))
    assert at.digest(one) == at.digest(wrapped)
    assert at.digest(one) != at.digest(changed)


def test_digest_canonical_form_is_pinned() -> None:
    sidecar = at.parse_sidecar(
        fx.sidecar_text(
            "s",
            [
                {"id": "a", "file": "f1", "before": "x y", "after": "p q"},
                {"id": "b", "file": "f2", "before": "", "after": "new"},
            ],
        )
    )
    assert at.digest(sidecar) == "b2213e6ca4c2af52a6048e22e69e2139e678b24f1d08395eb3e0755634580596"  # pragma: allowlist secret


def test_cli_record_and_verify(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(repo)]
    assert at.main(["record", "--slug", SLUG, *root]) == 0
    out = capsys.readouterr().out
    digest_hex = out.split("digest=")[1].split()[0]
    assert "blocks=2" in out
    assert at.main(["record", "--slug", "nope", *root]) == 2

    fx.write(repo, "doc.md", HEAD_TEXT)
    assert at.main(["verify", "--slug", SLUG, "--base", "main", "--expect", digest_hex, *root]) == 0
    assert f"APPROVED-TEXT OK {digest_hex}" in capsys.readouterr().out
    assert at.main(["verify", "--slug", SLUG, "--base", "main", "--expect", "0" * 64, *root]) == 1
    assert "digest" in capsys.readouterr().out

    fx.write(repo, "doc.md", HEAD_TEXT + "stray\n")
    assert at.main(["verify", "--slug", SLUG, "--base", "main", *root]) == 1
    assert "APPROVED-TEXT MISMATCH doc.md" in capsys.readouterr().out


def test_cli_verify_default_base_and_errors(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(repo)]
    fx.write(repo, "doc.md", HEAD_TEXT)
    assert at.main(["verify", "--slug", SLUG, *root]) == 0
    assert at.main(["verify", "--slug", "nope", "--base", "main", *root]) == 2
    fx.commit_file(repo, at.sidecar_rel("broken"), "schema_version: [1\n", "broken")
    fx.push_main(repo)
    assert at.main(["verify", "--slug", "broken", "--base", "origin/main", *root]) == 2
    assert "APPROVED-TEXT ERROR" in capsys.readouterr().out


def test_cli_verify_sidecar_edits_fail(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(repo)]
    fx.write(repo, at.sidecar_rel(SLUG), fx.sidecar_text(SLUG, BLOCKS[:1]))
    assert at.main(["verify", "--slug", SLUG, "--base", "main", *root]) == 1
    assert "APPROVED-TEXT SIDECAR changed" in capsys.readouterr().out
    (repo / at.sidecar_rel(SLUG)).unlink()
    assert at.main(["verify", "--slug", SLUG, "--base", "main", *root]) == 1
    assert "APPROVED-TEXT SIDECAR deleted" in capsys.readouterr().out
    fx.write(repo, at.sidecar_rel("fresh"), fx.sidecar_text("fresh", BLOCKS))
    assert at.main(["verify", "--slug", "fresh", "--base", "main", *root]) == 2


def test_cli_main_reads_sys_argv(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["approved_text", "record", "--slug", SLUG, "--root", str(repo)])
    assert at.main() == 0


def test_committed_sidecars_are_wellformed() -> None:
    root = Path(__file__).resolve().parents[1]
    sidecars = sorted((root / at.SIDECAR_DIR).glob("*.yaml"))
    assert sidecars
    for path in sidecars:
        sidecar = at.parse_sidecar(path.read_text(encoding="utf-8"))
        assert sidecar.slug == path.stem
        assert (root / "docs" / "plans" / f"PLAN-{sidecar.slug}.yaml").is_file()
