"""Classifier for a transient DuckLake reader infra outage (Decision 155).

Sole home for `is_reader_unavailable`, extracted verbatim in behaviour from the
already-landed private copy at scripts/data_quality_execute.py (`_is_reader_unavailable` /
`_TRANSIENT_HTTP_RE`), so the two consumers cannot drift.

A 502/503/504 from the DuckLake reader indicates TRANSIENT unavailability -- it is NOT
reliably a cold-resume signal.

Deliberately homed in scripts/ops_portal/ beside its writer-side counterpart
writer_transport.py, and NOT in src/, because the active manifests that wildcard-include
src/ would package any new src/ file and pull in Lambda build/deploy/smoke-test legs for
what is a pure-logic helper.

Allow-list (2026-10-14 amendment on Decision 155): an HTTP 503 whose error_type is in _TRANSIENT_ERROR_TYPES
(the serving handlers' typed `catalog_unavailable`, a dead catalog connection) is also transient; every other
error_type-bearing failure still gates. The literal lives here (scripts/, see above) and a parity test asserts it equal
to src/common/ducklake_handler_errors.py's TRANSIENT_ERROR_TYPES. Consumers (for impact only, licensing nothing):
scripts/ops_portal/decisions.py (DCG-03 guard), scripts/data_quality_execute.py, and the two
scripts/preflight/ci_rca_gauges.py sites. A 504 read_deadline_exceeded carries an error_type, so it gates; only
event-journal verbs answer it and none of those consumers calls one.
"""

from __future__ import annotations

import re

_TRANSIENT_HTTP_RE = re.compile(r"failed \(HTTP (502|503|504)\)")
_ERROR_TYPE_RE = re.compile(r"""error_type["']?\s*[=:]\s*["']?([A-Za-z_][A-Za-z0-9_]*)""")
_TRANSIENT_ERROR_TYPES = frozenset({"catalog_unavailable"})


def _error_type(exc: BaseException, msg: str) -> str | None:
    """The failure's error_type: the parsed body's when the exception carries one, else read from the message text."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error_type"), str):
        return body["error_type"]
    match = _ERROR_TYPE_RE.search(msg)
    return match.group(1) if match else None


def is_reader_unavailable(exc: BaseException) -> bool:
    """True when exc indicates a transient DuckLake reader infra outage.

    Transient: requests ConnectionError/Timeout, or a RuntimeError whose message
    matches the reader's own transient set {502,503,504} with no structured error_type marker, or an
    HTTP 503 whose error_type is in the allow-list. Other structured handler errors (HTTP 500 +
    error_type, 4xx) are not transient -- they gate.
    """
    try:
        import requests as _req  # noqa: PLC0415

        if isinstance(exc, (_req.ConnectionError, _req.Timeout)):
            return True
    except ImportError:
        pass
    if isinstance(exc, RuntimeError):
        msg = str(exc)
        match = _TRANSIENT_HTTP_RE.search(msg)
        if match:
            error_type = _error_type(exc, msg)
            if error_type is None and "error_type" not in msg:
                return True
            if match.group(1) == "503" and error_type in _TRANSIENT_ERROR_TYPES:
                return True
    return False
