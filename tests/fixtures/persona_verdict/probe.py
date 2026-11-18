# ruff: noqa: INP001
"""The deterministic non-LLM probe persona of the verdict chain (PLAN-persona-verdict-chain).

Reads one input JSON path from argv and prints a result chosen by the input's stripped marker. Stdlib only; no network.
Removed by PLAN-persona-verdict-fixture-run.
"""

from __future__ import annotations

import json
import sys


def result_for(marker: str) -> str:
    if marker == "proceed":
        return json.dumps({"outcome": "success", "verdict": "PROCEED"})
    if marker == "revise":
        return json.dumps(
            {
                "outcome": "success",
                "verdict": "REVISE",
                "findings": [{"id": "probe:revise-1", "title": "probe revise finding", "blocking": True}],
            }
        )
    if marker == "block":
        findings = [
            {"id": "probe:block-1", "title": "probe block finding one", "blocking": True},
            {"id": "probe:block-2", "title": "probe block finding two", "blocking": True},
        ]
        return json.dumps({"outcome": "success", "verdict": "BLOCK", "findings": findings})
    if marker == "flags":
        findings = [{"id": "probe:flag-1", "title": "probe flag finding", "blocking": False}]
        return json.dumps({"outcome": "success", "verdict": "FLAGS_FOUND", "findings": findings})
    if marker == "fail":
        return json.dumps({"outcome": "failure", "failure": "input_unusable", "detail": "probe fail marker"})
    return "this is not json {"


def main(argv: list[str]) -> int:
    with open(argv[0], encoding="utf-8") as handle:
        marker = str(json.load(handle).get("marker", "")).strip()
    print(result_for(marker))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
