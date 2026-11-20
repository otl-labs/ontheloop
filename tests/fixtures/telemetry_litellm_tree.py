"""A hand-written litellm tree for the LL-5 tests (PLAN-litellm-claude-code-scoping).

One run_persona run as producer batches, per telemetry-event-envelope.yaml LITELLM PARSER_VERSION 1: the root session
open, its top-level run open, one model_call with no parent turn (litellm emits no turn rows), and the end-record session
close and run close only when the run finished. litellm_batches feeds the real writer (append_events); stored_rows gives
the same rows in stored shape for the pure checks. PARSER_VERSION is 4 on purpose, not the producer's version 1: it
reaches the silent checks' PARSER_FLOOR, so the tests hold the producer filter rather than the floor (at version 1 the
floor alone hides the rows, which is why the hazard is latent until litellm reaches 4).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

T0 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
HOST = "gha:123/1/456/1"
REF = f"litellm:{HOST}"
PARSER_VERSION = 4

Batches = list[tuple[str, list[dict[str, Any]]]]


def _row(second: int, kind: str, external_ref: str, entity_ref: str, **columns: Any) -> dict[str, Any]:
    stamp = T0 + timedelta(seconds=second)
    return {
        "event_kind": kind,
        "event_timestamp": stamp,
        "session_started_at": T0,
        "source_ordinal": second,
        "external_ref": external_ref,
        "entity_ref": entity_ref,
        "producer": "litellm",
        "producer_version": "litellm-1",
        "parser_version": PARSER_VERSION,
        **columns,
    }


def _run(second: int, kind: str, external_ref: str, **columns: Any) -> dict[str, Any]:
    run = {
        "session_ref": REF,
        "agent_type": "code-review",
        "definition_sha256": "a" * 64,
        "trigger": "workflow_run",
        "host_run_ref": HOST,
        "model": "deepseek-flash",
    }
    return _row(second, kind, external_ref, f"{REF}/run", **run, **columns)


def litellm_batches(*, ended: bool) -> Batches:
    """Producer batches of one run_persona run: open rows and one model_call; the end-record closes when *ended*."""
    call = _row(
        2,
        "point",
        f"{REF}/call/1/1/model_call",
        f"{REF}/call/1/1",
        session_ref=REF,
        observation_type="model_call",
        name="persona_turn",
        model="deepseek-flash",
        tokens_input=10,
        tokens_output=5,
        tokens_cache_read=0,
        tokens_cache_creation=0,
        reasoning_visibility="none",
        billing_shape="metered_marginal",
        usage_final=True,
        usage_source="record",
    )
    sessions = [_row(0, "open", f"{REF}#0/open", REF, workflow="persona", execution_attempt=1)]
    agents = [_run(0, "open", f"{REF}/run#0/open")]
    if ended:
        sessions.append(_row(5, "close", f"{REF}/end#0/close", REF, outcome="success"))
        agents.append(_run(5, "close", f"{REF}/end#0/run_close", outcome="success"))
    return [("telemetry_observations", [call]), ("telemetry_agents", agents), ("telemetry_sessions", sessions)]


def stored_rows(batches: Batches, session_id: str = "s-litellm") -> dict[str, list[dict[str, Any]]]:
    """The batches in stored shape (ids filled as the writer would key them), without a catalog."""
    return {
        name: [
            {
                "observation_id": row["entity_ref"],
                "agent_run_id": row["entity_ref"],
                **row,
                "session_id": session_id,
                "event_id": f"{name}-{i}",
                "created_timestamp": row["event_timestamp"],
            }
            for i, row in enumerate(rows)
        ]
        for name, rows in batches
    }
