"""Mirror test package; also holds the synthetic friction_signature_counts rows and reader fakes the tests share."""

from __future__ import annotations

from typing import Any

from src.common.ducklake_event_read_verbs import FRICTION_RULES

VERSION = FRICTION_RULES["classifier_version"]
PLANTED_TOOL = "mcp__planted_connector__secret_tool_zq"
PLANTED_SESSION = "01PLANTEDSESSIONID0000000000"
PLANTED_ROOT = "PLANTED-ROOT-KEY"
PLANTED_BRANCH = "claude/planted-branch-zq"


def row(tree: int, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "root_session_id": f"{PLANTED_SESSION}{tree}",
        "harness_session_key": f"{PLANTED_ROOT}{tree}",
        "session_started_at": f"2026-11-1{tree % 10}T0{tree % 10}:00:00Z",
        "branch": PLANTED_BRANCH,
        "roots_in_tree": 1,
        "workflow": None,
        "observation_type": "process_event",
        "signature": "hook:never_on_main",
        "disposition": None,
        "rule_id": "r01",
        "label": "branch_policy_block",
        "class": "exception",
        "generic": False,
        "fact_count": 1,
        "session_count": 1,
        "classifier_version": VERSION,
        "derivation_conflicted": False,
    }
    base.update(over)
    return base


def generic_row(tree: int, facts: int) -> dict[str, Any]:
    return row(tree, signature="hook:PreToolUse.Bash", rule_id="r90", label="unmapped_block", generic=True, fact_count=facts)


def recurring(sessions: int, facts: int, **over: Any) -> list[dict[str, Any]]:
    """Rows of one group spread over `sessions` trees, summing to `facts` facts."""
    out = []
    for i in range(sessions):
        out.append(row(i + 1, fact_count=facts // sessions + (1 if i < facts % sessions else 0), **over))
    return out


def repeat_run(sessions: int, facts: int, tool: str = PLANTED_TOOL) -> list[dict[str, Any]]:
    return recurring(
        sessions,
        facts,
        observation_type="tool_call",
        signature=tool,
        rule_id="run",
        label="repeated_tool_failure",
        **{"class": "rework"},
    )


def rec(
    rec_id: str, status: str, detector_id: str, subject: str, created: str = "2026-11-01", updated: str = "2026-11-02"
) -> dict[str, Any]:
    import json

    return {
        "id": rec_id,
        "status": status,
        "source": "feedback_loop",
        "created_timestamp": created,
        "last_updated_timestamp": updated,
        "context_v2_json": json.dumps({"detector_id": detector_id, "subject_key": subject}),
    }


class Reader:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        recs: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
        rec_error: Exception | None = None,
    ) -> None:
        self.rows, self.recs, self.error, self.rec_error = rows or [], recs or [], error, rec_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((verb, params))
        if self.error is not None:
            raise self.error
        return list(self.rows)

    def current_state(self, table: str, **kw: Any) -> list[dict[str, Any]]:
        self.calls.append((table, kw))
        if self.rec_error is not None:
            raise self.rec_error
        return list(self.recs)
