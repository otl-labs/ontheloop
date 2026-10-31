"""Advisory acceptance_decidable runner: executed only as a subprocess by plan_lint, imported by no module.

Usage: python -m scripts.roadmap.plan_lint.acceptance <rec-id>...   Prints one line per rec.
"""

from __future__ import annotations

import sys
from typing import Any, Callable


def _default_fetch(ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    from src.common.ducklake_reader_client import fetch_recs_by_ids, make_reader  # noqa: PLC0415

    return fetch_recs_by_ids(make_reader(profile="agent_platform"), ids)


def main(
    argv: list[str] | None = None, *, fetch: Callable[[list[str]], tuple[list[dict[str, Any]], list[str]]] | None = None
) -> int:
    from scripts.executor.acceptance_lint import lint_acceptance_command  # noqa: PLC0415

    ids = list(sys.argv[1:] if argv is None else argv)
    rows, missing = (fetch or _default_fetch)(ids)
    for rec in rows:
        ok, reason = lint_acceptance_command(rec.get("acceptance"), require_decidable=True)
        first = str(reason).strip().splitlines()[0] if reason else "no reason given"
        print(f"{rec.get('id')}: acceptance decidable" if ok else f"{rec.get('id')}: acceptance UNDECIDABLE ({first})")
    for rec_id in missing:
        print(f"{rec_id}: not found by the reader")
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
