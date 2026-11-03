"""CLI over the core only: ranked open recommendations related to files, as JSON (default) or one line per hit.

    bin/venv-python -m scripts.recs_related FILE [FILE ...] [--terms "a b"] [--limit N] [--exclude IDS] [--table]

Exit codes: 0 ok; 2 an unusable query (no files and no terms, bad arguments); 1 a reader or repo failure, with the
error on stderr (a failure is never an empty result, Decision 55).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from scripts.recs_related.envelope import Envelope, Hit
from scripts.recs_related.related_recs import DEFAULT_LIMIT, MAX_LIMIT, related_recs


def _limit(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer") from exc
    if not 1 <= value <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"must be 1..{MAX_LIMIT}; got {value}")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.recs_related", description="Open recommendations related to files.")
    parser.add_argument("files", nargs="*", help="repo-relative (or absolute in-repo) paths")
    parser.add_argument("--terms", default="", help="free-text keywords, whitespace separated")
    parser.add_argument(
        "--limit", type=_limit, default=DEFAULT_LIMIT, help=f"page size, 1..{MAX_LIMIT} (default {DEFAULT_LIMIT})"
    )
    parser.add_argument("--exclude", default="", help="comma-separated rec ids to skip (the previous page's next_exclude)")
    parser.add_argument("--table", action="store_true", help="one line per hit plus an envelope line, instead of JSON")
    return parser


def _tier(hit: Hit) -> str:
    return "near(dir)" if hit.tier == "near" and all(w.startswith("ancestor_dir:") for w in hit.why) else hit.tier


def _table(env: Envelope) -> str:
    lines = [
        "\t".join([h.id, h.priority or "-", _tier(h), f"{h.score:.2f}", h.file or "-", h.title, "; ".join(h.why)])
        for h in env.hits
    ]
    channels = ",".join(f"{name}={status}" for name, status in env.channels.items())
    lines.append(
        f"# total={env.total} returned={env.returned} complete={str(env.complete).lower()} "
        f"direct_total={env.direct_total} direct_complete={str(env.direct_complete).lower()} "
        f"next_exclude={','.join(env.next_exclude) if env.next_exclude else '-'} channels[{channels}]"
    )
    lines.extend(f"# note: {note}" for note in env.notes)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.files and not args.terms.strip():
        print("recs_related: pass at least one FILE or --terms", file=sys.stderr)
        return 2
    exclude = [i for i in args.exclude.split(",") if i.strip()]
    try:
        env = related_recs(args.files, args.terms, args.limit, exclude)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 -- the CLI boundary: any reader or repo failure is reported and exits non-zero
        print(f"recs_related: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(_table(env) if args.table else env.to_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
