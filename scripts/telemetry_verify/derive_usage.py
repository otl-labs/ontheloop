"""Independent re-derivation of the usage verbs (model_usage_rollup, tool_use_rollup, session_startup_baseline) from deduped
stored rows: pure functions over passed-in rows, no catalog, no SQL (Decision 84 I-3).

Written from the contract text of PLAN-telemetry-usage-reader-verbs (docs/contracts/ducklake_telemetry_reader.yaml, usage
verbs invariant), never from src/common/ducklake_event_read_sql.py. A tree is the deduped rows sharing one root's
(tenant_id, project_id, session_started_at) pin (Decision 199 cl.2); every row is a derivation over one tree, and a verb never
sums across trees. Dedupe, generations and the per-session conflict owner come from derive.Scope and dedupe.py; column names
come from ducklake_reader_client.VERB_FIELDS (never from src.common.event_usage_fields, which the oracle allowlist excludes).

Declared conventions with no contract text, aligned to the verbs' observable behaviour: a session with observation rows but no
sessions row is not a root and has no workflow or agent_name beyond its agent rows; the tool open row of an observation is the
earliest by (event_timestamp, source_ordinal, event_id) and its current close the latest; an unmatched mcp__ name has
no server.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from scripts.telemetry_verify.dedupe import Row, normalise_row
from scripts.telemetry_verify.derive import AGENTS, OBSERVATIONS, SESSIONS, TRANSCRIPTS, Scope, Tables
from src.common.ducklake_reader_client import VERB_FIELDS

_MCP_SERVER = re.compile(r"^mcp__(.+?)__")
_TOKEN_SUMS = (
    ("tokens_input", "tokens_input_total"),
    ("tokens_output", "tokens_output_total"),
    ("tokens_cache_read", "tokens_cache_read_total"),
    ("tokens_cache_creation", "tokens_cache_creation_total"),
    ("cache_creation_1h_tokens", "cache_creation_1h_total"),
    ("cache_creation_5m_tokens", "cache_creation_5m_total"),
)
Pin = tuple[Any, Any, datetime]


def _pin(row: Row) -> Pin:
    return (row["tenant_id"], row["project_id"], row["session_started_at"])


def _order(row: Row) -> tuple[Any, ...]:
    return (row["event_timestamp"], -1 if row.get("source_ordinal") is None else row["source_ordinal"], row["event_id"])


def _first(rows: list[Row], column: str) -> Any:
    return next((r[column] for r in sorted(rows, key=_order) if r.get(column) is not None), None)


def _last(rows: list[Row], column: str) -> Any:
    return next((r[column] for r in sorted(rows, key=_order, reverse=True) if r.get(column) is not None), None)


def _total(values: list[Any]) -> Any:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _nulls_last(value: Any) -> tuple[bool, Any]:
    return (value is None, "" if value is None else value)


class _Trees:
    """The real-purpose trees of one window with their sessions, per-session facts and tree-level columns and stamps."""

    def __init__(self, tables: Tables, projects: Mapping[str, str], day_from: str, day_to: str, verb: str) -> None:
        first = datetime.fromisoformat(day_from).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(day_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
        ids = {
            row["session_id"]
            for name in (SESSIONS, OBSERVATIONS, AGENTS, TRANSCRIPTS)
            for row in map(normalise_row, tables.get(name) or [])
            if first <= row["session_started_at"] < end
        }
        self.scope = Scope(tables, verb, ids)
        self.verb = verb
        sessions, observations = self.scope.tables[SESSIONS], self.scope.tables[OBSERVATIONS]
        self.session_rows: dict[tuple[Pin, str], list[Row]] = {}
        self.pins: dict[Pin, set[str]] = {}
        for row in sessions:
            self.session_rows.setdefault((_pin(row), row["session_id"]), []).append(row)
        for row in (*sessions, *observations):
            self.pins.setdefault(_pin(row), set()).add(row["session_id"])
        self.pins = {pin: sids for pin, sids in self.pins.items() if projects.get(pin[1]) == "real"}
        self.observations = observations
        self.agents = self.scope.tables.get(AGENTS, [])
        self.last_event = {
            pin: max(r["event_timestamp"] for r in (*sessions, *observations) if _pin(r) == pin) for pin in self.pins
        }

    def is_root(self, pin: Pin, session_id: str) -> bool:
        rows = self.session_rows.get((pin, session_id))
        return rows is not None and all(r.get("parent_session_id") is None for r in rows)

    def parent(self, pin: Pin, session_id: str) -> Any:
        rows = self.session_rows.get((pin, session_id), [])
        return max((r["parent_session_id"] for r in rows if r.get("parent_session_id") is not None), default=None)

    def roots(self, pin: Pin) -> list[str]:
        return sorted(sid for sid in self.pins[pin] if self.is_root(pin, sid))

    def tree_columns(self, pin: Pin) -> dict[str, Any]:
        roots = self.roots(pin)
        root = roots[0] if roots else None
        rows = self.session_rows.get((pin, root), []) if root else []
        sids = self.pins[pin]
        generations = sorted({(p, v) for (p, sid), v in self.scope.generations.items() if sid in sids})
        keys = sorted(key[1:] for key in self.scope.conflicts if key[0] in sids)
        return {
            "root_session_id": root,
            "harness_session_key": _first(rows, "harness_session_key"),
            "branch": _first(rows, "branch"),
            "workflow": _first(rows, "workflow"),
            "session_started_at": pin[2],
            "last_event_timestamp": self.last_event[pin],
            "roots_in_tree": len(roots),
            "generations": [{"producer": p, "parser_version": v} for p, v in generations],
            "conflicted_grain_keys": [
                {"table_name": t, "producer": p, "event_id": e, "parser_version": v} for t, p, e, v in keys
            ],
            "derivation_conflicted": bool(keys),
        }

    def finish(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{column: row[column] for column in VERB_FIELDS[self.verb]} for row in rows]  # type: ignore[union-attr]


def model_usage_rollup(tables: Tables, projects: Mapping[str, str], day_from: str, day_to: str) -> list[dict[str, Any]]:
    trees = _Trees(tables, projects, day_from, day_to, "model_usage_rollup")
    out: list[tuple[Any, ...]] = []
    for pin in trees.pins:
        calls = [r for r in trees.observations if _pin(r) == pin and r.get("observation_type") == "model_call"]
        for model in {r.get("model") for r in calls}:
            mine = [r for r in calls if r.get("model") == model]
            row = {
                **trees.tree_columns(pin),
                "model": model,
                "session_count": len({r["session_id"] for r in mine}),
                "model_call_count": len(mine),
                **{name: _total([r.get(column) for r in mine]) for column, name in _TOKEN_SUMS},
                "usage_missing_count": sum(1 for r in mine if r.get("tokens_input") is None or r.get("tokens_output") is None),
            }
            out.append((row["session_started_at"], _nulls_last(row["root_session_id"]), pin[1], _nulls_last(model), row))
    return trees.finish([entry[-1] for entry in sorted(out, key=lambda e: e[:-1])])


def tool_use_rollup(tables: Tables, projects: Mapping[str, str], day_from: str, day_to: str) -> list[dict[str, Any]]:
    trees = _Trees(tables, projects, day_from, day_to, "tool_use_rollup")
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    tools = [r for r in trees.observations if r.get("observation_type") == "tool_call" and _pin(r) in trees.pins]
    for (pin, sid, observation), opens in _by_observation(tools, "open").items():
        opener = min(opens, key=_order)
        closes = _by_observation(tools, "close").get((pin, sid, observation), [])
        outcome = max(closes, key=_order).get("outcome") if closes else None
        root = trees.is_root(pin, sid)
        rows = trees.session_rows.get((pin, sid), [])
        agent = None if root else _last([r for r in trees.agents if r["session_id"] == sid], "agent_name")
        name = opener.get("name")
        match = _MCP_SERVER.match(name) if isinstance(name, str) else None
        meta = json.loads(opener["metadata"]) if opener.get("metadata") else {}
        attribution = meta.get("attribution_mcp_server") if isinstance(meta, dict) else None
        key = (pin, root, agent, _first(rows, "workflow"), name, match.group(1) if match else None, attribution)
        group = groups.setdefault(key, {"calls": 0, "error": 0, "blocked": 0, "interrupted": 0, "sessions": set()})
        group["calls"] += 1
        group["sessions"].add(sid)
        for state in ("error", "blocked", "interrupted"):
            group[state] += outcome == state
    out = []
    for (pin, root, agent, workflow, name, server, attribution), group in groups.items():
        row = {
            **trees.tree_columns(pin),
            "is_root": root,
            "agent_name": agent,
            "workflow": workflow,
            "tool_name": name,
            "mcp_server": server,
            "attribution_mcp_server": attribution,
            "call_count": group["calls"],
            "error_count": group["error"],
            "blocked_count": group["blocked"],
            "interrupted_count": group["interrupted"],
            "session_count": len(group["sessions"]),
        }
        order = (row["session_started_at"], _nulls_last(row["root_session_id"]), not root, agent is not None, agent or "")
        out.append((*order, *map(_nulls_last, (workflow, name, server, attribution)), row))
    return trees.finish([entry[-1] for entry in sorted(out, key=lambda e: e[:-1])])


def _by_observation(rows: list[Row], kind: str) -> dict[tuple[Pin, str, Any], list[Row]]:
    grouped: dict[tuple[Pin, str, Any], list[Row]] = {}
    for row in rows:
        if row["event_kind"] == kind:
            grouped.setdefault((_pin(row), row["session_id"], row["observation_id"]), []).append(row)
    return grouped


def session_startup_baseline(tables: Tables, projects: Mapping[str, str], day_from: str, day_to: str) -> list[dict[str, Any]]:
    trees = _Trees(tables, projects, day_from, day_to, "session_startup_baseline")
    out = []
    for pin in trees.pins:
        for sid in trees.pins[pin]:
            rows = trees.session_rows.get((pin, sid))
            if rows is None:
                continue
            root = trees.is_root(pin, sid)
            calls = sorted(
                (r for r in trees.observations if r["session_id"] == sid and r.get("observation_type") == "model_call"),
                key=_order,
            )
            call = calls[0] if calls else {}
            row = {
                **trees.tree_columns(pin),
                **trees.scope.stamps(sid),
                "session_id": sid,
                "parent_session_id": trees.parent(pin, sid),
                "is_root": root,
                "workflow": _first(rows, "workflow"),
                "agent_name": None if root else _last([r for r in trees.agents if r["session_id"] == sid], "agent_name"),
                "model_call_count": len(calls),
                "first_call_at": call.get("event_timestamp"),
                "model": call.get("model"),
                **{c: call.get(c) for c in ("tokens_input", "tokens_cache_read", "tokens_cache_creation", "tokens_output")},
                **{c: call.get(c) for c in ("cache_creation_1h_tokens", "cache_creation_5m_tokens")},
                "usage_missing": (call.get("tokens_input") is None or call.get("tokens_output") is None) if calls else None,
            }
            out.append((pin[2], _nulls_last(row["root_session_id"]), pin[1], not root, sid, row))
    return trees.finish([entry[-1] for entry in sorted(out, key=lambda e: e[:-1])])
