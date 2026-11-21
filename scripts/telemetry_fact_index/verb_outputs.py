"""Declared reader-verb output columns: no SQL execution, no duckdb (rec-5439 moves these onto the verbs)."""

from __future__ import annotations

from dataclasses import dataclass, field

TRANSCRIPTS = "telemetry_transcripts"

_ROW_MASKED_KEYS = (
    "encoding",
    "content_bytes",
    "content_sha256",
    "detector",
    "data_class",
    "storage",
    "offset",
    "length",
    "masked_bytes",
    "masked_sha256",
    "mask_counts",
    "data",
    "omitted_reason",
)
_SESSION_ENVELOPE_KEYS = ("rows", "next_offset", "total_rows", "conflicts", "detector", "data_class")
_SESSION_ROW_KEYS = (
    "session_id",
    "producer",
    "event_id",
    "parser_version",
    "event_timestamp",
    "purpose",
    "origin",
    "content_bytes",
    "content_sha256",
    "omitted_reason",
    "storage",
    "text",
    "masked_bytes",
    "masked_sha256",
    "mask_counts",
    "too_large",
)

DECLARED_CONTENT_VERBS: dict[str, tuple[str, ...]] = {
    "transcript_row_masked": _ROW_MASKED_KEYS,
    "session_transcript_masked": (*_SESSION_ENVELOPE_KEYS, *_SESSION_ROW_KEYS),
}

CONTENT_MAPS: dict[str, dict[str, str]] = {
    "transcript_content": {"data": "content", "omitted_reason": "content_omitted_reason"},
    "transcript_row_masked": {"data": "content", "omitted_reason": "content_omitted_reason"},
    "session_transcript_masked": {"text": "content", "omitted_reason": "content_omitted_reason"},
}


@dataclass(frozen=True)
class VerbOutput:
    verb: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    agent_facing: bool = True
    masked: bool = False
    content_map: dict[str, str] = field(default_factory=dict)
    row_json_columns: dict[str, tuple[str, ...]] = field(default_factory=dict)


def declared_outputs() -> dict[str, VerbOutput]:
    """Every EVENT_READS and CONTENT_READS verb with its declared output columns."""
    from src.common.ducklake_content_read_verbs import CONTENT_READS  # noqa: PLC0415
    from src.common.ducklake_event_read_verbs import EVENT_READS  # noqa: PLC0415
    from src.common.ducklake_event_reads import compared_columns  # noqa: PLC0415
    from src.common.ducklake_reader_client import VERB_FIELDS  # noqa: PLC0415

    out: dict[str, VerbOutput] = {}
    for verb, read in EVENT_READS.items():
        columns = VERB_FIELDS.get(verb)
        if not isinstance(columns, tuple):
            raise ValueError(f"EVENT_READS verb {verb!r} has no declared output tuple in VERB_FIELDS")
        row_json = {t: compared_columns(t) for t in read.tables} if "row_json" in columns else {}
        out[verb] = VerbOutput(
            verb, tuple(read.tables), columns, read.agent_facing, False, dict(CONTENT_MAPS.get(verb, {})), row_json
        )
    for verb, read in CONTENT_READS.items():
        declared = DECLARED_CONTENT_VERBS.get(verb)
        if declared is None:
            raise ValueError(f"CONTENT_READS verb {verb!r} has no output declaration in verb_outputs.py")
        out[verb] = VerbOutput(verb, tuple(read.tables), declared, read.agent_facing, True, dict(CONTENT_MAPS.get(verb, {})))
    return out
