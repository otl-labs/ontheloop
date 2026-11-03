# complexity-waiver: decision-43  (evaluate_rec_relevance is a multi-path decision tree by design)
"""Recommendation relevance evaluator (T3.8 / CD.36).

Deterministic-first: acceptance probe -> target-existence -> decision-contradiction.
Semantic fallback: commit-file correlation + closed-sibling Jaccard + open-duplicate.
Returns (verdict, evidence). Never raises at import time.
"""

from __future__ import annotations

import argparse
import importlib
import re
import subprocess
import sys
from pathlib import Path

RELEVANCE_VERDICTS: frozenset[str] = frozenset(
    {
        "relevant",
        "satisfied",
        "superseded",
        "duplicate",
        "contradicted",
        "stale_target",
        "blocked_by_decision",
        "unknown",
    }
)

_ACCEPTANCE_TIMEOUT_S: int = 10

_DECISION_ID_RE = re.compile(r"\b(?:Decision|CD)\.?\s*(\d+)\b", re.IGNORECASE)


def _run_acceptance_probe(cmd: str, timeout: int = _ACCEPTANCE_TIMEOUT_S) -> bool:
    """Run the acceptance command. Returns True on exit code 0, False on failure or timeout."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _title_tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _title_jaccard(a: str, b: str) -> float:
    """Lowercased alphanumeric-token Jaccard similarity between two title strings."""
    ta = _title_tokens(a)
    tb = _title_tokens(b)
    union = ta | tb
    if not union:
        return 0.0
    return len(ta & tb) / len(union)


def _file_paths_correlate(rec_file: str, changed: str) -> bool:
    """Return True when rec_file and changed refer to the same repo file (suffix match)."""
    if rec_file == changed:
        return True
    parts_rec = rec_file.split("/")
    parts_changed = changed.split("/")
    n = min(len(parts_rec), len(parts_changed))
    return parts_rec[-n:] == parts_changed[-n:]


def _scan_decision_contradiction(
    rec: dict,
    decisions: list[dict],
) -> tuple[str, str] | None:
    """Return (verdict, evidence) if rec cites a decision in a contradicting/pending state, else None."""
    text = " ".join(filter(None, [rec.get("context"), rec.get("acceptance"), rec.get("title")]))
    cited_ids = {m.group(1) for m in _DECISION_ID_RE.finditer(text)}
    if not cited_ids:
        return None
    for dec in decisions:
        dec_num = str(dec.get("decision_id") or "").strip()
        if not dec_num or dec_num not in cited_ids:
            continue
        status_lower = (dec.get("status") or "").lower()
        if "superseded by" in status_lower:
            return "contradicted", f"Decision {dec_num} is superseded (status: {status_lower[:80]})"
        if "pending" in status_lower:
            return "blocked_by_decision", f"Decision {dec_num} is pending (status: {status_lower[:80]})"
    return None


def evaluate_rec_relevance(
    rec: dict,
    *,
    run_acceptance_probe: bool = False,
    acceptance_timeout: int = _ACCEPTANCE_TIMEOUT_S,
    recent_commits: list[dict] | None = None,
    closed_recs: list[dict] | None = None,
    open_recs: list[dict] | None = None,
    decisions: list[dict] | None = None,
    repo_root: Path | None = None,
) -> tuple[str, str]:
    """Return (verdict, evidence) for a single open rec. Deterministic-first.

    Deterministic signals (checked in order):
    1. Acceptance probe (only when run_acceptance_probe=True and acceptance is not None/empty).
       A rec with acceptance=None/empty cannot be probed; it resolves to a semantic or unknown verdict.
    2. Target-file existence (if rec.file is set and path is absent from repo root).
    3. Decision-contradiction scan (deterministic, reads decisions list only).

    Semantic fallback:
    4a. Commit-file correlation: file modified in a recent commit after rec creation.
    4b. Closed-sibling Jaccard: title similarity >= 0.5 to a closed rec on the same file.
    4c. Open duplicate: title similarity >= 0.7 to another open rec on the same file.
    5. Default: "relevant" or "unknown" when no signals are available.
    """
    root = repo_root or Path.cwd()
    acceptance_cmd = (rec.get("acceptance") or "").strip()

    if run_acceptance_probe and acceptance_cmd:
        if _run_acceptance_probe(acceptance_cmd, timeout=acceptance_timeout):
            return "satisfied", f"acceptance probe passed: {acceptance_cmd}"

    rec_file = (rec.get("file") or "").strip()
    if rec_file:
        target = root / rec_file if not Path(rec_file).is_absolute() else Path(rec_file)
        if not target.exists():
            return "stale_target", f"target file absent: {rec_file}"

    if decisions:
        dec_result = _scan_decision_contradiction(rec, decisions)
        if dec_result is not None:
            return dec_result

    if recent_commits and rec_file:
        rec_created = (rec.get("created_timestamp") or "").strip()
        for commit in recent_commits:
            commit_date = (commit.get("date") or "").strip()
            if rec_created and commit_date and commit_date <= rec_created:
                continue
            for changed in commit.get("files") or []:
                if _file_paths_correlate(rec_file, changed):
                    sha = (commit.get("sha") or "")[:8]
                    return "satisfied", f"semantic: file {rec_file} modified in commit {sha}"

    rec_title = (rec.get("title") or "").strip()
    if closed_recs and rec_title:
        for sibling in closed_recs:
            sib_file = (sibling.get("file") or "").strip()
            if rec_file and sib_file and not _file_paths_correlate(rec_file, sib_file):
                continue
            if _title_jaccard(rec_title, sibling.get("title") or "") >= 0.5:
                sib_id = sibling.get("id") or ""
                return "superseded", f"semantic: closed sibling {sib_id} title similarity >= 0.5"

    rec_id = (rec.get("id") or "").strip()
    if open_recs and rec_title:
        for other in open_recs:
            if (other.get("id") or "").strip() == rec_id:
                continue
            other_file = (other.get("file") or "").strip()
            if rec_file and other_file and not _file_paths_correlate(rec_file, other_file):
                continue
            if _title_jaccard(rec_title, other.get("title") or "") >= 0.7:
                other_id = other.get("id") or ""
                return "duplicate", f"semantic: open duplicate {other_id} title similarity >= 0.7"

    if not recent_commits and not closed_recs and not decisions:
        return "unknown", "no signals available to assess relevance"
    return "relevant", "no resolution signals detected"


def implement_recheck(rec_ids: list[str], *, reader=None) -> list[tuple[str, str, str, str | None]]:  # noqa: ANN001
    """Re-check bundled recs at /implement time: (rec_id, verdict, evidence, proposal). NEVER closes a rec
    (rec-4592): no deterministic auto-close and no update_rec; the merged trailer closes. A rec the reader
    lacks yields verdict "missing". A satisfied rec gets no proposal."""
    # Loaded by name, not `import`: the contract validator imports this module only for RELEVANCE_VERDICTS, and a
    # static edge to the portal and reader would widen its pre_globs closure to ~56 unrelated files.
    portal = importlib.import_module("scripts.ops_data_portal")
    reader_client = importlib.import_module("src.common.ducklake_reader_client")
    rows, _missing = reader_client.fetch_recs_by_ids(reader if reader is not None else reader_client.make_reader(), rec_ids)
    by_id = {r.get("id"): r for r in rows}
    results: list[tuple[str, str, str, str | None]] = []
    for rec_id in rec_ids:
        rec = by_id.get(rec_id)
        if rec is None:
            results.append((rec_id, "missing", "", None))
            continue
        verdict, evidence = evaluate_rec_relevance(rec, run_acceptance_probe=True)
        proposal = None
        if verdict not in ("relevant", "unknown", "satisfied"):
            proposal = portal.propose_or_close_rec(rec_id, verdict, evidence, deterministic=False)
        results.append((rec_id, verdict, evidence, proposal))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.rec_relevance")
    parser.add_argument("--implement-recheck", required=True, metavar="rec-A[,rec-B]")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    ids = [i.strip() for i in args.implement_recheck.split(",") if i.strip()]
    exit_code = 0
    for rec_id, verdict, evidence, proposal in implement_recheck(ids):
        if verdict == "missing":
            print(f"{rec_id} missing")
            exit_code = 1
            continue
        print(f"{rec_id} {verdict} | {evidence[:120]}")
        if proposal:
            print(proposal)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
