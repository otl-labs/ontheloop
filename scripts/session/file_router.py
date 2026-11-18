"""Route lookup over docs/contracts/file-router.yaml: print only the rows that match every term.

Loads the router through scripts.checks.hygiene.validate_placement's private _load_router and
_validate_routes_shape (no second parser; a private cross-module import, as scripts/session/merge_step.py
does). Usage: bin/venv-python -m scripts.session.file_router <term> [<term> ...] [--router PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.checks.hygiene.validate_placement import _load_router, _validate_routes_shape

_DEFAULT_ROUTER = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "file-router.yaml"


def _description(route: dict) -> str:
    desc = route.get("description")
    return " ".join(desc.split()) if isinstance(desc, str) else ""


def _format(route: dict) -> str:
    return f"{route['topic']}: {', '.join(route['targets'])} -- {_description(route)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.session.file_router", description=__doc__)
    parser.add_argument("terms", nargs="+", help="case-insensitive terms; a route must contain all of them")
    parser.add_argument("--router", type=Path, default=_DEFAULT_ROUTER, help="router file (tests)")
    args = parser.parse_args(argv)

    loaded = _load_router(args.router)
    if isinstance(loaded, str):
        print(f"file_router: {loaded}", file=sys.stderr)
        return 1
    valid, _malformed, _dups = _validate_routes_shape(loaded)

    terms = [t.lower() for t in args.terms]
    lines = []
    for route in valid:
        haystack = " ".join([route["topic"], *route["targets"], _description(route)]).lower()
        if all(t in haystack for t in terms):
            lines.append(_format(route))
    if not lines:
        print(f"file_router: no route matches all of: {' '.join(args.terms)}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    print(f"{len(lines)} route(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
