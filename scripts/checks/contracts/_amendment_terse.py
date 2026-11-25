"""Pointer tokens, terse derivation and compaction order for amendment_log rows (Decision 275 cl.4, rec-5452).

Pure helpers that never raise on malformed input (Decision 55). "Lossless" means pointer tokens only: PLAN-<slug>,
rec-N, Decision N (Decisions lists, "cl.M" suffixes and dec-N normalise to it), tier ids (T4.32, T-1.24, T2.25a) and
CD.N. Audit finding ids (CFR-05, NS ids), PR numbers, SHAs, run ids and contract_version notes may drop, as Decision
275 cl.4 allows: git blame on the row recovers them. Imported by the compaction tool now and by the retention check
under rec-5453.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.checks.contracts import _amendment_retention as _ret

_PLAN = re.compile(r"(?<![A-Za-z0-9-])PLAN-([a-z0-9][a-z0-9-]*)")
_REC = re.compile(r"(?<![A-Za-z0-9-])rec-(\d+)(?!\w)")
_DEC_LIST = re.compile(r"(?<![A-Za-z0-9])Decisions?\s+(\d+(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*|\s*/\s*)\d+)*)")
_DEC_SHORT = re.compile(r"(?<![A-Za-z0-9-])dec-(\d+)(?!\w)")
_TIER = re.compile(r"(?<![A-Za-z0-9.-])T-?\d+\.\d+[a-z]?(?!\w)")
_CD = re.compile(r"(?<![A-Za-z0-9.-])CD\.(\d+)(?!\w)")
_NUMBER = re.compile(r"\d+")
_ANCHORS = ("PLAN-", "rec-", "Decision ")
_DEFAULT_KEYS = ("date", "semantic_break", "change_class", "summary")
REASONS = ("malformed", "extra_keys", "no_pointer", "overflow", "not_full")


@dataclass(frozen=True)
class Refusal:
    reason: str


def _ordered_tokens(text: object) -> list[str]:
    if not isinstance(text, str):
        return []
    found: list[tuple[int, str]] = []
    for match in _PLAN.finditer(text):
        found.append((match.start(), "PLAN-" + match.group(1).rstrip("-")))
    for match in _REC.finditer(text):
        found.append((match.start(), f"rec-{int(match.group(1))}"))
    for match in _DEC_LIST.finditer(text):
        for number in _NUMBER.finditer(match.group(1)):
            found.append((match.start(1) + number.start(), f"Decision {int(number.group())}"))
    for match in _DEC_SHORT.finditer(text):
        found.append((match.start(), f"Decision {int(match.group(1))}"))
    for match in _TIER.finditer(text):
        found.append((match.start(), match.group()))
    for match in _CD.finditer(text):
        found.append((match.start(), f"CD.{int(match.group(1))}"))
    seen: set[str] = set()
    ordered: list[str] = []
    for _pos, token in sorted(found):
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered


def pointer_tokens(text: object) -> set[str]:
    """The normalised pointer tokens of a summary; an empty set for anything that is not a string."""
    return set(_ordered_tokens(text))


def _cfg(cfg: object) -> dict[str, Any]:
    return cfg if isinstance(cfg, dict) else {}


def _terse_cfg(cfg: dict[str, Any]) -> tuple[tuple[str, ...], int]:
    terse = cfg.get("terse_row")
    terse = terse if isinstance(terse, dict) else {}
    keys = terse.get("keys")
    limit = terse.get("summary_max_chars", 160)
    keys = tuple(keys) if isinstance(keys, list) and keys else _DEFAULT_KEYS
    return keys, limit if isinstance(limit, int) and not isinstance(limit, bool) else 160


def _label(summary: str) -> str:
    depth = 0
    for at, char in enumerate(summary):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ":" and depth == 0 and summary[at + 1 : at + 2] == " ":
            label = summary[:at].strip()
            return label if _ordered_tokens(label) else ""
    return ""


def derive_terse(row: object, cfg: object) -> str | Refusal:
    """The terse summary of a full row, or the Refusal naming why the row cannot be compacted."""
    config = _cfg(cfg)
    keys, limit = _terse_cfg(config)
    if not isinstance(row, dict) or any(key not in row for key in keys) or not isinstance(row.get("summary"), str):
        return Refusal("malformed")
    if row.get("semantic_break") is True or _ret.is_terse(row, config):
        return Refusal("not_full")
    if set(row) - set(keys):
        return Refusal("extra_keys")
    summary = " ".join(row["summary"].split())
    tokens = _ordered_tokens(summary)
    if not any(token.startswith(_ANCHORS) for token in tokens):
        return Refusal("no_pointer")
    label = _label(summary)
    if label:
        have = pointer_tokens(label)
        missing = [token for token in tokens if token not in have]
        terse = label + ("; " + ", ".join(missing) if missing else "")
    else:
        terse = ", ".join(tokens)
    return terse if len(terse) <= limit else Refusal("overflow")


def compactable(row: object, cfg: object) -> bool:
    return isinstance(derive_terse(row, cfg), str)


def lossless(old_row: object, new_row: object, cfg: object) -> str | None:
    """None when new_row is a terse rewrite of old_row that keeps its pointer tokens, else the reason it is not."""
    config = _cfg(cfg)
    if not _ret.is_terse(new_row, config):
        return "the rewritten row is not terse"
    if not isinstance(old_row, dict) or not isinstance(new_row, dict):
        return "a row is not a mapping"
    for key in ("date", "semantic_break", "change_class"):
        if old_row.get(key) != new_row.get(key):
            return f"{key} changed"
    dropped = sorted(pointer_tokens(old_row.get("summary")) - pointer_tokens(new_row.get("summary")))
    return f"pointer tokens dropped: {', '.join(dropped)}" if dropped else None


def exempt_file(path: object, cfg: object) -> bool:
    """The rule _amendment_retention._exempt_file applies: basename equality with an exempt_files entry."""
    exempt = _cfg(cfg).get("exempt_files")
    return any(Path(str(path)).name == Path(str(f)).name for f in exempt) if isinstance(exempt, list) else False


def rows_past_keep(log: object, cfg: object) -> list[int]:
    """Indexes of full rows past the newest keep_full full rows, oldest first.

    Full rows are ranked by date (string form); among equal dates the row later in the file counts as older. Logs are
    not date-ordered in either direction, so position alone never defines "newest".
    """
    if not isinstance(log, list):
        return []
    config = _cfg(cfg)
    keep = config.get("keep_full")
    keep = keep if isinstance(keep, int) and not isinstance(keep, bool) and keep >= 0 else 5
    full_ids = {id(row) for row in _ret.full_rows(log, config)}
    full = [i for i, row in enumerate(log) if id(row) in full_ids]

    def date_of(i: int) -> str:
        row = log[i]
        return str(row.get("date", "")) if isinstance(row, dict) else ""

    newest_first = sorted(full, key=lambda i: (date_of(i), -i), reverse=True)
    return list(reversed(newest_first[keep:]))


def compaction_order(log: object, cfg: object) -> list[int]:
    """Indexes of compactable full rows past keep_full, oldest first."""
    if not isinstance(log, list):
        return []
    return [i for i in rows_past_keep(log, cfg) if compactable(log[i], cfg)]
