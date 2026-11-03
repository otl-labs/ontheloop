"""The telemetry writer's capability handshake (T2.36, slice 2a-2, plan 4; slice 3b's check before it ships rows).

capabilities() is what `describe` advertises beyond the verb list: the blob protocol (version, the inline threshold, the
cap, the item limit, the grant lifetime) and, per event table, the caller-facing schema view (the columns a caller may
send, the NOT NULL set, the accepted values) with a fingerprint of exactly that view. Everything derives from the bundled
projection (one rule source, Decision 210 cl.3). There is no parser_version ceiling and no contract_version predicate: a
producer-only fix must never need a writer deploy (Fable consult Q9). Connectionless (Decision 88 cl.1): no catalog read.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from src.row_rules.rules import ContentRule
from src.telemetry.append import EventTableSpec
from src.telemetry.blobs import BLOB_PROTOCOL_VERSION, GRANT_TTL_SECONDS, MAX_BLOB_ITEMS


def _view(spec: EventTableSpec) -> dict[str, Any]:
    return {
        "caller_known_columns": sorted(spec.caller_known_columns),
        "not_null": sorted(spec.not_null),
        "accepted_values": {column: sorted(values) for column, values in sorted(spec.rules.accepted_values.items())},
    }


def capabilities(specs: Mapping[str, EventTableSpec], payload_rule: ContentRule) -> dict[str, Any]:
    if payload_rule.cap is None:
        raise ValueError("the payload rule declares no cap")
    schema: dict[str, Any] = {}
    for table, spec in sorted(specs.items()):
        view = _view(spec)
        canonical = json.dumps(view, sort_keys=True, separators=(",", ":"))
        schema[table] = {**view, "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
    return {
        "blob_protocol": {
            "version": BLOB_PROTOCOL_VERSION,
            "inline_threshold_bytes": payload_rule.threshold,
            "cap_bytes": payload_rule.cap,
            "max_items": MAX_BLOB_ITEMS,
            "grant_ttl_seconds": GRANT_TTL_SECONDS,
        },
        "schema": schema,
    }


__all__ = ["capabilities"]
