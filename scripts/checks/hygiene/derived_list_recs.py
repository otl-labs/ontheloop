"""The derived-list scan's filer (rec-5516), in decision-census's shape: turns a scan --result into at most --max recs.

Every open derived-list rec (source code-smell, first context line "derived_list=<path>") holds one of the --max slots
whatever its file's current rank, so a weekly run never stacks more; the remaining slots go to the highest-scored
unmarked derived_candidate files with no open rec. A rec closed while its file is still an unmarked candidate is
re-filed, so declining one means adding a hand-kept marker to the file. The result file is untrusted input. Every
write is attempted; a failed write exits 1 (Decision 84 I-4, no outbox). Exit 2 (nothing written): malformed result
or failed lookup (Decision 215).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from scripts import rec_episode

SOURCE = "code-smell"
TOKEN = "derived_list="
DEFAULT_MAX = 3
CLASSES = ("derived_candidate", "baseline", "none")
KIND_WORDS = {
    "path": "tracked paths",
    "glob": "path globs",
    "check": "check names",
    "module": "modules",
    "workflow": "workflows",
    "stem": "file stems",
    "trigger_paths": "trigger paths",
    "none": "unresolved entries",
    "unparsed": "unparsed entries",
}
_RESULT_KEYS = {"schema", "note", "files"}
_FILE_KEYS = {
    "path",
    "class",
    "kind",
    "entries",
    "resolved_share",
    "coupled_appends",
    "size_pressure",
    "score",
    "marker",
    "step",
}
_PATH_RE = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_TEXT = 200
_STEPS = (
    "derive the list at read time from the source it names and delete the hand copy",
    "move the judgment field onto the source (the entity the entry names) and derive the rest",
    "generate the list with a drift check",
    "one file per entry, so parallel plans never edit the same lines",
    "keep it, with a test that verifies both directions against the source",
    'accept it as hand-kept with a "# hand-kept: rec-NNNN <reason>" comment in its first 15 lines',
)

PortalCaller = Callable[[str, dict[str, Any]], Any]


class ResultError(Exception):
    """The scan result file is malformed (exit 2)."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _check_file(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != _FILE_KEYS:
        raise ResultError("file entry keys")
    path = entry["path"]
    if not isinstance(path, str) or not _PATH_RE.fullmatch(path) or ".." in path.split("/"):
        raise ResultError("path")
    if entry["class"] not in CLASSES or entry["kind"] not in KIND_WORDS:
        raise ResultError("class or kind")
    if not _is_int(entry["entries"]) or entry["entries"] < 0 or not _is_int(entry["coupled_appends"]):
        raise ResultError("counts")
    if not all(_is_num(entry[k]) for k in ("resolved_share", "size_pressure", "score", "coupled_appends")):
        raise ResultError("scores")
    if entry["marker"] is not None and not isinstance(entry["marker"], str):
        raise ResultError("marker")
    if not _is_int(entry["step"]) or not 0 <= entry["step"] <= len(_STEPS):
        raise ResultError("step")
    marker = None if entry["marker"] is None else _CTRL_RE.sub("", entry["marker"])[:_MAX_TEXT]
    return {**entry, "marker": marker}


def load_result(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResultError(f"cannot read result: {exc}") from exc
    if not isinstance(data, dict) or set(data) != _RESULT_KEYS or data["schema"] != 1:
        raise ResultError("result keys or schema")
    if data["note"] is not None and not isinstance(data["note"], str):
        raise ResultError("note")
    if not isinstance(data["files"], list):
        raise ResultError("files")
    return {"files": [_check_file(entry) for entry in data["files"]]}


def acceptance_for(path: str) -> str:
    return f"bin/venv-python -m scripts.checks.hygiene.derived_list_scan --assert-dispositioned {path}"


def rec_fields(entry: dict[str, Any]) -> dict[str, Any]:
    path, words = entry["path"], KIND_WORDS[entry["kind"]]
    step = entry["step"] or 1
    order = "\n".join(f"{n}. {text}" for n, text in enumerate(_STEPS, start=1))
    context = (
        f"{TOKEN}{path}\n"
        f"{path} is a hand-kept list of {entry['entries']} entries naming {words} the repo can already enumerate, so "
        "every new entity means a hand edit here: a silent source of drift, cap pressure and parallel-plan collisions.\n"
        f"Evidence (weekly derived-list scan, rec-5516): entries={entry['entries']} resolved_share={entry['resolved_share']} "
        f"coupled_appends={entry['coupled_appends']} (90-day commits whose added line here names a file added in the same "
        f"commit) size_pressure={entry['size_pressure']} score={entry['score']}.\n"
        f"Remediation order (take the first that fits):\n{order}\n"
        f"The scan suggests step {step}"
        + (" (a registered check already reads this list)." if step == 2 else ".")
        + "\nTo decline, add the hand-kept marker (step 6): a rec closed without one is re-filed by the next weekly run "
        "(Decision 103)."
    )
    return {
        "title": f"Derived-list candidate: {path} ({entry['entries']} entries naming {words})",
        "file": path,
        "status": "open",
        "source": "code-smell",
        "priority": "Medium",
        "effort": "M",
        "risk": "low",
        "automatable": False,
        "verification_tier": "V1",
        "tags": ["derived-list"],
        "context": context,
        "acceptance": acceptance_for(path),
    }


def _token_path(rec: dict[str, Any]) -> Optional[str]:
    first = (rec.get("context") or "").split("\n", 1)[0].strip()
    return first[len(TOKEN) :] if first.startswith(TOKEN) and len(first) > len(TOKEN) else None


def open_by_path(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for rec in rec_episode.find_recs(SOURCE, rows=rows):
        path = _token_path(rec)
        if path is not None:
            by_path.setdefault(path, rec)
    return by_path


def _file(fields: dict[str, Any], portal_caller: Optional[PortalCaller], profile: Optional[str]) -> Any:
    if portal_caller is not None:
        return portal_caller("file", fields)
    from scripts import ops_data_portal  # noqa: PLC0415

    return ops_data_portal.file_rec(fields, profile=profile)


def run(
    result: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    max_open: int = DEFAULT_MAX,
    portal_caller: Optional[PortalCaller] = None,
    profile: Optional[str] = None,
    dry_run: bool = False,
) -> tuple[int, str]:
    """File into the free slots; returns (failed write count, the summary line)."""
    existing = open_by_path(rows)
    candidates = [f for f in result["files"] if f["class"] == "derived_candidate"]
    unmarked = sorted((f for f in candidates if not f["marker"]), key=lambda f: (-f["score"], f["path"]))
    live = {f["path"] for f in unmarked}
    slots = max(0, max_open - len(existing))
    filed, failures = 0, []
    verb = "would_file" if dry_run else "filed"
    for entry in [f for f in unmarked if f["path"] not in existing][:slots]:
        if dry_run:
            filed += 1
            print(f"[derived_list] would_file {entry['path']}")
            continue
        try:
            rec_id = _file(rec_fields(entry), portal_caller, profile)
        except Exception as exc:  # noqa: BLE001 -- every write is attempted; the failure is named and exits 1
            failures.append(f"{entry['path']}: {type(exc).__name__}: {_CTRL_RE.sub('', str(exc))[:_MAX_TEXT]}")
            continue
        filed += 1
        print(f"[derived_list] filed {entry['path']} rec={rec_id}")
    for failure in failures:
        print(f"[derived_list] FAILED {failure}")
    summary = (
        f"[derived_list] summary candidates={len(candidates)} {verb}={filed} unchanged={len(existing)} "
        f"skipped_marked={len(candidates) - len(unmarked)} stale_open={sum(1 for p in existing if p not in live)}"
    )
    return len(failures), summary


def main(argv: Optional[list[str]] = None, *, portal_caller: Optional[PortalCaller] = None, rows: Any = None) -> int:
    parser = argparse.ArgumentParser(description="File at most --max derived-list recs from a derived_list_scan --result")
    parser.add_argument("--file", required=True)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile", default=None)
    args = parser.parse_args(argv)
    try:
        result = load_result(args.file)
    except ResultError as exc:
        print(f"[derived_list] result error: {exc}")
        return 2
    try:
        live_rows = rows
        if live_rows is None:
            reader = rec_episode._make_reader(args.profile)
            live_rows = reader.current_state("ops_recommendations", row_filter=f"source = '{SOURCE}'")
        open_by_path(live_rows)
    except Exception as exc:  # noqa: BLE001 -- a failed lookup writes nothing (Decision 215)
        print(f"[derived_list] lookup error: {type(exc).__name__}: {_CTRL_RE.sub('', str(exc))[:_MAX_TEXT]}")
        return 2
    failed, summary = run(
        result, live_rows, max_open=args.max, portal_caller=portal_caller, profile=args.profile, dry_run=args.dry_run
    )
    print(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
