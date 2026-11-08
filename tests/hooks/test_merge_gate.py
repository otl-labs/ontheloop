"""The merge gate adapter, run through the shim as the harness does (payload on stdin)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures import merge_overlap_repos as fx


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("gate")
    f = fx.Fixture(tmp)
    f.branch_pr(1, "a.txt", "pr\n", name="pr1")  # overlaps once main edits a.txt
    f.branch_pr(2, "b.txt", "pr\n", name="pr2")  # disjoint
    f.branch_pr(3, "c.txt", "pr\n", name="pr3", message="pr 3\n\nClaude-Session: https://claude.ai/code/session_BUILD\n")
    f.advance_main("a.txt", "main\n")
    project = tmp / "project"
    project.mkdir()
    fx.install_project(project)
    # the project dir is itself the gated checkout
    fx.git(project, "init", "-b", "main")
    fx.git(project, "remote", "add", "origin", f.origin_url)
    f.set_github_identity(project)
    other = tmp / "other"
    other.mkdir()
    fx.git(other, "init", "-b", "main")
    outside = tmp / "outside"
    outside.mkdir()
    return {"project": project, "other": other, "outside": outside, "origin": f.origin, "tmp": tmp}


def _run(env: dict[str, Path], payload: str, cwd: str = "other", **kw):  # noqa: ANN003, ANN202
    return fx.run_shim(env["project"], payload, cwd=env[cwd], **kw)


def _no_private_ref(env: dict[str, Path]) -> None:
    refs = fx.git(env["project"], "for-each-ref", "refs/merge-overlap")
    assert refs == ""


@pytest.mark.parametrize("cwd", ["other", "outside"])
def test_gates_the_fixture_checkout_whatever_the_cwd(env: dict[str, Path], cwd: str) -> None:
    r = _run(env, fx.merge_payload(1), cwd)
    assert r.returncode == 2 and "OVERLAP" in r.stderr


def test_overlap_denies_naming_files_and_remedy(env: dict[str, Path]) -> None:
    r = _run(env, fx.merge_payload(1))
    assert r.returncode == 2
    assert "a.txt" in r.stderr and "update_pull_request_branch" in r.stderr
    assert "unavailable" not in r.stderr.lower()
    _no_private_ref(env)


def test_clear_allows_silently(env: dict[str, Path]) -> None:
    r = _run(env, fx.merge_payload(2))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")
    _no_private_ref(env)


def test_float_pull_number_accepted(env: dict[str, Path]) -> None:
    assert _run(env, fx.merge_payload(2.0)).returncode == 0


def test_auto_merge_and_full_server_gated_the_same(env: dict[str, Path]) -> None:
    for tool in ("mcp__github__enable_pr_auto_merge", "mcp__github-full__merge_pull_request"):
        r = _run(env, fx.merge_payload(1, tool=tool))
        assert r.returncode == 2 and "OVERLAP" in r.stderr


def test_fetch_failure_denies(env: dict[str, Path]) -> None:
    r = _run(env, fx.merge_payload(999))
    assert r.returncode == 2 and "fetch failed" in r.stderr


def test_malformed_json_denies(env: dict[str, Path]) -> None:
    r = _run(env, '{"tool_name": "mcp__github__merge_pull_request", ')
    assert r.returncode == 2 and "malformed" in r.stderr


@pytest.mark.parametrize("number", [None, True, "1", 0, -3])
def test_bad_pull_number_denies(env: dict[str, Path], number: object) -> None:
    r = _run(env, fx.merge_payload(number))
    assert r.returncode == 2 and "pullNumber" in r.stderr


def test_malformed_expected_sha_denies(env: dict[str, Path]) -> None:
    r = _run(env, fx.merge_payload(2, expectedHeadSha="zzzz"))
    assert r.returncode == 2 and "zzzz" in r.stderr


def test_mismatched_expected_sha_names_both(env: dict[str, Path]) -> None:
    r = _run(env, fx.merge_payload(2, expectedHeadSha="deadbeef"))
    assert r.returncode == 2 and "deadbeef" in r.stderr and "differs" in r.stderr


def test_short_and_upper_expected_sha_resolve(env: dict[str, Path]) -> None:
    head = fx.git(env["origin"], "rev-parse", "refs/pull/2/head")
    assert _run(env, fx.merge_payload(2, expectedHeadSha=head[:8].upper())).returncode == 0
    assert _run(env, fx.merge_payload(2, expectedHeadSha=head)).returncode == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/OTL-Labs/OnTheLoop",
        "git@github.com:otl-labs/ontheloop.git",
        "http://proxyuser@127.0.0.1:41234/git/otl-labs/ontheloop",
    ],
)
def test_origin_forms_reach_the_predicate(env: dict[str, Path], url: str) -> None:
    project = env["project"]
    old = fx.git(project, "config", "--get", "remote.origin.url")
    fx.git(project, "config", "remote.origin.url", url)
    fx.git(project, "config", "--add", f"url.file://{env['origin']}.insteadOf", url)
    try:
        r = _run(env, fx.merge_payload(1))
    finally:
        fx.git(project, "config", "remote.origin.url", old)
        fx.git(project, "config", "--fixed-value", "--unset", f"url.file://{env['origin']}.insteadOf", url)
    assert r.returncode == 2 and "OVERLAP" in r.stderr


def test_insteadof_mapped_origin_reads_identity_from_config(env: dict[str, Path]) -> None:
    assert fx.git(env["project"], "config", "--get", "remote.origin.url") == fx.GITHUB_URL
    assert "OVERLAP" in _run(env, fx.merge_payload(1)).stderr


def test_missing_origin_denies_unavailable(env: dict[str, Path]) -> None:
    project = env["project"]
    old = fx.git(project, "config", "--get", "remote.origin.url")
    fx.git(project, "remote", "remove", "origin")
    try:
        r = _run(env, fx.merge_payload(1))
    finally:
        fx.git(project, "remote", "add", "origin", env["origin"].as_uri())
        fx.git(project, "config", "remote.origin.url", old)
    assert r.returncode == 2 and "UNAVAILABLE" in r.stderr


def test_other_repository_and_other_tool_allowed(env: dict[str, Path]) -> None:
    import json

    other_repo = json.dumps(
        {"tool_name": "mcp__github__merge_pull_request", "tool_input": {"owner": "x", "repo": "y", "pullNumber": 1}}
    )
    assert _run(env, other_repo).returncode == 0
    assert _run(env, fx.merge_payload(1, tool="mcp__github__merge_pull_request_note")).returncode == 0
    assert _run(env, fx.merge_payload(1, tool="mcp__github__get_file_contents")).returncode == 0


def test_fetch_leaves_fetch_head_untouched_and_uses_private_ref(env: dict[str, Path]) -> None:
    fh = env["project"] / ".git" / "FETCH_HEAD"
    before = fh.read_bytes() if fh.exists() else None
    assert _run(env, fx.merge_payload(1)).returncode == 2
    assert (fh.read_bytes() if fh.exists() else None) == before
    _no_private_ref(env)


def test_shallow_clone_resolves_only_after_deepen(tmp_path: Path) -> None:
    f = fx.Fixture(tmp_path)
    f.branch_pr(1, "a.txt", "pr\n")
    for i in range(4):
        f.advance_main("a.txt" if i == 3 else f"m{i}.txt", f"{i}\n")
    project = f.clone("shallow", depth=1)
    assert fx.git(project, "rev-parse", "--is-shallow-repository") == "true"
    fx.install_project(project)
    r = fx.run_shim(project, fx.merge_payload(1), cwd=tmp_path)
    assert r.returncode == 2 and "OVERLAP" in r.stderr and "a.txt" in r.stderr


_BUILD = "pr 3\n\nClaude-Session: https://claude.ai/code/session_BUILD\n"


def test_foreign_trailer_in_commit_message_denies(env: dict[str, Path]) -> None:
    msg = "pr 3\n\nClaude-Session: https://claude.ai/code/session_COORD\n"
    r = _run(env, fx.merge_payload(3, commit_message=msg))
    assert r.returncode == 2 and "attribution" in r.stderr
    assert "Claude-Session: https://claude.ai/code/session_BUILD" in r.stderr
    _no_private_ref(env)


def test_branch_trailer_in_commit_message_allows(env: dict[str, Path]) -> None:
    r = _run(env, fx.merge_payload(3, commit_message=_BUILD))
    assert (r.returncode, r.stdout, r.stderr) == (0, "", "")


def test_dropped_branch_trailer_denies(env: dict[str, Path]) -> None:
    r = _run(env, fx.merge_payload(3, commit_message="pr 3\n"))
    assert r.returncode == 2 and "missing ['BUILD']" in r.stderr


def test_no_commit_message_or_no_trailer_unchanged(env: dict[str, Path]) -> None:
    assert _run(env, fx.merge_payload(3)).returncode == 0
    assert _run(env, fx.merge_payload(2, commit_message="pr 2\n")).returncode == 0


def test_non_string_commit_message_denies(env: dict[str, Path]) -> None:
    r = _run(env, fx.merge_payload(3, commit_message=["x"]))
    assert r.returncode == 2 and "commit_message" in r.stderr and "malformed" in r.stderr
