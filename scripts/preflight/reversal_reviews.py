"""Overdue reversal-review sensor (rec-5264): every due or fired stanza gets exactly one owning rec.

Reads decision_conditions.evaluate(); for each stanza that is fired, manual-review-due or MALFORMED, looks for an
open or in_progress rec titled "Review Decision N ..." or "Supersede Decision N ..." (two fixed title-prefix sweeps,
run only when something is due) and, when none exists, files one. Alarm-not-gate (Decision 55): it never closes or
edits a rec, a filed rec closes through its acceptance command, and a reader or portal failure raises (Decision 84
I-4, no outbox). A closed owner on a still-due stanza gets a new rec.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from scripts.decisions_md import _DECISIONS_MD_PATHS
from scripts.preflight.decision_conditions import DecisionConditionState, evaluate

SOURCE = "reversal_review"
_SWEEPS = ("Review Decision %", "Supersede Decision %")
_OWNER_RE = re.compile(r"^(?:Review|Supersede) Decision (\d+)\b")
_OPEN = ("open", "in_progress")
_DUE = ("fired", "manual-review-due", "MALFORMED")


def _owners(reader: Any) -> dict[int, list[str]]:
    owners: dict[int, list[str]] = {}
    for prefix in _SWEEPS:
        for row in reader.named("recs_by_title_prefix", title_prefix=prefix) or []:
            m = _OWNER_RE.match(row.get("title") or "")
            if m and row.get("status") in _OPEN:
                owners.setdefault(int(m.group(1)), []).append(str(row["id"]))
    return {k: sorted(set(v), key=lambda r: int(r.split("-")[1])) for k, v in owners.items()}


def _source_files(paths: list[Path]) -> dict[int, str]:
    found: dict[int, str] = {}
    for path in paths:
        for r in evaluate(paths=[path]):
            found.setdefault(r.decision_id, path.as_posix())
    return found


def _tick(text: str) -> str:
    return f"`{text}`"


def _title(r: DecisionConditionState) -> str:
    n = r.decision_id
    if r.state == "MALFORMED":
        return f"Review Decision {n} stanza unreadable: {(r.error or '').splitlines()[0][:120]}"
    if r.state == "fired":
        ids = ", ".join(_tick(i) for i in r.fired_condition_ids)
        return f"Review Decision {n} before {r.review_by}: reversal condition fired ({ids})"
    return f"Review Decision {n} before {r.review_by}: reversal review due"


def _context(r: DecisionConditionState) -> str:
    lines = [f"Decision {r.decision_id} reversal stanza state: {r.state}."]
    if r.error:
        lines.append(f"Stanza error: {r.error}")
        lines.append("Repair the stanza so it parses, or supersede the Decision.")
        return "\n".join(lines)
    lines.append(f"review_by {r.review_by}. on_trigger: {_tick(r.on_trigger or '')}")
    for c in r.conditions:
        lines.append(f"- {_tick(c.id)} kind {c.kind}, holds {c.holds}, fired {c.fired}")
    lines.append(
        "Review the Decision, then re-arm the stanza (move review_by later with a note) or supersede it. "
        "Pick the new date with: bin/venv-python -m scripts.preflight.review_dates --next <your new review date>"
    )
    return "\n".join(lines)


def acceptance_for(decision_id: int) -> str:
    return f"bin/venv-python -m scripts.preflight.decision_conditions --assert-not-surfaced {decision_id}"


def _fields(r: DecisionConditionState, file: str) -> dict[str, Any]:
    return {
        "title": _title(r),
        "file": file,
        "status": "open",
        "source": "reversal_review",
        "priority": "High",
        "effort": "S",
        "risk": "low",
        "automatable": False,
        "verification_tier": "V1",
        "context": _context(r),
        "acceptance": acceptance_for(r.decision_id),
    }


def run(
    *,
    dry_run: bool = False,
    profile: Optional[str] = None,
    results: Optional[list[DecisionConditionState]] = None,
    reader: Any = None,
    file_rec: Optional[Callable[..., Any]] = None,
    paths: Optional[list[Path]] = None,
    emit: Callable[[str], None] = print,
) -> dict[str, int]:
    all_paths = paths if paths is not None else list(_DECISIONS_MD_PATHS)
    evaluated = results if results is not None else evaluate(paths=all_paths)
    due = [r for r in evaluated if r.state in _DUE]
    summary = {"due": len(due), "filed": 0, "owned": 0, "malformed": sum(r.state == "MALFORMED" for r in due)}
    if due:
        if reader is None:
            from scripts.preflight import _common  # noqa: PLC0415

            reader = _common._make_reader(profile=profile)
        owners = _owners(reader)
        files = _source_files(all_paths)
        for r in sorted(due, key=lambda x: x.decision_id):
            head = f"[reversal_review] Decision {r.decision_id} {r.state}"
            err = f" (error: {r.error})" if r.state == "MALFORMED" else ""
            own = owners.get(r.decision_id, [])
            if own:
                warn = " (warning: more than one owner)" if len(own) > 1 else ""
                emit(f"{head} owned by {', '.join(own)}{warn}{err}")
                summary["owned"] += 1
            elif dry_run:
                emit(f"{head} would-file")
            else:
                if file_rec is None:
                    from scripts.ops_data_portal import file_rec as portal_file  # noqa: PLC0415

                    file_rec = portal_file
                rec_id = file_rec(_fields(r, files.get(r.decision_id, "docs/DECISIONS.md")), profile=profile)
                emit(f"{head} filed {rec_id}")
                summary["filed"] += 1
    emit(
        f"[reversal_review] summary due={summary['due']} filed={summary['filed']} "
        f"owned={summary['owned']} malformed={summary['malformed']}"
    )
    return summary


def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile", default=None)
    args = parser.parse_args(argv)
    run(dry_run=args.dry_run, profile=args.profile)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli())
