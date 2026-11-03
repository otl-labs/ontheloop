"""Shared fakes for scripts/ci/ducklake_artifacts.py tests (rec-4372): moved verbatim out of
tests/test_ducklake_artifacts.py so tests/test_ducklake_attribution.py can share them without a
cross-test import (tests/CLAUDE.md No cross-test imports).
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from typing import Any

_TOOLING_STUBS: tuple[str, ...] = (
    "scripts/build_lambda.py",
    "scripts/build_lambda_config.py",
    "scripts/build_lambda_packaging.py",
    "scripts/build_lambda_deploy.py",
    "scripts/lambda_manifest.py",
)


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _NoSuchKeyError(Exception):
    """Botocore-shaped ClientError: carries a `response` dict with Error.Code so
    fetch_reviewed's absence predicate (duck-typed from exc.response) can read it."""

    def __init__(self, message: str, code: str = "NoSuchKey") -> None:
        super().__init__(message)
        self.response: dict[str, Any] = {"Error": {"Code": code}}


class _FakeS3Client:
    """objects: {key: bytes} pre-seeds get_object/download_file reads.

    uploads: list of (local_path, bucket, key) from upload_file.
    put_objects: list of (bucket, key, body_bytes) from put_object (inputs.json, superseded-by.json).
    downloads: list of (bucket, key, filename) from download_file.
    copies: list of (bucket, copy_source, key) from copy_object.
    """

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.uploads: list[tuple[str, str, str]] = []
        self.put_objects: list[tuple[str, str, bytes]] = []
        self.downloads: list[tuple[str, str, str]] = []
        self.copies: list[tuple[str, dict[str, str], str]] = []

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key not in self.objects:
            raise _NoSuchKeyError(f"NoSuchKey: {Key}")
        return {"Body": _FakeBody(self.objects[Key])}

    def put_object(self, Bucket: str, Key: str, Body: bytes, **_: Any) -> None:  # noqa: N803
        self.put_objects.append((Bucket, Key, Body))
        self.objects[Key] = Body

    def upload_file(self, local_path: str, bucket: str, key: str) -> None:
        self.uploads.append((local_path, bucket, key))
        self.objects[key] = Path(local_path).read_bytes()

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        if key not in self.objects:
            raise _NoSuchKeyError(f"NoSuchKey: {key}")
        Path(filename).write_bytes(self.objects[key])
        self.downloads.append((bucket, key, filename))

    def copy_object(self, Bucket: str, CopySource: dict[str, str], Key: str) -> None:  # noqa: N803
        self.copies.append((Bucket, CopySource, Key))
        self.objects[Key] = self.objects[f"{CopySource['Key']}"]


def _write_packages(tmp_path: Path, names: list[str], content: bytes = b"zip-bytes") -> Path:
    packages_dir = tmp_path / "lambda-packages"
    packages_dir.mkdir(exist_ok=True)
    for name in names:
        (packages_dir / name).write_bytes(content)
    return packages_dir


def _zip_bytes(entries: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = compression
            zf.writestr(info, data)
    return buffer.getvalue()


def _git_init(root: Path) -> None:
    """A minimal tracked tree for scripts/ci/ducklake_inputs.py tests: the five tooling files
    (git-tracked, content irrelevant) plus whatever the caller writes under *root* beforehand."""
    for stub in _TOOLING_STUBS:
        path = root / stub
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# tooling stub\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)


def _git_commit_all(root: Path, message: str) -> str:
    """Stage and commit everything under *root*; returns the new commit's full sha."""
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", message],
        cwd=root,
        check=True,
    )
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True)
    return result.stdout.strip()
