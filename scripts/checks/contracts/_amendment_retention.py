"""Top-level amendment_log retention and new-row identity (Decision 274 cl.4, rec-4119).

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
    terse = cfg.get("terse_row") if isinstance(cfg.get("terse_row"), dict) else {}
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


def retention_violation(name: str, base_data: object, head_data: object, cfg: dict[str, Any] | None = None) -> str | None:
    cfg = cfg if cfg is not None else load_retention()
    if cfg is None or _exempt_file(name, cfg):
        return None
    base_log, head_log = _log(base_data), _log(head_data)
    if rows_dropped(base_log, head_log):
        return (
            f"top-level amendment_log lost {len(base_log) - len(head_log)} row(s); Decision 274 compacts rows to terse "
            "rows, never drops them"
        )
    if genuinely_new(base_log, head_log) == 0:
        return None
    keep = max(cfg["keep_full"], len(full_rows(base_log, cfg)))
    held = len(full_rows(head_log, cfg))
    if held > keep:
        return (
            f"adding a top-level amendment_log row leaves {held} full rows, above max({cfg['keep_full']}, "
            f"{len(full_rows(base_log, cfg))} on the base); compact an older row to a terse row in the same change "
            "(contract-population.yaml amendment_log_retention)"
        )
    return None


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


def pass2_messages(name: str, base_data: object, head_data: object, both_class_d: bool) -> list[str]:
    messages: list[str] = []
    retention = retention_violation(name, base_data, head_data)
    if retention:
        messages.append(f"Contract drift (retention): {name}: {retention}")
    rewrite = rewrite_violation(base_data, head_data) if both_class_d else None
    if rewrite:
        messages.append(f"Contract drift (amendment): {name}: {rewrite}")
    return messages
