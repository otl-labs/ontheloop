"""Mirror test for src/common/ducklake_telemetry_blobstore.py (the content_blobs s3 adapter) and the T2.36 c4 guard.

Fast-tier nodes use a hand fake client (no botocore). Two nodes take botocore through a function-scope importorskip: an
offline presign with fake credentials and a Stubber HEAD. Credential-shaped values are built at runtime."""

from __future__ import annotations

import ast
import hashlib
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

from src.common import ducklake_telemetry_blobstore as store_mod
from src.common.ducklake_scd2_schema import DuckLakeRuntimeError
from src.common.ducklake_telemetry_blobstore import (
    BLOB_COMMIT_MAX_AGE_DAYS,
    INTEGRITY_REASONS,
    BlobGrantSelfCheckError,
    BlobIntegrityError,
    BlobRoot,
    BlobStoreAccessError,
    BlobStoreUnavailableError,
    S3BlobStore,
)
from src.telemetry.blobs import sha256_hex_to_b64
from tests.fixtures.telemetry_blob_fakes import SIGNED, ClientErrorLike, FakeS3Client, presigned_url

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)
SHA = "ab" * 32
URI = "tenant/project/session/" + SHA
BLOB_ROOT = BlobRoot("bucket", "telemetry-blobs/ducklake_smoke/")
HEAD_OK = {"ContentLength": 70000, "ChecksumSHA256": sha256_hex_to_b64(SHA), "LastModified": NOW - timedelta(days=1)}


def store(client: Any) -> S3BlobStore:
    return S3BlobStore(client, BLOB_ROOT, clock=lambda: NOW)


def test_verify_states_from_head() -> None:
    assert store(FakeS3Client(HEAD_OK)).verify(URI, SHA, 70000) == "verified"
    assert store(FakeS3Client({**HEAD_OK, "ContentLength": 69999})).verify(URI, SHA, 70000) == "size_mismatch"
    assert (
        store(FakeS3Client({**HEAD_OK, "ChecksumSHA256": sha256_hex_to_b64("cd" * 32)})).verify(URI, SHA, 70000) == "mismatch"
    )
    assert (
        store(FakeS3Client({k: v for k, v in HEAD_OK.items() if k != "ChecksumSHA256"})).verify(URI, SHA, 70000) == "mismatch"
    )
    old = {**HEAD_OK, "LastModified": NOW - timedelta(days=BLOB_COMMIT_MAX_AGE_DAYS, seconds=1)}
    assert store(FakeS3Client(old)).verify(URI, SHA, 70000) == "stale"
    edge = {**HEAD_OK, "LastModified": NOW - timedelta(days=BLOB_COMMIT_MAX_AGE_DAYS)}
    assert store(FakeS3Client(edge)).verify(URI, SHA, 70000) == "verified"
    for code in ("404", "NoSuchKey", "NotFound"):
        assert store(FakeS3Client(ClientErrorLike(code, 404))).verify(URI, SHA, 70000) == "missing"
    key = BLOB_ROOT.prefix + URI
    assert store(FakeS3Client(HEAD_OK)).verify(URI, SHA, 70000, now=NOW + timedelta(days=30)) == "stale"
    assert FakeS3Client(HEAD_OK).calls == [] and key.startswith("telemetry-blobs/ducklake_smoke/")


def test_checksum_outranks_length_and_length_outranks_stale() -> None:
    checksums = {
        "equal": sha256_hex_to_b64(SHA),
        "absent": None,
        "different": sha256_hex_to_b64("cd" * 32),
        "composite": sha256_hex_to_b64(SHA) + "-2",
    }
    for name, value in checksums.items():
        for length, size in (("equal", 70000), ("different", 69999)):
            head = {**HEAD_OK, "ContentLength": size}
            if value is None:
                del head["ChecksumSHA256"]
            else:
                head["ChecksumSHA256"] = value
            got = store(FakeS3Client(head)).verify(URI, SHA, 70000)
            if (name, length) == ("equal", "equal"):
                assert got == "verified"
            elif name == "equal":
                assert got == "size_mismatch"
            else:
                assert got == "mismatch", (name, length)
    old = {**HEAD_OK, "ContentLength": 69999, "LastModified": NOW - timedelta(days=BLOB_COMMIT_MAX_AGE_DAYS, seconds=1)}
    assert store(FakeS3Client(old)).verify(URI, SHA, 70000) == "size_mismatch"


def test_head_403_is_missing_only_when_the_list_finds_nothing() -> None:
    absent = FakeS3Client(ClientErrorLike("403", 403), {"Contents": []})
    assert store(absent).verify(URI, SHA, 70000) == "missing"
    assert [c for c, _ in absent.calls] == ["head_object", "list_objects_v2"]
    assert absent.calls[1][1] == {"Bucket": "bucket", "Prefix": BLOB_ROOT.prefix + URI, "MaxKeys": 1}
    present = FakeS3Client(ClientErrorLike("AccessDenied", 403), {"Contents": [{"Key": BLOB_ROOT.prefix + URI}]})
    with pytest.raises(BlobStoreAccessError):
        store(present).verify(URI, SHA, 70000)
    other = FakeS3Client(ClientErrorLike("403", 403), {"Contents": [{"Key": BLOB_ROOT.prefix + URI + "x"}]})
    assert store(other).verify(URI, SHA, 70000) == "missing"
    denied_list = FakeS3Client(ClientErrorLike("403", 403), ClientErrorLike("AccessDenied", 403))
    with pytest.raises(BlobStoreAccessError):
        store(denied_list).verify(URI, SHA, 70000)


def test_throttle_and_5xx_are_unavailable() -> None:
    for code, status in (("SlowDown", 503), ("Throttling", 400), ("InternalError", 500), ("X", 502), ("Y", 429)):
        with pytest.raises(BlobStoreUnavailableError) as caught:
            store(FakeS3Client(ClientErrorLike(code, status))).verify(URI, SHA, 70000)
        assert "secret" not in str(caught.value) and caught.value.__cause__ is None
    for name in ("ReadTimeoutError", "ConnectTimeoutError", "EndpointConnectionError"):
        with pytest.raises(BlobStoreUnavailableError):
            store(FakeS3Client(type(name, (Exception,), {})("secret-key"))).verify(URI, SHA, 70000)
    with pytest.raises(BlobStoreAccessError) as caught:
        store(FakeS3Client(ClientErrorLike("InvalidObjectState", 400))).verify(URI, SHA, 70000)
    assert "secret" not in str(caught.value)
    with pytest.raises(BlobStoreUnavailableError):
        store(FakeS3Client(HEAD_OK, ClientErrorLike("SlowDown", 503))).count_objects("a/")


def test_head_asks_for_checksum_mode() -> None:
    client = FakeS3Client(HEAD_OK)
    store(client).verify(URI, SHA, 70000)
    assert client.calls == [
        ("head_object", {"Bucket": "bucket", "Key": BLOB_ROOT.prefix + URI, "ChecksumMode": "ENABLED"}),
    ]


def test_grant_self_check_refuses_an_unsigned_checksum() -> None:
    good = FakeS3Client(url=presigned_url("k"))
    grant = store(good).grant_put(URI, SHA, 70000)
    assert grant.headers == {"Content-Length": "70000", "x-amz-checksum-sha256": sha256_hex_to_b64(SHA)}
    assert grant.expires_at == "2026-01-10T00:15:00.000Z" and "https" not in repr(grant)
    name, kwargs = good.calls[0]
    assert name == "put_object" and kwargs["ExpiresIn"] == 900 and kwargs["HttpMethod"] == "PUT"
    assert kwargs["Params"] == {
        "Bucket": "bucket",
        "Key": BLOB_ROOT.prefix + URI,
        "ContentLength": 70000,
        "ChecksumSHA256": sha256_hex_to_b64(SHA),
    }
    for bad in (
        presigned_url(signed="content-length;host"),
        presigned_url(signed="host;x-amz-checksum-sha256"),
        presigned_url(signed=SIGNED + ";x-amz-meta-a"),
        presigned_url(extra="&x-amz-checksum-crc32=AAAAAA=="),
        presigned_url(extra="&X-Amz-Sdk-Checksum-Algorithm=CRC32"),
        presigned_url(token=False),
    ):
        with pytest.raises(BlobGrantSelfCheckError) as caught:
            store(FakeS3Client(url=bad)).grant_put(URI, SHA, 70000)
        assert "https" not in str(caught.value) and "X-Amz" not in str(caught.value)


def test_root_is_scoped_and_disjoint_from_the_data_path() -> None:
    ok = BlobRoot.from_env("s3://bucket/telemetry-blobs/ducklake_smoke/", "s3://bucket/ducklake_smoke/data/", "ducklake_smoke")
    assert (ok.bucket, ok.prefix, ok.key("a/b")) == (
        "bucket",
        "telemetry-blobs/ducklake_smoke/",
        "telemetry-blobs/ducklake_smoke/a/b",
    )
    for root, data in (
        ("s3://bucket/telemetry-blobs/other/", "s3://bucket/d/"),
        ("s3://bucket/telemetry-blobs/ducklake_smoke", "s3://bucket/d/"),
        ("s3://bucket/ducklake_smoke/", "s3://bucket/d/"),
        ("https://bucket/telemetry-blobs/ducklake_smoke/", "s3://bucket/d/"),
        ("s3:///telemetry-blobs/ducklake_smoke/", "s3://bucket/d/"),
        ("s3://bucket/telemetry-blobs/ducklake_smoke/", "s3://bucket/telemetry-blobs/"),
        ("s3://bucket/telemetry-blobs/ducklake_smoke/", "s3://bucket/telemetry-blobs/ducklake_smoke/sub/"),
        ("s3://bucket/telemetry-blobs/ducklake_smoke/", "s3://bucket/"),
    ):
        with pytest.raises(DuckLakeRuntimeError):
            BlobRoot.from_env(root, data, "ducklake_smoke")
    assert BlobRoot.from_env("s3://bucket/telemetry-blobs/ducklake_smoke/", "s3://other/telemetry-blobs/", "ducklake_smoke")


def test_count_objects_pages_through_the_prefix() -> None:
    pages = iter(
        [
            {"Contents": [{"Size": 5}], "IsTruncated": True, "NextContinuationToken": "t1"},
            {"Contents": [{"Size": 7}, {"Size": 1}], "IsTruncated": False},
        ]
    )

    class Paged(FakeS3Client):
        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(("list_objects_v2", kwargs))
            return next(pages)

    client = Paged()
    assert store(client).count_objects("t/p/s/") == {"count": 3, "bytes": 13}
    assert client.calls[0][1]["Prefix"] == BLOB_ROOT.prefix + "t/p/s/" and client.calls[1][1]["ContinuationToken"] == "t1"
    assert store(FakeS3Client()).count_objects("t/p/s/") == {"count": 0, "bytes": 0}


_OBJECT_READS = frozenset({"get_object", "download_file", "download_fileobj", "select_object_content", "copy_object"})
_S3_OPERATIONS = _OBJECT_READS | {
    "put_object", "delete_object", "delete_objects", "head_object", "list_objects", "list_objects_v2", "list_object_versions",
    "upload_file", "upload_fileobj", "copy", "create_multipart_upload", "upload_part", "complete_multipart_upload",
    "abort_multipart_upload", "generate_presigned_url", "generate_presigned_post", "put_object_acl", "restore_object",
}  # fmt: skip
_SQL_READ = re.compile(r"(read_parquet|read_json|read_csv|read_blob|read_text)", re.IGNORECASE)
_SQL_COPY = re.compile(r"\bCOPY\b[^;]*?\bFROM\b", re.DOTALL)
_SEAM = ("src/common/ducklake_telemetry_blobstore.py", "get_blob")


def _bundled(function: str | None = None) -> list[str]:
    """The .py files named by the DuckLake manifests (every ducklake_* function, or just one)."""
    found: set[str] = set()
    pattern = f"ducklake_{function}" if function else "ducklake_*"
    for manifest in sorted((ROOT / "src" / "lambdas").glob(f"{pattern}/manifest.yaml")):
        for include in yaml.safe_load(manifest.read_text(encoding="utf-8"))["includes"]:
            path = ROOT / include
            found.update(p.relative_to(ROOT).as_posix() for p in ([path] if path.suffix == ".py" else path.rglob("*.py")))
    return sorted(found)


def _scan(rel: str) -> tuple[list[tuple[str, int]], list[tuple[str, int]], list[str]]:
    """(S3 operation calls, SQL object-storage reads, names of get_blob definitions) outside docstrings."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    seam_lines: set[int] = set()
    if rel == _SEAM[0]:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == _SEAM[1]:
                seam_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    calls, sql, seams = [], [], []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.FunctionDef) and node.name == _SEAM[1]:
            seams.append(node.name)
        if line in seam_lines:
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _S3_OPERATIONS:
            calls.append((node.func.attr, line))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            text = node.value
            if (_SQL_READ.search(text) and "s3://" in text) or _SQL_COPY.search(text) or "ducklake_add_data_files" in text:
                sql.append((text[:40], line))
    return calls, sql, seams


def test_no_ducklake_bundle_ingests_from_object_storage() -> None:
    """T2.36 c4 guard. Staging is an S3 object read whose bytes become catalog rows. Leg (a): no module bundled by any DuckLake
    manifest (the only processes that write catalog rows, Decision 84 I-1) reads an object (get_object, download_file,
    download_fileobj, select_object_content, copy_object) or holds SQL that reads object storage into a table (read_* over
    s3://, COPY ... FROM, ducklake_add_data_files). Uploads and listings are not reads (catalog_dr's upload_file, the
    maintenance list paginator). One named seam is admitted: a function get_blob in the blob store adapter, the content_blobs
    port's read verb (rec-4291, slice 2b), which returns bytes to a caller and never feeds a table. Leg (b): the telemetry
    writer bundle's S3 client calls are exactly head_object, generate_presigned_url (put_object) and list_objects_v2."""
    every = _bundled()
    assert _SEAM[0] in every and "src/common/ducklake_runtime.py" in every
    for rel in every:
        calls, sql, _ = _scan(rel)
        assert not [c for c in calls if c[0] in _OBJECT_READS], (rel, calls)
        assert not sql, (rel, sql)
    writer = _bundled("telemetry_writer")
    used = {name for rel in writer for name, _ in _scan(rel)[0]}
    assert used == {"head_object", "generate_presigned_url", "list_objects_v2"}, used
    for rel in writer:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if rel != _SEAM[0]:
            assert _SEAM[1] not in text, rel
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "generate_presigned_url":
                assert isinstance(node.args[0], ast.Constant) and node.args[0].value == "put_object", rel


def test_the_guard_flags_a_read_and_admits_only_the_named_seam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        'def go(c, con):\n    c.get_object(Bucket="b")\n    con.execute("SELECT * FROM read_parquet(\'s3://b/x\')")\n'
        '    con.execute("COPY t FROM \'x\'")\n    c.upload_file("a", "b", "c")\n    c.list_objects_v2()\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(f"{__name__}.ROOT", tmp_path)
    calls, sql, _ = _scan("sample.py")
    assert [c[0] for c in calls] == ["get_object", "upload_file", "list_objects_v2"] and len(sql) == 2
    seam = tmp_path / _SEAM[0]
    seam.parent.mkdir(parents=True)
    seam.write_text(
        'def get_blob(c):\n    return c.get_object(Bucket="b")\n\ndef other(c):\n    c.get_object(Bucket="b")\n',
        encoding="utf-8",
    )
    calls, _, seams = _scan(_SEAM[0])
    assert seams == ["get_blob"] and [c[0] for c in calls] == ["get_object"] and calls[0][1] == 5


def _offline_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    pytest.importorskip("botocore")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA" + "0" * 16)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s" * 40)
    monkeypatch.setenv("AWS_SESSION_TOKEN", "tok" + "x" * 20)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    return store_mod.make_s3_client("eu-west-2")


@pytest.mark.aws
def test_real_presign_signs_length_host_and_checksum(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _offline_client(monkeypatch)
    grant = S3BlobStore(client, BLOB_ROOT, clock=lambda: NOW).grant_put(URI, SHA, 70000)
    parsed = urlparse(grant.url)
    query = {k.lower(): v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
    assert parsed.hostname == "bucket.s3.eu-west-2.amazonaws.com" and parsed.path == "/" + BLOB_ROOT.prefix + URI
    assert query["x-amz-signedheaders"] == "content-length;host;x-amz-checksum-sha256" and query["x-amz-expires"] == "900"
    assert "x-amz-security-token" in query and not [
        k for k in query if k.startswith(("x-amz-checksum-", "x-amz-sdk-checksum"))
    ]


@pytest.mark.aws
def test_real_head_stub_carries_checksum_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.stub import Stubber

    client = _offline_client(monkeypatch)
    with Stubber(client) as stub:
        stub.add_response(
            "head_object",
            {**HEAD_OK, "LastModified": NOW - timedelta(days=1)},
            expected_params={"Bucket": "bucket", "Key": BLOB_ROOT.prefix + URI, "ChecksumMode": "ENABLED"},
        )
        assert S3BlobStore(client, BLOB_ROOT, clock=lambda: NOW).verify(URI, SHA, 70000) == "verified"
        stub.assert_no_pending_responses()


def test_make_s3_client_pins_the_config_and_tolerates_an_older_botocore(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    seen: list[dict[str, Any]] = []

    class Config:
        def __init__(self, **kw: Any) -> None:
            if "request_checksum_calculation" in kw and seen:
                raise TypeError("old botocore")
            seen.append(kw)

    boto3 = types.SimpleNamespace(client=lambda name, region_name, config: (name, region_name, config))
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.config", types.SimpleNamespace(Config=Config))
    name, region, _ = store_mod.make_s3_client("eu-west-2")
    assert (name, region) == ("s3", "eu-west-2") and seen[0]["max_pool_connections"] == 32
    assert seen[0]["s3"] == {"addressing_style": "virtual"} and seen[0]["request_checksum_calculation"] == "when_required"
    store_mod.make_s3_client("eu-west-2")
    assert "request_checksum_calculation" not in seen[1]


def test_lazy_store_builds_once_and_delegates() -> None:
    built: list[int] = []
    inner = store(FakeS3Client(HEAD_OK, url=presigned_url("k")))

    def factory() -> S3BlobStore:
        built.append(1)
        return inner

    lazy = store_mod.LazyBlobStore(factory)
    assert built == [] and lazy.verify(URI, SHA, 70000) == "verified"
    assert lazy.grant_put(URI, SHA, 70000, now=NOW).headers["Content-Length"] == "70000"
    assert lazy.count_objects("a/") == {"count": 0, "bytes": 0} and built == [1]
    delegated = store_mod.LazyBlobStore(lambda: store(ObjectClient()))
    assert delegated.get_blob(OBJ_URI, OBJ_SHA, len(OBJ), offset=0, length=3) == OBJ[:3]
    lazy.reset()
    lazy.verify(URI, SHA, 70000)
    assert built == [1, 1]


def test_presign_errors_are_classified_without_the_key() -> None:
    class Failing(FakeS3Client):
        def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
            raise ClientErrorLike("AccessDenied", 403)

    with pytest.raises(BlobStoreAccessError) as caught:
        store(Failing()).grant_put(URI, SHA, 70000)
    assert "secret" not in str(caught.value)


OBJ = bytes(range(256)) * 2
OBJ_SHA = hashlib.sha256(OBJ).hexdigest()
OBJ_URI = f"01ARZ3NDEKTSV4RRFFQ69G5FAV/01ARZ3NDEKTSV4RRFFQ69G5FAW/01ARZ3NDEKTSV4RRFFQ69G5FAX/{OBJ_SHA}"
ETAG = '"0123456789abcdef"'
OBJ_HEAD = {"ContentLength": len(OBJ), "ChecksumSHA256": sha256_hex_to_b64(OBJ_SHA), "ETag": ETAG, "LastModified": NOW}


class ObjectClient(FakeS3Client):
    """FakeS3Client plus a ranged get_object over OBJ; `get` is a response override or an exception to raise."""

    def __init__(self, head: Any = None, get: Any = None) -> None:
        super().__init__(OBJ_HEAD if head is None else head)
        self.get = get

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_object", kwargs))
        if isinstance(self.get, Exception):
            raise self.get
        if self.get is not None:
            return self.get
        first, last = (int(n) for n in kwargs["Range"].removeprefix("bytes=").split("-"))
        return {"Body": io.BytesIO(OBJ[first : last + 1])}


def read(client: Any, *, offset: int = 10, length: int = 10, uri: str = OBJ_URI, sha: str = OBJ_SHA) -> bytes:
    return store(client).get_blob(uri, sha, len(OBJ), offset=offset, length=length)


def test_get_blob_verifies_then_reads_the_range() -> None:
    client = ObjectClient()
    assert read(client) == OBJ[10:20]
    key = BLOB_ROOT.prefix + OBJ_URI
    assert client.calls == [
        ("head_object", {"Bucket": "bucket", "Key": key, "ChecksumMode": "ENABLED"}),
        ("get_object", {"Bucket": "bucket", "Key": key, "Range": "bytes=10-19", "IfMatch": ETAG}),
    ]
    assert read(ObjectClient(), offset=len(OBJ) - 4, length=4) == OBJ[-4:]


def test_get_blob_integrity_reasons() -> None:
    cases: dict[str, Any] = {
        "checksum_absent": ObjectClient({k: v for k, v in OBJ_HEAD.items() if k != "ChecksumSHA256"}),
        "checksum_mismatch": ObjectClient({**OBJ_HEAD, "ChecksumSHA256": sha256_hex_to_b64("cd" * 32)}),
        "length_mismatch": ObjectClient({**OBJ_HEAD, "ContentLength": len(OBJ) + 1}),
        "changed_during_read": ObjectClient(get=ClientErrorLike("PreconditionFailed", 412)),
        "forbidden_or_missing": ObjectClient(ClientErrorLike("403", 403)),
        "missing": ObjectClient(ClientErrorLike("NoSuchKey", 404)),
    }
    for reason, client in cases.items():
        with pytest.raises(BlobIntegrityError) as caught:
            read(client)
        assert caught.value.reason == reason, reason
    for client in (ObjectClient(get=ClientErrorLike("AccessDenied", 403)),):
        with pytest.raises(BlobIntegrityError) as caught:
            read(client)
        assert caught.value.reason == "forbidden_or_missing" and caught.value.status == 403
    with pytest.raises(BlobIntegrityError) as caught:
        read(ObjectClient(get={"Body": io.BytesIO(b"short")}))
    assert caught.value.reason == "length_mismatch"
    with pytest.raises(BlobIntegrityError) as caught:
        read(ObjectClient({k: v for k, v in OBJ_HEAD.items() if k != "ETag"}))
    assert caught.value.reason == "changed_during_read"
    for uri in ("t/p/s/" + OBJ_SHA, OBJ_URI.lower(), OBJ_URI + "/x", OBJ_URI[:-1] + "0", "../" + OBJ_URI):
        client = ObjectClient()
        with pytest.raises(BlobIntegrityError) as caught:
            read(client, uri=uri)
        assert caught.value.reason == "uri_malformed" and client.calls == []
    assert set(cases) | {"uri_malformed"} == set(INTEGRITY_REASONS)


def test_get_blob_store_faults_are_classified_without_the_message() -> None:
    account = "1" * 12
    arn = f"arn:aws:sts::{account}:assumed-role/reader/session"

    class Leaky(Exception):
        def __init__(self, code: str, status: int) -> None:
            super().__init__(f"{arn} s3://secret-bucket/{BLOB_ROOT.prefix}{OBJ_URI} {account}")
            self.response = {"Error": {"Code": code, "Message": arn}, "ResponseMetadata": {"HTTPStatusCode": status}}

    expected: list[tuple[Any, str, int, type[Exception]]] = [
        (Leaky("SlowDown", 503), "SlowDown", 503, BlobStoreUnavailableError),
        (Leaky("InternalError", 500), "InternalError", 500, BlobStoreUnavailableError),
        (Leaky("InvalidRequest", 400), "InvalidRequest", 400, BlobStoreAccessError),
        (Leaky("AccessDenied", 403), "AccessDenied", 403, BlobIntegrityError),
        (Leaky("NoSuchKey", 404), "NoSuchKey", 404, BlobIntegrityError),
    ]
    for exc, code, status, kind in expected:
        for client in (ObjectClient(exc), ObjectClient(get=exc)):
            with pytest.raises(kind) as caught:
                read(client)
            text = str(caught.value)
            assert arn not in text and account not in text and "secret-bucket" not in text and OBJ_SHA not in text
            assert (caught.value.code, caught.value.status) == (code, status)  # type: ignore[attr-defined]
            assert caught.value.__cause__ is None


def test_writer_integrity_raise_keeps_its_reason() -> None:
    err = BlobIntegrityError("a stored object does not match its content-addressed key")
    assert err.reason == "checksum_mismatch" and (err.code, err.status) == ("", 0)
    assert str(err) == "a stored object does not match its content-addressed key"


@pytest.mark.aws
def test_real_ranged_get_stub_carries_range_and_if_match(monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.stub import Stubber

    client = _offline_client(monkeypatch)
    key = BLOB_ROOT.prefix + OBJ_URI
    with Stubber(client) as stub:
        stub.add_response(
            "head_object",
            OBJ_HEAD,
            expected_params={"Bucket": "bucket", "Key": key, "ChecksumMode": "ENABLED"},
        )
        stub.add_response(
            "get_object",
            {"Body": _StubBody(OBJ[10:20]), "ContentLength": 10},
            expected_params={"Bucket": "bucket", "Key": key, "Range": "bytes=10-19", "IfMatch": ETAG},
        )
        assert read(client) == OBJ[10:20]
        stub.assert_no_pending_responses()


def _StubBody(data: bytes) -> Any:
    from botocore.response import StreamingBody

    return StreamingBody(io.BytesIO(data), len(data))
