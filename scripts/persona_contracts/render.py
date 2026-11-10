"""Deterministic renderer for persona prompts (T4.10a c5 mechanism).

One contract always renders the same bytes: string.Template.substitute with exactly the index's placeholders, the
quoted-content clause first, LF line endings, one trailing newline, UTF-8. A rendered line ending in whitespace is
refused so the trailing-whitespace hook can never rewrite committed prompt bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import string
import sys
from pathlib import Path

from scripts.persona_contracts.schema import ROOT, PersonaContract, PersonaIndex, load_contract, load_index

_ESCAPED_DOLLAR = re.compile(r"\$\$")


def _placeholder_values(contract: PersonaContract) -> dict[str, str]:
    budget = contract.budget
    return {
        "persona": contract.persona,
        "contract_version": str(contract.contract_version),
        "capabilities": "\n".join(contract.capabilities),
        "output_schema": json.dumps(contract.output_schema, sort_keys=True, indent=2),
        "failure_outputs": "\n".join(f"{f.id}: {f.meaning}" for f in contract.failure_outputs),
        "budget": "\n".join(f"{k}: {budget.get(k)}" for k in ("max_turns", "max_wall_clock_seconds", "max_revisions")),
    }


def render(contract: PersonaContract, index: PersonaIndex) -> bytes:
    if _ESCAPED_DOLLAR.search(contract.prompt_template):
        raise ValueError("prompt_template holds a stray '$$'")
    values = _placeholder_values(contract)
    allowed = set(index.rendered_prompt.placeholders)
    values = {k: v for k, v in values.items() if k in allowed}
    body = string.Template(contract.prompt_template).substitute(values)
    text = index.rendered_prompt.quoted_content_clause.strip() + "\n\n" + body
    text = text.replace("\r\n", "\n").rstrip("\n") + "\n"
    for number, line in enumerate(text.split("\n"), start=1):
        if line != line.rstrip(" \t"):
            raise ValueError(f"rendered line {number} ends in whitespace")
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contract_for(root: Path, row_contract: str) -> PersonaContract:
    return load_contract(root / row_contract)


def check(root: Path = ROOT) -> list[str]:
    index = load_index(root)
    stale: list[str] = []
    for row in index.personas:
        target = root / row.rendered_prompt
        try:
            fresh = render(_contract_for(root, row.contract), index)
        except (OSError, ValueError, KeyError) as exc:
            stale.append(f"{row.persona}: cannot render ({exc})")
            continue
        if not target.is_file():
            stale.append(f"{row.persona}: rendered prompt {row.rendered_prompt} is missing")
        elif target.read_bytes() != fresh:
            stale.append(f"{row.persona}: rendered prompt {row.rendered_prompt} is stale")
    return stale


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.persona_contracts.render")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--write", metavar="PERSONA")
    args = parser.parse_args(argv)
    if args.check:
        problems = check(root)
        for problem in problems:
            print(f"RENDER-CHECK FAIL {problem}")
        if problems:
            return 1
        print(f"RENDER-CHECK OK {len(load_index(root).personas)}")
        return 0
    index = load_index(root)
    rows = [r for r in index.personas if r.persona == args.write]
    if not rows:
        print(f"unknown persona {args.write}", file=sys.stderr)
        return 2
    data = render(_contract_for(root, rows[0].contract), index)
    out = root / rows[0].rendered_prompt
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(sha256_hex(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
