"""The telemetry writer client for the capture hooks (rules TR-1..TR-4).

A fourth SigV4 Function-URL client (PC-01) with its own SSM lookup and a status-returning invoke, because the others raise
on non-200. Every HTTP request, blob PUT, SSM and STS call is bounded by the pass budget: each timeout is
min(130 s, remaining), and one botocore Config (a single attempt, timeouts clamped) is installed as the botocore session's
default client config so the SSM client and the assume-role STS client behind the profile both inherit it.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from scripts.aws_profile import DEFAULT_SSO_PROFILE, resolve_aws_profile
from scripts.telemetry_capture.state import CaptureState
from src.telemetry.blobs import blob_row
from src.turn_capture.render import render_datetime

URL_ENV = "DUCKLAKE_TELEMETRY_WRITER_URL"
SSM_PARAMETER = "/agent-platform/ducklake/telemetry_writer_url"
DEFAULT_REGION = "eu-west-2"
COMPONENT = "turn_capture"
MAX_TIMEOUT_S = 130.0
MAX_INVOKE_BODY_BYTES = 5242880
MAX_ROWS_PER_TABLE = 2000
MAX_ROWS_PER_REQUEST = 5000
RETRY_STATUSES = frozenset({0, 429, 503})
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0
_URL_REGION = re.compile(r"\.lambda-url\.([a-z0-9-]+)\.on\.aws")

Http = Callable[[str, str, bytes, Mapping[str, str], float], tuple[int, bytes]]
Item = tuple[str, dict[str, Any]]


class CaptureUnavailable(Exception):
    """The pass cannot reach the writer: reason is a fixed token (no_credentials: an absent credential; credential_error:
    the profile, STS or credential chain raised; credential_transport: the chain failed to connect, e.g. SessionEnd after
    Claude Code stopped its local proxy; no_writer_url; deadline). A token never carries a message."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class Budget:
    deadline: float
    clock: Callable[[], float] = time.monotonic

    @classmethod
    def start(cls, seconds: float, clock: Callable[[], float] = time.monotonic) -> Budget:
        return cls(clock() + seconds, clock)

    def remaining(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def timeout(self) -> float:
        return min(MAX_TIMEOUT_S, self.remaining())


def _default(value: Any) -> str:
    if isinstance(value, datetime):
        return render_datetime(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def encode(value: Any) -> str:
    return json.dumps(value, default=_default)


def invoke_size(body: Mapping[str, Any]) -> int:
    """The writer's measure: the exact body string re-escaped as a JSON string."""
    return len(json.dumps(encode(body)))


def _escaped(text: str) -> int:
    return len(json.dumps(text)) - 2


def urllib_request(method: str, url: str, data: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    """(status, body); a transport failure raises OSError or TimeoutError without the URL."""
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 -- https Function URL or writer grant
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except urllib.error.URLError:
        raise OSError("writer transport failure") from None


def _json_or_none(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


class WriterClient:
    def __init__(
        self,
        url: str,
        sign: Callable[[bytes], Mapping[str, str]],
        budget: Budget,
        *,
        http: Http = urllib_request,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.url, self._sign, self.budget = url, sign, budget
        self._http, self._sleep, self._rng = http, sleep, rng

    def _timeout(self) -> float:
        timeout = self.budget.timeout()
        if timeout <= 0:
            raise TimeoutError("pass deadline reached")
        return timeout

    def invoke_once(self, payload: Mapping[str, Any]) -> tuple[int, Any]:
        """One signed POST (re-signed per attempt), its timeout clamped to the budget."""
        timeout = self._timeout()
        body = encode(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", **self._sign(body)}
        status, raw = self._http("POST", self.url, body, headers, timeout)
        return status, _json_or_none(raw)

    def invoke(self, payload: Mapping[str, Any]) -> tuple[int, Any]:
        """TR-2: 429, 503 and transport errors retry with full-jitter backoff while the deadline allows; status 0 is a
        transport failure."""
        attempt = 0
        while True:
            try:
                status, body = self.invoke_once(payload)
            except (OSError, TimeoutError):
                status, body = 0, None
            if status not in RETRY_STATUSES:
                return status, body
            pause = self._rng() * min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt)
            if self.budget.remaining() <= pause:
                return status, body
            self._sleep(pause)
            attempt += 1

    def put(self, url: str, headers: Mapping[str, str], data: bytes) -> tuple[int, bytes]:
        """One blob PUT under a writer grant, timeout clamped (deliver_blobs owns the retries)."""
        timeout = self._timeout()
        return self._http("PUT", url, data, {"Content-Type": "application/octet-stream", **headers}, timeout)


def url_region(url: str) -> str:
    found = _URL_REGION.search(url)
    if found is None:
        raise CaptureUnavailable("no_writer_url")
    return found.group(1)


def resolve_url(env: Mapping[str, str], state: CaptureState | None, ssm_get: Callable[[], str | None]) -> str:
    """TR-1: the env override, else the state dir's cached URL, else the SSM parameter (then cached)."""
    url = (env.get(URL_ENV) or "").rstrip("/")
    if url:
        return url
    cached = state.writer_url() if state is not None else None
    if cached:
        return cached
    try:
        url = (ssm_get() or "").rstrip("/")
    except Exception:  # noqa: BLE001 -- any SSM failure is the same fixed reason, never the message
        url = ""
    if not url:
        raise CaptureUnavailable("no_writer_url")
    if state is not None:
        state.remember_writer_url(url)
    return url


def make_client(
    env: Mapping[str, str],
    state: CaptureState | None,
    budget: Budget,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: Callable[[], float] = random.random,
) -> WriterClient:
    """The production client: botocore SigV4 under the pinned agent_platform profile, every call clamped to the budget.

    The ambient AWS_PROFILE is not honoured, so an ADMIN environment still signs as PlatformDev; environment keys (CI) and
    Lambda keep the default chain.
    """
    import boto3  # noqa: PLC0415
    import botocore.session  # noqa: PLC0415
    from botocore.auth import SigV4Auth  # noqa: PLC0415
    from botocore.awsrequest import AWSRequest  # noqa: PLC0415
    from botocore.config import Config  # noqa: PLC0415
    from botocore.exceptions import (  # noqa: PLC0415
        ConnectionClosedError,
        ConnectTimeoutError,
        EndpointConnectionError,
        ProxyConnectionError,
    )

    timeout = budget.timeout()
    if timeout <= 0:
        raise CaptureUnavailable("deadline")
    core = botocore.session.get_session()
    core.set_default_client_config(Config(retries={"total_max_attempts": 1}, connect_timeout=timeout, read_timeout=timeout))
    try:
        session = boto3.Session(botocore_session=core, profile_name=resolve_aws_profile(DEFAULT_SSO_PROFILE))
        credentials = session.get_credentials()
        if credentials is None:
            raise CaptureUnavailable("no_credentials")
        credentials.get_frozen_credentials()
    except CaptureUnavailable:
        raise
    except (ProxyConnectionError, EndpointConnectionError, ConnectTimeoutError, ConnectionClosedError):
        raise CaptureUnavailable("credential_transport") from None
    except Exception:  # noqa: BLE001 -- profile, STS or credential-chain failure: one fixed reason, no message
        raise CaptureUnavailable("credential_error") from None
    ssm_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION

    def ssm_get() -> str | None:
        answer = session.client("ssm", region_name=ssm_region).get_parameter(Name=SSM_PARAMETER, WithDecryption=False)
        return answer["Parameter"]["Value"]

    url = resolve_url(env, state, ssm_get)
    region = url_region(url)

    def sign(body: bytes) -> Mapping[str, str]:
        request = AWSRequest(method="POST", url=url, data=body, headers={"Content-Type": "application/json"})
        SigV4Auth(credentials.get_frozen_credentials(), "lambda", region).add_auth(request)
        return dict(request.headers.items())

    return WriterClient(url, sign, budget, sleep=sleep, rng=rng)


@dataclass(frozen=True)
class Limits:
    blob_items: int
    threshold: int
    cap: int
    rows_per_table: int = MAX_ROWS_PER_TABLE
    rows: int = MAX_ROWS_PER_REQUEST
    body_bytes: int = MAX_INVOKE_BODY_BYTES


def append_body(project_ref: str, items: Sequence[Item]) -> dict[str, Any]:
    """One append_telemetry body: contiguous rows of one table form one batch, in the given (record_turn) order."""
    batches: list[dict[str, Any]] = []
    for table, row in items:
        if not batches or batches[-1]["table"] != table:
            batches.append({"table": table, "rows": []})
        batches[-1]["rows"].append(row)
    return {"action": "append_telemetry", "project_ref": project_ref, "component": COMPONENT, "batches": batches}


def chunk(items: Sequence[Item], project_ref: str, limits: Limits) -> list[list[Item]]:
    """TR-3: split the flat row sequence, in order, under every per-request cap.

    The body measure is additive over the body string's pieces (JSON escaping is per character), so each row costs at
    most its escaped JSON plus a two-character separator and each batch its escaped empty shell plus one: an upper bound
    on the exact measure, never an under-count.
    """
    base = invoke_size(append_body(project_ref, []))
    out: list[list[Item]] = []
    current: list[Item] = []
    size, blobs, per_table = base, 0, Counter[str]()
    for table, row in items:
        is_blob = int(blob_row(row, limits.threshold, limits.cap))
        row_cost = _escaped(encode(row)) + 2
        shell = _escaped(encode({"table": table, "rows": []})) + 2
        cost = row_cost + (shell if not current or current[-1][0] != table else 0)
        full = (
            per_table[table] + 1 > limits.rows_per_table
            or len(current) + 1 > limits.rows
            or blobs + is_blob > limits.blob_items
            or size + cost > limits.body_bytes
        )
        if current and full:
            out.append(current)
            current, size, blobs, per_table = [], base, 0, Counter()
            cost = row_cost + shell
        current.append((table, row))
        size, blobs = size + cost, blobs + is_blob
        per_table[table] += 1
    if current:
        out.append(current)
    return out
