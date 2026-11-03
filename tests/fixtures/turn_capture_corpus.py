"""Shared helpers for the turn-capture tests: synthetic record builders, the golden corpus loader, normalisation and the
single golden-digest definition. Stdlib only (fast tier); the corpus is synthetic and no credential-shaped literal is
ever written here -- secret-shaped strings are assembled at runtime by build_secret().
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from src.telemetry.identity import KEY_PLANS, derive_entity_key, derive_event_id
from src.turn_capture.render import render_datetime
from src.turn_capture.transcript import MemTree

SID = "aaaaaaaa-0000-4000-8000-000000000001"
TENANT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
PROJECT = "01BX5ZZKBKACTAV9WEVGEMMVRY"
PROJECT_REF = "example/project"
GOLDEN_DIR = Path(__file__).parent / "turn_capture" / "golden"
_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


@functools.cache
def normalised_out() -> frozenset[str]:
    return frozenset(json.loads((GOLDEN_DIR / "index.json").read_text(encoding="utf-8"))["normalised_out"])


def ts(seconds: float) -> str:
    stamp = _EPOCH + timedelta(milliseconds=round(seconds * 1000))
    return f"{stamp:%Y-%m-%dT%H:%M:%S}.{stamp.microsecond // 1000:03d}Z"


def _base(uuid: str, t: float, **extra: Any) -> dict[str, Any]:
    rec = {"uuid": uuid, "timestamp": ts(t), "sessionId": SID, "gitBranch": "claude/test", "version": "2.1.0", "cwd": "/w"}
    rec.update(extra)
    return rec


def queue_op(t: float, *, uuid: str | None = None) -> dict[str, Any]:
    rec: dict[str, Any] = {"type": "queue-operation", "operation": "enqueue", "timestamp": ts(t), "sessionId": SID}
    if uuid:
        rec["uuid"] = uuid
    return rec


def prompt(uuid: str, t: float, pid: str, text: Any, **extra: Any) -> dict[str, Any]:
    return _base(uuid, t, type="user", promptId=pid, message={"role": "user", "content": text}, **extra)


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def thinking_block(text: str) -> dict[str, Any]:
    return {"type": "thinking", "thinking": text, "signature": "sig"}


def tool_use_block(tool_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input, "caller": {"type": "direct"}}


def assistant(
    uuid: str,
    t: float,
    mid: str,
    blocks: list[Any],
    *,
    usage: dict[str, Any] | None = None,
    api_block: int | None = None,
    model: str = "claude-test-1",
    stop_reason: str | None = "end_turn",
    **extra: Any,
) -> dict[str, Any]:
    use = (
        {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}
        if usage is None
        else usage
    )
    message = {
        "id": mid,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": use,
    }
    rec = _base(uuid, t, type="assistant", message=message, requestId="req_" + mid, **extra)
    if api_block is not None:
        rec["apiBlockIndex"] = api_block
    return rec


def tool_result(
    uuid: str,
    t: float,
    tool_use_id: str,
    content: Any,
    *,
    pid: str | None = None,
    is_error: bool | None = None,
    tur: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    block: dict[str, Any] = {"tool_use_id": tool_use_id, "type": "tool_result", "content": content}
    if is_error is not None:
        block["is_error"] = is_error
    rec = _base(uuid, t, type="user", message={"role": "user", "content": [block]}, **extra)
    if pid is not None:
        rec["promptId"] = pid
    if tur is not None:
        rec["toolUseResult"] = tur
    return rec


def hook(
    uuid: str,
    t: float,
    hook_name: str,
    *,
    tool_use_id: str | None = None,
    exit_code: int | None = 0,
    command: str | None = None,
    a_type: str = "hook_success",
    **extra: Any,
) -> dict[str, Any]:
    att: dict[str, Any] = {"type": a_type, "hookName": hook_name, "hookEvent": hook_name.split(":")[0]}
    if tool_use_id is not None:
        att["toolUseID"] = tool_use_id
    if exit_code is not None:
        att["exitCode"] = exit_code
    if command is not None:
        att["command"] = command
    att.update(extra)
    return _base(uuid, t, type="attachment", attachment=att)


STOP_COMMAND = '"$CLAUDE_PROJECT_DIR/bin/venv-python" "$CLAUDE_PROJECT_DIR/.claude/hooks/stop_log_turn.py"'
WITNESS_STDERR = "[stop_log_turn] turn witness\n"
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def witness(uuid: str, t: float, event: str = "Stop") -> dict[str, Any]:
    """The capture shim's exit-0 hook_success attachment at a Stop (SubagentStop when *event* says so, in a child file)."""
    return hook(uuid, t, event, command=STOP_COMMAND, stderr=WITNESS_STDERR, stdout="", toolUseID="stop-" + uuid)


def other_stop_hook(uuid: str, t: float) -> dict[str, Any]:
    return hook(uuid, t, "Stop", command="bash ./other_stop.sh", stderr="", stdout="", toolUseID="stop-" + uuid)


def stop_feedback(uuid: str, t: float, pid: str) -> dict[str, Any]:
    """The isMeta record a blocking Stop hook leaves: the turn's promptId, the feedback text."""
    return prompt(uuid, t, pid, "Stop hook feedback:\n[bash ./gate.sh]: not yet", isMeta=True)


def blocking_error(uuid: str, t: float) -> dict[str, Any]:
    return hook(uuid, t, "Stop", a_type="hook_blocking_error", exit_code=2, command="bash ./gate.sh", blockingError="not yet")


def stop_summary(uuid: str, t: float) -> dict[str, Any]:
    return _base(uuid, t, type="system", subtype="stop_hook_summary", hookCount=2, hookErrors=[], toolUseID="stop-" + uuid)


def api_error(uuid: str, t: float, kind: str = "server_error") -> dict[str, Any]:
    rec = assistant(
        uuid, t, "00000000-0000-4000-8000-0000000000e1", [text_block("API Error: Connection refused")],
        usage=ZERO_USAGE, model="<synthetic>", isApiErrorMessage=True, error=kind,
    )  # fmt: skip
    rec["message"]["stop_reason"] = "stop_sequence"
    return rec


def attachment(uuid: str, t: float, a_type: str, **fields: Any) -> dict[str, Any]:
    return _base(uuid, t, type="attachment", attachment={"type": a_type, **fields})


def pin_attachment(
    uuid: str, t: float, ref: str, facts: str | None, hook_name: str = "SessionStart:startup"
) -> dict[str, Any]:
    """The SessionStart pin hook's attachment: its command carries --project-ref, its stderr the checkout facts line."""
    command = f'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/session_start_log_turn.sh" --project-ref={ref}'
    line = "[session_start_log_turn] capture pin" + (f" {facts}" if facts else "") + "\n"
    return hook(uuid, t, hook_name, command=command, stderr=line)


def pin_facts_text(checkout: str = "1", base: str = "2", dirty: str = "false") -> str:
    """Synthetic low-entropy facts: a repeated hex digit is a 40-character sha, 'unknown' stays literal."""
    sha = lambda v: v if v == "unknown" else v * 40  # noqa: E731
    return f"checkout={sha(checkout)} base={sha(base)} dirty={dirty}"


def remote_change(
    uuid: str, t: float, url: str = "https://example.invalid/code/session_AAAA1111bbbb2222cccc3333"
) -> dict[str, Any]:
    return attachment(uuid, t, "remote_session_change", url=url)


def task_notice(
    uuid: str, t: float, agent_id: str, status: str | None = "completed", raw_status: str | None = None
) -> dict[str, Any]:
    """A background completion notice as a queued_command attachment; raw_status replaces the whole <status> element."""
    middle = raw_status if raw_status is not None else ("" if status is None else f"<status>{status}</status>\n")
    return attachment(
        uuid, t, "queued_command", prompt=f"<task-notification>\n<task-id>{agent_id}</task-id>\n{middle}</task-notification>"
    )


def child(rec: dict[str, Any], agent_id: str) -> dict[str, Any]:
    return {**rec, "agentId": agent_id, "isSidechain": True}


def jsonl(records: list[dict[str, Any]], tail: str = "") -> str:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in records) + tail


def make_files(
    root: list[dict[str, Any]],
    children: dict[str, list[dict[str, Any]]] | None = None,
    sidecars: dict[str, str] | None = None,
    root_tail: str = "",
) -> dict[str, str]:
    files = {f"{SID}.jsonl": jsonl(root, root_tail)}
    for agent_id, records in (children or {}).items():
        files[f"{SID}/subagents/agent-{agent_id}.jsonl"] = jsonl(records)
    for name, body in (sidecars or {}).items():
        files[f"{SID}/tool-results/{name}"] = body
    return files


def build_secret(kind: str) -> str:
    """Secret-shaped strings assembled by concatenation, so no credential-shaped literal is committed."""
    filler = "abcdefghijklmnopqrstuvwxyz0123456789"
    parts = {
        "aws_access": ("AK" + "IA", "ABCDEFGHIJKLMNOP"),
        "aws_secret": ("wJalrXUtnFEMI/K7MDENG", "/bPxRfiCYEXAMPLEKEY"),
        "aws_session": ("FwoGZXIvYXdzE" + "JDH", "//////////wEaDExampleSessionTokenValue+/=="),
        "anthropic": ("sk-" + "ant-", "api03-" + filler + "_-" + filler[:8]),
        "github": ("gh" + "p_", filler + filler[:8]),
        "github_pat": ("github" + "_pat_", (filler + "_") * 3),
        "bearer": ("Bear" + "er ", "abcdef0123456789abcdef0123456789xyz"),
    }
    head, tail = parts[kind]
    return head + tail


MCP_OVERFLOW_TEXT = (
    "Error: result (15 characters) exceeds maximum allowed tokens. "
    "Output has been saved to /elsewhere/tool-results/mcp-overflow.txt\nFormat: JSON array"
)


def mcp_overflow_files() -> dict[str, str]:
    """An MCP tool_result whose toolUseResult is the plain string naming the saved output file."""
    records = [
        prompt("u1", 1, "p1", "call the mcp tool"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "mcp__srv__big", {"q": 1})]),
        tool_result("r1", 3, "tu1", MCP_OVERFLOW_TEXT, pid="p1", is_error=True, tur=MCP_OVERFLOW_TEXT),
        prompt("u2", 5, "p2", "thanks"),
    ]
    return make_files(records, sidecars={"mcp-overflow.txt": '[{"rows": 15}]\n'})


def hooks_before_first_record_files() -> dict[str, str]:
    """SessionStart hook records written AFTER a queue-operation but timestamped earlier (the probed shape): the first
    hook carries the earliest timestamp and the hooks ascend among themselves."""
    records = [
        queue_op(5, uuid="q0"),
        hook("hk1", 1, "SessionStart:startup", exit_code=0),
        hook("hk2", 2, "SessionStart:startup", exit_code=0),
        prompt("u1", 6, "p1", "go"),
        assistant("a1", 7, "m1", [text_block("done")]),
        prompt("u2", 9, "p2", "next"),
    ]
    return make_files(records)


def reasoning_forms_files() -> dict[str, str]:
    """One response per reasoning form: summarized, summarized (precedence), redacted, omitted (tokens only), none with
    zero tokens reported, none with none reported."""
    redacted = {"type": "redacted_thinking", "data": "ZW5j"}

    def use(thinking: int | None) -> dict[str, Any]:
        details = {} if thinking is None else {"output_tokens_details": {"thinking_tokens": thinking}}
        return {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 40,
            **details,
        }

    records = [
        prompt("u1", 1, "p1", "think"),
        assistant("a1", 2, "ms1", [thinking_block("plan"), text_block("one")], usage=use(7)),
        assistant("a2", 3, "ms2", [thinking_block("plan"), redacted, text_block("two")], usage=use(None)),
        assistant("a3", 4, "mr1", [redacted, text_block("three")], usage=use(3)),
        assistant("a4", 5, "mo1", [text_block("four")], usage=use(5)),
        assistant("a5", 6, "mn1", [text_block("five")], usage=use(0)),
        assistant("a6", 7, "mn2", [text_block("six")], usage=use(None)),
        prompt("u2", 9, "p2", "next"),
    ]  # fmt: skip
    return make_files(records)


OVER_CAP_TEST_CAP = 16


def full_output_omitted_over_cap_files() -> dict[str, str]:
    """A persisted output larger than the case's own test-only cap (OVER_CAP_TEST_CAP): a tool_output omission row."""
    records = [
        prompt("u1", 1, "p1", "dump"),
        assistant("a1", 2, "m1", [tool_use_block("tu1", "Bash", {"command": "dump"})]),
        tool_result(
            "r1", 3, "tu1", "preview of the dump", pid="p1",
            tur={"persistedOutputPath": "/elsewhere/tool-results/dump.txt", "persistedOutputSize": 43},
        ),
        prompt("u2", 5, "p2", "ok"),
    ]  # fmt: skip
    return make_files(records, sidecars={"dump.txt": "0123456789\nabcdefghij\n0123456789\nabcdefghij"})


def fork_boundary(files: dict[str, str]) -> int:
    """1 + the line index of the first attachment of the last SessionStart:fork hook group in the root file, else 0.

    A group ends at the next user or assistant record. The root cuts of every schedule start here: an earlier cut is
    rule K-1's halting case, pinned by its own test.
    """
    boundary, in_group = 0, False
    for index, line in enumerate(files[f"{SID}.jsonl"].split("\n")[:-1]):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        att = rec.get("attachment") if isinstance(rec, dict) else None
        if isinstance(rec, dict) and rec.get("type") in ("user", "assistant"):
            in_group = False
        elif isinstance(att, dict) and str(att.get("hookName", "")).startswith("SessionStart:fork") and not in_group:
            in_group, boundary = True, index + 1
    return boundary


def _turn(n: int, sidecar: str | None) -> list[dict[str, Any]]:
    t = 10.0 * n
    tool = f"tool{n}"
    tur = {"persistedOutputPath": f"/e/tool-results/{sidecar}", "persistedOutputSize": 9} if sidecar else None
    return [
        prompt(f"lu{n}", t, f"lp{n}", f"question {n}"),
        assistant(f"la{n}", t + 1, f"lm{n}", [tool_use_block(tool, "Bash", {"command": "ls"})]),
        tool_result(f"lr{n}", t + 2, tool, f"out {n}", pid=f"lp{n}", tur=tur),
        assistant(f"lb{n}", t + 3, f"ln{n}", [text_block("done")]),
    ]


def large_tree(n_lines: int, first: int = 0) -> dict[str, str]:
    """A synthetic root tree of about n_lines records (four per turn) with one persisted sidecar per 500 lines."""
    records, sidecars = [], {}
    for n in range(first, first + n_lines // 4):
        name = f"side{n}.txt" if (n - first) % 125 == 0 else None
        records += _turn(n, name)
        if name:
            sidecars[name] = f"sidecar body {n}\n"
    return make_files(records, sidecars=sidecars)


def resumed_large_tree(n_lines: int, finalizations: int) -> list[dict[str, str]]:
    """The tree after each of *finalizations* finalizations: every later one resumes (SessionStart:resume) and grows."""
    per = n_lines // finalizations // 4
    records: list[dict[str, Any]] = []
    sidecars: dict[str, str] = {}
    trees = []
    for k in range(finalizations):
        if k:
            records.append(hook(f"lh{k}", 10.0 * k * per - 1, "SessionStart:resume", command="bash ./resume.sh"))
        for n in range(k * per, (k + 1) * per):
            name = f"side{n}.txt" if (n - k * per) % 125 == 0 else None
            records += _turn(n, name)
            if name:
                sidecars[name] = f"sidecar body {n}\n"
        trees.append(make_files(records, sidecars=dict(sidecars)))
    return trees


RESUME_TAIL = 100000.0


def resume_tail() -> list[dict[str, Any]]:
    return [
        hook("rz-h", RESUME_TAIL, "SessionStart:resume", command="bash ./resume.sh"),
        prompt("rz-u", RESUME_TAIL + 1, "rz-p", "back again"),
        assistant("rz-a", RESUME_TAIL + 2, "rz-m", [text_block("welcome back")]),
    ]


def root_lines(files: dict[str, str]) -> list[str]:
    return files[f"{SID}.jsonl"].splitlines(keepends=True)


def with_root(files: dict[str, str], body: str) -> dict[str, str]:
    return {**files, f"{SID}.jsonl": body}


def case_names() -> list[str]:
    return list(json.loads((GOLDEN_DIR / "index.json").read_text(encoding="utf-8"))["cases"])


def load_case(name: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    body = json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8"))
    return (secrets_files() if name == "secrets.json" else body["files"]), body["expected_rows"]


def case_full_output_cap(name: str) -> int | None:
    """The case's own test-only full_output_cap_bytes override (None when the case carries none)."""
    cap = json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8")).get("full_output_cap_bytes")
    return cap if type(cap) is int else None


@contextlib.contextmanager
def case_cap(name: str) -> Iterator[None]:
    """Patch record_turn.FULL_OUTPUT_CAP_BYTES to the case's override for the duration of the block.

    Every corpus consumer wraps its record_turn calls in it (load_case alone applies nothing, because consumers call
    record_turn after it returns).
    """
    from src.turn_capture import record_turn as module  # noqa: PLC0415

    cap = case_full_output_cap(name)
    if cap is None:
        yield
        return
    saved = module.FULL_OUTPUT_CAP_BYTES
    module.FULL_OUTPUT_CAP_BYTES = cap
    try:
        yield
    finally:
        module.FULL_OUTPUT_CAP_BYTES = saved


def mem_tree(files: dict[str, str]) -> MemTree:
    return MemTree(files)


def _render(value: Any) -> Any:
    return render_datetime(value) if isinstance(value, datetime) else value


def assert_content_hashes(batches: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """content_sha256 is the sha256 of the UTF-8 bytes of content and content_bytes their length. The digest is dropped
    from the committed golden rows (64-hex strings trip detect-secrets and bloat .secrets.baseline past its size budget)
    and asserted here for every emitted row instead."""
    for _, rows in batches:
        for row in rows:
            if "content" in row:
                data = row["content"].encode("utf-8")
                assert row["content_sha256"] == hashlib.sha256(data).hexdigest(), row["external_ref"]
                assert row["content_bytes"] == len(data), row["external_ref"]


def assert_output_identities(files: dict[str, str], batches: list[tuple[str, list[dict[str, Any]]]]) -> None:
    """output_sha256/output_bytes/output_lines of a tool_call close describe the RAW output: the named sidecar's bytes
    when the harness persisted it, else the model-visible text. The digest is dropped from the committed goldens
    (64-hex values trip detect-secrets), so it is recomputed here for every close row that carries one."""
    for table, rows in batches:
        if table != "telemetry_observations":
            continue
        for row in rows:
            if row.get("output_capture") not in ("captured", "omitted_oversize"):
                continue
            name = json.loads(row["metadata"])["persisted_output"]
            data = files[f"{SID}/tool-results/{name}"].encode("utf-8")
            assert row["output_sha256"] == hashlib.sha256(data).hexdigest(), row["external_ref"]
            assert row["output_bytes"] == len(data), row["external_ref"]
            assert row["output_lines"] == data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0), row[
                "external_ref"
            ]


def normalise(batches: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Canonical rows: derived ids added with the fixed test tenant/project; None columns and the golden index's
    normalised_out columns dropped; datetimes rendered; sorted by table then event_id."""
    dropped = normalised_out()
    out: list[dict[str, Any]] = []
    for table, rows in batches:
        for row in rows:
            norm = {k: _render(v) for k, v in row.items() if v is not None and k not in dropped}
            for column, plan in KEY_PLANS[table].items():
                ref = row.get(plan.ref_field)
                if ref is not None:
                    norm[column] = derive_entity_key(plan.domain_tag, TENANT, PROJECT, ref, row["session_started_at"])
            norm["event_id"] = derive_event_id(table, TENANT, PROJECT, row["external_ref"], row["event_timestamp"])
            norm["table"] = table
            out.append(norm)
    return sorted(out, key=lambda r: (r["table"], r["event_id"]))


def golden_digest(rows_by_case: dict[str, list[dict[str, Any]]], order: list[str]) -> str:
    """sha256 over, per case in index order, the case basename, a newline and the canonical expected_rows JSON."""
    parts = [
        f"{name}\n{json.dumps(rows_by_case[name], sort_keys=True, separators=(',', ':'), ensure_ascii=False)}"
        for name in order
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def secrets_files() -> dict[str, str]:
    """The secrets case inputs, assembled at runtime (never committed as literals)."""
    s = build_secret
    escaped = json.dumps({"SecretAccessKey": s("aws_secret"), "SessionToken": s("aws_session")})
    records = [
        prompt("s1", 1, "p1", f"keys {s('aws_access')} and {s('anthropic')} and {s('github')}"),
        assistant(
            "s2", 2, "ms1",
            [
                text_block(f"token {s('github_pat')} then {s('bearer')}"),
                tool_use_block("tus1", "Bash", {"command": f"echo {escaped}"}),
            ],
        ),
        tool_result(
            "s3", 3, "tus1", f"export AWS_SECRET_ACCESS_KEY={s('aws_secret')}\nexport AWS_SESSION_TOKEN={s('aws_session')}\n",
            pid="p1",
        ),
        hook("s4", 4, "PostToolUse:Bash", tool_use_id="tus1", command=f"bash -c 'curl -H \"Authorization: {s('bearer')}\"'"),
    ]  # fmt: skip
    return make_files(records)


def make_ctx(billing_shape: str = "fixed_non_rollover_allowance"):
    from src.turn_capture.transcripts import Ctx  # noqa: PLC0415

    return Ctx("claude_code", "test-build", 1, _EPOCH, billing_shape, 8388608)


def parse_records(root, children=None, final=True):
    from src.turn_capture.streams import parse_tree  # noqa: PLC0415

    return parse_tree(mem_tree(make_files(root, children)), None, final)


class Row(dict):
    """A row whose omitted (NULL) columns read as None."""

    def __missing__(self, key: str) -> None:
        return None


def rows_of(rows: list[dict[str, Any]]) -> list[Row]:
    return [Row(r) for r in rows]
