"""Push-path and fetch behaviour of scripts/ci/ducklake_artifacts.py beyond the two filed nodes in
tests/test_ducklake_artifacts.py (rec-4372 attribution, rec-4198 diagnostic isolation). Every
fail-closed node asserts the message's final not-attributed line names its own reason; every
push-path node pins or clears GITHUB_SHA (CI sets it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scripts.ci.ducklake_artifacts import (
    DUCKLAKE_ARTIFACT_NAMES,
    DucklakeArtifactError,
    assert_and_upload,
    fetch_reviewed,
    main,
    upload_only,
)
from scripts.ci.ducklake_inputs import record_inputs
from tests.fixtures.ducklake_artifact_fakes import (
    _FakeS3Client,
    _git_commit_all,
    _git_init,
    _NoSuchKeyError,
    _write_packages,
    _zip_bytes,
)


def _write_writer_fixture(repo: Path, foo_content: str) -> None:
    manifest_dir = repo / "src" / "lambdas" / "ducklake_writer"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.joinpath("manifest.yaml").write_text("artifact: ducklake-writer.zip\nincludes:\n  - src/common/foo.py\n")
    common_dir = repo / "src" / "common"
    common_dir.mkdir(parents=True, exist_ok=True)
    common_dir.joinpath("foo.py").write_text(foo_content)


def _build_writer_zip(packages_dir: Path, foo_content: str) -> bytes:
    packages_dir.mkdir(parents=True, exist_ok=True)
    data = _zip_bytes({"src/common/foo.py": foo_content.encode()})
    packages_dir.joinpath("ducklake-writer.zip").write_bytes(data)
    return data


def _seed_reference(client: _FakeS3Client, pr_sha: str, name: str, zip_bytes: bytes, record: dict[str, Any]) -> None:
    client.objects[f"lambda-packages/{pr_sha}/{name}"] = zip_bytes
    client.objects[f"lambda-packages/{pr_sha}/inputs.json"] = json.dumps(record).encode()


# ---------------------------------------------------------------------------
# Fail-closed push-path nodes
# ---------------------------------------------------------------------------


def test_push_same_inputs_different_bytes_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    pr_sha = _git_commit_all(repo, "init")

    pr_dir = tmp_path / "pr"
    pr_bytes = _build_writer_zip(pr_dir, "print('v1')\n")
    reference = record_inputs(pr_dir, ["ducklake-writer.zip"], repo)

    merge_sha = pr_sha  # no file actually moved; use a distinct sha below
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    merge_sha = "f" * 40
    packages_dir = repo / "lambda-packages"
    # Same content as the reference, but different zip compression -> container-only byte diff.
    packages_dir.mkdir()
    packages_dir.joinpath("ducklake-writer.zip").write_bytes(
        _zip_bytes({"src/common/foo.py": b"print('v1')\n"}, compression=0)
    )

    client = _FakeS3Client()
    _seed_reference(client, pr_sha, "ducklake-writer.zip", pr_bytes, reference)
    monkeypatch.chdir(repo)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(
            ["ducklake-writer.zip"], pr_sha, "push", "bucket", packages_dir=packages_dir, s3_client=client, merge_sha=merge_sha
        )
    assert "DUCKLAKE_ZIP_MISMATCH" in str(excinfo.value)
    assert "not attributed:" in str(excinfo.value)
    assert "only the container bytes differ" in str(excinfo.value)
    assert client.uploads == []


def test_push_mismatch_without_inputs_record_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "shanoinputsrecord"
    client = _FakeS3Client(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"remote-bytes"})
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert "not attributed:" in str(excinfo.value)
    assert client.uploads == []


def test_push_layer_mismatch_without_moved_input_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git_init(repo)
    version_dir = repo / "config" / "lambda" / "ducklake"
    version_dir.mkdir(parents=True)
    version_dir.joinpath("version.yaml").write_text("duckdb_version: 1.0\n")
    pr_sha = _git_commit_all(repo, "init")

    pr_dir = tmp_path / "pr"
    pr_dir.mkdir()
    pr_bytes = _zip_bytes({"site-packages/x.py": b"v1"})
    pr_dir.joinpath("ducklake-deps-layer.zip").write_bytes(pr_bytes)
    reference = record_inputs(pr_dir, ["ducklake-deps-layer.zip"], repo)

    merge_sha = "b" * 40
    packages_dir = repo / "lambda-packages"
    packages_dir.mkdir()
    packages_dir.joinpath("ducklake-deps-layer.zip").write_bytes(_zip_bytes({"site-packages/x.py": b"v2-different-bytes"}))

    client = _FakeS3Client()
    _seed_reference(client, pr_sha, "ducklake-deps-layer.zip", pr_bytes, reference)
    monkeypatch.chdir(repo)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(
            ["ducklake-deps-layer.zip"],
            pr_sha,
            "push",
            "bucket",
            packages_dir=packages_dir,
            s3_client=client,
            merge_sha=merge_sha,
        )
    assert "not attributed:" in str(excinfo.value)
    assert "tracked input did not move" in str(excinfo.value)
    assert client.uploads == []


def test_push_partly_attributed_mismatch_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    version_dir = repo / "config" / "lambda" / "ducklake"
    version_dir.mkdir(parents=True)
    version_dir.joinpath("version.yaml").write_text("duckdb_version: 1.0\n")
    _git_init(repo)
    pr_sha = _git_commit_all(repo, "init")

    pr_dir = tmp_path / "pr"
    pr_writer_bytes = _build_writer_zip(pr_dir, "print('v1')\n")
    pr_layer_bytes = _zip_bytes({"site-packages/x.py": b"v1"})
    pr_dir.joinpath("ducklake-deps-layer.zip").write_bytes(pr_layer_bytes)
    reference = record_inputs(pr_dir, ["ducklake-writer.zip", "ducklake-deps-layer.zip"], repo)

    (repo / "src" / "common" / "foo.py").write_text("print('v2')\n")  # genuinely moved
    merge_sha = _git_commit_all(repo, "edit foo (layer untouched)")

    packages_dir = repo / "lambda-packages"
    packages_dir.mkdir()
    packages_dir.joinpath("ducklake-writer.zip").write_bytes(_zip_bytes({"src/common/foo.py": b"print('v2')\n"}))
    packages_dir.joinpath("ducklake-deps-layer.zip").write_bytes(_zip_bytes({"site-packages/x.py": b"v2-container-only"}))

    client = _FakeS3Client()
    _seed_reference(client, pr_sha, "ducklake-writer.zip", pr_writer_bytes, reference)
    client.objects[f"lambda-packages/{pr_sha}/ducklake-deps-layer.zip"] = pr_layer_bytes
    monkeypatch.chdir(repo)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(
            ["ducklake-writer.zip", "ducklake-deps-layer.zip"],
            pr_sha,
            "push",
            "bucket",
            packages_dir=packages_dir,
            s3_client=client,
            merge_sha=merge_sha,
        )
    assert "not attributed:" in str(excinfo.value)
    assert client.uploads == []


def test_push_unset_merge_sha_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "shaunsetmerge"
    client = _FakeS3Client(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"remote-bytes"})
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert "not attributed: no merge sha available" in str(excinfo.value)
    assert client.uploads == []


def test_push_merge_sha_equals_artifact_sha_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "shaselfequal"
    client = _FakeS3Client(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"remote-bytes"})
    monkeypatch.setenv("GITHUB_SHA", sha)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert "not attributed: no merge sha available" in str(excinfo.value)
    assert client.uploads == []


def test_push_inputs_record_not_describing_reference_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "shabadrecord"
    bogus_record = {
        "schema": 1,
        "tooling": {},
        "artifacts": {
            "ducklake-writer.zip": {
                "md5": "wrong-md5-does-not-match-reference",
                "kind": "function",
                "entries": {},
                "inputs": {},
            }
        },
    }
    client = _FakeS3Client(
        objects={
            f"lambda-packages/{sha}/ducklake-writer.zip": b"remote-bytes",
            f"lambda-packages/{sha}/inputs.json": json.dumps(bogus_record).encode(),
        }
    )
    monkeypatch.setenv("GITHUB_SHA", "c" * 40)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert "not attributed: the PR's inputs.json does not describe the reference zips just read" in str(excinfo.value)
    assert client.uploads == []


def test_push_inputs_record_missing_artifact_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Schema matches, but the record's artifacts dict simply has no entry for the mismatched
    name at all -- still 'does not describe the reference'."""
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "shamissingartifact"
    record_without_artifact = {"schema": 1, "tooling": {}, "artifacts": {}}
    client = _FakeS3Client(
        objects={
            f"lambda-packages/{sha}/ducklake-writer.zip": b"remote-bytes",
            f"lambda-packages/{sha}/inputs.json": json.dumps(record_without_artifact).encode(),
        }
    )
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert "not attributed: the PR's inputs.json does not describe the reference zips just read" in str(excinfo.value)
    assert client.uploads == []


def test_push_inputs_record_entries_mismatch_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """md5 matches the reference zip just read, but the recorded entries dict does not -- still
    'does not describe the reference' (a record whose md5 collided or was hand-edited)."""
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "shaentriesmismatch"
    remote_bytes = _zip_bytes({"src/common/foo.py": b"print(1)\n"})
    record_with_wrong_entries = {
        "schema": 1,
        "tooling": {},
        "artifacts": {
            "ducklake-writer.zip": {
                "md5": __import__("hashlib").md5(remote_bytes).hexdigest(),
                "kind": "function",
                "entries": {"src/common/foo.py": "deliberately-wrong-sha256"},
                "inputs": {},
            }
        },
    }
    client = _FakeS3Client(
        objects={
            f"lambda-packages/{sha}/ducklake-writer.zip": remote_bytes,
            f"lambda-packages/{sha}/inputs.json": json.dumps(record_with_wrong_entries).encode(),
        }
    )
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert "not attributed: the PR's inputs.json does not describe the reference zips just read" in str(excinfo.value)
    assert client.uploads == []


def test_push_schema_mismatch_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "shaschema"
    wrong_schema_record = {"schema": 0, "tooling": {}, "artifacts": {}}
    client = _FakeS3Client(
        objects={
            f"lambda-packages/{sha}/ducklake-writer.zip": b"remote-bytes",
            f"lambda-packages/{sha}/inputs.json": json.dumps(wrong_schema_record).encode(),
        }
    )
    monkeypatch.setenv("GITHUB_SHA", "d" * 40)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert "not attributed: the PR's inputs.json does not describe the reference zips just read" in str(excinfo.value)
    assert client.uploads == []


def test_push_malformed_inputs_record_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "shamalformed"
    client = _FakeS3Client(
        objects={
            f"lambda-packages/{sha}/ducklake-writer.zip": b"remote-bytes",
            f"lambda-packages/{sha}/inputs.json": b"{not valid json!!",
        }
    )
    monkeypatch.setenv("GITHUB_SHA", "e" * 40)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert "not attributed:" in str(excinfo.value)
    assert client.uploads == []


def test_push_identical_with_preexisting_pointer_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"same-bytes")
    sha = "shapointerexists"
    pointer = {"schema": 1, "artifact_sha": "g" * 40, "reason": "DUCKLAKE_ZIP_INPUTS_MOVED", "moved": {}}
    client = _FakeS3Client(
        objects={
            f"lambda-packages/{sha}/ducklake-writer.zip": b"same-bytes",
            f"lambda-packages/{sha}/superseded-by.json": json.dumps(pointer).encode(),
        }
    )
    monkeypatch.setenv("GITHUB_SHA", "h" * 40)

    with pytest.raises(DucklakeArtifactError, match="refusing to upload over a pointer"):
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert client.uploads == []


def test_push_identical_with_403_shaped_pointer_read_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 (or any non-404/NoSuchKey) error reading the pointer fails closed even when every
    zip is otherwise byte-identical."""
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"same-bytes")
    sha = "sha403pointer"

    class _Forbidden(_FakeS3Client):
        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
            if Key.endswith("superseded-by.json"):
                raise _NoSuchKeyError("Forbidden", code="403")
            return super().get_object(Bucket=Bucket, Key=Key)

    client = _Forbidden(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"same-bytes"})
    monkeypatch.setenv("GITHUB_SHA", "i" * 40)

    with pytest.raises(DucklakeArtifactError, match="DUCKLAKE_FETCH_BAD_POINTER"):
        assert_and_upload(names, sha, "push", "bucket", packages_dir=packages_dir, s3_client=client)
    assert client.uploads == []


def test_push_non_verbatim_entry_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    pr_sha = _git_commit_all(repo, "init")

    pr_dir = tmp_path / "pr"
    pr_bytes = _build_writer_zip(pr_dir, "print('v1')\n")
    reference = record_inputs(pr_dir, ["ducklake-writer.zip"], repo)

    merge_sha = "j" * 40
    packages_dir = repo / "lambda-packages"
    packages_dir.mkdir()
    # The repo's foo.py is untouched (same tracked blob); the rebuilt zip entry is not a verbatim
    # copy of it (e.g. a filtering bug), so it earns no input and md5 still differs.
    packages_dir.joinpath("ducklake-writer.zip").write_bytes(_zip_bytes({"src/common/foo.py": b"print('v1')  # filtered\n"}))

    client = _FakeS3Client()
    _seed_reference(client, pr_sha, "ducklake-writer.zip", pr_bytes, reference)
    monkeypatch.chdir(repo)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(
            ["ducklake-writer.zip"], pr_sha, "push", "bucket", packages_dir=packages_dir, s3_client=client, merge_sha=merge_sha
        )
    assert "not attributed:" in str(excinfo.value)
    assert client.uploads == []


# ---------------------------------------------------------------------------
# fetch_reviewed: follows a superseded-by.json pointer one hop
# ---------------------------------------------------------------------------


def test_fetch_follows_pointer_one_hop(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip"]
    pr_sha = "a" * 40
    merge_sha = "1" * 40
    pointer = {"schema": 1, "artifact_sha": merge_sha, "reason": "DUCKLAKE_ZIP_INPUTS_MOVED", "moved": {}}
    client = _FakeS3Client(
        objects={
            f"lambda-packages/{pr_sha}/superseded-by.json": json.dumps(pointer).encode(),
            f"lambda-packages/{merge_sha}/ducklake-writer.zip": b"merge-tree-bytes",
        }
    )

    fetch_reviewed(names, pr_sha, "bucket", packages_dir=tmp_path, s3_client=client)

    assert (tmp_path / "ducklake-writer.zip").read_bytes() == b"merge-tree-bytes"
    assert client.downloads == [
        ("bucket", f"lambda-packages/{merge_sha}/ducklake-writer.zip", str(tmp_path / "ducklake-writer.zip"))
    ]
    assert client.copies == [
        (
            "bucket",
            {"Bucket": "bucket", "Key": f"lambda-packages/{merge_sha}/ducklake-writer.zip"},
            "lambda-packages/ducklake-writer.zip",
        )
    ]


def test_fetch_bad_pointer_fails_closed(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip"]
    sha = "n" * 40

    malformed = _FakeS3Client(objects={f"lambda-packages/{sha}/superseded-by.json": b"{not json"})
    with pytest.raises(DucklakeArtifactError, match="DUCKLAKE_FETCH_BAD_POINTER"):
        fetch_reviewed(names, sha, "bucket", packages_dir=tmp_path, s3_client=malformed)
    assert malformed.downloads == []

    self_ref_pointer = {"schema": 1, "artifact_sha": sha, "reason": "x", "moved": {}}
    self_referencing = _FakeS3Client(
        objects={f"lambda-packages/{sha}/superseded-by.json": json.dumps(self_ref_pointer).encode()}
    )
    with pytest.raises(DucklakeArtifactError, match="DUCKLAKE_FETCH_BAD_POINTER"):
        fetch_reviewed(names, sha, "bucket", packages_dir=tmp_path, s3_client=self_referencing)
    assert self_referencing.downloads == []

    class _Forbidden(_FakeS3Client):
        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
            if Key.endswith("superseded-by.json"):
                raise _NoSuchKeyError("Forbidden", code="403")
            return super().get_object(Bucket=Bucket, Key=Key)

    forbidden = _Forbidden(objects={})
    with pytest.raises(DucklakeArtifactError, match="DUCKLAKE_FETCH_BAD_POINTER"):
        fetch_reviewed(names, sha, "bucket", packages_dir=tmp_path, s3_client=forbidden)
    assert forbidden.downloads == []


def test_fetch_pointer_target_missing_fails_closed(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip"]
    pr_sha = "b" * 40
    merge_sha = "2" * 40
    pointer = {"schema": 1, "artifact_sha": merge_sha, "reason": "x", "moved": {}}
    client = _FakeS3Client(objects={f"lambda-packages/{pr_sha}/superseded-by.json": json.dumps(pointer).encode()})

    with pytest.raises(DucklakeArtifactError, match="DUCKLAKE_FETCH_MISSING"):
        fetch_reviewed(names, pr_sha, "bucket", packages_dir=tmp_path, s3_client=client)
    assert client.copies == []


# ---------------------------------------------------------------------------
# upload_only() + main() --mode upload-only
# ---------------------------------------------------------------------------


def test_main_upload_only_invokes_expected_build_and_skips_assert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main()'s --mode upload-only runs exactly `scripts.build_lambda --ducklake-only --profile ""
    --bucket <bucket>` with no --skip-upload, and never calls assert_and_upload."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = _FakeS3Client()
    calls: list[Any] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
        calls.append(cmd)
        return MagicMock(returncode=0)

    with patch("scripts.ci.ducklake_artifacts.subprocess.run", side_effect=_fake_run):
        with patch("scripts.ci.ducklake_artifacts.record_inputs", return_value={"schema": 1, "tooling": {}, "artifacts": {}}):
            with patch("scripts.ci.ducklake_artifacts.assert_and_upload") as mock_assert:
                with patch.dict("sys.modules", {"boto3": fake_boto3}):
                    rc = main(["pull_request", "a" * 40, "my-bucket", "--mode", "upload-only"])

    assert rc == 0
    mock_assert.assert_not_called()
    assert len(calls) == 1
    cmd = calls[0]
    assert "scripts.build_lambda" in cmd
    assert "--ducklake-only" in cmd
    assert "--skip-upload" not in cmd
    assert cmd[cmd.index("--bucket") + 1] == "my-bucket"


def test_upload_only_writes_inputs_record_after_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    monkeypatch.chdir(repo)
    packages_dir = repo / "lambda-packages"
    packages_dir.mkdir()
    for name in DUCKLAKE_ARTIFACT_NAMES:
        if name != "ducklake-writer.zip":
            (packages_dir / name).write_bytes(_zip_bytes({}))
    _build_writer_zip(packages_dir, "print('v1')\n")

    client = _FakeS3Client()
    sha = "a" * 40
    real_run = __import__("subprocess").run

    def _run(cmd: list[str], **kwargs: Any) -> Any:
        if "scripts.build_lambda" in cmd:
            return MagicMock(returncode=0)
        return real_run(cmd, **kwargs)  # let the git ls-files call through for real

    with patch("scripts.ci.ducklake_artifacts.subprocess.run", side_effect=_run) as mock_run:
        upload_only(sha, "my-bucket", packages_dir=packages_dir, root=repo, s3_client=client)

    build_calls = [c for c in mock_run.call_args_list if "scripts.build_lambda" in c.args[0]]
    assert len(build_calls) == 1
    key = f"lambda-packages/{sha}/inputs.json"
    assert key in client.objects
    record = json.loads(client.objects[key])
    assert record["schema"] == 1
    assert "ducklake-writer.zip" in record["artifacts"]


def test_upload_only_record_upload_failure_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _write_writer_fixture(repo, "print('v1')\n")
    _git_init(repo)
    _git_commit_all(repo, "init")
    monkeypatch.chdir(repo)
    for name in DUCKLAKE_ARTIFACT_NAMES:
        (repo / "lambda-packages").mkdir(exist_ok=True)
        (repo / "lambda-packages" / name).write_bytes(_zip_bytes({}))
    _build_writer_zip(repo / "lambda-packages", "print('v1')\n")

    class _BrokenPut(_FakeS3Client):
        def put_object(self, **kwargs: Any) -> None:
            raise RuntimeError("S3 is down")

    client = _BrokenPut()
    with patch("scripts.ci.ducklake_artifacts.subprocess.run", return_value=MagicMock(returncode=0)):
        with pytest.raises(DucklakeArtifactError, match="DUCKLAKE_INPUTS_RECORD_FAILED"):
            upload_only("a" * 40, "my-bucket", packages_dir=repo / "lambda-packages", root=repo, s3_client=client)


def test_main_mode_upload_only_surfaces_error_as_exit_one() -> None:
    with patch(
        "scripts.ci.ducklake_artifacts.upload_only", side_effect=DucklakeArtifactError("DUCKLAKE_INPUTS_RECORD_FAILED boom")
    ):
        rc = main(["pull_request", "sha1", "bucket", "--mode", "upload-only"])
    assert rc == 1


# ---------------------------------------------------------------------------
# action.yml wiring: the upload-only branch runs scripts.ci.ducklake_artifacts --mode upload-only
# ---------------------------------------------------------------------------


def test_upload_only_branch_invokes_artifacts_module() -> None:
    action_path = Path(__file__).resolve().parents[1] / ".github" / "actions" / "build-ducklake-artifacts" / "action.yml"
    body = action_path.read_text(encoding="utf-8")
    start = body.index('if [ "$MODE" = "upload-only" ]; then')
    end = body.index('if [ "$MODE" = "fetch" ]; then')
    upload_only_section = body[start:end]
    assert "scripts.ci.ducklake_artifacts" in upload_only_section
    assert "--mode upload-only" in upload_only_section
    assert "scripts.build_lambda" not in upload_only_section
