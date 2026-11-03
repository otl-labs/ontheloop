"""The content_blobs port's s3 adapter for the telemetry writer and reader (T2.36, slice 2a-2, plan 4; ports.yaml).

The writer never puts, reads or deletes object bytes. It issues short-lived presigned PUTs bound to a payload's SHA-256 and
length (grant_put), proves an object by one HEAD (verify) and counts a session's objects through its own prefix-conditioned
List (count_objects). The reader's transcript_content verb reads a stored object's bytes (get_blob): one HEAD proves the
checksum and length, then a ranged GET bound to that HEAD's ETag returns the bytes. boto3 and botocore are imported only
inside make_s3_client, so this module imports in the fast tier; errors are classified from the botocore error shape
(Error.Code, ResponseMetadata.HTTPStatusCode) and exception class names.

Never logs, returns or raises a presigned URL, key, digest, account id or ARN beyond the Grant object itself; every error
here carries a fixed message (Decision 101). Imported by the telemetry writer's and the reader's bundles (the T2.36 c4 guard).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from src.common.ducklake_scd2_schema import DuckLakeRuntimeError
from src.telemetry.blobs import GRANT_TTL_SECONDS, sha256_hex_to_b64

BLOB_COMMIT_MAX_AGE_DAYS = 7
BLOB_PREFIX_ROOT = "telemetry-blobs"
_URI_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}/[0-9A-HJKMNP-TV-Z]{26}/[0-9A-HJKMNP-TV-Z]{26}/[0-9a-f]{64}$")
INTEGRITY_REASONS = (
    "uri_malformed",
    "checksum_absent",
    "checksum_mismatch",
    "length_mismatch",
    "changed_during_read",
    "forbidden_or_missing",
    "missing",
)
VERIFIED, MISSING, STALE, MISMATCH, SIZE_MISMATCH = "verified", "missing", "stale", "mismatch", "size_mismatch"
_SIGNED_HEADERS = "content-length;host;x-amz-checksum-sha256"
_MISSING_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
_UNAVAILABLE_CODES = frozenset(
    {
        "SlowDown",
        "Throttling",
        "ThrottlingException",
        "RequestLimitExceeded",
        "ServiceUnavailable",
        "InternalError",
        "RequestTimeout",
    }
)
_UNAVAILABLE_CLASSES = frozenset(
    {
        "ConnectTimeoutError",
        "ReadTimeoutError",
        "EndpointConnectionError",
        "ConnectionClosedError",
        "ConnectionError",
        "ReadTimeout",
        "TimeoutError",
        "ProxyConnectionError",
        "SSLError",
    }
)


class BlobStoreUnavailableError(Exception):
    """Throttling, a 5xx or a timeout after botocore's own retries: the caller answers 503."""

    code = ""
    status = 0


class BlobStoreAccessError(Exception):
    """Any other client error. Never read as an absent object; pages as a deploy or permission defect."""

    code = ""
    status = 0


class BlobIntegrityError(Exception):
    """A stored object whose SHA-256 is absent or differs from its content-addressed key: pages, never retried.

    reason is from INTEGRITY_REASONS; code and status carry the botocore Error.Code and HTTP status when get_blob raised it.
    """

    code = ""
    status = 0

    def __init__(self, message: str = "blob integrity fault", *, reason: str = "checksum_mismatch") -> None:
        super().__init__(message)
        self.reason = reason


class BlobGrantSelfCheckError(Exception):
    """A presigned URL that does not sign exactly the headers the protocol binds: pages, never issued."""


@dataclass(frozen=True)
class BlobRoot:
    bucket: str
    prefix: str  # telemetry-blobs/<meta_schema>/

    @classmethod
    def from_env(cls, blob_root: str, data_path: str, meta_schema: str) -> BlobRoot:
        """s3://<bucket>/telemetry-blobs/<meta_schema>/ only, disjoint from the catalog data path."""
        expected = f"{BLOB_PREFIX_ROOT}/{meta_schema}/"
        scheme, _, rest = blob_root.partition("://")
        bucket, _, prefix = rest.partition("/")
        if scheme != "s3" or not bucket or prefix != expected:
            raise DuckLakeRuntimeError("TELEMETRY_BLOB_ROOT must be s3://<bucket>/telemetry-blobs/<meta_schema>/")
        data_scheme, _, data_rest = data_path.partition("://")
        data_bucket, _, data_prefix = data_rest.partition("/")
        data_prefix = data_prefix.rstrip("/") + "/" if data_prefix else ""
        if (
            data_scheme == "s3"
            and data_bucket == bucket
            and (data_prefix.startswith(prefix) or prefix.startswith(data_prefix))
        ):
            raise DuckLakeRuntimeError("TELEMETRY_BLOB_ROOT must be disjoint from TELEMETRY_DATA_PATH")
        return cls(bucket, prefix)

    def key(self, content_uri: str) -> str:
        return self.prefix + content_uri


@dataclass(frozen=True)
class Grant:
    url: str = field(repr=False)
    headers: dict[str, str]
    expires_at: str


def make_s3_client(region: str) -> Any:
    """The writer's S3 client: SigV4, virtual-hosted regional addressing, standard retries, a pool for the 16-way fan-out."""
    import boto3  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415

    options: dict[str, Any] = {
        "signature_version": "s3v4",
        "s3": {"addressing_style": "virtual"},
        "retries": {"mode": "standard", "max_attempts": 3},
        "connect_timeout": 3,
        "read_timeout": 10,
        "max_pool_connections": 32,
    }
    try:
        config = Config(request_checksum_calculation="when_required", **options)
    except TypeError:  # an older botocore has no default checksum to suppress; the grant self-check still guards the URL
        config = Config(**options)
    return boto3.client("s3", region_name=region, config=config)


class LazyBlobStore:
    """One store per container, built on first use from the environment, so a request with no blob never needs it."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._store: Any = None

    def _get(self) -> Any:
        if self._store is None:
            self._store = self._factory()
        return self._store

    def verify(self, *args: Any, **kwargs: Any) -> str:
        return str(self._get().verify(*args, **kwargs))

    def grant_put(self, *args: Any, **kwargs: Any) -> Grant:
        grant: Grant = self._get().grant_put(*args, **kwargs)
        return grant

    def count_objects(self, *args: Any, **kwargs: Any) -> dict[str, int]:
        counted: dict[str, int] = self._get().count_objects(*args, **kwargs)
        return counted

    def get_blob(self, *args: Any, **kwargs: Any) -> bytes:
        data: bytes = self._get().get_blob(*args, **kwargs)
        return data

    def reset(self) -> None:
        self._store = None


def _error_shape(exc: Exception) -> tuple[str, int]:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return "", 0
    error = response.get("Error") or {}
    meta = response.get("ResponseMetadata") or {}
    return str(error.get("Code", "")), int(meta.get("HTTPStatusCode", 0) or 0)


def _classify(exc: Exception) -> Exception:
    code, status = _error_shape(exc)
    if code in _UNAVAILABLE_CODES or status == 429 or status >= 500 or type(exc).__name__ in _UNAVAILABLE_CLASSES:
        return BlobStoreUnavailableError(f"blob store unavailable ({type(exc).__name__}, status {status})")
    return BlobStoreAccessError(f"blob store access error ({type(exc).__name__}, status {status})")


def _is_missing(exc: Exception) -> bool:
    code, status = _error_shape(exc)
    return code in _MISSING_CODES or status == 404


def _read_fault(exc: Exception) -> Exception:
    """get_blob's error for a failed S3 call: typed, with the botocore code and status and never the message."""
    code, status = _error_shape(exc)
    if status == 412 or code == "PreconditionFailed":
        fault: Exception = BlobIntegrityError("blob integrity fault (changed_during_read)", reason="changed_during_read")
    elif _is_missing(exc):
        fault = BlobIntegrityError("blob integrity fault (missing)", reason="missing")
    elif _is_forbidden(exc):
        fault = BlobIntegrityError("blob integrity fault (forbidden_or_missing)", reason="forbidden_or_missing")
    else:
        fault = _classify(exc)
    fault.code, fault.status = code, status  # type: ignore[attr-defined]
    return fault


def _integrity(reason: str) -> BlobIntegrityError:
    return BlobIntegrityError(f"blob integrity fault ({reason})", reason=reason)


def _is_forbidden(exc: Exception) -> bool:
    code, status = _error_shape(exc)
    return status == 403 or code in {"403", "AccessDenied", "Forbidden"}


class S3BlobStore:
    def __init__(self, client: Any, root: BlobRoot, *, clock: Callable[[], datetime] | None = None) -> None:
        self._client = client
        self._root = root
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _exact_key_present(self, key: str) -> bool:
        try:
            listed = self._client.list_objects_v2(Bucket=self._root.bucket, Prefix=key, MaxKeys=1)
        except Exception as exc:  # noqa: BLE001 -- classified; the message (a bucket/key) is dropped
            raise _classify(exc) from None
        return any(item.get("Key") == key for item in listed.get("Contents") or [])

    def verify(self, content_uri: str, sha_hex: str, size: int, *, now: datetime | None = None) -> str:
        """verified | missing | stale | mismatch | size_mismatch from one HEAD with ChecksumMode ENABLED.

        Checked in order: missing; a stored checksum that is absent or differs from the key's digest (a multipart composite
        value differs by construction) is mismatch; a length different from the declared size, with the checksum equal, is
        size_mismatch; then stale; then verified.

        A 404 is missing. A 403 is followed by one exact-key List (the role's List is prefix-conditioned, so a HEAD of an
        absent key most likely answers 403): absent -> missing, present -> BlobStoreAccessError, never missing.
        """
        key = self._root.key(content_uri)
        try:
            head = self._client.head_object(Bucket=self._root.bucket, Key=key, ChecksumMode="ENABLED")
        except Exception as exc:  # noqa: BLE001 -- classified below; the message is dropped
            if _is_missing(exc):
                return MISSING
            if _is_forbidden(exc):
                if self._exact_key_present(key):
                    raise BlobStoreAccessError("blob store access error (HEAD denied for a present object)") from None
                return MISSING
            raise _classify(exc) from None
        if head.get("ChecksumSHA256") != sha256_hex_to_b64(sha_hex):
            return MISMATCH
        if head.get("ContentLength") != size:
            return SIZE_MISMATCH
        modified = head.get("LastModified")
        moment = now or self._clock()
        if isinstance(modified, datetime) and moment - modified > timedelta(days=BLOB_COMMIT_MAX_AGE_DAYS):
            return STALE
        return VERIFIED

    def get_blob(self, content_uri: str, sha_hex: str, size: int, *, offset: int, length: int) -> bytes:
        """The bytes [offset, offset + length) of a stored object, read from the object one HEAD just verified.

        The URI must be tenant/project/session/<sha_hex> in canonical form. One HEAD with ChecksumMode ENABLED proves the
        stored SHA-256 and length against the row; the ranged GET carries IfMatch on that HEAD's ETag, so the bytes come from
        the verified object (no s3:GetObjectVersion). Every fault is typed with a reason and never carries a key or message.
        """
        if _URI_RE.match(content_uri) is None or content_uri.rsplit("/", 1)[-1] != sha_hex:
            raise _integrity("uri_malformed")
        key = self._root.key(content_uri)
        try:
            head = self._client.head_object(Bucket=self._root.bucket, Key=key, ChecksumMode="ENABLED")
        except Exception as exc:  # noqa: BLE001 -- classified; the message (a bucket/key) is dropped
            raise _read_fault(exc) from None
        stored = head.get("ChecksumSHA256")
        if stored is None:
            raise _integrity("checksum_absent")
        if stored != sha256_hex_to_b64(sha_hex):
            raise _integrity("checksum_mismatch")
        etag = head.get("ETag")
        if head.get("ContentLength") != size:
            raise _integrity("length_mismatch")
        if not etag:
            raise _integrity("changed_during_read")
        try:
            got = self._client.get_object(
                Bucket=self._root.bucket, Key=key, Range=f"bytes={offset}-{offset + length - 1}", IfMatch=etag
            )
            data: bytes = got["Body"].read()
        except Exception as exc:  # noqa: BLE001 -- classified; the message (a bucket/key) is dropped
            raise _read_fault(exc) from None
        if len(data) != length:
            raise _integrity("length_mismatch")
        return data

    def grant_put(self, content_uri: str, sha_hex: str, size: int, *, now: datetime | None = None) -> Grant:
        """A 15-minute presigned PUT bound to the digest and length; refused (self-check) if it signs anything else."""
        key = self._root.key(content_uri)
        b64 = sha256_hex_to_b64(sha_hex)
        try:
            url = self._client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self._root.bucket, "Key": key, "ContentLength": size, "ChecksumSHA256": b64},
                ExpiresIn=GRANT_TTL_SECONDS,
                HttpMethod="PUT",
            )
        except Exception as exc:  # noqa: BLE001 -- the message can carry the key
            raise _classify(exc) from None
        self._self_check(url)
        expires = (now or self._clock()) + timedelta(seconds=GRANT_TTL_SECONDS)
        return Grant(
            url, {"Content-Length": str(size), "x-amz-checksum-sha256": b64}, expires.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        )

    @staticmethod
    def _self_check(url: str) -> None:
        query = {k.lower(): v for k, v in parse_qs(urlparse(url).query, keep_blank_values=True).items()}
        signed = query.get("x-amz-signedheaders", [""])[0]
        unbound = [k for k in query if k.startswith(("x-amz-checksum-", "x-amz-sdk-checksum"))]
        if signed != _SIGNED_HEADERS or "x-amz-security-token" not in query or unbound:
            raise BlobGrantSelfCheckError("presigned grant does not bind exactly content-length, host and the sha256 checksum")

    def count_objects(self, prefix: str) -> dict[str, int]:
        """Objects and bytes under prefix (relative to the blob root), through the writer's own prefix-conditioned List."""
        count = size = 0
        token: str | None = None
        while True:
            args: dict[str, Any] = {"Bucket": self._root.bucket, "Prefix": self._root.key(prefix)}
            if token:
                args["ContinuationToken"] = token
            try:
                page = self._client.list_objects_v2(**args)
            except Exception as exc:  # noqa: BLE001 -- classified; the message is dropped
                raise _classify(exc) from None
            for item in page.get("Contents") or []:
                count += 1
                size += int(item.get("Size", 0))
            token = page.get("NextContinuationToken") if page.get("IsTruncated") else None
            if not token:
                return {"count": count, "bytes": size}


__all__ = [
    "BLOB_COMMIT_MAX_AGE_DAYS",
    "INTEGRITY_REASONS",
    "BlobGrantSelfCheckError",
    "BlobIntegrityError",
    "BlobRoot",
    "BlobStoreAccessError",
    "BlobStoreUnavailableError",
    "Grant",
    "LazyBlobStore",
    "MISMATCH",
    "MISSING",
    "S3BlobStore",
    "SIZE_MISMATCH",
    "STALE",
    "VERIFIED",
    "make_s3_client",
]
