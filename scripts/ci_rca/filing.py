"""Parser for ci-rca agent filing signal.

Reads a `claude -p --output-format json` transcript file and extracts the
rec id from the agent's terminal `FILED: rec-NNN` marker. Never matches a
bare rec-NNN mention -- only the explicit marker counts as a real filing.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Single ordered scan so the LAST marker wins (honours the "multiple markers -> last"
# contract). The `none` alternative is case-insensitive; rec ids are lowercase by the
# agent contract (file_rec emits lowercase ids), so rec matching stays case-sensitive.
# CIRCA-07: the category token is OPTIONAL in the trailing group so a legacy single-token
# `FILED: rec-NNN` marker (no category) still parses -- backward compat for extract_filed_rec_id.
_MARKER_PATTERN = re.compile(
    r"^[ \t]*FILED:[ \t]*(rec-\d+|smoke-\d+|[Nn][Oo][Nn][Ee])(?:[ \t]+([A-Za-z0-9_]+))?[ \t]*$", re.MULTILINE
)


def _admitted(rec_id: str) -> bool:
    """A smoke-N marker counts only while the CI RCA smoke sink is armed (rec-5493), never in a real run."""
    return not rec_id.startswith("smoke-") or bool(os.environ.get("CI_RCA_SMOKE_SINK"))


def extract_filed_rec_id(output_path: str | Path) -> str | None:
    """Return the rec id from the FILED: marker, or None if not found.

    Tries to parse the file as a JSON envelope (claude -p --output-format json)
    and extracts text from the 'result' field. Falls back to raw text on parse
    failure; a file that is not one JSON envelope yields no marker from inside an
    escaped JSON string.

    Scans all FILED: markers in document order and returns the LAST one. A
    trailing `FILED: none` (or no marker at all) yields None; an earlier
    `FILED: none` does NOT suppress a later `FILED: rec-NNN`. Never matches a
    bare rec-NNN substring outside the marker.
    """
    path = Path(output_path)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    text = _extract_text(raw)

    matches = [m for m in _MARKER_PATTERN.findall(text) if _admitted(m[0])]
    if not matches:
        return None
    last = matches[-1][0]
    return None if last.lower() == "none" else last


def extract_filed_recs(output_path: str | Path) -> list[tuple[str, str]]:
    """Return ALL (rec_id, category) pairs from FILED: markers, in document order (CIRCA-07).

    One rec per failed check is now mandatory (one bundle = one filing per category), so a
    multi-failure run emits one `FILED: rec-NNN <category>` marker per rec. The single-marker
    (no category token) and `FILED: none` cases still behave: a marker without a category
    yields category=='' (legacy compat); `FILED: none` entries are skipped entirely (not a
    filing). Never matches a bare rec-NNN mention outside the marker.
    """
    path = Path(output_path)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    text = _extract_text(raw)
    matches = [m for m in _MARKER_PATTERN.findall(text) if _admitted(m[0])]
    pairs: list[tuple[str, str]] = []
    for rec_id, category in matches:
        if rec_id.lower() == "none":
            continue
        pairs.append((rec_id, category))
    return pairs


def _extract_text(raw: str) -> str:
    """Try to extract 'result' string from JSON envelope; fall back to raw."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw
    if isinstance(data, dict):
        result = data.get("result")
        if isinstance(result, str) and result:
            return result
    return raw


def _warn_if_corrupt_envelope(path: str) -> None:
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if not any(line.startswith("{") for line in raw.splitlines()):
        return
    try:
        json.loads(raw)
    except ValueError as exc:
        print(
            f"ci_rca.filing: {path} is not a single claude -p JSON envelope ({exc}); scanning raw text",
            file=sys.stderr,
        )


def main() -> None:
    argv = sys.argv[1:]
    all_mode = "--all" in argv
    positional = [a for a in argv if a != "--all"]
    if len(positional) != 1:
        print("Usage: ci_rca_filing.py [--all] <output_path>", file=sys.stderr)
        sys.exit(1)

    _warn_if_corrupt_envelope(positional[0])

    if all_mode:
        # CIRCA-07: one "rec_id,category" line per filed rec (workflow's per-category mark_rca
        # gating). Legacy single-marker entries print with an empty category (rec_id,).
        for rec_id, category in extract_filed_recs(positional[0]):
            print(f"{rec_id},{category}")
        return

    rec_id = extract_filed_rec_id(positional[0])
    if rec_id is None:
        print("No FILED: rec-NNN marker found in output", file=sys.stderr)
        sys.exit(1)

    print(rec_id)


if __name__ == "__main__":
    main()
