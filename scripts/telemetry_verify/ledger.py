"""Claude Code ledger reconciliation over one transcript tree (pure; counts and names only, never content).

Claude Code writes a cost-state record (modelUsage per requestedModel, cumulative) into the root stream at each resume. This
module sums the four token classes of everything BEFORE such a record, per model, and classifies each model of the record into
the finding vocabulary below. It reads the tree through the TranscriptTree protocol with its own json walk, never through
src/turn_capture/streams.py, so a producer parse defect cannot hide in both sides of the comparison.

The prefix of a cut is the root records before the cost-state line plus every sub-agent stream whose spawn record precedes the
cut (a spawn record is a toolUseResult carrying the agent's id, in the root before the cut or in an included stream). Responses
are the last record per message.id; synthetic (model "<synthetic>") and API-error records are excluded. A model's key is the
record's requestedModel, else message.model: the key of the ledger's modelUsage map.

The cumulative reading of a later cost-state record is the declared expectation, measured only on single-record sessions; each
finding carries the record's index so a mismatch on a later record is visible as such. A post-cut record never counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from src.turn_capture.transcript import ROOT, TranscriptTree, read_lines

CLASSES = ("input", "output", "cache_read", "cache_creation")
USAGE_KEYS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_input_tokens",
    "cache_creation": "cache_creation_input_tokens",
}
_LEDGER_KEYS = {
    "input": "inputTokens",
    "output": "outputTokens",
    "cache_read": "cacheReadInputTokens",
    "cache_creation": "cacheCreationInputTokens",
}
AUXILIARY_MODEL_PREFIXES = ("claude-haiku-",)
_COMPACTION_HOOKS = ("SessionStart:compact", "PreCompact", "PostCompact")

# The declared classes and the owner or reason that makes each acceptable; anything outside it is an alarm.
DECLARED = {
    "ledger_exact": "the ledger matches the transcript prefix on all four classes",
    "ledger_output_residual_nonfinal": "rec-4354: streaming-start output snapshots leave output short by the final call",
    "ledger_auxiliary_only": "an auxiliary model with calls only the ledger sees (the Claude Code side calls)",
    "ledger_bounded_auxiliary": "an auxiliary-prefix model that also has transcript records, compared as ledger >= transcript",
    "ledger_bounded_compaction": "a compaction before the cut: its call has no assistant record [inferred], so a bound",
    "ledger_bounded_background": "a background sub-agent with no completion notice before the cut may straddle it, so a bound",
    "ledger_unavailable": "a cost-state record with no modelUsage map, counted and never compared",
}
ALARMS = (
    "ledger_input_class_mismatch",
    "ledger_output_residual_unexplained",
    "ledger_output_excess",
    "ledger_model_unexplained",
)


@dataclass(frozen=True)
class Finding:
    model: str | None
    cls: str
    cut_index: int
    cut_line: int
    shortfall: int = 0
    nonfinal_calls: int = 0
    ledger: dict[str, int] = field(default_factory=dict)
    transcript: dict[str, int] = field(default_factory=dict)

    @property
    def alarm(self) -> bool:
        return self.cls in ALARMS


@dataclass
class _Prefix:
    totals: dict[str, dict[str, int]] = field(default_factory=dict)
    nonfinal: dict[str, int] = field(default_factory=dict)
    background_models: set[str] = field(default_factory=set)
    compaction: bool = False


def model_key(record: dict[str, Any]) -> str | None:
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    requested = record.get("requestedModel")
    key = requested if isinstance(requested, str) and requested else message.get("model")
    return key if isinstance(key, str) and key != "<synthetic>" else None


def _keyed_responses(records: list[dict[str, Any]]) -> list[tuple[int, str, dict[str, Any]]]:
    """(index, message id, record) of the last assistant record per message.id, synthetic and API-error records excluded."""
    last: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, record in enumerate(records):
        message = record.get("message")
        if record.get("type") != "assistant" or not isinstance(message, dict) or record.get("isApiErrorMessage"):
            continue
        if model_key(record) is None or not isinstance(message.get("usage"), dict):
            continue
        last[str(message.get("id") or f"line-{index}")] = (index, record)
    return [(index, key, record) for key, (index, record) in last.items()]


def last_responses(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The last assistant record per message.id, synthetic and API-error records excluded."""
    return [record for _, _, record in _keyed_responses(records)]


def _load(tree: TranscriptTree, key: str) -> tuple[list[dict[str, Any]], list[int]]:
    """(the parsed records of a stream, the file line number of each): malformed lines are skipped in both."""
    raw = tree.read(key)
    items = [] if raw is None else [line for line in read_lines(raw) if line.data is not None]
    return [item.data for item in items if item.data is not None], [item.line for item in items]


def read_stream(tree: TranscriptTree, key: str) -> list[dict[str, Any]]:
    return _load(tree, key)[0]


def find_spawns(records: list[dict[str, Any]]) -> list[tuple[int, str, bool]]:
    """(line, agentId, is_async) of every spawn record in a stream."""
    out = []
    for index, record in enumerate(records):
        result = record.get("toolUseResult")
        if isinstance(result, dict) and isinstance(result.get("agentId"), str):
            out.append((index, result["agentId"], bool(result.get("isAsync"))))
    return out


def _noticed(records: list[dict[str, Any]]) -> set[str]:
    done = set()
    for record in records:
        attachment = record.get("attachment")
        prompt = attachment.get("prompt") if isinstance(attachment, dict) else None
        if isinstance(prompt, str) and "<task-notification>" in prompt and "<task-id>" in prompt:
            done.add(prompt.split("<task-id>", 1)[1].split("</task-id>", 1)[0])
    return done


def _compaction(records: list[dict[str, Any]]) -> bool:
    for record in records:
        attachment = record.get("attachment")
        if isinstance(attachment, dict) and str(attachment.get("hookName", "")).startswith(_COMPACTION_HOOKS):
            return True
    return False


def _add(prefix: _Prefix, records: list[dict[str, Any]]) -> set[str]:
    models = set()
    for record in last_responses(records):
        key = model_key(record)
        assert key is not None
        usage = record["message"]["usage"]
        total = prefix.totals.setdefault(key, dict.fromkeys(CLASSES, 0))
        for name in CLASSES:
            value = usage.get(USAGE_KEYS[name])
            total[name] += value if isinstance(value, int) else 0
        message = record["message"]
        if not message.get("stop_reason") and not record.get("isAbortedMidStream"):
            prefix.nonfinal[key] = prefix.nonfinal.get(key, 0) + 1
        models.add(key)
    return models


def _included(
    tree: TranscriptTree, before: list[dict[str, Any]]
) -> Iterator[tuple[str, bool, list[dict[str, Any]], list[int]]]:
    """(agent id, is_async, records, file lines) of each sub-agent stream spawned before the cut, nested ones included."""
    pending = [(agent, is_async) for _, agent, is_async in find_spawns(before)]
    included: set[str] = set()
    while pending:
        agent, is_async = pending.pop()
        if agent in included:
            continue
        included.add(agent)
        records, lines = _load(tree, agent)
        yield agent, is_async, records, lines
        pending += [(nested, nested_async) for _, nested, nested_async in find_spawns(records)]


def _prefix(tree: TranscriptTree, root: list[dict[str, Any]], cut: int) -> _Prefix:
    before = root[:cut]
    prefix = _Prefix(compaction=_compaction(before))
    _add(prefix, before)
    noticed = _noticed(before)
    for agent, is_async, records, _ in _included(tree, before):
        models = _add(prefix, records)
        if is_async and agent not in noticed:
            prefix.background_models |= models
    return prefix


def prefix_calls(tree: TranscriptTree, cut_line: int) -> dict[str, list[tuple[str, int, str]]]:
    """Per model key, the (stream key, file line, message id) of each response the cost-state prefix at cut_line counts.

    cut_line is the position in the parsed root stream, as Finding.cut_line. The walk is the one _prefix sums, so a call
    listed here is exactly a call the ledger leg counted; the line is the last record of the response, as a FILE line number.
    """
    root, root_lines = _load(tree, ROOT)
    before = root[:cut_line]
    out: dict[str, list[tuple[str, int, str]]] = {}

    def collect(key: str, records: list[dict[str, Any]], lines: list[int]) -> None:
        for index, message_id, record in _keyed_responses(records):
            model = model_key(record)
            assert model is not None
            out.setdefault(model, []).append((key, lines[index], message_id))

    collect(ROOT, before, root_lines)
    for agent, _, records, lines in _included(tree, before):
        collect(agent, records, lines)
    return out


def file_line(tree: TranscriptTree, cut_line: int) -> int:
    """The file line number of the parsed root record at *cut_line* (Finding.cut_line is a position among parsed records)."""
    return _load(tree, ROOT)[1][cut_line]


def _ledger_totals(entry: Any) -> dict[str, int]:
    entry = entry if isinstance(entry, dict) else {}
    return {name: entry[_LEDGER_KEYS[name]] if isinstance(entry.get(_LEDGER_KEYS[name]), int) else 0 for name in CLASSES}


def _classify(model: str, ledger: dict[str, int] | None, seen: dict[str, int] | None, prefix: _Prefix) -> tuple[str, int]:
    """(finding class, output shortfall) of one model."""
    auxiliary = model.startswith(AUXILIARY_MODEL_PREFIXES)
    if ledger is None:
        return "ledger_model_unexplained", 0
    if seen is None:
        return ("ledger_auxiliary_only" if auxiliary else "ledger_model_unexplained"), 0
    bound = None
    if auxiliary:
        bound = "ledger_bounded_auxiliary"
    elif prefix.compaction:
        bound = "ledger_bounded_compaction"
    elif model in prefix.background_models:
        bound = "ledger_bounded_background"
    if bound is not None:
        if any(ledger[name] < seen[name] for name in CLASSES if name != "output"):
            return "ledger_input_class_mismatch", 0
        return (bound, 0) if ledger["output"] >= seen["output"] else ("ledger_output_excess", 0)
    if any(ledger[name] != seen[name] for name in CLASSES if name != "output"):
        return "ledger_input_class_mismatch", 0
    shortfall = ledger["output"] - seen["output"]
    if shortfall == 0:
        return "ledger_exact", 0
    if shortfall < 0:
        return "ledger_output_excess", 0
    nonfinal = prefix.nonfinal.get(model, 0)
    return ("ledger_output_residual_nonfinal" if nonfinal else "ledger_output_residual_unexplained"), shortfall


def reconcile_ledger(tree: TranscriptTree) -> list[Finding]:
    """One Finding per model of every cost-state record in the root stream (a record without modelUsage: one unavailable)."""
    root = read_stream(tree, ROOT)
    findings: list[Finding] = []
    cut_index = 0
    for line, record in enumerate(root):
        if record.get("type") != "cost-state":
            continue
        usage = record.get("modelUsage")
        if not isinstance(usage, dict):
            findings.append(Finding(None, "ledger_unavailable", cut_index, line))
        else:
            prefix = _prefix(tree, root, line)
            for model in sorted(set(usage) | set(prefix.totals)):
                ledger = _ledger_totals(usage[model]) if model in usage else None
                cls, shortfall = _classify(model, ledger, prefix.totals.get(model), prefix)
                findings.append(
                    Finding(
                        model,
                        cls,
                        cut_index,
                        line,
                        shortfall=shortfall,
                        nonfinal_calls=prefix.nonfinal.get(model, 0),
                        ledger=ledger or {},
                        transcript=prefix.totals.get(model, {}),
                    )
                )
        cut_index += 1
    return findings
