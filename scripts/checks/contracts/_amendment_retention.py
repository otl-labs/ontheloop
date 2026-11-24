"""Top-level amendment_log retention and new-row identity (Decision 275 cl.4, rec-4119).

Pure helpers behind validate_contract_drift's Pass 2: the retention rule held at
docs/contracts/contract-population.yaml `amendment_log_retention`, the dropped-row guard, and the rewrite check
that closes the reworded-old-row loophole scripts.checks.contracts._population's set membership leaves. Each
returns a message, a list or None and never raises on malformed input (Decision 55). _population.py is not
edited: it sits at its SLOC budget and its import graph reaches gated --pre entries.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common

_POPULATION_REL = "docs/contracts/contract-population.yaml"
_KEY = "amendment_log_retention"
_DEFAULT_TERSE_KEYS = ("date", "semantic_break", "change_class", "summary")


def load_retention(root: Path | None = None) -> dict[str, Any] | None:
    """The retention mapping, or None when the file or key is absent or malformed."""
    path = (root or _common.ROOT) / _POPULATION_REL
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    cfg = data.get(_KEY) if isinstance(data, dict) else None
    if not isinstance(cfg, dict) or not isinstance(cfg.get("keep_full"), int) or isinstance(cfg.get("keep_full"), bool):
        return None
    return cfg


def _log(data: object) -> list[Any]:
    log = data.get("amendment_log") if isinstance(data, dict) else None
    return list(log) if isinstance(log, list) else []


def is_terse(row: object, cfg: dict[str, Any]) -> bool:
    terse: dict[str, Any] = cfg.get("terse_row") or {}
    terse = terse if isinstance(terse, dict) else {}
    keys = tuple(terse.get("keys") or _DEFAULT_TERSE_KEYS)
    limit = terse.get("summary_max_chars", 160)
    if not isinstance(row, dict) or set(row) != set(keys):
        return False
    summary = row.get("summary")
    return isinstance(summary, str) and len(summary) <= limit


def _exempt(row: object) -> bool:
    return isinstance(row, dict) and row.get("semantic_break") is True


def full_rows(log: list[Any], cfg: dict[str, Any]) -> list[Any]:
    """Rows counted against keep_full: neither terse nor exempt (semantic_break true)."""
    return [row for row in log if not is_terse(row, cfg) and not _exempt(row)]


def _identity(row: object) -> str:
    body = {k: v for k, v in row.items() if k != "date"} if isinstance(row, dict) else row
    return json.dumps(body, sort_keys=True, default=str)


def genuinely_new(base_log: list[Any], head_log: list[Any]) -> int:
    """Rows only the head carries, minus rows only the base carries (multisets, dates ignored), floor 0: a reworded
    or re-dated old row is a rewrite, never a new row."""
    base = Counter(_identity(row) for row in base_log)
    head = Counter(_identity(row) for row in head_log)
    return max(0, sum((head - base).values()) - sum((base - head).values()))


def rows_dropped(base_log: list[Any], head_log: list[Any]) -> bool:
    return len(head_log) < len(base_log)


def _exempt_file(name: str, cfg: dict[str, Any]) -> bool:
    return any(Path(str(name)).name == Path(str(f)).name for f in cfg.get("exempt_files") or [])


def _canon(row: object) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def row_pointers(row: object) -> set[str]:
    """Pointer tokens over every string value of a row, migration_story included."""
    from scripts.checks.contracts import _amendment_terse

    found: set[str] = set()
    for text in _strings(row):
        found |= _amendment_terse.pointer_tokens(text)
    return found


def _anchored(tokens: set[str]) -> bool:
    return any(t.startswith(("PLAN-", "rec-", "Decision ")) for t in tokens)


def _summary_tokens(row: object) -> set[str]:
    from scripts.checks.contracts import _amendment_terse

    return _amendment_terse.pointer_tokens(row.get("summary")) if isinstance(row, dict) else set()


def pair_rows(
    base_log: list[Any], head_log: list[Any], cfg: dict[str, Any]
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[int], list[int]]:
    """(matches, pairs, new_rows, unpaired_base): index pairs, head indexes and base indexes.

    Equal canonical dumps (dates included) are matches, as a multiset. Each remaining base row then pairs with the first
    remaining head row that is a lossless compaction of it; a base row still left pairs with the first remaining head
    row of equal date, semantic_break and change_class, so its failure names the right row.
    """
    from scripts.checks.contracts import _amendment_terse

    free = list(range(len(head_log)))
    matches: list[tuple[int, int]] = []
    rest: list[int] = []
    for b, row in enumerate(base_log):
        hit = next((h for h in free if _canon(head_log[h]) == _canon(row)), None)
        if hit is None:
            rest.append(b)
        else:
            free.remove(hit)
            matches.append((b, hit))
    pairs: list[tuple[int, int]] = []
    left: list[int] = []
    for b in rest:
        needed = row_pointers(base_log[b])
        hit = next(
            (
                h
                for h in free
                if _amendment_terse.lossless(base_log[b], head_log[h], cfg) is None and needed <= _summary_tokens(head_log[h])
            ),
            None,
        )
        if hit is None:
            left.append(b)
        else:
            free.remove(hit)
            pairs.append((b, hit))
    unpaired: list[int] = []
    for b in left:
        row = base_log[b]

        def same(h: int, row: Any = row) -> bool:
            other = head_log[h]
            keys = ("date", "semantic_break", "change_class")
            return isinstance(row, dict) and isinstance(other, dict) and all(row.get(k) == other.get(k) for k in keys)

        hit = next((h for h in free if same(h)), None)
        if hit is None:
            unpaired.append(b)
        else:
            free.remove(hit)
            pairs.append((b, hit))
    return matches, pairs, free, unpaired


def _name(row: object) -> str:
    if isinstance(row, dict):
        return f"row dated {row.get('date')} ({row.get('change_class')})"
    return "a row that is not a mapping"


def change_violation(base_log: list[Any], head_log: list[Any], cfg: dict[str, Any]) -> list[str]:
    """Old rows change only by a lossless terse compaction that keeps every pointer (Decision 282 cl.3)."""
    from scripts.checks.contracts import _amendment_terse

    _matches, pairs, _new, unpaired = pair_rows(base_log, head_log, cfg)
    out: list[str] = []
    for b in unpaired:
        out.append(f"{_name(base_log[b])}: row dropped, re-dated or reclassified")
    for b, h in pairs:
        old, new = base_log[b], head_log[h]
        reason = None
        if not isinstance(old, dict) or is_terse(old, cfg) or _exempt(old):
            reason = "terse or semantic-break row changed"
        else:
            reason = _amendment_terse.lossless(old, new, cfg)
            lost = sorted(row_pointers(old) - _summary_tokens(new))
            if reason is None and lost:
                reason = f"pointer lost: {', '.join(lost)}"
            if reason is None and not _anchored(row_pointers(old)) and not _anchored(_summary_tokens(new)):
                reason = (
                    "no pointer named: name the row's plan, rec or Decision in the terse summary (find it from git "
                    "blame of the row)"
                )
        if reason:
            out.append(f"{_name(old)}: changed other than by a lossless compaction ({reason})")
    return out


def _selectable(text: str | None, log: list[Any], cfg: dict[str, Any]) -> set[int]:
    from scripts.checks.contracts import _amendment_terse, amendment_compact

    every = {**cfg, "keep_full": 0}
    if text is None:
        return set(_amendment_terse.compaction_order(log, every))
    return set(amendment_compact.selected_rows(text, every))


def _selected(text: str | None, log: list[Any], cfg: dict[str, Any]) -> list[int]:
    from scripts.checks.contracts import _amendment_terse, amendment_compact

    if text is None:
        return _amendment_terse.compaction_order(log, cfg)
    return amendment_compact.selected_rows(text, cfg)


def new_row_violation(head_log: list[Any], new_rows: list[int], cfg: dict[str, Any], head_text: str | None) -> list[str]:
    """A new row not marking a semantic break is an anchored terse row or one the tool can compact later."""
    selectable: set[int] | None = None
    out: list[str] = []
    for h in new_rows:
        row = head_log[h]
        if not isinstance(row, dict):
            out.append(f"new row {h} is not a mapping")
            continue
        if _exempt(row):
            continue
        if is_terse(row, cfg) and _anchored(_summary_tokens(row)):
            continue
        if selectable is None:
            selectable = _selectable(head_text, head_log, cfg)
        if h not in selectable:
            out.append(
                f"new {_name(row)} is neither a terse row naming its plan, rec or Decision nor a row the compaction tool "
                "can compact (four keys, a pointer and a label that fits): keep it terse and put detail in the plan"
            )
    return out


def selectability_violation(
    base_log: list[Any], head_log: list[Any], base_text: str | None, head_text: str | None, cfg: dict[str, Any]
) -> list[str]:
    """A row the tool could compact on the base stays compactable on the head (no comment or shared line added)."""
    if base_text is None or head_text is None:
        return []
    matches, _pairs, _new, _unpaired = pair_rows(base_log, head_log, cfg)
    base_ok, head_ok = _selectable(base_text, base_log, cfg), _selectable(head_text, head_log, cfg)
    return [
        f"{_name(base_log[b])}: no longer compactable by the tool (a comment or shared line inside its span)"
        for b, h in matches
        if b in base_ok and h not in head_ok
    ]


def retention_violation(
    name: str,
    base_data: object,
    head_data: object,
    cfg: dict[str, Any] | None = None,
    head_text: str | None = None,
) -> str | None:
    cfg = cfg if cfg is not None else load_retention()
    if cfg is None or _exempt_file(name, cfg):
        return None
    base_log, head_log = _log(base_data), _log(head_data)
    if rows_dropped(base_log, head_log):
        return (
            f"top-level amendment_log lost {len(base_log) - len(head_log)} row(s); Decision 275 compacts rows to terse "
            "rows, never drops them"
        )
    if genuinely_new(base_log, head_log) == 0:
        return None
    f_base, f_head = len(full_rows(base_log, cfg)), len(full_rows(head_log, cfg))
    hold = max(cfg["keep_full"], f_base)
    target = max(cfg["keep_full"], f_base - 1)
    selected = _selected(head_text, head_log, cfg)
    if f_head <= hold and (f_head <= target or not selected):
        return None
    count = min(len(selected), f_head - target)
    head = (
        f"adding a top-level amendment_log row leaves {f_head} full rows, above "
        f"max({cfg['keep_full']}, {f_base} on the base - 1) (Decision 282 cl.1)"
    )
    if not selected:
        return (
            f"{head}; the compaction tool selects no row here, so compact an older row by hand to a lossless terse row "
            "in the same change (contract-population.yaml amendment_log_retention)"
        )
    command = (
        f"bin/venv-python -m scripts.checks.contracts.amendment_compact --write --rows {count} docs/contracts/"
        f"{Path(str(name)).name}"
    )
    message = f"{head}; compact {count} older row(s) in the same change with: {command}"
    owed = f_head - len(selected) - hold
    if owed > 0:
        from scripts.checks.contracts import _amendment_terse

        chosen = set(selected)
        dates = [
            str(head_log[i].get("date"))
            for i in _amendment_terse.rows_past_keep(head_log, cfg)
            if i not in chosen and isinstance(head_log[i], dict)
        ]
        message += (
            f"; {owed} more must be compacted by hand to lossless terse rows (the tool refuses them; candidates dated "
            f"{', '.join(dates)})"
        )
    return message


def rewrite_violation(base_data: object, head_data: object) -> str | None:
    """Non-log content changed and a head row is absent from the base, yet no row is genuinely new."""
    if not isinstance(base_data, dict) or not isinstance(head_data, dict):
        return None

    def _canonical(data: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in data.items() if k != "amendment_log"}

    base_log, head_log = _log(base_data), _log(head_data)
    if _canonical(base_data) == _canonical(head_data):
        return None
    if not [row for row in head_log if row not in base_log]:
        return None
    if genuinely_new(base_log, head_log) > 0:
        return None
    return "parsed content changed but the only amendment_log rows offered are rewrites of existing rows, not a new row"


def pass2_messages(
    name: str,
    base_data: object,
    head_data: object,
    both_class_d: bool,
    base_text: str | None = None,
    head_text: str | None = None,
) -> list[str]:
    messages: list[str] = []
    cfg = load_retention()
    if cfg is not None and not _exempt_file(name, cfg):
        base_log, head_log = _log(base_data), _log(head_data)
        retention = retention_violation(name, base_data, head_data, cfg, head_text)
        if retention:
            messages.append(f"Contract drift (retention): {name}: {retention}")
        _matches, _pairs, new_rows, _unpaired = pair_rows(base_log, head_log, cfg)
        legs = (
            ("change", change_violation(base_log, head_log, cfg)),
            ("new-row", new_row_violation(head_log, new_rows, cfg, head_text)),
            ("selectability", selectability_violation(base_log, head_log, base_text, head_text, cfg)),
        )
        for label, found in legs:
            messages.extend(f"Contract drift ({label}): {name}: {one}" for one in found)
    rewrite = rewrite_violation(base_data, head_data) if both_class_d else None
    if rewrite:
        messages.append(f"Contract drift (amendment): {name}: {rewrite}")
    return messages
