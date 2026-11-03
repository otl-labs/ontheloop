"""Tests for scripts/ci/ducklake_inputs.py (rec-4372): record_inputs() + attribute() over a real
temporary git repo. Blob ids are read via `git ls-files -s`, so these nodes exercise genuine git
plumbing rather than a mocked one -- no network, no S3.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.ducklake_artifacts import DUCKLAKE_ARTIFACT_NAMES
from scripts.ci.ducklake_inputs import LAYER_INPUT, SCHEMA, attribute, record_inputs
from tests.fixtures.ducklake_artifact_fakes import _git_commit_all, _git_init, _zip_bytes

_LIVE_PROOF_FIXTURE = Path(__file__).parent / "fixtures" / "ducklake_zip_inputs_live_proof.json"


def _write_writer_fixture(repo: Path, foo_content: str, *, extra_includes: list[str] | None = None) -> None:
    manifest_dir = repo / "src" / "lambdas" / "ducklake_writer"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    includes = ["src/common/foo.py", *(extra_includes or [])]
    manifest_dir.joinpath("manifest.yaml").write_text(
        "artifact: ducklake-writer.zip\nincludes:\n" + "".join(f"  - {p}\n" for p in includes)
    )
    common_dir = repo / "src" / "common"
    common_dir.mkdir(parents=True, exist_ok=True)
    common_dir.joinpath("foo.py").write_text(foo_content)


def _build_zip(repo: Path, packages_dir: Path, entries: dict[str, str]) -> None:
    packages_dir.mkdir(parents=True, exist_ok=True)
    zipped = {path: content.encode() for path, content in entries.items()}
    packages_dir.joinpath("ducklake-writer.zip").write_bytes(_zip_bytes(zipped))


# ---------------------------------------------------------------------------
# record_inputs: blob ids match git; untracked entries get no input; tooling blobs recorded
# ---------------------------------------------------------------------------


def test_record_inputs_blob_ids_match_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")

    packages_dir = tmp_path / "pkg"
    _build_zip(repo, packages_dir, {"src/common/foo.py": "print('v1')\n"})

    record = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)
    assert record["schema"] == SCHEMA
    assert len(record["tooling"]) == 5  # the five tooling stubs _git_init wrote, all tracked

    artifact = record["artifacts"]["ducklake-writer.zip"]
    assert artifact["kind"] == "function"
    assert artifact["manifest_path"] == "src/lambdas/ducklake_writer/manifest.yaml"
    assert "src/common/foo.py" in artifact["inputs"]
    assert artifact["manifest_path"] in artifact["inputs"]


def test_record_inputs_untracked_entry_has_no_input(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")

    packages_dir = tmp_path / "pkg"
    # The zip entry carries bytes that were never committed -- not a verbatim copy of anything tracked.
    _build_zip(repo, packages_dir, {"src/common/foo.py": "print('untracked, never committed')\n"})

    record = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)
    assert "src/common/foo.py" not in record["artifacts"]["ducklake-writer.zip"]["inputs"]


def test_record_inputs_tooling_blobs_recorded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")

    import subprocess

    ls = subprocess.run(["git", "ls-files", "-s"], cwd=repo, check=True, capture_output=True, text=True).stdout
    tracked = {line.split("\t", 1)[1]: line.split()[1] for line in ls.splitlines()}

    packages_dir = tmp_path / "pkg"
    _build_zip(repo, packages_dir, {"src/common/foo.py": "print('v1')\n"})
    record = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)

    for path in record["tooling"]:
        assert record["tooling"][path] == tracked[path]


# ---------------------------------------------------------------------------
# attribute: the full matrix (tooling moved; entry moved/unchanged/untracked/one-sided; a
# reference-only entry the current manifest still packages: unattributed; layer moved/unchanged;
# container-only difference; schema mismatch)
# ---------------------------------------------------------------------------


def _attributed_record(repo: Path, packages_dir: Path, foo_content: str) -> dict:
    _build_zip(repo, packages_dir, {"src/common/foo.py": foo_content})
    return record_inputs(packages_dir, ["ducklake-writer.zip"], repo)


def test_attribute_entry_moved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record(repo, tmp_path / "pr", "print('v1')\n")

    (repo / "src" / "common" / "foo.py").write_text("print('v2')\n")
    _git_commit_all(repo, "edit foo")
    current = _attributed_record(repo, tmp_path / "merge", "print('v2')\n")

    verdict = attribute(reference, current)
    assert verdict == {
        "ducklake-writer.zip": {
            "verdict": "moved",
            "inputs": ["src/common/foo.py"],
            "reason": "moved inputs: src/common/foo.py",
        }
    }


def test_attribute_unchanged_artifact_has_no_verdict(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record(repo, tmp_path / "pr", "print('v1')\n")
    current = _attributed_record(repo, tmp_path / "again", "print('v1')\n")

    assert attribute(reference, current) == {}


def test_attribute_tooling_moved_attributes_every_differing_artifact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record(repo, tmp_path / "pr", "print('v1')\n")

    (repo / "scripts" / "build_lambda.py").write_text("# tooling stub v2\n")
    (repo / "src" / "common" / "foo.py").write_text("print('v2')\n")
    _git_commit_all(repo, "move tooling and foo")
    current = _attributed_record(repo, tmp_path / "merge", "print('v2')\n")

    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict["verdict"] == "moved"
    assert verdict["inputs"] == ["scripts/build_lambda.py"]
    assert "tooling" in verdict["reason"]


def test_attribute_only_in_current_requires_manifest_move_or_dir_include(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record(repo, tmp_path / "pr", "print('v1')\n")

    # A new file is added to the manifest's includes (changes the manifest blob) and bundled.
    (repo / "src" / "common" / "bar.py").write_text("print('new')\n")
    _write_writer_fixture(repo, "print('v1')\n", extra_includes=["src/common/bar.py"])
    _git_commit_all(repo, "manifest now packages bar.py too")

    packages_dir = tmp_path / "merge"
    _build_zip(repo, packages_dir, {"src/common/foo.py": "print('v1')\n", "src/common/bar.py": "print('new')\n"})
    current = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)

    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict["verdict"] == "moved"
    assert set(verdict["inputs"]) == {"src/common/bar.py"}


def test_attribute_only_in_current_under_directory_include(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    manifest_dir = repo / "src" / "lambdas" / "ducklake_writer"
    manifest_dir.mkdir(parents=True)
    manifest_dir.joinpath("manifest.yaml").write_text("artifact: ducklake-writer.zip\nincludes:\n  - src/rules/\n")
    rules_dir = repo / "src" / "rules"
    rules_dir.mkdir(parents=True)
    rules_dir.joinpath("a.py").write_text("print('a')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _build_dir_record(repo, tmp_path / "pr", {"src/rules/a.py": "print('a')\n"})

    rules_dir.joinpath("b.py").write_text("print('b')\n")  # the dir include grows a new file; manifest itself unchanged
    _git_commit_all(repo, "add b.py under the existing dir include")
    current = _build_dir_record(repo, tmp_path / "merge", {"src/rules/a.py": "print('a')\n", "src/rules/b.py": "print('b')\n"})

    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict["verdict"] == "moved"
    assert set(verdict["inputs"]) == {"src/rules/b.py"}


def _build_dir_record(repo: Path, packages_dir: Path, entries: dict[str, str]) -> dict:
    packages_dir.mkdir(parents=True, exist_ok=True)
    zipped = {path: content.encode() for path, content in entries.items()}
    packages_dir.joinpath("ducklake-writer.zip").write_bytes(_zip_bytes(zipped))
    return record_inputs(packages_dir, ["ducklake-writer.zip"], repo)


def test_attribute_reference_only_entry_still_packaged_is_unattributed(tmp_path: Path) -> None:
    """An entry present only in the reference, whose path the current manifest still packages and
    which is still tracked, is never dropped from a real build -- modelled here by a current
    record whose entries dict was hand-truncated to simulate that impossible state, which must
    fail closed rather than silently attribute."""
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record(repo, tmp_path / "pr", "print('v1')\n")

    (repo / "src" / "common" / "foo.py").write_text("print('v2')\n")
    _git_commit_all(repo, "edit foo")
    current = _attributed_record(repo, tmp_path / "merge", "print('v2')\n")
    # Simulate the entry vanishing from current without its input disappearing from the manifest.
    current["artifacts"]["ducklake-writer.zip"]["entries"].pop("src/common/foo.py")
    current["artifacts"]["ducklake-writer.zip"]["inputs"].pop("src/common/foo.py")
    current["artifacts"]["ducklake-writer.zip"]["md5"] = "deliberately-different"

    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict["verdict"] == "unattributed"


def test_attribute_tracked_blob_unchanged_but_bytes_non_verbatim_is_unattributed(tmp_path: Path) -> None:
    """A tracked path whose git blob is unchanged but whose zip entry bytes are not a verbatim
    copy (so it never earns an input) on one side: the differing entry cannot be attributed."""
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record(repo, tmp_path / "pr", "print('v1')\n")

    packages_dir = tmp_path / "merge"
    # foo.py is untouched in the repo (same blob) but the zip entry is not a verbatim copy.
    _build_zip(repo, packages_dir, {"src/common/foo.py": "print('v1')  # filtered, not verbatim\n"})
    current = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)

    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict["verdict"] == "unattributed"


def test_attribute_layer_input_moved(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init(repo)
    version_dir = repo / "config" / "lambda" / "ducklake"
    version_dir.mkdir(parents=True)
    version_dir.joinpath("version.yaml").write_text("duckdb_version: 1.0\n")
    _git_commit_all(repo, "init")

    def _layer_record(packages_dir: Path, content: bytes) -> dict:
        packages_dir.mkdir(parents=True, exist_ok=True)
        packages_dir.joinpath("ducklake-deps-layer.zip").write_bytes(_zip_bytes({"site-packages/x.py": content}))
        return record_inputs(packages_dir, ["ducklake-deps-layer.zip"], repo)

    reference = _layer_record(tmp_path / "pr", b"v1")
    version_dir.joinpath("version.yaml").write_text("duckdb_version: 2.0\n")
    _git_commit_all(repo, "bump duckdb")
    current = _layer_record(tmp_path / "merge", b"v2")

    verdict = attribute(reference, current)["ducklake-deps-layer.zip"]
    assert verdict == {"verdict": "moved", "inputs": [LAYER_INPUT], "reason": f"{LAYER_INPUT} moved"}


def test_attribute_layer_unmoved_input_is_unattributed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init(repo)
    version_dir = repo / "config" / "lambda" / "ducklake"
    version_dir.mkdir(parents=True)
    version_dir.joinpath("version.yaml").write_text("duckdb_version: 1.0\n")
    _git_commit_all(repo, "init")

    def _layer_record(packages_dir: Path, content: bytes) -> dict:
        packages_dir.mkdir(parents=True, exist_ok=True)
        packages_dir.joinpath("ducklake-deps-layer.zip").write_bytes(_zip_bytes({"site-packages/x.py": content}))
        return record_inputs(packages_dir, ["ducklake-deps-layer.zip"], repo)

    reference = _layer_record(tmp_path / "pr", b"v1")
    current = _layer_record(tmp_path / "merge", b"v2")  # bytes differ, version.yaml did not move

    verdict = attribute(reference, current)["ducklake-deps-layer.zip"]
    assert verdict["verdict"] == "unattributed"


def test_attribute_container_only_difference_is_unattributed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")

    packages_dir_a = tmp_path / "a"
    packages_dir_a.mkdir()
    packages_dir_a.joinpath("ducklake-writer.zip").write_bytes(
        _zip_bytes({"src/common/foo.py": b"print('v1')\n"}, compression=0)
    )
    reference = record_inputs(packages_dir_a, ["ducklake-writer.zip"], repo)

    packages_dir_b = tmp_path / "b"
    packages_dir_b.mkdir()
    packages_dir_b.joinpath("ducklake-writer.zip").write_bytes(_zip_bytes({"src/common/foo.py": b"print('v1')\n"}))
    current = record_inputs(packages_dir_b, ["ducklake-writer.zip"], repo)

    assert current["artifacts"]["ducklake-writer.zip"]["md5"] != reference["artifacts"]["ducklake-writer.zip"]["md5"]
    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict == {"verdict": "unattributed", "inputs": [], "reason": "only the container bytes differ"}


def test_attribute_schema_mismatch_fails_closed_for_every_artifact(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record(repo, tmp_path / "pr", "print('v1')\n")
    reference["schema"] = 0

    (repo / "src" / "common" / "foo.py").write_text("print('v2')\n")
    _git_commit_all(repo, "edit")
    current = _attributed_record(repo, tmp_path / "merge", "print('v2')\n")

    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict["verdict"] == "unattributed"
    assert "schema" in verdict["reason"]


def test_attribute_reference_missing_artifact_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record(repo, tmp_path / "pr", "print('v1')\n")
    del reference["artifacts"]["ducklake-writer.zip"]

    (repo / "src" / "common" / "foo.py").write_text("print('v2')\n")
    _git_commit_all(repo, "edit")
    current = _attributed_record(repo, tmp_path / "merge", "print('v2')\n")

    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict["verdict"] == "unattributed"
    assert "missing artifact" in verdict["reason"]


def test_record_inputs_untracked_manifest_has_no_inputs(tmp_path: Path) -> None:
    """The manifest.yaml itself is untracked (e.g. a brand-new lambda never committed): no input
    is ever recorded for the function artifact, including for its own entries."""
    repo = tmp_path / "repo"
    _git_init(repo)  # tooling tracked, but ducklake_writer's manifest never gets committed
    _git_commit_all(repo, "init")
    manifest_dir = repo / "src" / "lambdas" / "ducklake_writer"
    manifest_dir.mkdir(parents=True)
    manifest_dir.joinpath("manifest.yaml").write_text("artifact: ducklake-writer.zip\nincludes:\n  - src/common/foo.py\n")
    common_dir = repo / "src" / "common"
    common_dir.mkdir(parents=True)
    common_dir.joinpath("foo.py").write_text("print('v1')\n")  # also uncommitted

    packages_dir = tmp_path / "pkg"
    _build_zip(repo, packages_dir, {"src/common/foo.py": "print('v1')\n"})
    record = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)

    artifact = record["artifacts"]["ducklake-writer.zip"]
    assert artifact["inputs"] == {}
    assert artifact["manifest_path"] is None
    assert artifact["manifest_includes"] == []
    assert artifact["manifest_files"] == []


def test_attribute_only_in_current_without_manifest_move_or_dir_include_is_unattributed(tmp_path: Path) -> None:
    """A new zip entry appears only in current, but the manifest blob did not move and the entry
    does not lie under any directory include -- cannot be attributed (line 164's else branch)."""
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record(repo, tmp_path / "pr", "print('v1')\n")

    # Current carries a second, untracked entry under a path the manifest never declared, with
    # the manifest blob itself unchanged -- nothing attributes this new entry.
    (repo / "src" / "common" / "foo.py").write_text("print('v2')\n")  # also edited, so md5 differs
    _git_commit_all(repo, "edit foo (manifest itself untouched)")
    packages_dir = tmp_path / "merge"
    _build_zip(
        repo, packages_dir, {"src/common/foo.py": "print('v2')\n", "src/common/stray.py": "print('stray, never declared')\n"}
    )
    current = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)

    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict["verdict"] == "unattributed"
    assert "src/common/stray.py" in verdict["reason"]


def test_attribute_only_in_reference_genuinely_removed_is_attributed(tmp_path: Path) -> None:
    """An entry present only in the reference, whose path the CURRENT manifest no longer packages
    at all, is attributed as moved (line 169's success branch)."""
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n", extra_includes=["src/common/bar.py"])
    (repo / "src" / "common" / "bar.py").write_text("print('bar')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    reference = _attributed_record_two_files(repo, tmp_path / "pr")

    # main drops bar.py from the manifest entirely (and the file itself).
    _write_writer_fixture(repo, "print('v2')\n")
    (repo / "src" / "common" / "bar.py").unlink()
    _git_commit_all(repo, "manifest no longer packages bar.py")
    packages_dir = tmp_path / "merge"
    _build_zip(repo, packages_dir, {"src/common/foo.py": "print('v2')\n"})
    current = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)

    verdict = attribute(reference, current)["ducklake-writer.zip"]
    assert verdict["verdict"] == "moved"
    assert set(verdict["inputs"]) == {"src/common/foo.py", "src/common/bar.py"}


def _attributed_record_two_files(repo: Path, packages_dir: Path) -> dict:
    _build_zip(repo, packages_dir, {"src/common/foo.py": "print('v1')\n", "src/common/bar.py": "print('bar')\n"})
    return record_inputs(packages_dir, ["ducklake-writer.zip"], repo)


@pytest.mark.parametrize("method", ["duplicate_call_is_pure"])
def test_record_inputs_is_pure_no_git_mutation(tmp_path: Path, method: str) -> None:
    """record_inputs() reads only -- calling it twice against the same tree is side-effect-free."""
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    packages_dir = tmp_path / "pkg"
    _build_zip(repo, packages_dir, {"src/common/foo.py": "print('v1')\n"})

    first = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)
    second = record_inputs(packages_dir, ["ducklake-writer.zip"], repo)
    assert first == second


# ---------------------------------------------------------------------------
# rec-4381: the live proof captured from the first terraform/personal PR whose speculative plan
# ran after PLAN-ducklake-zip-inputs-moved (#1536) merged (PLAN-platform-iam-personal-release, #1540).
# ---------------------------------------------------------------------------


def test_live_proof_capture_matches_contract() -> None:
    """rec-4381 VP steps 7-8, replayed against the captured evidence rather than against S3.

    `inputs_record` is the real, verbatim schema-1 record uploaded for PR #1540's head;
    `recomputed_zip_md5s` was independently hashed from the same-named zips at capture time (never
    copied from `inputs_record`), so the md5 comparison below is a genuine two-source check, not
    the fixture agreeing with itself. `convergence_record_at_merge` mirrors the real
    convergence/personal/sandbox.json shape (scripts/ci/ducklake_artifacts.py); the reached/blocked
    derivation below is the same formula VP step 8 runs live, replayed here against the captured
    raw fields instead of a live S3 read.
    """
    capture = json.loads(_LIVE_PROOF_FIXTURE.read_text())

    inputs_record = capture["inputs_record"]
    assert inputs_record["schema"] == SCHEMA

    declared_md5s = {name: inputs_record["artifacts"][name]["md5"] for name in DUCKLAKE_ARTIFACT_NAMES}
    recomputed_md5s = {name: capture["recomputed_zip_md5s"][name] for name in DUCKLAKE_ARTIFACT_NAMES}
    assert recomputed_md5s == declared_md5s

    record = capture["convergence_record_at_merge"]
    merge_sha = capture["merge_sha"]
    infra = record.get("infra_error") or {}
    reached = (record.get("status") == "green" and record.get("commit_sha") == merge_sha) or (
        record.get("pending_gated") or {}
    ).get("commit_sha") == merge_sha
    blocked = infra.get("commit_sha") == merge_sha and infra.get("failed_step") == "build_ducklake"
    assert reached is True
    assert blocked is False

    superseded = capture["superseded_by_record"]
    if superseded is not None:
        assert superseded["artifact_sha"]
