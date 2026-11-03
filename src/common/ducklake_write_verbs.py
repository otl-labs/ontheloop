"""Write-verb registry -- the pre-established write verbs the writer Lambda serves (extracted from ducklake_scd2_schema.py).

Moved verbatim by the telemetry table-registration plan (Decision 124 facade-plus-siblings, Decision 128
decompose-by-default); ducklake_scd2_schema re-exports every name here. Pure, I/O-free, no module-scope
import of the facade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Write-verb registry -- the pre-established write verbs the writer Lambda serves (CD.10 / CD.15
# describe surface). Mirrors NAMED_READS' shape for the write side; params_schema is descriptive
# metadata only -- schema_gate (per-table field_semantics) remains the enforced write contract.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteVerb:
    """One pre-established write verb: a description + a descriptive params_schema."""

    verb: str
    description: str
    params_schema: dict[str, Any]


VERB_REGISTRY: dict[str, WriteVerb] = {
    wv.verb: wv
    for wv in (
        WriteVerb(
            verb="write_ops",
            description=(
                "Raw SCD2 upsert into an ops_* table (history MERGE-on-ULID append + current "
                "write-through, schema-gated, bounded-OCC-retried). No id allocation, no require_exists."
            ),
            params_schema={
                "type": "object",
                "properties": {"table": {"type": "string"}, "record": {"type": "object"}},
                "required": ["table", "record"],
            },
        ),
        WriteVerb(
            verb="update_ops",
            description=(
                "Update an existing ops_* record (record is the FULL merged row). Loud-fails "
                "(ReferentialError) if the merge key is absent, or (StatusTransitionError) if the "
                "update would silently reactivate a resolved rec."
            ),
            params_schema={
                "type": "object",
                "properties": {"table": {"type": "string"}, "record": {"type": "object"}},
                "required": ["table", "record"],
            },
        ),
        WriteVerb(
            verb="file_ops",
            description=(
                "Create one ops_* record, allocating its merge key inside the write transaction "
                "(writer-owned keyspace, Decision 84 I-2). idempotency_ulid replays to the "
                "originally-allocated id on a response-lost retry."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "record": {"type": "object"},
                    "idempotency_ulid": {"type": "string"},
                },
                "required": ["table", "record"],
            },
        ),
        WriteVerb(
            verb="create_ops_tables",
            description=(
                "Admin provisioning verb: create (optionally force-recreate) an ops_* table pair with "
                "partition transforms; bootstraps/repairs the writer-owned entity-id counter."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "table": {"type": "string"},
                    "force_recreate_tables": {"type": "boolean"},
                    "confirm_force_recreate": {"type": "string"},
                },
                "required": ["table"],
            },
        ),
    )
}


def describe_write_verbs() -> dict[str, dict[str, Any]]:
    """Per-verb description + params_schema for every VERB_REGISTRY entry (agent-facing `describe`)."""
    return {verb: {"description": wv.description, "params_schema": wv.params_schema} for verb, wv in VERB_REGISTRY.items()}
