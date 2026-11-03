"""Pure builders for telemetry_transcripts rows.

Content is scrubbed BEFORE content_sha256 and content_bytes; an empty payload yields no row; image/binary
blocks are replaced by a {type, media_type, sha256, bytes} stub. A tool_result row holds what the model saw (the
in-transcript text). A persisted output (a Bash persistedOutputPath or an MCP-overflow message naming a saved file)
adds ONE tool_output row holding the full sidecar, or, above the cap, ONE omission row carrying the raw bytes'
identity and no payload. content_truncated is a defect detector no path sets. Attachments emit no transcript rows in
v3 (attachment-derived system transcripts are deferred: no consumer).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.turn_capture.cursor import CaptureCursor
from src.turn_capture.refs import full_output_ref, transcript_ref, turn_entity_ref
from src.turn_capture.scrub import scrub_text
from src.turn_capture.streams import (
    HARNESS_DENIAL_PREFIXES,
    HARNESS_INTERRUPT_SENTINELS,
    ParsedTree,
    Rec,
    Result,
    block_base,
    emitting_streams,
    is_api_error,
)
from src.turn_capture.transcript import TranscriptTree

TRANSCRIPT_COLUMNS = (
    "event_kind",
    "event_timestamp",
    "session_started_at",
    "source_ordinal",
    "external_ref",
    "entity_ref",
    "session_ref",
    "observation_ref",
    "producer",
    "producer_version",
    "parser_version",
    "purpose",
    "origin",
    "content",
    "content_uri",
    "content_omitted_reason",
    "content_sha256",
    "content_bytes",
    "content_truncated",
    "token_count",
    "model",
    "rec_id",
)


@dataclass(frozen=True)
class Ctx:
    producer: str
    producer_version: str
    parser_version: int
    started: datetime
    billing_shape: str
    full_output_cap_bytes: int


@dataclass(frozen=True)
class Digest:
    """The RAW identity of a persisted output: byte length, sha256 and line count, streamed, never decoded."""

    size: int
    sha256: str
    lines: int


@dataclass(frozen=True)
class Output:
    """A readable persisted output: its raw identity plus the storable scrubbed text (None when omitted as oversize)."""

    digest: Digest
    text: str | None


def line_count(lf_bytes: int, size: int, last_byte_is_lf: bool) -> int:
    return lf_bytes + (1 if size > 0 and not last_byte_is_lf else 0)


class Sidecars:
    """Sidecar reads under the cursor's pins: a present pin must still match, an absent pin is honoured.

    output() resolves each sidecar once per pass (the digest is cached), so however many builders read it, it is
    streamed or read once. A sidecar whose raw size exceeds the cap is only ever streamed in chunks.
    """

    def __init__(self, tree: TranscriptTree, cursor: CaptureCursor | None) -> None:
        self._tree = tree
        self._cursor = cursor
        self.used: dict[str, str | None] = {}
        self.reads = 0
        self._outputs: dict[str, Output | None] = {}

    def output(self, name: str, cap: int) -> Output | None:
        if name not in self._outputs:
            self._outputs[name] = self._load(name, cap)
        return self._outputs[name]

    def _pin(self, name: str, sha: str | None) -> None:
        if self._cursor is not None:
            self._cursor.check_sidecar(name, sha)
        self.used[name] = sha

    def _load(self, name: str, cap: int) -> Output | None:
        pins = self._cursor.sidecars if self._cursor is not None else {}
        if name in pins and pins[name] is None:
            return None
        size = self._tree.sidecar_size(name)
        if size is None:
            self._pin(name, None)
            return None
        if size > cap:
            hasher, total, lf, last = hashlib.sha256(), 0, 0, b""
            self.reads += 1
            for chunk in self._tree.sidecar_chunks(name):
                hasher.update(chunk)
                total += len(chunk)
                lf += chunk.count(b"\n")
                last = chunk[-1:]
            sha = hasher.hexdigest()
            self._pin(name, sha)
            return Output(Digest(total, sha, line_count(lf, total, last == b"\n")), None)
        self.reads += 1
        data = self._tree.sidecar(name)
        if data is None:
            self._pin(name, None)
            return None
        sha = hashlib.sha256(data).hexdigest()
        self._pin(name, sha)
        digest = Digest(len(data), sha, line_count(data.count(b"\n"), len(data), data.endswith(b"\n")))
        text = clean(data.decode("utf-8", "replace"))
        return Output(digest, None if len(text.encode("utf-8")) > cap else text)


@dataclass(frozen=True)
class Persisted:
    """A tool result whose full output the harness wrote to a sidecar; mcp is the overflow-message form."""

    name: str
    mcp: bool


_SAVED_RE = re.compile(r"Output has been saved to (\S+)")


def _basename(path: str) -> str:
    return re.split(r"[\\/]", path)[-1]


def persisted_output(res: Result) -> Persisted | None:
    """The sidecar a tool result names: a Bash persistedOutputPath, or an MCP-overflow string naming a saved file."""
    info = res.use_result
    if isinstance(info, dict):
        path = info.get("persistedOutputPath")
        name = _basename(path) if isinstance(path, str) else ""
        return Persisted(name, False) if name else None
    if isinstance(info, str) and (match := _SAVED_RE.search(info)) and (name := _basename(match.group(1))):
        return Persisted(name, True)
    return None


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _stub(block: Any) -> Any:
    if not isinstance(block, dict):
        return block
    source = block.get("source")
    if block.get("type") != "image" and not (isinstance(source, dict) and "data" in source):
        return block
    source = source if isinstance(source, dict) else {}
    data = source.get("data")
    raw = data if isinstance(data, str) else ""
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        decoded = raw.encode("utf-8", "replace")
    return {
        "type": block.get("type"),
        "media_type": source.get("media_type"),
        "sha256": hashlib.sha256(decoded).hexdigest(),
        "bytes": len(decoded),
    }


def text_of(content: Any) -> str:
    """Payload text of a prompt or tool_result content value."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list) and all(
        isinstance(b, dict) and isinstance(b.get("text"), str) and b.get("type") == "text" for b in content
    ):
        return "\n".join(b["text"] for b in content)
    if isinstance(content, list):
        return canonical_json([_stub(b) for b in content])
    return canonical_json(content)


def clean(text: str) -> str:
    return scrub_text(text).text.encode("utf-8", "replace").decode("utf-8")


def is_sentinel(text: str) -> bool:
    return any(text.startswith(p) for p in HARNESS_DENIAL_PREFIXES) or text in HARNESS_INTERRUPT_SENTINELS


def _row(
    ctx: Ctx,
    rec: Rec,
    block: int,
    session_ref: str,
    obs_ref: str,
    purpose: str,
    origin: str,
    text: str | None,
    model: Any,
    *,
    ref: str | None = None,
    cleaned: bool = False,
    omitted: Digest | None = None,
) -> dict[str, Any]:
    """One transcript row. *text* None with *omitted* is a typed omission: raw identity, no payload."""
    ref = ref or transcript_ref(rec.uuid, block)
    row: dict[str, Any] = dict.fromkeys(TRANSCRIPT_COLUMNS)
    row.update(
        event_kind="point",
        event_timestamp=rec.ts,
        session_started_at=ctx.started,
        source_ordinal=rec.line,
        external_ref=ref,
        entity_ref=ref,
        session_ref=session_ref,
        observation_ref=obs_ref,
        producer=ctx.producer,
        producer_version=ctx.producer_version,
        parser_version=ctx.parser_version,
        purpose=purpose,
        origin=origin,
        content_truncated=False,
        model=model if isinstance(model, str) else None,
    )
    if omitted is not None:
        row.update(content_omitted_reason="oversize", content_sha256=omitted.sha256, content_bytes=omitted.size)
    else:
        assert text is not None
        payload = text if cleaned else clean(text)
        row.update(
            content=payload,
            content_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            content_bytes=len(payload.encode("utf-8")),
        )
    return {k: v for k, v in row.items() if v is not None}


def _prompt_like(ctx: Ctx, rec: Rec, session_ref: str, turn_ref: str, purpose: str, origin: str) -> list[dict[str, Any]]:
    content = rec.data["message"]["content"]
    blocks = [content] if isinstance(content, str) else list(content)
    rows = []
    for i, block in enumerate(blocks):
        if isinstance(block, str):
            text = block
        elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            text = block["text"]
        else:
            stub = _stub(block)
            text = canonical_json(stub) if stub is not block else ""
        if text != "":
            rows.append(_row(ctx, rec, i, session_ref, turn_ref, purpose, origin, text, None))
    return rows


def _assistant(ctx: Ctx, rec: Rec, session_ref: str, diag: Counter[str]) -> list[dict[str, Any]]:
    message = rec.data["message"]
    rows = []
    for i, block in enumerate(message["content"]):
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "thinking":
            purpose, text = "thinking", block.get("thinking")
        elif kind == "text":
            purpose, text = "response", block.get("text")
        elif kind == "tool_use":
            purpose, text = "tool_input", canonical_json(block.get("input"))
        else:
            diag[f"unknown_block_type:{kind}"] += 1
            continue
        if isinstance(text, str) and text != "":
            rows.append(
                _row(
                    ctx,
                    rec,
                    block_base(rec.data) + i,
                    session_ref,
                    message["id"],
                    purpose,
                    "agent",
                    text,
                    message.get("model"),
                )
            )
    return rows


def _full_output_row(ctx: Ctx, res: Result, session_ref: str, out: Output) -> dict[str, Any]:
    ref = full_output_ref(res.rec.uuid, res.block)
    omitted = out.digest if out.text is None else None
    return _row(
        ctx, res.rec, res.block, session_ref, res.tool_use_id, "tool_output", "tool", out.text, None,
        ref=ref, cleaned=True, omitted=omitted,
    )  # fmt: skip


def _tool_result(ctx: Ctx, res: Result, session_ref: str, sidecars: Sidecars, diag: Counter[str]) -> list[dict[str, Any]]:
    visible = text_of(res.body.get("content"))
    info = res.use_result
    persisted = persisted_output(res)
    denial = isinstance(res.rec.data.get("toolDenialKind"), str)
    if isinstance(info, str) and persisted is None and not denial:
        diag["unmatched_string_tool_use_result"] += 1
    rows: list[dict[str, Any]] = []
    if visible != "":
        harness = denial or is_sentinel(visible) or (persisted is not None and persisted.mcp)
        origin = "harness" if harness else "tool"
        rows.append(_row(ctx, res.rec, res.block, session_ref, res.tool_use_id, "tool_result", origin, visible, None))
    out = sidecars.output(persisted.name, ctx.full_output_cap_bytes) if persisted is not None else None
    if out is not None:
        size = info.get("persistedOutputSize") if isinstance(info, dict) else None
        if type(size) is int and size != out.digest.size:
            diag["sidecar_size_mismatch"] += 1
        if out.text != "":
            rows.append(_full_output_row(ctx, res, session_ref, out))
    return rows


def build_transcripts(parsed: ParsedTree, ctx: Ctx, sidecars: Sidecars, diag: Counter[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stream in emitting_streams(parsed):
        sref = stream.session_ref
        for turn in (t for t in stream.turns if t.closed):
            tref = turn_entity_ref(sref, turn.prompt_id)
            for rec in turn.recs:
                if rec.kind == "prompt":
                    harness = rec.data.get("isMeta") is True or is_sentinel(text_of(rec.data["message"]["content"]))
                    rows += _prompt_like(
                        ctx, rec, sref, tref, "system" if harness else "prompt", "harness" if harness else "human"
                    )
                elif rec.kind == "companion":
                    rows += _prompt_like(ctx, rec, sref, tref, "system", "harness")
                elif rec.kind == "assistant" and is_api_error(rec.data):
                    rows += _prompt_like(ctx, rec, sref, tref, "system", "harness")
                elif rec.kind == "assistant":
                    rows += _assistant(ctx, rec, sref, diag)
            for use in turn.tool_uses:
                if use.result is not None:
                    rows += _tool_result(ctx, use.result, sref, sidecars, diag)
            for res in turn.orphan_results:
                rows += _tool_result(ctx, res, sref, sidecars, diag)
    return rows
