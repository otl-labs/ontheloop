"""Tests for scripts/ci/ducklake_artifacts.py (T2.42 c4 / rec-2659, DEP-08; fetch_reviewed +
Decision 154 / rec-2862; rec-4372 push-path attribution + rec-4198 diagnostic-cannot-mask-mismatch).

Covers: byte-mismatch fails closed; on push the assert runs and a missing per-sha object fails
closed; the dual-write targets BOTH the fixed and per-sha keys; workflow_dispatch (any non-"push"
event_name) skips the assert; fetch_reviewed() downloads the reviewed per-sha artifacts and
server-side copies them onto the fixed key with no rebuild, failing closed on any missing object;
rec-4189: the deps layer rebuilds byte-identically across build interpreters and a mismatch names
the differing zip entries; rec-4372: a push-path mismatch whose every difference traces to a
moved tracked input proceeds on merge-tree zips, uploaded only to the fixed key and the merge-sha
prefix, leaving the PR-head prefix untouched and a superseded-by.json pointer behind; rec-4198:
_zip_entry_diff's own failure can never mask the DUCKLAKE_ZIP_MISMATCH it is composing.
No live S3 -- boto3 is fully mocked via an injected fake client.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

import scripts.build_lambda_packaging as bm
from scripts.ci.ducklake_artifacts import (
    DUCKLAKE_ARTIFACT_NAMES,
    DucklakeArtifactError,
    _md5_hex,
    assert_and_upload,
    build_ducklake_only,
    fetch_reviewed,
    main,
)
from scripts.ci.ducklake_inputs import record_inputs
from tests.fixtures.ducklake_artifact_fakes import (
    _FakeBody,  # noqa: F401
    _FakeS3Client,
    _git_commit_all,
    _git_init,
    _NoSuchKeyError,  # noqa: F401
    _write_packages,
    _zip_bytes,
)

# ---------------------------------------------------------------------------
# assert_and_upload -- push path: byte-identical passes, dual-write happens
# ---------------------------------------------------------------------------


def test_push_byte_identical_passes_and_dual_writes(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip", "ducklake-reader.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"same-bytes")
    sha = "artifactsha123"
    client = _FakeS3Client(objects={f"lambda-packages/{sha}/{n}": b"same-bytes" for n in names})

    assert_and_upload(names, sha, "push", "my-bucket", packages_dir=packages_dir, s3_client=client)

    assert len(client.uploads) == 4  # 2 names x (fixed + per-sha)
    for name in names:
        assert (str(packages_dir / name), "my-bucket", f"lambda-packages/{name}") in client.uploads
        assert (str(packages_dir / name), "my-bucket", f"lambda-packages/{sha}/{name}") in client.uploads


# ---------------------------------------------------------------------------
# assert_and_upload -- push path: byte MISMATCH fails closed, no upload attempted
# ---------------------------------------------------------------------------


def test_push_byte_mismatch_fails_closed(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "artifactsha123"
    client = _FakeS3Client(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"different-remote-bytes"})

    with pytest.raises(DucklakeArtifactError, match="DUCKLAKE_ZIP_MISMATCH"):
        assert_and_upload(names, sha, "push", "my-bucket", packages_dir=packages_dir, s3_client=client)

    assert client.uploads == []  # fails closed BEFORE any upload


def test_push_missing_per_sha_object_fails_closed(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "artifactsha123"
    client = _FakeS3Client(objects={})  # nothing uploaded yet -- PR job never ran

    with pytest.raises(DucklakeArtifactError, match="DUCKLAKE_ZIP_MISMATCH"):
        assert_and_upload(names, sha, "push", "my-bucket", packages_dir=packages_dir, s3_client=client)

    assert client.uploads == []


def test_push_mismatch_stops_at_first_bad_name_not_partial_upload(tmp_path: Path) -> None:
    # rec-2755 parity: a mismatch on ANY artifact must fail the whole batch closed, never a
    # partial dual-write of the artifacts that happened to check out fine before it.
    names = ["ducklake-writer.zip", "ducklake-reader.zip", "ducklake-maintenance.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "sha1"
    client = _FakeS3Client(
        objects={
            f"lambda-packages/{sha}/ducklake-writer.zip": b"local-bytes",  # matches
            f"lambda-packages/{sha}/ducklake-reader.zip": b"MISMATCHED",  # fails here
            # ducklake-maintenance.zip never checked -- mismatch on #2 stops the loop
        }
    )
    with pytest.raises(DucklakeArtifactError):
        assert_and_upload(names, sha, "push", "my-bucket", packages_dir=packages_dir, s3_client=client)
    assert client.uploads == []


# ---------------------------------------------------------------------------
# assert_and_upload -- non-push event_name skips the assert entirely
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event_name", ["workflow_dispatch", "pull_request", "something_else"])
def test_non_push_event_names_skip_assert_and_upload_directly(tmp_path: Path, event_name: str) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"whatever-bytes")
    sha = "shaXYZ"
    # No pre-seeded objects at all -- if the assert ran, get_object would raise _NoSuchKeyError.
    client = _FakeS3Client(objects={})

    assert_and_upload(names, sha, event_name, "my-bucket", packages_dir=packages_dir, s3_client=client)

    assert len(client.uploads) == 2  # fixed + per-sha, no assert attempted
    assert (str(packages_dir / "ducklake-writer.zip"), "my-bucket", "lambda-packages/ducklake-writer.zip") in client.uploads
    assert (
        str(packages_dir / "ducklake-writer.zip"),
        "my-bucket",
        f"lambda-packages/{sha}/ducklake-writer.zip",
    ) in client.uploads


# ---------------------------------------------------------------------------
# Dual-write targets BOTH keys for every artifact (full 7-artifact roster)
# ---------------------------------------------------------------------------


def test_dual_write_targets_both_fixed_and_per_sha_keys_for_all_artifacts(tmp_path: Path) -> None:
    names = list(DUCKLAKE_ARTIFACT_NAMES)
    packages_dir = _write_packages(tmp_path, names, content=b"content")
    sha = "fullrostersha"
    client = _FakeS3Client(objects={})

    assert_and_upload(names, sha, "workflow_dispatch", "bucket-x", packages_dir=packages_dir, s3_client=client)

    fixed_keys = {key for (_, _, key) in client.uploads if not key.startswith(f"lambda-packages/{sha}/")}
    per_sha_keys = {key for (_, _, key) in client.uploads if key.startswith(f"lambda-packages/{sha}/")}
    assert fixed_keys == {f"lambda-packages/{n}" for n in names}
    assert per_sha_keys == {f"lambda-packages/{sha}/{n}" for n in names}
    assert len(names) == 7  # sanity: the roster is genuinely the full seven artifacts


# ---------------------------------------------------------------------------
# _md5_hex + s3_client=None default construction
# ---------------------------------------------------------------------------


def test_md5_hex_matches_hashlib() -> None:
    data = b"some artifact bytes"
    assert _md5_hex(data) == hashlib.md5(data).hexdigest()


def test_assert_and_upload_constructs_boto3_client_when_none_injected(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names)
    fake_client = _FakeS3Client(objects={})
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client

    with patch.dict("sys.modules", {"boto3": fake_boto3}):
        assert_and_upload(names, "sha1", "workflow_dispatch", "bucket", region="eu-west-1", packages_dir=packages_dir)

    fake_boto3.client.assert_called_once_with("s3", region_name="eu-west-1")
    assert len(fake_client.uploads) == 2


# ---------------------------------------------------------------------------
# build_ducklake_only -- thin subprocess wrapper
# ---------------------------------------------------------------------------


def test_build_ducklake_only_invokes_expected_command() -> None:
    with patch("scripts.ci.ducklake_artifacts.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        build_ducklake_only(cwd="/some/dir")
    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert "scripts.build_lambda" in cmd
    assert "--ducklake-only" in cmd
    assert "--skip-upload" in cmd
    assert kwargs["cwd"] == "/some/dir"
    assert kwargs["check"] is True


# ---------------------------------------------------------------------------
# CLI entrypoint (main) -- build + assert_and_upload wiring, --skip-build, error surfacing
# ---------------------------------------------------------------------------


def test_main_skip_build_calls_assert_and_upload_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_packages(tmp_path, list(DUCKLAKE_ARTIFACT_NAMES))
    calls: list[Any] = []

    def _fake_assert_and_upload(names, artifact_sha, event_name, bucket, region, **kwargs):
        calls.append((names, artifact_sha, event_name, bucket, region))

    with patch("scripts.ci.ducklake_artifacts.build_ducklake_only") as mock_build:
        with patch("scripts.ci.ducklake_artifacts.assert_and_upload", side_effect=_fake_assert_and_upload):
            rc = main(["push", "sha1", "my-bucket", "--skip-build"])

    assert rc == 0
    mock_build.assert_not_called()
    assert len(calls) == 1
    names, sha, event_name, bucket, region = calls[0]
    assert list(names) == list(DUCKLAKE_ARTIFACT_NAMES)
    assert (sha, event_name, bucket, region) == ("sha1", "push", "my-bucket", "eu-west-2")


def test_main_without_skip_build_calls_build_first() -> None:
    with patch("scripts.ci.ducklake_artifacts.build_ducklake_only") as mock_build:
        with patch("scripts.ci.ducklake_artifacts.assert_and_upload") as mock_assert:
            rc = main(["workflow_dispatch", "sha2", "bucket-y"])
    assert rc == 0
    mock_build.assert_called_once()
    mock_assert.assert_called_once()


def test_main_surfaces_ducklake_artifact_error_as_exit_one(capsys: pytest.CaptureFixture) -> None:
    with patch("scripts.ci.ducklake_artifacts.build_ducklake_only"):
        with patch(
            "scripts.ci.ducklake_artifacts.assert_and_upload",
            side_effect=DucklakeArtifactError("DUCKLAKE_ZIP_MISMATCH boom"),
        ):
            rc = main(["push", "sha3", "bucket-z"])
    assert rc == 1
    assert "DUCKLAKE_ZIP_MISMATCH" in capsys.readouterr().err


def test_main_custom_region_is_passed_through() -> None:
    with patch("scripts.ci.ducklake_artifacts.build_ducklake_only"):
        with patch("scripts.ci.ducklake_artifacts.assert_and_upload") as mock_assert:
            main(["push", "sha4", "bucket-w", "--region", "us-east-1"])
    assert mock_assert.call_args == call(list(DUCKLAKE_ARTIFACT_NAMES), "sha4", "push", "bucket-w", "us-east-1")


# ---------------------------------------------------------------------------
# fetch_reviewed -- Decision 154 / rec-2862: download reviewed per-sha artifacts, server-side
# copy each onto its fixed key, fail closed on any missing object, no rebuild.
# ---------------------------------------------------------------------------


def test_fetch_set_equals_assert_set() -> None:
    """fetch_reviewed iterates exactly DUCKLAKE_ARTIFACT_NAMES -- the fetch set and the assert
    set cannot drift apart because both are driven off the same tuple (proves acceptance
    criterion 2's fetch-set-equals-assert-set clause)."""
    sha = "fetchsetsha"
    objects = {f"lambda-packages/{sha}/{n}": b"reviewed-bytes" for n in DUCKLAKE_ARTIFACT_NAMES}
    client = _FakeS3Client(objects=objects)

    fetch_reviewed(
        list(DUCKLAKE_ARTIFACT_NAMES), sha, "my-bucket", packages_dir="/tmp/unused-fetch-set-equals", s3_client=client
    )

    fetched_keys = {key for (_, key, _) in client.downloads}
    assert fetched_keys == {f"lambda-packages/{sha}/{n}" for n in DUCKLAKE_ARTIFACT_NAMES}
    assert len(client.downloads) == len(DUCKLAKE_ARTIFACT_NAMES)


def test_fetch_reviewed_fails_closed_on_missing_object(tmp_path: Path) -> None:
    """A missing per-sha reference object raises DucklakeArtifactError before any copy is
    attempted -- a missing reviewed artifact must never let an apply proceed on unreviewed
    content (Decision 77 no-TOCTOU)."""
    names = ["ducklake-writer.zip", "ducklake-reader.zip"]
    sha = "missingobjsha"
    # Only the first name's per-sha object exists; the second is missing.
    client = _FakeS3Client(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"reviewed-bytes"})

    with pytest.raises(DucklakeArtifactError, match="DUCKLAKE_FETCH_MISSING"):
        fetch_reviewed(names, sha, "my-bucket", packages_dir=tmp_path, s3_client=client)

    assert client.copies == []  # fail-closed: no copy_object attempted


def test_fetch_reviewed_copies_onto_fixed_key_without_rewriting_per_sha(tmp_path: Path) -> None:
    """Each per-sha object is server-side copy_object'd onto its fixed key. The per-sha reference
    key is NEVER itself the copy_object destination -- rewriting it would be a self-copy moving
    ~183 MB for no gain, and it is the immutable reviewed reference."""
    names = list(DUCKLAKE_ARTIFACT_NAMES)
    sha = "copysha"
    objects = {f"lambda-packages/{sha}/{n}": b"reviewed-bytes" for n in names}
    client = _FakeS3Client(objects=objects)

    fetch_reviewed(names, sha, "bucket-x", packages_dir=tmp_path, s3_client=client)

    assert len(client.copies) == len(names)
    for bucket, copy_source, dest_key in client.copies:
        assert bucket == "bucket-x"
        assert dest_key in {f"lambda-packages/{n}" for n in names}
        assert not dest_key.startswith(f"lambda-packages/{sha}/")  # never rewrites the per-sha key
        assert copy_source == {
            "Bucket": "bucket-x",
            "Key": f"lambda-packages/{sha}/{dest_key.removeprefix('lambda-packages/')}",
        }


def test_fetch_reviewed_downloads_into_packages_dir(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip"]
    sha = "downloadsha"
    client = _FakeS3Client(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"reviewed-bytes"})

    fetch_reviewed(names, sha, "my-bucket", packages_dir=tmp_path, s3_client=client)

    downloaded_path = tmp_path / "ducklake-writer.zip"
    assert downloaded_path.read_bytes() == b"reviewed-bytes"


def test_fetch_reviewed_prints_fetch_ok_marker(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    names = ["ducklake-writer.zip"]
    sha = "markersha"
    client = _FakeS3Client(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"bytes"})

    fetch_reviewed(names, sha, "my-bucket", packages_dir=tmp_path, s3_client=client)

    assert "DUCKLAKE_FETCH_OK" in capsys.readouterr().out


def test_fetch_reviewed_prints_no_marker_on_failure(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    names = ["ducklake-writer.zip"]
    client = _FakeS3Client(objects={})

    with pytest.raises(DucklakeArtifactError):
        fetch_reviewed(names, "sha", "my-bucket", packages_dir=tmp_path, s3_client=client)

    assert "DUCKLAKE_FETCH_OK" not in capsys.readouterr().out


def test_fetch_reviewed_constructs_boto3_client_when_none_injected(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip"]
    sha = "noneinjectedsha"
    fake_client = _FakeS3Client(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"bytes"})
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = fake_client

    with patch.dict("sys.modules", {"boto3": fake_boto3}):
        fetch_reviewed(names, sha, "bucket", region="eu-west-1", packages_dir=tmp_path)

    fake_boto3.client.assert_called_once_with("s3", region_name="eu-west-1")
    assert len(fake_client.copies) == 1


# ---------------------------------------------------------------------------
# main() --mode fetch: no rebuild, no assert_and_upload call, fetch_reviewed wired correctly
# ---------------------------------------------------------------------------


def test_main_mode_fetch_never_invokes_build_ducklake_only(tmp_path: Path) -> None:
    with patch("scripts.ci.ducklake_artifacts.build_ducklake_only") as mock_build:
        with patch("scripts.ci.ducklake_artifacts.fetch_reviewed") as mock_fetch:
            rc = main(["push", "sha5", "bucket-v", "--mode", "fetch", "--skip-build"])
    assert rc == 0
    mock_build.assert_not_called()
    mock_fetch.assert_called_once_with(list(DUCKLAKE_ARTIFACT_NAMES), "sha5", "bucket-v", "eu-west-2")


def test_main_mode_fetch_never_invokes_assert_and_upload() -> None:
    with patch("scripts.ci.ducklake_artifacts.build_ducklake_only") as mock_build:
        with patch("scripts.ci.ducklake_artifacts.assert_and_upload") as mock_assert:
            with patch("scripts.ci.ducklake_artifacts.fetch_reviewed"):
                rc = main(["workflow_dispatch", "sha6", "bucket-u", "--mode", "fetch"])
    assert rc == 0
    mock_build.assert_not_called()
    mock_assert.assert_not_called()


def test_main_mode_fetch_surfaces_ducklake_artifact_error_as_exit_one(capsys: pytest.CaptureFixture) -> None:
    with patch(
        "scripts.ci.ducklake_artifacts.fetch_reviewed",
        side_effect=DucklakeArtifactError("DUCKLAKE_FETCH_MISSING boom"),
    ):
        rc = main(["push", "sha7", "bucket-t", "--mode", "fetch"])
    assert rc == 1
    assert "DUCKLAKE_FETCH_MISSING" in capsys.readouterr().err


def test_main_default_mode_is_build_and_unaffected_by_fetch_addition() -> None:
    """Backward-compat: omitting --mode preserves the pre-existing build+assert_and_upload path."""
    with patch("scripts.ci.ducklake_artifacts.build_ducklake_only") as mock_build:
        with patch("scripts.ci.ducklake_artifacts.assert_and_upload") as mock_assert:
            rc = main(["push", "sha8", "bucket-s"])
    assert rc == 0
    mock_build.assert_called_once()
    mock_assert.assert_called_once()


# ---------------------------------------------------------------------------
# rec-4189: deps-layer rebuild is byte-identical across build interpreters, and a push-path
# mismatch names the differing zip entries
# ---------------------------------------------------------------------------

_HOSTED_PYTHONS = (
    "/opt/hostedtoolcache/Python/3.12.14/x64/bin/python3",
    "/opt/hostedtoolcache/Python/3.12.15/x64/bin/python3",
)


def _fake_pip_target(cmd: list[str], **_: Any) -> SimpleNamespace:
    """Stand-in for `pip install --target`: a package, its dist-info, and a console-script wrapper
    whose shebang and RECORD hash carry the interpreter that ran pip (cmd[0]), as real pip writes them."""
    target = Path(cmd[cmd.index("--target") + 1])
    wrapper = f"#!{cmd[0]}\nimport sys\nfrom ulid.__main__ import entrypoint\n".encode()
    digest = base64.urlsafe_b64encode(hashlib.sha256(wrapper).digest()).rstrip(b"=").decode()
    for directory in ("ulid", "bin", "python_ulid-4.0.1.dist-info"):
        (target / directory).mkdir()
    (target / "ulid" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (target / "bin" / "ulid").write_bytes(wrapper)
    (target / "python_ulid-4.0.1.dist-info" / "RECORD").write_bytes(
        f"../../bin/ulid,sha256={digest},{len(wrapper)}\r\n"
        "ulid/__init__.py,sha256=abc,6\r\npython_ulid-4.0.1.dist-info/RECORD,,\r\n".encode()
    )
    return SimpleNamespace(returncode=0)


def test_deps_layer_rebuild_is_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """rec-4189: two builds whose pip ran under interpreters at different paths are byte-identical,
    and the layer carries no console-script wrapper or build-interpreter path."""
    built: list[bytes] = []
    for i, interpreter in enumerate(_HOSTED_PYTHONS):
        out_dir = tmp_path / f"out{i}"
        out_dir.mkdir()
        monkeypatch.setattr(sys, "executable", interpreter)
        monkeypatch.setattr(bm, "OUTPUT_DIR", out_dir)
        with patch("scripts.build_lambda_packaging.subprocess.run", side_effect=_fake_pip_target):
            built.append(bm.build_ducklake_deps_layer(tmp_path / f"build{i}").read_bytes())

    assert _md5_hex(built[0]) == _md5_hex(built[1])
    with zipfile.ZipFile(io.BytesIO(built[0])) as zf:
        names = zf.namelist()
        contents = b"".join(zf.read(name) for name in names)
    assert any(name.endswith("site-packages/ulid/__init__.py") for name in names)
    assert not [name for name in names if "/site-packages/bin/" in name]
    assert not [path for path in _HOSTED_PYTHONS if path.encode() in contents]


def _mismatch_message(tmp_path: Path, local: bytes, remote: bytes) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    packages_dir = _write_packages(tmp_path, ["ducklake-deps-layer.zip"], content=local)
    client = _FakeS3Client(objects={"lambda-packages/sha/ducklake-deps-layer.zip": remote})
    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(["ducklake-deps-layer.zip"], "sha", "push", "b", packages_dir=packages_dir, s3_client=client)
    assert client.uploads == []
    return str(excinfo.value)


def test_push_byte_mismatch_names_differing_entries(tmp_path: Path) -> None:
    shared = {f"pkg/m{i:02d}.py": b"same" for i in range(3)}
    local = _zip_bytes({**shared, "bin/ulid": b"#!/opt/a/python3", "only/local.py": b"l"})
    remote = _zip_bytes({**shared, "bin/ulid": b"#!/opt/b/python3", "only/remote.py": b"r"})
    message = _mismatch_message(tmp_path / "small", local, remote)
    first, *rest = message.splitlines()
    assert first.startswith("DUCKLAKE_ZIP_MISMATCH ducklake-deps-layer.zip: local=")
    assert "changed: bin/ulid" in rest[1:]
    assert "only-local: only/local.py" in rest[1:]
    assert "only-remote: only/remote.py" in rest[1:]
    assert not [line for line in rest if "pkg/m" in line]

    many_local = _zip_bytes({f"pkg/m{i:02d}.py": b"a" for i in range(13)})
    many_remote = _zip_bytes({f"pkg/m{i:02d}.py": b"b" for i in range(13)})
    capped = _mismatch_message(tmp_path / "many", many_local, many_remote).splitlines()
    assert "changed: pkg/m09.py" in capped and "changed: pkg/m10.py" not in capped
    assert "... and 3 more" in capped


def test_push_byte_mismatch_reports_container_only_difference(tmp_path: Path) -> None:
    entries = {"pkg/a.py": b"a" * 200, "pkg/b.py": b"b"}
    message = _mismatch_message(tmp_path, _zip_bytes(entries), _zip_bytes(entries, zipfile.ZIP_STORED))
    assert "entry contents identical" in message.splitlines()[1]


def test_push_byte_mismatch_without_zip_bytes_says_diff_unavailable(tmp_path: Path) -> None:
    message = _mismatch_message(tmp_path, b"not-a-zip", b"also-not-a-zip")
    assert message.splitlines()[0].startswith("DUCKLAKE_ZIP_MISMATCH ducklake-deps-layer.zip: local=")
    assert message.splitlines()[1].startswith("entry diff unavailable")


# ---------------------------------------------------------------------------
# rec-4198: _zip_entry_diff's own failure can never mask the DUCKLAKE_ZIP_MISMATCH it explains
# ---------------------------------------------------------------------------


def test_push_byte_mismatch_diff_error_cannot_mask_mismatch(tmp_path: Path) -> None:
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "shadiag"
    client = _FakeS3Client(objects={f"lambda-packages/{sha}/ducklake-writer.zip": b"different-remote-bytes"})

    with patch("scripts.ci.ducklake_artifacts.zipfile.ZipFile", side_effect=RuntimeError("boom, not a BadZipFile")):
        with pytest.raises(DucklakeArtifactError) as excinfo:
            assert_and_upload(names, sha, "push", "my-bucket", packages_dir=packages_dir, s3_client=client)

    message = str(excinfo.value)
    assert message.splitlines()[0].startswith("DUCKLAKE_ZIP_MISMATCH ducklake-writer.zip: local=")
    assert "entry diff unavailable: boom, not a BadZipFile" in message
    assert client.uploads == []


# ---------------------------------------------------------------------------
# rec-4372: a push-path mismatch whose every difference traces to a moved tracked input proceeds
# on merge-tree zips; the PR-head prefix is never touched and a superseded-by.json pointer is left
# ---------------------------------------------------------------------------


def test_prmerge_concurrent_same_file_zip_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest_dir = repo / "src" / "lambdas" / "ducklake_writer"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.yaml").write_text("artifact: ducklake-writer.zip\nincludes:\n  - src/common/foo.py\n")
    common_dir = repo / "src" / "common"
    common_dir.mkdir(parents=True)
    (common_dir / "foo.py").write_text("print('v1')\n")

    _git_init(repo)
    pr_sha = _git_commit_all(repo, "pr head")

    pr_build_dir = tmp_path / "pr-build"
    pr_build_dir.mkdir()
    pr_zip_bytes = _zip_bytes({"src/common/foo.py": (common_dir / "foo.py").read_bytes()})
    (pr_build_dir / "ducklake-writer.zip").write_bytes(pr_zip_bytes)
    reference_record = record_inputs(pr_build_dir, ["ducklake-writer.zip"], repo)

    (common_dir / "foo.py").write_text("print('v2 -- edited on main after the PR plan')\n")
    merge_sha = _git_commit_all(repo, "main moves foo.py")

    packages_dir = repo / "lambda-packages"
    packages_dir.mkdir()
    merge_zip_bytes = _zip_bytes({"src/common/foo.py": (common_dir / "foo.py").read_bytes()})
    (packages_dir / "ducklake-writer.zip").write_bytes(merge_zip_bytes)

    client = _FakeS3Client(
        objects={
            f"lambda-packages/{pr_sha}/ducklake-writer.zip": pr_zip_bytes,
            f"lambda-packages/{pr_sha}/inputs.json": json.dumps(reference_record).encode(),
        }
    )
    monkeypatch.chdir(repo)

    assert_and_upload(
        ["ducklake-writer.zip"], pr_sha, "push", "my-bucket", packages_dir=packages_dir, s3_client=client, merge_sha=merge_sha
    )

    pr_prefix_uploads = [key for (_, _, key) in client.uploads if key.startswith(f"lambda-packages/{pr_sha}/")]
    assert pr_prefix_uploads == []  # the PR-head prefix's zips stay untouched
    assert (str(packages_dir / "ducklake-writer.zip"), "my-bucket", "lambda-packages/ducklake-writer.zip") in client.uploads
    assert (
        str(packages_dir / "ducklake-writer.zip"),
        "my-bucket",
        f"lambda-packages/{merge_sha}/ducklake-writer.zip",
    ) in client.uploads

    put_keys = {key for (_, key, _) in client.put_objects}
    assert f"lambda-packages/{merge_sha}/inputs.json" in put_keys
    assert f"lambda-packages/{pr_sha}/superseded-by.json" in put_keys
    pointer = json.loads(
        next(body for (_, key, body) in client.put_objects if key == f"lambda-packages/{pr_sha}/superseded-by.json")
    )
    assert pointer == {
        "schema": 1,
        "artifact_sha": merge_sha,
        "reason": "DUCKLAKE_ZIP_INPUTS_MOVED",
        "moved": {"ducklake-writer.zip": ["src/common/foo.py"]},
    }


def test_prmerge_identical_rebuild_fails_closed_without_merge_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a usable merge sha (unset, or equal to artifact_sha), a push mismatch still fails
    closed even when the record would otherwise attribute it cleanly (rec-4372 guard)."""
    names = ["ducklake-writer.zip"]
    packages_dir = _write_packages(tmp_path, names, content=b"local-bytes")
    sha = "samemergesha"
    client = _FakeS3Client(
        objects={
            f"lambda-packages/{sha}/ducklake-writer.zip": b"remote-bytes",
            f"lambda-packages/{sha}/inputs.json": b"{}",
        }
    )
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    with pytest.raises(DucklakeArtifactError) as excinfo:
        assert_and_upload(names, sha, "push", "my-bucket", packages_dir=packages_dir, s3_client=client)
    assert "not attributed: no merge sha available" in str(excinfo.value)
    assert client.uploads == []

    with pytest.raises(DucklakeArtifactError) as excinfo2:
        assert_and_upload(names, sha, "push", "my-bucket", packages_dir=packages_dir, s3_client=client, merge_sha=sha)
    assert "not attributed: no merge sha available" in str(excinfo2.value)
    assert client.uploads == []
