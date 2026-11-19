"""The append-only census's filer (rec-5410): turns a census --result file into deduplicated recommendations.

One Medium rec per offending merge (token `merge=<sha>` on the context's first line) plus one ledger-defect rollup
(token `census_class=ledger_defect`). A rec is updated only when its text changes and closed only when the census
replayed its merge and found it acknowledged; a rec a person closed is not open, so a still-open finding is re-filed.
The result file is untrusted input. Every write is attempted; a failed write exits 1 (Decision 84 I-4, no outbox).
Exit 2 (nothing written): malformed result, failed lookup, or an assertion run whose census cannot complete.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from scripts import rec_episode
from scripts.checks import _common

SOURCE = "decision_census"
KINDS = ("embedding", "stanza", "vanished", "stub")
_RESULT_KEYS = {"schema", "head", "merges", "replayed", "acknowledged", "open", "ledger_defects"}
_OPEN_KEYS = {"merge", "pr", "decisions"}
_DECISION_KEYS = {"decision", "digest", "kinds", "line"}
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_DIGEST_RE = re.compile(r"[0-9a-f]{16}")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_TEXT = 300
_LEDGER_TOKEN = "census_class=ledger_defect"
_LEDGER_ACCEPTANCE = "bin/venv-python -m scripts.checks.decisions.append_only_census_recs --assert-ledger-clean"
_LEDGER_REL = "config/agent/decision_census.yaml"

_MERGE_DISPOSITION = (
    "Disposition: land a dated Update on each listed Decision naming the merge, plus one ledger row per decision in "
    "config/agent/decision_census.yaml, in one PR. A Resolves trailer may close this rec, otherwise the census closes "
    "it on its next run once the merge is acknowledged. Never restore the old text (Decision 177)."
)
_LEDGER_DISPOSITION = (
    "Disposition: fix each listed ledger row (stale digest, missing annotation or a row matching no finding). Only the "
    "census closes this rec; a Resolves trailer naming it is refused by design."
)

PortalCaller = Callable[[str, dict[str, Any]], Any]


class ResultError(Exception):
    """The census result file is malformed (exit 2)."""


def _clean(text: Any) -> str:
    return _CTRL_RE.sub("", text)[:_MAX_TEXT]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_decision(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != _DECISION_KEYS:
        raise ResultError("decision entry keys")
    number, digest, kinds, line = entry["decision"], entry["digest"], entry["kinds"], entry["line"]
    if not _is_int(number) or number < 1:
        raise ResultError("decision number")
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        raise ResultError("digest")
    if not isinstance(kinds, list) or not kinds or any(k not in KINDS for k in kinds):
        raise ResultError("kinds")
    if line is not None and not isinstance(line, str):
        raise ResultError("line")
    return {"decision": number, "digest": digest, "kinds": list(kinds), "line": None if line is None else _clean(line)}


def _check_sha(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise ResultError(f"{what} is not a 40-hex sha")
    return value


def load_result(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ResultError(f"cannot read result: {exc}") from exc
    if not isinstance(data, dict) or set(data) != _RESULT_KEYS or data["schema"] != 1:
        raise ResultError("result keys or schema")
    _check_sha(data["head"], "head")
    if not _is_int(data["merges"]) or not _is_int(data["acknowledged"]):
        raise ResultError("counts")
    if not isinstance(data["replayed"], list):
        raise ResultError("replayed")
    replayed = {_check_sha(sha, "replayed entry") for sha in data["replayed"]}
    if not isinstance(data["ledger_defects"], list) or any(not isinstance(d, str) for d in data["ledger_defects"]):
        raise ResultError("ledger_defects")
    if not isinstance(data["open"], list):
        raise ResultError("open")
    opened = []
    for entry in data["open"]:
        if not isinstance(entry, dict) or set(entry) != _OPEN_KEYS:
            raise ResultError("open entry keys")
        merge = _check_sha(entry["merge"], "open merge")
        if merge not in replayed:
            raise ResultError("open merge missing from replayed")
        if entry["pr"] is not None and not _is_int(entry["pr"]):
            raise ResultError("pr")
        if not isinstance(entry["decisions"], list) or not entry["decisions"]:
            raise ResultError("decisions")
        opened.append({"merge": merge, "pr": entry["pr"], "decisions": [_check_decision(d) for d in entry["decisions"]]})
    return {
        "replayed": replayed,
        "open": opened,
        "ledger_defects": [_clean(d) for d in data["ledger_defects"]],
    }


def acceptance_for(merge: str, decisions: list[int]) -> str:
    alternation = "|".join(str(n) for n in decisions)
    return (
        f"test \"$(grep -cE '^  - \\{{merge: {merge}, decision: ({alternation}), ' {_LEDGER_REL})\" -eq {len(decisions)} && "
        f"grep -qE '^> \\*\\*Update \\(.*{merge[:8]}' docs/DECISIONS.md docs/DECISIONS_ARCHIVE.md"
    )


def _numbers(entry: dict[str, Any]) -> list[int]:
    return sorted(d["decision"] for d in entry["decisions"])


def _merge_title(entry: dict[str, Any]) -> str:
    pr = f" (#{entry['pr']})" if entry["pr"] is not None else ""
    listed = ", ".join(str(n) for n in _numbers(entry))
    return f"Append-only census: merge {entry['merge'][:8]}{pr} rewrote ratified lines in Decision {listed}"


def _merge_context(entry: dict[str, Any]) -> str:
    lines = [f"merge={entry['merge']}"]
    for d in sorted(entry["decisions"], key=lambda x: x["decision"]):
        shown = d["line"] if d["line"] is not None else "(none)"
        lines.append(f"Decision {d['decision']} digest={d['digest']} kinds={','.join(d['kinds'])} line={shown}")
    lines.append(_MERGE_DISPOSITION)
    return "\n".join(lines)


def _merge_fields(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": _merge_title(entry),
        "file": "docs/DECISIONS.md",
        "status": "open",
        "source": "decision_census",
        "priority": "Medium",
        "effort": "S",
        "risk": "low",
        "automatable": False,
        "verification_tier": "V1",
        "context": _merge_context(entry),
        "acceptance": acceptance_for(entry["merge"], _numbers(entry)),
    }


def _ledger_fields(defects: list[str]) -> dict[str, Any]:
    body = "\n".join(f"LEDGER_DEFECT {d}" for d in defects)
    return {
        "title": f"Append-only census: {len(defects)} acknowledgement ledger defect(s)",
        "file": _LEDGER_REL,
        "status": "open",
        "source": "decision_census",
        "priority": "Medium",
        "effort": "S",
        "risk": "low",
        "automatable": False,
        "verification_tier": "V1",
        "context": f"{_LEDGER_TOKEN}\n{body}\n{_LEDGER_DISPOSITION}",
        "acceptance": _LEDGER_ACCEPTANCE,
    }


def _tokens(rec: dict[str, Any]) -> list[str]:
    first = (rec.get("context") or "").split("\n", 1)[0]
    return first.split()


def _merge_of(rec: dict[str, Any]) -> Optional[str]:
    for token in _tokens(rec):
        if token.startswith("merge="):
            return token[len("merge=") :]
    return None


def _resolution(what: str) -> str:
    run = ""
    if os.environ.get("GITHUB_RUN_ID") and os.environ.get("GITHUB_REPOSITORY"):
        run = f" Run: https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}."
    return f"Append-only census: {what} is acknowledged in {_LEDGER_REL} on main.{run}"


class _Writer:
    def __init__(self, portal_caller: Optional[PortalCaller], profile: Optional[str], dry_run: bool) -> None:
        self.portal_caller, self.profile, self.dry_run = portal_caller, profile, dry_run
        self.failed: list[str] = []
        self.counts = {"filed": 0, "updated": 0, "unchanged": 0, "closed": 0}

    def _call(self, verb: str, payload: dict[str, Any]) -> Any:
        if self.portal_caller is not None:
            return self.portal_caller(verb, payload)
        from scripts import ops_data_portal  # noqa: PLC0415

        if verb == "file":
            return ops_data_portal.file_rec(payload, profile=self.profile)
        rec_id = payload["id"]
        ops_data_portal.update_rec(rec_id, {k: v for k, v in payload.items() if k != "id"}, profile=self.profile)
        return rec_id

    def act(self, verb: str, label: str, payload: Optional[dict[str, Any]], rec: Optional[dict[str, Any]]) -> None:
        shown = "" if rec is None else f" rec={rec['id']}"
        if verb == "unchanged":
            self.counts["unchanged"] += 1
            print(f"unchanged {label}{shown}")
            return
        past = {"file": "filed", "update": "updated", "close": "closed"}[verb]
        if self.dry_run:
            self.counts[past] += 1
            print(f"would-{verb} {label}{shown}")
            return
        try:
            result = self._call("file" if verb == "file" else "update", payload or {})
        except Exception as exc:  # noqa: BLE001 -- every write is attempted; the failure is named and exits 1
            self.failed.append(f"{verb} {label}: {type(exc).__name__}: {_clean(str(exc))}")
            return
        self.counts[past] += 1
        rec_id = result if verb == "file" else rec["id"]  # type: ignore[index]
        print(f"{past} {label} rec={rec_id}")


def _episode(
    writer: _Writer,
    label: str,
    desired: Optional[dict[str, Any]],
    existing: Optional[dict[str, Any]],
    close_ok: bool,
    what: str,
) -> str:
    if desired is not None and existing is None:
        writer.act("file", label, desired, None)
        return "filed"
    if desired is not None and existing is not None:
        changed = {k: desired[k] for k in ("title", "context", "acceptance") if existing.get(k) != desired[k]}
        if not changed:
            writer.act("unchanged", label, None, existing)
            return "unchanged"
        writer.act("update", label, {"id": existing["id"], **changed}, existing)
        return "updated"
    if existing is not None and close_ok:
        writer.act("close", label, {"id": existing["id"], "status": "closed", "resolution": _resolution(what)}, existing)
        return "closed"
    return "none"


def run(
    result: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    portal_caller: Optional[PortalCaller] = None,
    profile: Optional[str] = None,
    dry_run: bool = False,
) -> tuple[int, str]:
    """Apply the episodes; returns (failed write count, the summary line)."""
    open_recs = rec_episode.find_recs(SOURCE, rows=rows)
    by_merge: dict[str, dict[str, Any]] = {}
    ledger_rec: Optional[dict[str, Any]] = None
    for rec in open_recs:
        merge = _merge_of(rec)
        if merge is not None:
            by_merge.setdefault(merge, rec)
        elif _LEDGER_TOKEN in _tokens(rec) and ledger_rec is None:
            ledger_rec = rec
    writer = _Writer(portal_caller, profile, dry_run)
    open_merges = {entry["merge"]: entry for entry in result["open"]}
    for merge, entry in open_merges.items():
        _episode(writer, f"merge={merge}", _merge_fields(entry), by_merge.get(merge), False, "")
    for merge, rec in by_merge.items():
        if merge not in open_merges and merge in result["replayed"]:
            _episode(writer, f"merge={merge}", None, rec, True, f"merge {merge[:8]}")
    defects = result["ledger_defects"]
    ledger = _episode(
        writer, "ledger", _ledger_fields(defects) if defects else None, ledger_rec, True, "the ledger defect set"
    )
    c = writer.counts
    summary = (
        f"[decision_census] summary open_merges={len(open_merges)} filed={c['filed']} updated={c['updated']} "
        f"unchanged={c['unchanged']} closed={c['closed']} ledger={ledger}"
    )
    for failure in writer.failed:
        print(f"FAILED {failure}")
    return len(writer.failed), summary


def _assert_ledger_clean(head: str) -> int:
    from scripts.checks.decisions import append_only_census as census_mod  # noqa: PLC0415

    try:
        result = census_mod.census(_common.ROOT, head)
    except census_mod.CensusError as exc:
        print(f"CENSUS ERROR: {exc}")
        return 2
    if not result.self_test_ok:
        print("CENSUS ERROR: self-test failed, the detector is blind")
        return 2
    for defect in result.defects:
        print(f"LEDGER_DEFECT {defect}")
    return 1 if result.defects else 0


def main(argv: Optional[list[str]] = None, *, portal_caller: Optional[PortalCaller] = None, rows: Any = None) -> int:
    parser = argparse.ArgumentParser(description="File, update and close decision_census recs from a census --result")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", default=None)
    group.add_argument("--assert-ledger-clean", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    if args.assert_ledger_clean:
        return _assert_ledger_clean(args.head)
    try:
        result = load_result(args.file)
    except ResultError as exc:
        print(f"RESULT ERROR: {exc}")
        return 2
    try:
        live_rows = rows
        if live_rows is None:
            reader = rec_episode._make_reader(args.profile)
            live_rows = reader.current_state("ops_recommendations", row_filter=f"source = '{SOURCE}'")
        rec_episode.find_recs(SOURCE, rows=live_rows)
    except Exception as exc:  # noqa: BLE001 -- a failed lookup writes nothing (Decision 215)
        print(f"LOOKUP ERROR: {type(exc).__name__}: {_clean(str(exc))}")
        return 2
    failed, summary = run(result, live_rows, portal_caller=portal_caller, profile=args.profile, dry_run=args.dry_run)
    print(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
